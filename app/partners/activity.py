"""Partner activity log — everything a partner does, visible to the Super Admin.

One collection (``partner_activity``) holds:

* ``auth``     — sign-in, failed sign-in, 2FA prompts, sign-out (partner scope)
* ``request``  — every Partner Portal / Partner API request (method, path,
                 status, session or API key, duration)
* ``action``   — every audited partner event (mirrors ``paudit``: applications,
                 commissions, payouts, coupons, deals, settings… including the
                 Super Admin's actions on that partner)

The audit log (``audit_logs``) stays the tamper-evident record; this feed is
the searchable operational view. Rows expire after the program setting
``activity_retention_days`` (TTL index on ``expires_at``).
"""
import logging
import time
from datetime import timedelta
from typing import Any, Dict, Optional

from app.db.models import utcnow
from app.db.mongo import get_sync_db

logger = logging.getLogger(__name__)

COLL = "partner_activity"
KINDS = ("auth", "request", "action")
# the portal's 60-second unread-badge poll is not activity
_SKIP = {("GET", "/api/partner/v1/notifications", "unread=true&limit=1")}


def _retention_days(db) -> int:
    try:
        from app.partners.service import get_program_settings
        return max(7, int(get_program_settings(db).get("activity_retention_days") or 365))
    except Exception:
        return 365


def record(kind: str, action: str, *, partner_id: Optional[str] = None, user_id: Optional[str] = None,
           email: Optional[str] = None, actor: Optional[str] = None, ip: Optional[str] = None,
           user_agent: Optional[str] = None, method: Optional[str] = None, path: Optional[str] = None,
           status: Optional[int] = None, via_api_key: bool = False, api_key_id: Optional[str] = None,
           duration_ms: Optional[int] = None, success: bool = True,
           details: Optional[Dict[str, Any]] = None, db=None) -> None:
    """Best effort: activity logging never breaks the request it describes."""
    try:
        db = db if db is not None else get_sync_db()
        if db is None:
            return
        from app.partners.service import ip_hash
        now = utcnow()
        db[COLL].insert_one({
            "kind": kind, "action": action, "partner_id": str(partner_id) if partner_id else None,
            "user_id": str(user_id) if user_id else None, "email": (email or "").lower() or None,
            "actor": actor or email, "ip_hash": ip_hash(ip), "user_agent": (user_agent or "")[:200] or None,
            "method": method, "path": (path or "")[:300] or None, "status": status,
            "via_api_key": via_api_key, "api_key_id": api_key_id, "duration_ms": duration_ms,
            "success": success, "details": details or {}, "at": now,
            "expires_at": now + timedelta(days=_retention_days(db)),
        })
    except Exception as e:
        logger.debug("partner activity write failed: %s", e)


def record_request(request, response_status: int, started: float) -> None:
    """Called by the auth gate after every Partner Portal / API request."""
    ctx = getattr(request.state, "partner_ctx", None)
    claims = getattr(request.state, "user", None) or {}
    path, method = request.url.path, request.method
    if (method, path, request.url.query) in _SKIP:
        return
    if ctx is None and not claims.get("user_id"):
        return  # unauthenticated / invalid key: covered by security events + fraud flags
    from app.admin.audit import request_meta
    meta = request_meta(request)
    record("request", f"{method} {path}",
           partner_id=getattr(ctx, "partner_id", None) or claims.get("partner_id") or None,
           user_id=getattr(ctx, "user_id", None) or claims.get("user_id"),
           email=getattr(ctx, "email", None) or claims.get("email"), ip=meta.get("ip"),
           user_agent=meta.get("user_agent"), method=method, path=path, status=response_status,
           via_api_key=bool(getattr(ctx, "via_api_key", False)), api_key_id=getattr(ctx, "api_key_id", None),
           duration_ms=int((time.perf_counter() - started) * 1000), success=response_status < 400,
           actor=claims.get("impersonated_by") and f"{claims['impersonated_by']} (as partner)",
           details={**({"query": request.url.query[:200]} if request.url.query else {}),
                    **({"impersonated_by": claims["impersonated_by"]} if claims.get("impersonated_by") else {})})


def partner_id_for_user(db, user_id: Optional[str]) -> Optional[str]:
    if not user_id or db is None:
        return None
    from app.partners import constants as K
    p = db[K.PARTNERS].find_one({"user_id": str(user_id)}, {"_id": 1})
    return str(p["_id"]) if p else None


def summary(db, partner_id: str) -> Dict[str, Any]:
    """Last sign-in / activity, request volume and API usage for one partner."""
    since = utcnow() - timedelta(days=30)
    q = {"partner_id": str(partner_id)}
    last_login = db[COLL].find_one({**q, "kind": "auth", "action": "login", "success": True}, sort=[("at", -1)])
    last_any = db[COLL].find_one(q, sort=[("at", -1)])
    return {
        "last_login_at": (last_login or {}).get("at"),
        "last_activity_at": (last_any or {}).get("at"),
        "requests_30d": db[COLL].count_documents({**q, "kind": "request", "at": {"$gte": since}}),
        "api_calls_30d": db[COLL].count_documents({**q, "kind": "request", "via_api_key": True,
                                                   "at": {"$gte": since}}),
        "failed_logins_30d": db[COLL].count_documents({**q, "kind": "auth", "action": "login",
                                                       "success": False, "at": {"$gte": since}}),
        "denied_30d": db[COLL].count_documents({**q, "kind": "request", "status": {"$in": [401, 403, 404]},
                                                "at": {"$gte": since}}),
        "actions_30d": db[COLL].count_documents({**q, "kind": "action", "at": {"$gte": since}}),
    }
