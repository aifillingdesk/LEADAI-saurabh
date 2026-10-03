"""Partner fraud flags — one review queue for every suspicious signal.

A flag NEVER changes money on its own. When it concerns a referral it only
*holds* that referral's unpaid commissions (``requires_manual_approval``):
they still qualify, but nothing is approved or paid until a Super Admin
resolves the flag. Financial changes (reversal / invalidation) happen only
through an explicit, audited Super Admin decision.

Signals: self_referral, suspicious_referral (shared IP), duplicate_customer,
attribution_conflict, attribution_tampering, click_flood, coupon_abuse,
chargeback, cross_partner_access, privilege_probe, invalid_api_access,
unauthorized_action.
"""
import logging
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.partners import constants as K
from app.partners.service import clean, db_or_503, oid, paudit

logger = logging.getLogger(__name__)

FLAGS = "partner_fraud_flags"
SEVERITIES = ("low", "medium", "high")
FLAG_TYPES = {
    "self_referral": "Self-referral blocked",
    "suspicious_referral": "Referral from the partner's own network",
    "duplicate_customer": "Possible duplicate customer",
    "attribution_conflict": "Conflicting attribution signals",
    "attribution_tampering": "Tampered referral cookie",
    "click_flood": "Click flood",
    "coupon_abuse": "Possible coupon abuse",
    "chargeback": "Chargeback on a referral payment",
    "cross_partner_access": "Attempt to access another partner's data",
    "privilege_probe": "Partner tried an admin-only API",
    "invalid_api_access": "Invalid partner API key used",
    "unauthorized_action": "Partner action denied by permissions",
}
# signals that are noisy by nature: at most one open flag per subject per day
_DAILY = {"click_flood", "cross_partner_access", "privilege_probe", "invalid_api_access",
          "unauthorized_action", "attribution_tampering"}


def raise_flag(flag_type: str, *, partner_id: Optional[str], severity: str = "medium",
               subject_type: Optional[str] = None, subject_id: Optional[str] = None,
               details: Optional[Dict[str, Any]] = None, hold_referral_id: Optional[str] = None,
               db=None) -> Optional[Dict[str, Any]]:
    """Open (or refresh) a flag. Best effort — never raises into the caller."""
    try:
        db = db if db is not None else get_sync_db()
        if db is None:
            return None
        now = utcnow()
        key = {"type": flag_type, "partner_id": partner_id, "subject_id": subject_id, "status": "open"}
        if flag_type in _DAILY:
            key["day"] = now.date().isoformat()
        existing = db[FLAGS].find_one(key)
        if existing:
            db[FLAGS].update_one({"_id": existing["_id"]}, {"$inc": {"occurrences": 1},
                                                            "$set": {"last_seen_at": now}})
            return existing
        doc = {**key, "severity": severity if severity in SEVERITIES else "medium",
               "label": FLAG_TYPES.get(flag_type, flag_type), "subject_type": subject_type,
               "referral_id": hold_referral_id, "details": details or {}, "occurrences": 1,
               "created_at": now, "last_seen_at": now, "resolved_at": None, "resolved_by": None,
               "resolution": None, "note": None}
        doc["_id"] = db[FLAGS].insert_one(doc).inserted_id
        if hold_referral_id and oid(hold_referral_id):
            hold_referral(db, hold_referral_id)
        paudit("partner.fraud.flagged", partner_id, actor="system",
               details={"type": flag_type, "severity": doc["severity"], "subject_type": subject_type,
                        "subject_id": subject_id, **(details or {})},
               resource_type="partner_fraud_flag", resource_id=str(doc["_id"]))
        from app.events.notifications import notify_super_admins
        notify_super_admins("security_event", f"Partner fraud alert: {doc['label']}",
                            _describe(db, doc), severity="danger" if doc["severity"] == "high" else "warning",
                            link="/superadmin#/partners?tab=fraud", data={"flag_id": str(doc["_id"])})
        return doc
    except Exception as e:  # never break the business flow that raised it
        logger.warning("fraud flag %s failed: %s", flag_type, e)
        return None


def _describe(db, doc: Dict[str, Any]) -> str:
    who = ""
    if doc.get("partner_id"):
        p = db[K.PARTNERS].find_one({"_id": oid(doc["partner_id"])}, {"name": 1, "company": 1})
        who = (p or {}).get("company") or (p or {}).get("name") or ""
    extra = ", ".join(f"{k}: {v}" for k, v in list((doc.get("details") or {}).items())[:3])
    return (f"{who} — " if who else "") + (extra or doc.get("label", ""))


def hold_referral(db, referral_id: str) -> None:
    """Hold, don't modify: flag the referral and stop its unpaid commissions
    from being approved / paid until review."""
    db[K.REFERRALS].update_one({"_id": oid(referral_id)}, {"$set": {"suspicious": True, "updated_at": utcnow()}})
    db[K.COMMISSIONS].update_many({"referral_id": str(referral_id),
                                   "status": {"$in": [K.C_PENDING, K.C_QUALIFIED]}},
                                  {"$set": {"requires_manual_approval": True, "updated_at": utcnow()}})


def resolve_flag(flag_id: str, decision: str, *, actor: Any, note: str = "",
                 ip: Optional[str] = None) -> Dict[str, Any]:
    """Super Admin review. ``dismiss`` = false positive (releases the hold when
    no other open flag concerns the referral); ``confirm`` = real issue
    (hold stays; optionally ``invalidate_referral`` reverses its commissions)."""
    db = db_or_503()
    f = db[FLAGS].find_one({"_id": oid(flag_id)}) if oid(flag_id) else None
    if not f:
        raise HTTPException(status_code=404, detail="Flag not found")
    if f["status"] != "open":
        raise HTTPException(status_code=409, detail="This flag is already resolved")
    if decision not in ("dismiss", "confirm", "invalidate_referral"):
        raise HTTPException(status_code=422, detail="decision must be dismiss, confirm or invalidate_referral")
    if decision != "dismiss" and not (note or "").strip():
        raise HTTPException(status_code=422, detail="A note is required")
    now = utcnow()
    who = actor.get("email") if isinstance(actor, dict) else getattr(actor, "email", None) or str(actor)
    status = "dismissed" if decision == "dismiss" else "confirmed"
    db[FLAGS].update_one({"_id": f["_id"], "status": "open"},
                         {"$set": {"status": status, "resolution": decision, "resolved_at": now,
                                   "resolved_by": who, "note": (note or "")[:500]}})
    rid = f.get("referral_id")
    if rid and decision == "dismiss" and not db[FLAGS].find_one({"referral_id": rid, "status": "open"}):
        from app.partners.referrals import review_referral
        review_referral(rid, "clear", actor=actor, reason=f"flag dismissed: {note}", ip=ip)
    if rid and decision == "invalidate_referral":
        from app.partners.referrals import review_referral
        ref = db[K.REFERRALS].find_one({"_id": oid(rid)})
        if ref and ref.get("status") == "active":
            review_referral(rid, "invalidate", actor=actor, reason=note, ip=ip)
    paudit(f"partner.fraud.{status}", f.get("partner_id"), actor=actor, ip=ip,
           details={"flag_id": flag_id, "type": f["type"], "decision": decision, "note": note},
           resource_type="partner_fraud_flag", resource_id=flag_id)
    return clean(db[FLAGS].find_one({"_id": f["_id"]}))
