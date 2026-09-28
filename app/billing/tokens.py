"""Token ledger — allocated / used / remaining per organization.

Two collections:
  * ``token_balances``  one doc per organization:
        {organization_id, allocated, used, remaining, source ("demo"|"plan"),
         expires_at, notified_thresholds: [80, 100], updated_at}
  * ``token_ledger``    append-only entries:
        {organization_id, user_id, type (allocate|consume|refund|adjust|expire),
         amount, reason, reference, actor, balance_after, created_at}

Consumption is ATOMIC: a single conditional ``update_one`` requires
``remaining >= amount`` and not-expired, so concurrent requests can never
overdraw. Organizations without a balance document are not token-metered
(legacy customers keep working); every approved demo and every confirmed
paid plan gets one.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.db.models import utcnow
from app.db.mongo import get_sync_db

logger = logging.getLogger(__name__)

BALANCES = "token_balances"
LEDGER = "token_ledger"
_THRESHOLDS = (80, 100)


class TokensExhaustedException(HTTPException):
    def __init__(self, needed: int, remaining: int, expired: bool = False):
        code = "TOKENS_EXPIRED" if expired else "TOKENS_EXHAUSTED"
        msg = ("Your tokens have expired. Choose a plan to continue."
               if expired else
               f"Not enough tokens ({remaining} left, {needed} needed). Choose a plan to continue.")
        super().__init__(status_code=402, detail={
            "success": False, "code": code, "error": code, "metric": "tokens",
            "needed": needed, "remaining": remaining, "upgrade_available": True,
            "message": msg,
        })


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _ledger(db, org_id: str, etype: str, amount: int, *, user_id=None, reason="",
            reference=None, actor=None, balance_after=None) -> None:
    try:
        db[LEDGER].insert_one({
            "organization_id": org_id, "user_id": user_id, "type": etype,
            "amount": int(amount), "reason": reason, "reference": reference,
            "actor": actor, "balance_after": balance_after, "created_at": utcnow(),
        })
    except Exception as e:
        logger.warning("token ledger write failed: %s", e)


def get_balance(organization_id: str) -> Optional[Dict[str, Any]]:
    """{allocated, used, remaining, source, expires_at, expired} or None
    when the organization is not token-metered."""
    db = get_sync_db()
    if db is None or not organization_id:
        return None
    doc = db[BALANCES].find_one({"organization_id": str(organization_id)})
    if not doc:
        return None
    exp = _aware(doc.get("expires_at"))
    return {
        "allocated": int(doc.get("allocated", 0)),
        "used": int(doc.get("used", 0)),
        "remaining": int(doc.get("remaining", 0)),
        "source": doc.get("source"),
        "expires_at": exp.isoformat() if exp else None,
        "expired": bool(exp and exp < datetime.now(timezone.utc)),
    }


def allocate(organization_id: str, amount: int, *, source: str, actor: str,
             reason: str = "", expires_at: Optional[datetime] = None,
             reset: bool = False) -> Dict[str, Any]:
    """Grant tokens. ``reset=True`` replaces the balance (new plan period)."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    org_id = str(organization_id)
    amount = int(amount)
    now = utcnow()
    if reset:
        db[BALANCES].update_one({"organization_id": org_id}, {"$set": {
            "allocated": amount, "used": 0, "remaining": amount, "source": source,
            "expires_at": expires_at, "notified_thresholds": [], "updated_at": now,
        }}, upsert=True)
    else:
        update: Dict[str, Any] = {"$inc": {"allocated": amount, "remaining": amount},
                                  "$set": {"source": source, "updated_at": now,
                                           "notified_thresholds": []},
                                  "$setOnInsert": {"used": 0}}
        if expires_at is not None:
            update["$set"]["expires_at"] = expires_at
        db[BALANCES].update_one({"organization_id": org_id}, update, upsert=True)
    bal = get_balance(org_id) or {}
    _ledger(db, org_id, "allocate", amount, reason=reason or source, actor=actor,
            balance_after=bal.get("remaining"))
    try:
        from app.admin.audit import audit
        audit("tokens.granted", "billing", user=actor, organization_id=org_id,
              resource_type="token_balance", resource_id=org_id,
              details={"amount": amount, "source": source, "reset": reset,
                       "expires_at": expires_at.isoformat() if expires_at else None})
    except Exception:
        pass
    return bal


def adjust(organization_id: str, delta: int, *, actor: str, reason: str) -> Dict[str, Any]:
    """Manual Super Admin adjustment (positive or negative)."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    org_id = str(organization_id)
    delta = int(delta)
    q: Dict[str, Any] = {"organization_id": org_id}
    if delta < 0:
        q["remaining"] = {"$gte": -delta}
    update: Dict[str, Any] = {"$inc": {"allocated": delta, "remaining": delta},
                              "$set": {"updated_at": utcnow()}}
    before = db[BALANCES].find_one({"organization_id": org_id}) or {}
    exp = _aware(before.get("expires_at"))
    if delta > 0 and exp and exp < datetime.now(timezone.utc):
        # a top-up of an expired balance is meant to be usable
        update["$set"]["expires_at"] = None
    res = db[BALANCES].update_one(q, update)
    if res.matched_count == 0:
        raise HTTPException(status_code=400, detail="Adjustment would make the balance negative")
    bal = get_balance(org_id) or {}
    if delta > 0:
        # re-arm the usage warnings that no longer apply after the top-up
        allocated = bal.get("allocated") or 0
        pct = (bal.get("used", 0) * 100 / allocated) if allocated else 0
        db[BALANCES].update_one({"organization_id": org_id},
                                {"$pull": {"notified_thresholds": {"$gt": pct}}})
    _ledger(db, org_id, "adjust", delta, reason=reason, actor=actor,
            balance_after=bal.get("remaining"))
    return bal


def consume(organization_id: str, amount: int, *, user_id: Optional[str] = None,
            reason: str = "", reference: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Atomically spend tokens. Raises TokensExhaustedException (402) when the
    balance is short or expired. Returns None when the org is not metered."""
    amount = int(amount)
    if amount <= 0 or not organization_id:
        return get_balance(organization_id) if organization_id else None
    db = get_sync_db()
    if db is None:
        return None
    org_id = str(organization_id)
    doc = db[BALANCES].find_one({"organization_id": org_id})
    if not doc:
        return None  # not token-metered
    exp = _aware(doc.get("expires_at"))
    if exp and exp < datetime.now(timezone.utc):
        raise TokensExhaustedException(amount, int(doc.get("remaining", 0)), expired=True)
    res = db[BALANCES].update_one(
        {"organization_id": org_id, "remaining": {"$gte": amount}},
        {"$inc": {"remaining": -amount, "used": amount}, "$set": {"updated_at": utcnow()}})
    if res.modified_count == 0:
        fresh = db[BALANCES].find_one({"organization_id": org_id}) or {}
        _notify_exhausted(db, org_id, fresh)
        raise TokensExhaustedException(amount, int(fresh.get("remaining", 0)))
    bal = get_balance(org_id) or {}
    _ledger(db, org_id, "consume", amount, user_id=user_id, reason=reason,
            reference=reference, balance_after=bal.get("remaining"))
    _check_thresholds(db, org_id, bal)
    return bal


def refund(organization_id: str, amount: int, *, reason: str,
           reference: Optional[str] = None) -> None:
    """Give tokens back (e.g. a search that failed before doing any work)."""
    amount = int(amount)
    db = get_sync_db()
    if db is None or amount <= 0:
        return
    org_id = str(organization_id)
    res = db[BALANCES].update_one({"organization_id": org_id, "used": {"$gte": amount}},
                                  {"$inc": {"remaining": amount, "used": -amount}})
    if res.modified_count:
        bal = get_balance(org_id) or {}
        _ledger(db, org_id, "refund", amount, reason=reason, reference=reference,
                balance_after=bal.get("remaining"))


def _notify_exhausted(db, org_id: str, bal: Dict[str, Any]) -> None:
    """A request refused for lack of tokens counts as "limit reached" even
    below 100% usage (e.g. 91/100 used, action costs 10) — alert once."""
    res = db[BALANCES].update_one(
        {"organization_id": org_id, "notified_thresholds": {"$ne": 100}},
        {"$addToSet": {"notified_thresholds": 100}})
    if not res.modified_count:
        return
    try:
        from app.events.notifications import notify_org_admins, notify_super_admins
        msg = (f"{bal.get('remaining', 0)} tokens left — not enough for the requested action. "
               "Top up or upgrade to continue.")
        notify_org_admins(org_id, "usage_threshold", "Token limit reached", msg, severity="danger",
                          link="/org-admin#subscription", data={"percent": 100})
        notify_super_admins("high_token_usage", "Token limit reached", f"Organization {org_id}: {msg}",
                            severity="warning", data={"organization_id": org_id})
    except Exception:
        pass


def _org_thresholds(db, org_id: str):
    """Warning thresholds: the organization's ``usage_warning_percent``
    (Org Admin settings) replaces the default warning level; 100% always fires."""
    warn = _THRESHOLDS[0]
    try:
        from bson import ObjectId
        org = db.organizations.find_one({"_id": ObjectId(str(org_id))},
                                        {"settings.usage_warning_percent": 1}) or {}
        value = int((org.get("settings") or {}).get("usage_warning_percent") or warn)
        if 1 <= value < 100:
            warn = value
    except Exception:
        pass
    return (warn, 100)


def _check_thresholds(db, org_id: str, bal: Dict[str, Any]) -> None:
    allocated = bal.get("allocated") or 0
    if allocated <= 0:
        return
    pct = bal.get("used", 0) * 100 / allocated
    for t in _org_thresholds(db, org_id):
        if pct < t:
            continue
        res = db[BALANCES].update_one(
            {"organization_id": org_id, "notified_thresholds": {"$ne": t}},
            {"$addToSet": {"notified_thresholds": t}})
        if not res.modified_count:
            continue
        try:
            from app.events.notifications import notify_org_admins, notify_super_admins
            title = f"Token usage reached {t}%"
            msg = f"{bal.get('used')}/{allocated} tokens used, {bal.get('remaining')} remaining."
            notify_org_admins(org_id, "usage_threshold", title, msg,
                              severity="warning" if t < 100 else "danger",
                              link="/org-admin#subscription", data={"percent": t})
            notify_super_admins("high_token_usage", title, f"Organization {org_id}: {msg}",
                                severity="warning", data={"organization_id": org_id})
        except Exception:
            pass
