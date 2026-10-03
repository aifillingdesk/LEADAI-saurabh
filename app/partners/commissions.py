"""Commission engine, wallet ledger and payouts.

Commissions are created ONLY from the existing billing lifecycle:
``confirm_subscription`` (first payment, Super Admin confirmed) and
``renew_subscription`` (each renewal payment); each one references the
organization, subscription and invoice it was earned on. A unique index on
(subscription_id, period_key) makes both hooks idempotent.

Lifecycle (statuses in ``constants``)::

    pending ──(qualification period ends)──► qualified ──(auto / Super Admin)──► approved
       │                                                                          │ payout schedule
       │                                                                          ▼
       └────────── reversed ◄── refund · chargeback · cancellation ◄──────── payable
                                                                                  │ payout requested
                                                                                  ▼
                                               paid ◄──(payout paid)── processing ──(failed/rejected)──► payable

A refund of an already PAID commission never rewrites paid history: it
creates a negative ``clawback`` entry (payable) deducted from the next
payout. The wallet is never edited directly — balances are derived from the
commission documents and every movement is written to the ledger
(``partner_wallet_transactions``). Payouts settle whole entries (FIFO).
"""
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.partners import constants as K
from app.partners.service import (
    clean, db_or_503, get_program_settings, masked_payout, money, notify_partner, oid, paudit,
    resolve_rule, text,
)

logger = logging.getLogger(__name__)


def _aware(dt):
    from app.billing.tokens import _aware as aware
    return aware(dt)


def _who(actor: Any) -> str:
    if isinstance(actor, dict):
        return actor.get("email") or "system"
    return getattr(actor, "email", None) or str(actor)


# ── wallet (derived) + ledger ────────────────────────────────────────────────

_BUCKETS = ("pending", "available", "processing", "paid", "reversed", "lifetime")


def compute_balances(partner_id: str, db=None) -> Dict[str, Dict[str, float]]:
    """Per currency, from the commission documents:
    pending = pending + qualified + approved · available = payable (clawbacks
    and debits included) · processing = in a payout · paid · reversed."""
    db = db_or_503(db)
    out: Dict[str, Dict[str, float]] = {}
    for c in db[K.COMMISSIONS].find({"partner_id": str(partner_id)},
                                    {"amount": 1, "currency": 1, "status": 1, "kind": 1,
                                     "reversed_amount": 1}):
        cur = (c.get("currency") or "USD").upper()
        b = out.setdefault(cur, dict.fromkeys(_BUCKETS, 0.0))
        amt = float(c.get("amount") or 0)
        st = c.get("status")
        if st in (K.C_PENDING, K.C_QUALIFIED, K.C_APPROVED):
            b["pending"] += amt
        elif st == K.C_PAYABLE:
            b["available"] += amt
        elif st == K.C_PROCESSING:
            b["processing"] += amt
        elif st == K.C_PAID:
            b["paid"] += amt
        # reversed: fully reversed commissions + partial reversals + clawbacks
        b["reversed"] += float(c.get("reversed_amount") or 0)
        if c.get("kind") == "clawback":
            b["reversed"] += -amt
        if st != K.C_REVERSED:
            b["lifetime"] += amt
    return {cur: {k: round(v, 2) for k, v in b.items()} for cur, b in out.items()}


def recompute_wallet(partner_id: str, db=None) -> Dict[str, Dict[str, float]]:
    db = db_or_503(db)
    balances = compute_balances(partner_id, db)
    db[K.WALLETS].update_one({"partner_id": str(partner_id)},
                             {"$set": {"balances": balances, "updated_at": utcnow()},
                              "$setOnInsert": {"created_at": utcnow()}}, upsert=True)
    return balances


def _ledger(db, partner_id: str, tx_type: str, amount: float, currency: str, *,
            commission: Optional[Dict[str, Any]] = None, commission_id: Optional[str] = None,
            payout_id: Optional[str] = None, actor: Any = "system", note: Optional[str] = None) -> None:
    c = commission or {}
    balances = compute_balances(partner_id, db).get(currency.upper(), {})
    db[K.WALLET_TX].insert_one({
        "partner_id": str(partner_id), "type": tx_type, "amount": money(amount),
        "currency": currency.upper(),
        "commission_id": commission_id or (str(c["_id"]) if c.get("_id") else None),
        "payout_id": payout_id, "organization_id": c.get("organization_id"),
        "subscription_id": c.get("subscription_id"), "invoice_id": c.get("invoice_id"),
        "company": c.get("company"),
        "available_after": balances.get("available", 0.0),
        "pending_after": balances.get("pending", 0.0),
        "actor": _who(actor), "note": note, "created_at": utcnow()})


def _notify(db, partner_id: str, ntype: str, title: str, message: str, **kw) -> None:
    partner = db[K.PARTNERS].find_one({"_id": oid(partner_id)})
    if partner:
        notify_partner(partner, ntype, title, message, **kw)


# ── commission generation (billing lifecycle hooks) ──────────────────────────

def _months_between(start, end) -> float:
    if not start or not end:
        return 0.0
    return (_aware(end) - _aware(start)).days / 30.44


def compute_commission_amount(rule: Dict[str, Any], base: float) -> float:
    ctype = rule.get("commission_type")
    if ctype == "percentage":
        value = base * float(rule["value"]) / 100.0
    elif ctype == "hybrid":
        value = base * float(rule.get("value") or 0) / 100.0 + float(rule.get("fixed_amount") or 0)
    else:
        value = float(rule["value"])
    if rule.get("max_per_payment"):
        value = min(value, float(rule["max_per_payment"]))
    return money(value)


def _latest_invoice_id(db, subscription_id: str) -> Optional[str]:
    inv = db.invoices.find_one({"subscription_id": str(subscription_id)}, {"_id": 1},
                               sort=[("created_at", -1)])
    return str(inv["_id"]) if inv else None


def _commission_base(sub: Optional[Dict[str, Any]], amount: float) -> Optional[float]:
    """Coupon interaction (set per coupon by the Super Admin): commission on
    the paid amount (default), on the list price, or none at all."""
    coupon = (sub or {}).get("coupon") or {}
    basis = coupon.get("commission_basis") or "paid"
    if basis == "none":
        return None
    if basis == "list" and coupon.get("list_amount"):
        return money(coupon["list_amount"])
    return money(amount)


def create_commission_for_payment(*, organization_id: str, subscription_id: str, amount: float,
                                  currency: Optional[str], plan_slug: Optional[str],
                                  period_key: str, event: str, period_start=None,
                                  invoice_id: Optional[str] = None, sub: Optional[Dict[str, Any]] = None,
                                  db=None) -> Optional[Dict[str, Any]]:
    """Commission for one verified + confirmed payment of a referred
    organization. Idempotent per (subscription_id, period_key)."""
    db = db if db is not None else get_sync_db()
    if db is None:
        return None
    ref = db[K.REFERRALS].find_one({"organization_id": str(organization_id), "status": "active"})
    if not ref:
        return None
    now = utcnow()
    paid = money(amount)
    if event == "initial":
        from app.partners.referrals import advance_stage
        advance_stage(db, str(organization_id), K.STAGE_CUSTOMER, subscription_id=str(subscription_id),
                      extra={"plan_id": plan_slug})
        ref = db[K.REFERRALS].find_one({"_id": ref["_id"]})
    partner = db[K.PARTNERS].find_one({"_id": oid(ref["partner_id"])})
    if not partner:
        return None
    if db[K.COMMISSIONS].find_one({"subscription_id": str(subscription_id), "period_key": period_key,
                                   "kind": "commission"}):
        return None  # duplicate conversion (re-delivered webhook / repeated confirmation)
    payment_key = f"{subscription_id}:{period_key}"
    db[K.REFERRALS].update_one({"_id": ref["_id"], "payments_seen": {"$ne": payment_key}},
                               {"$inc": {"revenue_total": paid},
                                "$addToSet": {"payments_seen": payment_key}})

    def skip(reason: str):
        paudit("partner.commission.skipped", str(partner["_id"]), actor="system",
               details={"organization_id": str(organization_id), "subscription_id": str(subscription_id),
                        "period_key": period_key, "reason": reason},
               resource_type="subscription", resource_id=str(subscription_id))
        return None

    if partner.get("status") != K.P_ACTIVE:
        return skip("partner_not_active")
    rule = resolve_rule(partner, plan_slug, db)
    if not rule:
        return skip("no_matching_rule")
    if event != "initial" and not rule.get("recurring"):
        return skip("one_time_rule")
    duration = int(rule.get("duration_months") or 0)
    if event != "initial" and duration and _months_between(ref.get("converted_at"), period_start or now) >= duration:
        return skip("commission_duration_ended")
    base = _commission_base(sub, paid)
    if base is None:
        return skip("coupon_excludes_commission")
    value = compute_commission_amount(rule, base)
    if rule.get("cap_amount"):
        earned = sum(float(c.get("amount") or 0) for c in db[K.COMMISSIONS].find(
            {"referral_id": str(ref["_id"]), "kind": "commission", "status": {"$ne": K.C_REVERSED}},
            {"amount": 1}))
        value = money(min(value, float(rule["cap_amount"]) - earned))
    if value <= 0:
        return skip("cap_reached" if rule.get("cap_amount") else "zero_amount")
    settings = get_program_settings(db)
    qdays = rule.get("qualification_days")
    qdays = int(settings.get("commission_hold_days") or 0) if qdays is None else int(qdays)
    manual = bool(ref.get("suspicious")) and bool(settings.get("hold_suspicious_commissions", True))
    doc = {
        "partner_id": str(partner["_id"]), "referral_id": str(ref["_id"]),
        "organization_id": str(organization_id), "company": ref.get("company"),
        "subscription_id": str(subscription_id), "invoice_id": invoice_id or _latest_invoice_id(db, subscription_id),
        "period_key": period_key, "event": event, "kind": "commission", "plan_id": plan_slug,
        "paid_amount": paid, "base_amount": base, "currency": (currency or "USD").upper(),
        "coupon_code": ((sub or {}).get("coupon") or {}).get("code"),
        "rule_id": str(rule["_id"]), "rule_name": rule.get("name"), "rate_type": rule["commission_type"],
        "rate_value": rule["value"], "rate_fixed": rule.get("fixed_amount") or 0.0,
        "amount": value, "original_amount": value, "reversed_amount": 0.0,
        "status": K.C_PENDING, "requires_manual_approval": manual,
        "hold_until": now + timedelta(days=qdays), "payout_id": None,
        "created_at": now, "updated_at": now,
        "history": [{"status": K.C_PENDING, "at": now, "by": "system",
                     "note": f"qualification period {qdays} day(s)"}],
    }
    try:
        doc["_id"] = db[K.COMMISSIONS].insert_one(doc).inserted_id
    except Exception:  # unique (subscription_id, period_key): a concurrent hook won
        return None
    db[K.REFERRALS].update_one({"_id": ref["_id"]}, {"$inc": {"commission_total": value}})
    _ledger(db, doc["partner_id"], "commission_earned", value, doc["currency"], commission=doc,
            note="first payment" if event == "initial" else f"renewal {period_key}")
    recompute_wallet(doc["partner_id"], db)
    paudit("partner.commission.created", doc["partner_id"], actor="system",
           details={"amount": value, "currency": doc["currency"], "event": event, "base": base,
                    "organization_id": str(organization_id), "invoice_id": doc["invoice_id"],
                    "manual": manual},
           resource_type="partner_commission", resource_id=str(doc["_id"]))
    notify_partner(partner, "partner_commission", "Commission earned",
                   f"You earned {doc['currency']} {value:.2f} from {ref.get('company') or 'a referral'} "
                   f"({'first payment' if event == 'initial' else 'renewal'}).",
                   severity="success", link="/partner#/commissions", email=True)
    return doc


def on_subscription_activated(sub: Dict[str, Any], db=None) -> None:
    """``confirm_subscription`` hook (Super Admin confirmed a verified payment)."""
    try:
        db = db if db is not None else get_sync_db()
        from app.partners.coupons import record_coupon_redemption
        record_coupon_redemption(sub, db)
        create_commission_for_payment(
            organization_id=str(sub["organization_id"]), subscription_id=str(sub["_id"]),
            amount=float(sub.get("amount") or 0), currency=sub.get("currency"),
            plan_slug=sub.get("plan_id"), period_key="initial", event="initial", sub=sub, db=db)
    except Exception as e:
        logger.warning("partner commission hook (activation) failed: %s", e)


def on_subscription_renewed(sub: Dict[str, Any], *, amount: float, period_start, db=None) -> None:
    """``renew_subscription`` hook (a renewal payment started a new period)."""
    try:
        create_commission_for_payment(
            organization_id=str(sub["organization_id"]), subscription_id=str(sub["_id"]),
            amount=amount, currency=sub.get("currency"), plan_slug=sub.get("plan_id"),
            period_key=_aware(period_start).date().isoformat(), event="renewal",
            period_start=period_start, sub=sub, db=db)
    except Exception as e:
        logger.warning("partner commission hook (renewal) failed: %s", e)


# ── commission state changes ─────────────────────────────────────────────────

def _load_commission(commission_id: str, db) -> Dict[str, Any]:
    c = db[K.COMMISSIONS].find_one({"_id": oid(commission_id)}) if oid(commission_id) else None
    if not c:
        raise HTTPException(status_code=404, detail="Commission not found")
    return c


def _commission_transition(db, c: Dict[str, Any], status: str, actor: Any, note: str = "",
                           extra: Optional[Dict[str, Any]] = None, *, strict: bool = True) -> bool:
    now = utcnow()
    res = db[K.COMMISSIONS].update_one(
        {"_id": c["_id"], "status": c["status"], "payout_id": c.get("payout_id")},
        {"$set": {"status": status, "updated_at": now, **(extra or {})},
         "$push": {"history": {"status": status, "at": now, "by": _who(actor), "note": note}}})
    if not res.modified_count and strict:
        raise HTTPException(status_code=409, detail="The commission changed meanwhile — reload and retry")
    return bool(res.modified_count)


def _schedule_due(settings: Dict[str, Any], now) -> bool:
    """True when approved commissions may become payable now."""
    sched = settings.get("payout_schedule") or "on_request"
    day = int(settings.get("payout_day") or 0)
    if sched == "weekly":
        return now.weekday() == min(day, 6)
    if sched == "monthly":
        return now.day == max(1, min(day, 28))
    return True


def _make_payable(db, c: Dict[str, Any], actor: Any, note: str) -> bool:
    if not _commission_transition(db, c, K.C_PAYABLE, actor, note, {"payable_at": utcnow()}, strict=False):
        return False
    _ledger(db, c["partner_id"], "commission_payable", c["amount"], c["currency"], commission=c,
            actor=actor, note=note)
    return True


def approve_commission(commission_id: str, *, actor: Any, ip: Optional[str] = None,
                       note: str = "") -> Dict[str, Any]:
    """Super Admin approval of a qualified commission (or an early approval of
    a pending one, recorded in the audit log)."""
    db = db_or_503()
    c = _load_commission(commission_id, db)
    if c["status"] not in (K.C_PENDING, K.C_QUALIFIED):
        raise HTTPException(status_code=409, detail=f"Commission is '{c['status']}' — cannot approve")
    early = c["status"] == K.C_PENDING
    _commission_transition(db, c, K.C_APPROVED, actor, note or ("approved before qualification" if early
                                                                else "approved by super admin"),
                           {"approved_at": utcnow(), "requires_manual_approval": False})
    c = db[K.COMMISSIONS].find_one({"_id": c["_id"]})
    _ledger(db, c["partner_id"], "commission_approved", c["amount"], c["currency"], commission=c, actor=actor)
    if _schedule_due(get_program_settings(db), utcnow()):
        _make_payable(db, c, actor, "payout schedule: payable now")
    recompute_wallet(c["partner_id"], db)
    paudit("partner.commission.approved", c["partner_id"], actor=actor, ip=ip,
           details={"amount": c["amount"], "currency": c["currency"], "early": early},
           resource_type="partner_commission", resource_id=commission_id)
    _notify(db, c["partner_id"], "partner_commission", "Commission approved",
            f"{c['currency']} {c['amount']:.2f} was approved.", severity="success", link="/partner#/wallet")
    return clean(db[K.COMMISSIONS].find_one({"_id": c["_id"]}))


def make_payable(commission_id: str, *, actor: Any, ip: Optional[str] = None) -> Dict[str, Any]:
    """Super Admin releases an approved commission before the payout schedule."""
    db = db_or_503()
    c = _load_commission(commission_id, db)
    if c["status"] != K.C_APPROVED:
        raise HTTPException(status_code=409, detail="Only approved commissions can be made payable")
    if not _make_payable(db, c, actor, "released by super admin"):
        raise HTTPException(status_code=409, detail="The commission changed meanwhile — reload and retry")
    recompute_wallet(c["partner_id"], db)
    paudit("partner.commission.payable", c["partner_id"], actor=actor, ip=ip,
           details={"amount": c["amount"]}, resource_type="partner_commission", resource_id=commission_id)
    return clean(db[K.COMMISSIONS].find_one({"_id": c["_id"]}))


def _reduce(db, c: Dict[str, Any], amount: float, *, actor: Any, reason: str, source: str,
            chargeback: bool = False) -> Dict[str, Any]:
    """Reverse ``amount`` of commission ``c`` (whole or partial).

    * unpaid and not in a payout -> amount reduced; fully -> status reversed;
    * inside a payout still under review -> taken out of the payout first;
    * paid, or inside a payout already approved/processing -> clawback entry.
    Returns {"reversed": x, "clawback": y}."""
    remaining = money(float(c.get("amount") or 0))
    amount = money(min(float(amount), remaining if c["status"] != K.C_PAID else
                       float(c.get("amount") or 0) - float(c.get("clawed_back") or 0)))
    if amount <= 0:
        return {"reversed": 0.0, "clawback": 0.0}
    now = utcnow()
    if c["status"] == K.C_PROCESSING and c.get("payout_id"):
        po = db[K.PAYOUTS].find_one({"_id": oid(c["payout_id"])})
        if po and po["status"] in (K.PO_REQUESTED, K.PO_REVIEW):
            # take it out of the not-yet-approved payout and re-total it
            db[K.COMMISSIONS].update_one({"_id": c["_id"]}, {"$set": {"status": K.C_PAYABLE, "payout_id": None}})
            _retotal_payout(db, po)
            c = db[K.COMMISSIONS].find_one({"_id": c["_id"]})
    if c["status"] in K.UNPAID_STATUSES:
        full = amount >= remaining - 0.004
        upd = {"amount": 0.0 if full else money(remaining - amount),
               "reversed_amount": money(float(c.get("reversed_amount") or 0) + amount),
               "reversal_reason": reason, "reversal_source": source, "chargeback": chargeback or c.get("chargeback", False),
               "reversed_at": now}
        new_status = K.C_REVERSED if full else c["status"]
        db[K.COMMISSIONS].update_one({"_id": c["_id"]}, {
            "$set": {**upd, "status": new_status, "updated_at": now},
            "$push": {"history": {"status": new_status, "at": now, "by": _who(actor),
                                  "note": f"{'reversed' if full else 'partially reversed'} {amount:.2f}: {reason}"}}})
        if c.get("referral_id") and c.get("kind") == "commission":
            db[K.REFERRALS].update_one({"_id": oid(c["referral_id"])}, {"$inc": {"commission_total": -amount}})
        _ledger(db, c["partner_id"], "commission_reversed", -amount, c["currency"], commission=c,
                actor=actor, note=reason)
        return {"reversed": amount, "clawback": 0.0}
    # paid (or locked in an approved / processing payout): claw back from the next payout
    claw = {"partner_id": c["partner_id"], "referral_id": c.get("referral_id"),
            "organization_id": c.get("organization_id"), "company": c.get("company"),
            "subscription_id": c.get("subscription_id"), "invoice_id": c.get("invoice_id"),
            "period_key": None, "event": "clawback", "kind": "clawback", "base_amount": 0.0,
            "currency": c["currency"], "amount": -amount, "original_amount": -amount,
            "reversed_amount": 0.0, "status": K.C_PAYABLE, "requires_manual_approval": False,
            "hold_until": now, "payout_id": None, "clawback_of": str(c["_id"]),
            "chargeback": chargeback, "note": f"Clawback: {reason}"[:300],
            "created_at": now, "updated_at": now, "payable_at": now,
            "history": [{"status": K.C_PAYABLE, "at": now, "by": _who(actor), "note": reason}]}
    claw["_id"] = db[K.COMMISSIONS].insert_one(claw).inserted_id
    db[K.COMMISSIONS].update_one({"_id": c["_id"]}, {
        "$inc": {"clawed_back": amount},
        "$set": {"chargeback": chargeback or c.get("chargeback", False), "updated_at": now},
        "$push": {"history": {"status": c["status"], "at": now, "by": _who(actor),
                              "note": f"clawback {amount:.2f}: {reason}"}}})
    if c.get("referral_id"):
        db[K.REFERRALS].update_one({"_id": oid(c["referral_id"])}, {"$inc": {"commission_total": -amount}})
    _ledger(db, c["partner_id"], "clawback", -amount, c["currency"], commission=claw, actor=actor, note=reason)
    return {"reversed": 0.0, "clawback": amount}


def reverse_commission(commission_id: str, *, actor: Any, reason: str, amount: Optional[float] = None,
                       ip: Optional[str] = None) -> Dict[str, Any]:
    """Super Admin reversal (fraud, dispute, manual correction). Partial when
    ``amount`` is given; paid commissions are clawed back."""
    db = db_or_503()
    c = _load_commission(commission_id, db)
    if not (reason or "").strip():
        raise HTTPException(status_code=422, detail="A reason is required")
    if c.get("kind") != "commission" or c["status"] == K.C_REVERSED:
        raise HTTPException(status_code=409, detail="Only active commissions can be reversed")
    full = float(c["amount"]) if c["status"] != K.C_PAID else float(c["amount"]) - float(c.get("clawed_back") or 0)
    amt = full if amount in (None, 0) else float(amount)
    if amt <= 0 or amt > full + 0.004:
        raise HTTPException(status_code=422, detail=f"Amount must be between 0 and {full:.2f}")
    res = _reduce(db, c, amt, actor=actor, reason=reason, source="manual")
    recompute_wallet(c["partner_id"], db)
    paudit("partner.commission.reversed", c["partner_id"], actor=actor, ip=ip,
           details={"amount": amt, "reason": reason, **res},
           resource_type="partner_commission", resource_id=commission_id)
    _notify(db, c["partner_id"], "partner_commission", "Commission reversed",
            f"{c['currency']} {amt:.2f} was reversed: {reason}", severity="warning",
            link="/partner#/commissions")
    return clean(db[K.COMMISSIONS].find_one({"_id": c["_id"]}))


def on_payment_refunded(*, subscription_id: Optional[str], invoice_id: Optional[str], refund_amount: float,
                        payment_amount: float, reason: str, chargeback: bool = False,
                        actor: Any = "system", db=None) -> Dict[str, float]:
    """Billing hook: a payment (invoice) was refunded / charged back. The
    commission earned on that payment is reversed in proportion; a paid one
    is clawed back. Referral revenue is reduced by the refunded amount."""
    db = db if db is not None else get_sync_db()
    totals = {"reversed": 0.0, "clawback": 0.0}
    if db is None or refund_amount <= 0:
        return totals
    q: Dict[str, Any] = {"kind": "commission", "status": {"$ne": K.C_REVERSED}}
    if invoice_id:
        q["invoice_id"] = str(invoice_id)
    elif subscription_id:
        q["subscription_id"] = str(subscription_id)
    else:
        return totals
    coms = list(db[K.COMMISSIONS].find(q).sort("created_at", -1).limit(1 if not invoice_id else 20))
    if not coms and invoice_id and subscription_id:
        # invoice not linked (older record): the most recent commission of the subscription
        coms = list(db[K.COMMISSIONS].find({"kind": "commission", "subscription_id": str(subscription_id),
                                            "status": {"$ne": K.C_REVERSED}}).sort("created_at", -1).limit(1))
    ratio = min(1.0, float(refund_amount) / float(payment_amount)) if payment_amount else 1.0
    partners = set()
    for c in coms:
        share = money(float(c.get("original_amount") or c.get("amount") or 0) * ratio)
        res = _reduce(db, c, share, actor=actor, reason=reason,
                      source="chargeback" if chargeback else "refund", chargeback=chargeback)
        totals["reversed"] += res["reversed"]
        totals["clawback"] += res["clawback"]
        partners.add(c["partner_id"])
        if chargeback:
            from app.partners.fraud import raise_flag
            raise_flag("chargeback", partner_id=c["partner_id"], severity="high", subject_type="referral",
                       subject_id=c.get("referral_id"), hold_referral_id=c.get("referral_id"),
                       details={"invoice_id": invoice_id, "amount": refund_amount, "company": c.get("company")},
                       db=db)
        if c.get("referral_id"):
            db[K.REFERRALS].update_one({"_id": oid(c["referral_id"])},
                                       {"$inc": {"revenue_total": -money(refund_amount)},
                                        "$push": {"events": {"stage": "chargeback" if chargeback else "refund",
                                                             "at": utcnow(), "amount": money(refund_amount),
                                                             "subscription_id": c.get("subscription_id")}},
                                        **({"$set": {"suspicious": True}} if chargeback else {})})
        paudit("partner.commission.refund_reversal", c["partner_id"], actor=actor,
               details={"invoice_id": invoice_id, "refund": refund_amount, "chargeback": chargeback, **res},
               resource_type="partner_commission", resource_id=str(c["_id"]))
        _notify(db, c["partner_id"], "partner_commission",
                "Chargeback on a referral payment" if chargeback else "Referral payment refunded",
                f"{c.get('company') or 'A customer'}'s payment was {'charged back' if chargeback else 'refunded'}; "
                f"{c['currency']} {res['reversed'] + res['clawback']:.2f} of commission was reversed.",
                severity="warning", link="/partner#/commissions", email=True)
    for pid in partners:
        recompute_wallet(pid, db)
    return {k: money(v) for k, v in totals.items()}


def on_subscription_ended(sub: Dict[str, Any], *, reason: str, db=None) -> None:
    """Billing hook: subscription cancelled / expired. Commissions still in
    their qualification period are reversed (program setting); the referral
    records the churn."""
    try:
        db = db if db is not None else get_sync_db()
        if db is None:
            return
        now = utcnow()
        org_id = str(sub.get("organization_id"))
        db[K.REFERRALS].update_one({"organization_id": org_id, "stage": K.STAGE_CUSTOMER},
                                   {"$set": {"churned_at": now, "updated_at": now},
                                    "$push": {"events": {"stage": "churned", "at": now, "reason": reason,
                                                         "subscription_id": str(sub.get("_id"))}}})
        if not get_program_settings(db).get("reverse_on_cancellation", True):
            return
        for c in db[K.COMMISSIONS].find({"subscription_id": str(sub["_id"]), "kind": "commission",
                                         "status": K.C_PENDING}):
            _reduce(db, c, float(c["amount"]), actor="system", reason=f"subscription {reason} during qualification",
                    source="cancellation")
            recompute_wallet(c["partner_id"], db)
            paudit("partner.commission.cancellation_reversal", c["partner_id"], actor="system",
                   details={"subscription_id": str(sub["_id"]), "reason": reason},
                   resource_type="partner_commission", resource_id=str(c["_id"]))
    except Exception as e:
        logger.warning("partner hook (subscription ended) failed: %s", e)


def create_adjustment(partner_id: str, *, amount: float, currency: str, reason: str, actor: Any,
                      ip: Optional[str] = None) -> Dict[str, Any]:
    """Manual credit (+) or debit (-) by a Super Admin, payable immediately."""
    db = db_or_503()
    partner = db[K.PARTNERS].find_one({"_id": oid(partner_id)}) if oid(partner_id) else None
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    amount = money(amount)
    if amount == 0 or abs(amount) > 1_000_000:
        raise HTTPException(status_code=422, detail="Adjustment amount is out of range")
    if not (reason or "").strip():
        raise HTTPException(status_code=422, detail="A reason is required")
    cur = (currency or "USD").upper()[:3]
    now = utcnow()
    doc: Dict[str, Any] = {"partner_id": str(partner["_id"]), "referral_id": None, "organization_id": None,
           "subscription_id": None, "invoice_id": None, "period_key": None, "event": "adjustment",
           "kind": "adjustment", "base_amount": 0.0, "currency": cur, "amount": amount,
           "original_amount": amount, "reversed_amount": 0.0, "status": K.C_PAYABLE,
           "requires_manual_approval": False, "hold_until": now, "payout_id": None,
           "note": reason.strip()[:300], "approved_at": now, "payable_at": now,
           "created_at": now, "updated_at": now,
           "history": [{"status": K.C_PAYABLE, "at": now, "by": _who(actor), "note": reason}]}
    doc["_id"] = db[K.COMMISSIONS].insert_one(doc).inserted_id
    _ledger(db, doc["partner_id"], "adjustment", amount, cur, commission=doc, actor=actor, note=reason)
    recompute_wallet(doc["partner_id"], db)
    paudit("partner.wallet.adjusted", doc["partner_id"], actor=actor, ip=ip,
           details={"amount": amount, "currency": cur, "reason": reason},
           resource_type="partner_commission", resource_id=str(doc["_id"]))
    notify_partner(partner, "partner_wallet", "Wallet adjustment",
                   f"{cur} {amount:+.2f}: {reason}", link="/partner#/wallet")
    return clean(doc)


def release_matured(db=None) -> int:
    """Background sweep (also callable on demand):
    pending past the qualification period -> qualified;
    qualified -> approved when auto-approval is on and nothing is flagged;
    approved -> payable when the payout schedule is due.
    Returns how many commissions qualified in this pass."""
    db = db if db is not None else get_sync_db()
    if db is None:
        return 0
    settings = get_program_settings(db)
    now = utcnow()
    qualified = 0
    touched = set()
    for c in db[K.COMMISSIONS].find({"status": K.C_PENDING, "kind": "commission",
                                     "hold_until": {"$lte": now}}).limit(1000):
        if _commission_transition(db, c, K.C_QUALIFIED, "system", "qualification period ended",
                                  {"qualified_at": now}, strict=False):
            qualified += 1
            touched.add(c["partner_id"])
            _ledger(db, c["partner_id"], "commission_qualified", c["amount"], c["currency"], commission=c,
                    note="qualification period ended")
    if settings.get("auto_approve_commissions", True):
        for c in db[K.COMMISSIONS].find({"status": K.C_QUALIFIED,
                                         "requires_manual_approval": {"$ne": True}}).limit(1000):
            if _commission_transition(db, c, K.C_APPROVED, "system", "auto-approved", {"approved_at": now},
                                      strict=False):
                touched.add(c["partner_id"])
                _ledger(db, c["partner_id"], "commission_approved", c["amount"], c["currency"], commission=c,
                        note="auto-approved")
    if _schedule_due(settings, now):
        for c in db[K.COMMISSIONS].find({"status": K.C_APPROVED}).limit(1000):
            if _make_payable(db, c, "system", f"payout schedule ({settings.get('payout_schedule')})"):
                touched.add(c["partner_id"])
    for pid in touched:
        recompute_wallet(pid, db)
        _notify(db, pid, "partner_commission", "Commissions moved forward",
                "Some of your commissions finished their qualification period, were approved or "
                "became payable. See your wallet for the current balance.",
                severity="success", link="/partner#/wallet")
    if qualified:
        logger.info("[partners] %d commission(s) qualified", qualified)
    return qualified


# ── payouts ──────────────────────────────────────────────────────────────────

def _payout_out(p: Dict[str, Any], *, admin: bool = False) -> Dict[str, Any]:
    out = clean({k: v for k, v in p.items() if k != "payout_snapshot"})
    snap = p.get("payout_snapshot")
    out["payout_snapshot"] = clean(snap) if admin else masked_payout(snap)
    return out


def _retotal_payout(db, po: Dict[str, Any]) -> None:
    held = list(db[K.COMMISSIONS].find({"payout_id": str(po["_id"])}, {"amount": 1}))
    total = money(sum(float(c["amount"]) for c in held))
    upd: Dict[str, Any] = {"amount": total, "commission_ids": [str(c["_id"]) for c in held], "updated_at": utcnow()}
    if not held or total <= 0:
        db[K.COMMISSIONS].update_many({"payout_id": str(po["_id"])},
                                      {"$set": {"status": K.C_PAYABLE, "payout_id": None}})
        upd.update({"status": K.PO_CANCELLED, "cancel_reason": "no payable balance left after a refund"})
    db[K.PAYOUTS].update_one({"_id": po["_id"]}, {"$set": upd})


def request_payout(partner: Dict[str, Any], *, currency: Optional[str] = None,
                   ip: Optional[str] = None) -> Dict[str, Any]:
    """Withdraw the whole available (payable) balance of one currency."""
    db = db_or_503()
    pid = str(partner["_id"])
    if not partner.get("payout_info"):
        raise HTTPException(status_code=422, detail="Add your payout details before requesting a payout")
    settings = get_program_settings(db)
    method = partner["payout_info"].get("method")
    if method not in (settings.get("payout_methods") or []):
        raise HTTPException(status_code=422, detail="Your payout method is no longer supported — update your payout details")
    balances = compute_balances(pid, db)
    if not currency:
        currency = max(balances, key=lambda c: balances[c]["available"]) if balances else "USD"
    currency = currency.upper()
    if db[K.PAYOUTS].find_one({"partner_id": pid, "currency": currency,
                               "status": {"$in": list(K.OPEN_PAYOUT_STATUSES)}}):
        raise HTTPException(status_code=409, detail="You already have a payout in progress for this currency")
    items = list(db[K.COMMISSIONS].find({"partner_id": pid, "currency": currency,
                                         "status": K.C_PAYABLE, "payout_id": None}).sort("created_at", 1))
    total = money(sum(float(c["amount"]) for c in items))
    minimum = float(settings.get("min_payout") or 0)
    if total <= 0 or total < minimum:
        raise HTTPException(status_code=422, detail=f"Available balance {currency} {total:.2f} is below "
                                                    f"the minimum payout of {currency} {minimum:.2f}")
    now = utcnow()
    payout = {"partner_id": pid, "amount": total, "currency": currency, "method": method,
              "payout_snapshot": partner["payout_info"], "status": K.PO_REQUESTED,
              "commission_ids": [str(c["_id"]) for c in items], "reference": None,
              "requested_at": now, "created_at": now, "updated_at": now,
              "history": [{"status": K.PO_REQUESTED, "at": now, "by": partner["email"]}]}
    payout["_id"] = db[K.PAYOUTS].insert_one(payout).inserted_id
    po_id = str(payout["_id"])
    claimed = db[K.COMMISSIONS].update_many(
        {"_id": {"$in": [c["_id"] for c in items]}, "status": K.C_PAYABLE, "payout_id": None},
        {"$set": {"status": K.C_PROCESSING, "payout_id": po_id, "updated_at": now},
         "$push": {"history": {"status": K.C_PROCESSING, "at": now, "by": partner["email"],
                               "note": f"payout {po_id}"}}})
    if claimed.modified_count != len(items):
        _retotal_payout(db, payout)  # raced with a reversal: re-total what this payout holds
        payout = db[K.PAYOUTS].find_one({"_id": payout["_id"]})
        total = payout["amount"]
    _ledger(db, pid, "payout_requested", -total, currency, payout_id=po_id, actor=partner["email"])
    notify_partner(partner, "partner_payout", "Payout requested",
                   f"Your payout of {currency} {total:.2f} was submitted for review.", link="/partner#/payouts")
    recompute_wallet(pid, db)
    paudit("partner.payout.requested", pid, actor=partner["email"], ip=ip,
           details={"amount": total, "currency": currency, "commissions": len(items)},
           resource_type="partner_payout", resource_id=po_id)
    from app.events.notifications import notify_super_admins
    notify_super_admins("partner_payout", "Partner payout requested",
                        f"{partner.get('name')} requested {currency} {total:.2f}", severity="warning",
                        link="/superadmin#/partners?tab=payouts", data={"payout_id": po_id})
    return _payout_out(db[K.PAYOUTS].find_one({"_id": payout["_id"]}))


def _load_payout(payout_id: str, db) -> Dict[str, Any]:
    p = db[K.PAYOUTS].find_one({"_id": oid(payout_id)}) if oid(payout_id) else None
    if not p:
        raise HTTPException(status_code=404, detail="Payout not found")
    return p


def _payout_transition(db, p, status: str, actor: Any, note: str = "",
                       extra: Optional[Dict[str, Any]] = None) -> None:
    now = utcnow()
    res = db[K.PAYOUTS].update_one(
        {"_id": p["_id"], "status": p["status"]},
        {"$set": {"status": status, "updated_at": now, **(extra or {})},
         "$push": {"history": {"status": status, "at": now, "by": _who(actor), "note": note}}})
    if not res.modified_count:
        raise HTTPException(status_code=409, detail="The payout changed meanwhile — reload and retry")


def _release(db, p, actor: Any, note: str) -> None:
    """Commissions of a rejected / failed / cancelled payout become payable again."""
    now = utcnow()
    db[K.COMMISSIONS].update_many({"payout_id": str(p["_id"]), "status": K.C_PROCESSING},
                                  {"$set": {"status": K.C_PAYABLE, "payout_id": None, "updated_at": now},
                                   "$push": {"history": {"status": K.C_PAYABLE, "at": now, "by": _who(actor),
                                                         "note": note}}})


def cancel_own_payout(partner: Dict[str, Any], payout_id: str, *, ip: Optional[str] = None) -> Dict[str, Any]:
    db = db_or_503()
    p = _load_payout(payout_id, db)
    if p["partner_id"] != str(partner["_id"]):
        raise HTTPException(status_code=404, detail="Payout not found")
    if p["status"] != K.PO_REQUESTED:
        raise HTTPException(status_code=409, detail="Only a requested payout can be cancelled")
    _payout_transition(db, p, K.PO_CANCELLED, partner["email"], "cancelled by partner")
    _release(db, p, partner["email"], "payout cancelled")
    _ledger(db, p["partner_id"], "payout_released", p["amount"], p["currency"],
            payout_id=payout_id, actor=partner["email"], note="cancelled")
    recompute_wallet(p["partner_id"], db)
    paudit("partner.payout.cancelled", p["partner_id"], actor=partner["email"], ip=ip,
           details={"amount": p["amount"]}, resource_type="partner_payout", resource_id=payout_id)
    return _payout_out(db[K.PAYOUTS].find_one({"_id": p["_id"]}))


def admin_payout_action(payout_id: str, action: str, *, actor: Any, reason: str = "",
                        reference: str = "", ip: Optional[str] = None) -> Dict[str, Any]:
    """Super Admin payout workflow:
    requested -> under_review -> approved | rejected; approved -> processing
    -> paid (transfer reference) | failed (balance payable again)."""
    db = db_or_503()
    p = _load_payout(payout_id, db)
    if action not in K.PAYOUT_ACTIONS:
        raise HTTPException(status_code=422, detail="Unknown payout action")
    allowed, target = K.PAYOUT_ACTIONS[action]
    if p["status"] not in allowed:
        raise HTTPException(status_code=409, detail=f"Payout is '{p['status']}' — cannot {action.replace('_', ' ')}")
    now = utcnow()
    amount = f"{p['currency']} {p['amount']:.2f}"
    if action in ("reject", "fail") and not (reason or "").strip():
        raise HTTPException(status_code=422, detail="A reason is required")
    if action == "mark_paid":
        ref = text(reference, 120)
        if not ref:
            raise HTTPException(status_code=422, detail="Enter the transfer reference")
        _payout_transition(db, p, target, actor, reason, {"paid_at": now, "reference": ref})
        db[K.COMMISSIONS].update_many(
            {"payout_id": payout_id, "status": K.C_PROCESSING},
            {"$set": {"status": K.C_PAID, "paid_at": now, "updated_at": now},
             "$push": {"history": {"status": K.C_PAID, "at": now, "by": _who(actor), "note": ref}}})
        _ledger(db, p["partner_id"], "payout_paid", -float(p["amount"]), p["currency"],
                payout_id=payout_id, actor=actor, note=ref)
        title, msg, sev = "Payout sent", f"{amount} was paid (reference {ref}).", "success"
    elif action in ("reject", "fail"):
        _payout_transition(db, p, target, actor, reason, {f"{target}_at": now, "failure_reason": reason})
        _release(db, p, actor, f"payout {target}")
        _ledger(db, p["partner_id"], "payout_released", p["amount"], p["currency"],
                payout_id=payout_id, actor=actor, note=reason)
        title = "Payout rejected" if action == "reject" else "Payout failed"
        msg, sev = f"{title}: {reason}. The balance is available again.", "warning"
    else:
        _payout_transition(db, p, target, actor, reason, {f"{target}_at": now})
        title = {"review": "Payout under review", "approve": "Payout approved",
                 "process": "Payout processing"}[action]
        msg, sev = f"Your payout of {amount} is {target.replace('_', ' ')}.", "info"
    recompute_wallet(p["partner_id"], db)
    paudit(f"partner.payout.{action}", p["partner_id"], actor=actor, ip=ip,
           details={"amount": p["amount"], "currency": p["currency"], "reason": reason,
                    "reference": reference or None, "to": target},
           resource_type="partner_payout", resource_id=payout_id)
    _notify(db, p["partner_id"], "partner_payout", title, msg, severity=sev, link="/partner#/payouts",
            email=action in ("approve", "mark_paid", "reject", "fail"))
    return _payout_out(db[K.PAYOUTS].find_one({"_id": p["_id"]}), admin=True)


def payout_out(p: Dict[str, Any], *, admin: bool = False) -> Dict[str, Any]:
    return _payout_out(p, admin=admin)


def recent_ledger(partner_id: str, limit: int = 20, db=None, skip: int = 0) -> List[Dict[str, Any]]:
    db = db_or_503(db)
    return [clean(t) for t in db[K.WALLET_TX].find({"partner_id": str(partner_id)})
            .sort("created_at", -1).skip(skip).limit(limit)]
