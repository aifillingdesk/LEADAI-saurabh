"""Automatic partner tiering (Super Admin configured, nothing hard-coded).

A tier with ``auto_assign`` and requirements (min customers / referrals /
revenue, optionally within the last ``period_days``) is earned when every
requirement that is set is met. With ``auto_tiering`` on, each active partner
moves to the highest earned tier (by ``order``); a move down happens only
when ``allow_tier_downgrade`` is on. Partners whose tier a Super Admin set
by hand (``tier_locked``) are left alone. Every change is audited and the
partner is notified.
"""
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.partners import constants as K
from app.partners.service import get_program_settings, notify_partner, oid, paudit

logger = logging.getLogger(__name__)


def partner_metrics(db, partner_id: str, period_days: int = 0) -> Dict[str, float]:
    q: Dict[str, Any] = {"partner_id": str(partner_id), "status": "active"}
    rq = dict(q)
    if period_days:
        since = utcnow() - timedelta(days=period_days)
        rq["signed_up_at"] = {"$gte": since}
    referrals = db[K.REFERRALS].count_documents(rq)
    cq = dict(q, stage=K.STAGE_CUSTOMER)
    if period_days:
        cq["converted_at"] = {"$gte": utcnow() - timedelta(days=period_days)}
    customers = db[K.REFERRALS].count_documents(cq)
    revenue = 0.0
    com_q: Dict[str, Any] = {"partner_id": str(partner_id), "kind": "commission", "status": {"$ne": K.C_REVERSED}}
    if period_days:
        com_q["created_at"] = {"$gte": utcnow() - timedelta(days=period_days)}
    for c in db[K.COMMISSIONS].find(com_q, {"paid_amount": 1, "base_amount": 1}):
        revenue += float(c.get("paid_amount") or c.get("base_amount") or 0)
    return {"referrals": referrals, "customers": customers, "revenue": round(revenue, 2)}


def tier_met(db, partner_id: str, tier: Dict[str, Any]) -> bool:
    req = tier.get("requirements") or {}
    if not any(float(req.get(k) or 0) for k in ("min_customers", "min_referrals", "min_revenue")):
        return False  # a tier without requirements is assigned by hand only
    m = partner_metrics(db, partner_id, int(req.get("period_days") or 0))
    return (m["customers"] >= int(req.get("min_customers") or 0)
            and m["referrals"] >= int(req.get("min_referrals") or 0)
            and m["revenue"] >= float(req.get("min_revenue") or 0))


def evaluate_tiers(db=None, *, partner_id: Optional[str] = None, force: bool = False,
                   actor: Any = "system") -> List[Dict[str, Any]]:
    """Returns the tier changes made."""
    db = db if db is not None else get_sync_db()
    if db is None:
        return []
    settings = get_program_settings(db)
    if not force and not settings.get("auto_tiering"):
        return []
    tiers = [t for t in db[K.TIERS].find({"status": "active", "auto_assign": {"$ne": False}}).sort("order", 1)]
    order = {str(t["_id"]): int(t.get("order") or 0) for t in db[K.TIERS].find({}, {"order": 1})}
    q: Dict[str, Any] = {"status": K.P_ACTIVE, "tier_locked": {"$ne": True}}
    if partner_id:
        q["_id"] = oid(partner_id)
    changes = []
    for p in db[K.PARTNERS].find(q):
        pid = str(p["_id"])
        earned = [t for t in tiers if tier_met(db, pid, t)]
        if not earned:
            continue
        best = max(earned, key=lambda t: int(t.get("order") or 0))
        current = str(p.get("tier_id") or "")
        if str(best["_id"]) == current:
            continue
        up = int(best.get("order") or 0) > order.get(current, -1)
        if not up and not settings.get("allow_tier_downgrade"):
            continue
        db[K.PARTNERS].update_one({"_id": p["_id"]}, {"$set": {"tier_id": str(best["_id"]), "updated_at": utcnow()},
                                                       "$push": {"tier_history": {"from": current or None,
                                                                                  "to": str(best["_id"]),
                                                                                  "at": utcnow(), "by": str(actor)}}})
        change = {"partner_id": pid, "from": current or None, "to": str(best["_id"]), "tier": best["name"],
                  "direction": "upgrade" if up else "downgrade"}
        changes.append(change)
        paudit(f"partner.tier.{change['direction']}d", pid, actor=actor, details=change)
        notify_partner(p, "partner_tier", f"Your partner tier is now {best['name']}",
                       ("Congratulations — you reached a new tier." if up else
                        "Your tier changed based on your recent performance."),
                       severity="success" if up else "info", link="/partner#/profile", email=up)
    db[K.SETTINGS].update_one({"_id": "program"}, {"$set": {"tiers_evaluated_at": utcnow()}}, upsert=True)
    if changes:
        logger.info("[partners] tier changes: %s", changes)
    return changes


def evaluate_if_due(db=None) -> List[Dict[str, Any]]:
    """Sweeper entry point: at most once a day."""
    db = db if db is not None else get_sync_db()
    if db is None:
        return []
    doc = db[K.SETTINGS].find_one({"_id": "program"}) or {}
    last = doc.get("tiers_evaluated_at")
    if last is not None:
        from app.billing.tokens import _aware
        if utcnow() - _aware(last) < timedelta(hours=23):
            return []
    return evaluate_tiers(db)
