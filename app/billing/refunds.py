"""Refunds and chargebacks on invoices.

Every paid period has an invoice (first payment: ``confirm_subscription``;
renewals: ``renew_subscription``). A refund — recorded by a Super Admin or
reported by a signature-verified provider webhook — updates that invoice,
the matching payment records and the payment events, is audited and
notified, and lets the partner program reverse the commission earned on
that payment (``app.partners.commissions.on_payment_refunded``).

Partial refunds accumulate per invoice and can never exceed its total.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import HTTPException

from app.db.models import utcnow
from app.db.mongo import get_async_db

logger = logging.getLogger(__name__)


def _oid(v: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(v))
    except Exception:
        return None


def _invoice_total(inv: Dict[str, Any]) -> float:
    return round(float(inv.get("total") if inv.get("total") is not None else inv.get("amount") or 0), 2)


async def refund_invoice(invoice_id: str, *, amount: Optional[float], reason: str, actor: Any,
                         source: str = "admin", chargeback: bool = False, db=None) -> Dict[str, Any]:
    if db is None:
        db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    inv = await db.invoices.find_one({"_id": _oid(invoice_id)}) if _oid(invoice_id) else None
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if not (reason or "").strip():
        raise HTTPException(status_code=422, detail="A reason is required")
    total = _invoice_total(inv)
    already = round(float(inv.get("refunded_amount") or 0), 2)
    remaining = round(total - already, 2)
    if remaining <= 0:
        raise HTTPException(status_code=409, detail="This invoice is already fully refunded")
    amt = remaining if amount in (None, 0) else round(float(amount), 2)
    if amt <= 0 or amt > remaining + 0.004:
        raise HTTPException(status_code=422, detail=f"Refund must be between 0 and {remaining:.2f}")
    now = utcnow()
    new_total = round(already + amt, 2)
    status = "refunded" if new_total >= total - 0.004 else "partially_refunded"
    who = actor.get("email") if isinstance(actor, dict) else str(actor)
    await db.invoices.update_one({"_id": inv["_id"]}, {
        "$set": {"status": status, "refunded_amount": new_total, "refunded_at": now, "updated_at": now},
        "$push": {"refunds": {"amount": amt, "reason": reason[:300], "source": source,
                              "chargeback": chargeback, "at": now, "by": who}}})
    sub_id = inv.get("subscription_id")
    # the payment records of this invoice: the ones linked to it, plus — for the
    # subscription's FIRST invoice — the checkout payment (linked by subscription)
    clauses: List[Dict[str, Any]] = [{"invoice_id": str(inv["_id"])}]
    if sub_id:
        first = await db.invoices.find_one({"subscription_id": str(sub_id)}, {"_id": 1},
                                           sort=[("created_at", 1)])
        if first and first["_id"] == inv["_id"]:
            clauses.append({"subscription_id": str(sub_id),
                            "status": {"$in": ["succeeded", "partially_refunded", "refunded"]}})
    pay_q: Dict[str, Any] = {"$or": clauses}
    await db.payments.update_many(pay_q, {"$set": {
        "status": "refunded" if status == "refunded" else "partially_refunded",
        "refunded_amount": new_total, "refund_required": False, "chargeback": chargeback or None,
        "updated_at": now}})
    await db.payment_events.insert_one({
        "organization_id": inv.get("organization_id"), "subscription_id": sub_id,
        "event": "chargeback" if chargeback else "refunded",
        "data": {"invoice_id": str(inv["_id"]), "amount": amt, "reason": reason, "source": source},
        "created_at": now})
    from app.admin.audit import aaudit
    await aaudit("payment.chargeback" if chargeback else "payment.refunded", "billing", user=actor,
                 organization_id=inv.get("organization_id"), resource_type="invoice",
                 resource_id=str(inv["_id"]),
                 details={"amount": amt, "total_refunded": new_total, "reason": reason, "source": source})
    from app.events.notifications import notify_org_admins, notify_super_admins
    notify_super_admins("payment_failed" if chargeback else "payment_received",
                        "Chargeback recorded" if chargeback else "Refund recorded",
                        f"{inv.get('currency') or ''} {amt:.2f} on invoice {inv.get('number') or inv['_id']}: {reason}",
                        severity="warning", link="/superadmin#/payments")
    if inv.get("organization_id"):
        notify_org_admins(inv["organization_id"], "payment_status",
                          "Payment refunded" if not chargeback else "Payment disputed",
                          f"{inv.get('currency') or ''} {amt:.2f} of invoice {inv.get('number') or ''} "
                          f"was {'charged back' if chargeback else 'refunded'}.",
                          severity="info", link="/dashboard#billing")
    from app.partners.commissions import on_payment_refunded
    partner = await asyncio.to_thread(
        on_payment_refunded, subscription_id=sub_id, invoice_id=str(inv["_id"]), refund_amount=amt,
        payment_amount=total, reason=reason, chargeback=chargeback, actor=actor)
    out = await db.invoices.find_one({"_id": inv["_id"]})
    return {"invoice_id": str(inv["_id"]), "status": out["status"], "refunded_amount": out["refunded_amount"],
            "refund": amt, "partner_commission": partner}


async def refund_from_provider_event(obj: Dict[str, Any], *, provider: str, chargeback: bool, db) -> None:
    """Provider refund / dispute webhook: find the payment, then its invoice."""
    pay_ref = obj.get("payment_intent") or obj.get("payment_id") or obj.get("id")
    payment = await db.payments.find_one({"provider_payment_id": pay_ref}) if pay_ref else None
    sub_id = (payment or {}).get("subscription_id") or (obj.get("metadata") or {}).get("subscription_id")
    if not sub_id:
        logger.warning("%s refund/dispute webhook: payment %s not found", provider, pay_ref)
        return
    inv = await db.invoices.find_one({"subscription_id": str(sub_id),
                                      "status": {"$in": ["paid", "partially_refunded"]}},
                                     sort=[("created_at", -1)])
    if not inv:
        logger.warning("%s refund/dispute webhook: no paid invoice for subscription %s", provider, sub_id)
        return
    if chargeback:
        minor = obj.get("amount")
    else:
        minor = obj.get("refund_amount") if obj.get("refund_amount") is not None else obj.get("amount_refunded")
    amount = (float(minor) / 100.0) if isinstance(minor, (int, float)) else None
    if amount is not None and not chargeback and obj.get("refund_amount") is None:
        # Stripe "amount_refunded" is cumulative for the charge
        amount = round(amount - float(inv.get("refunded_amount") or 0), 2)
        if amount <= 0:
            return
    try:
        await refund_invoice(str(inv["_id"]), amount=amount,
                             reason=("Chargeback" if chargeback else "Refund") + f" reported by {provider}",
                             actor=f"provider:{provider}", source=f"{provider}_webhook",
                             chargeback=chargeback, db=db)
    except HTTPException as e:
        if e.status_code != 409:  # already fully refunded = duplicate delivery
            raise
