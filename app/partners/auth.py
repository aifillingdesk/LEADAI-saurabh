"""Partner authentication + RBAC.

Partners sign in through the existing ``/api/auth/login`` with
``scope="partner"`` (same users collection, password hashing, lockout,
throttling, session tracking and revocation as every other account). Every
request re-reads the user and partner records, so suspension or permission
changes apply immediately.

API access: partner API keys live in the existing ``api_keys`` collection
(``owner_type="partner"``, prefix ``lap_live_``) and are read-only.
"""
import hashlib
import logging
import secrets
import uuid
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.service import _verify_and_migrate_password, session_user
from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.partners import constants as K
from app.partners.service import effective_permissions, oid

logger = logging.getLogger(__name__)


class PartnerContext(BaseModel):
    user_id: str
    email: str
    name: str = ""
    partner_id: str = ""
    application_id: str = ""
    application_status: str = ""
    status: str = ""                      # partner status ("" for applicants)
    partner_type: str = ""
    permissions: List[str] = Field(default_factory=list)
    via_api_key: bool = False
    api_key_id: Optional[str] = None
    session_id: Optional[str] = None
    impersonated_by: Optional[str] = None      # Super Admin "view as partner"

    @property
    def is_active_partner(self) -> bool:
        return bool(self.partner_id) and self.status == K.P_ACTIVE

    def audit_user(self) -> Dict[str, Any]:
        return {"user_id": self.user_id, "email": self.email, "role": "partner",
                "organization_id": None, "session_id": self.session_id,
                "impersonated_by": self.impersonated_by}


def _deny(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


# ── login (called from /api/auth/login) ──────────────────────────────────────

def verify_partner_login(email: str, password: str) -> tuple:
    """(claims, error). Errors before password verification are generic so
    the endpoint is not an account oracle."""
    db = get_sync_db()
    if db is None:
        return None, "Database unavailable"
    email = (email or "").strip().lower()
    user = db.users.find_one({"email": email})
    if not user or not password:
        return None, "Invalid email or password"
    ok, new_hash = _verify_and_migrate_password(password, (user.get("password_hash") or "").strip())
    if not ok:
        return None, "Invalid email or password"
    if new_hash:
        db.users.update_one({"_id": user["_id"]}, {"$set": {"password_hash": new_hash}})
    if user.get("status") in ("suspended", "disabled", "deactivated"):
        return None, "Account is suspended"
    uid = str(user["_id"])
    partner = db[K.PARTNERS].find_one({"user_id": uid})
    application = db[K.APPLICATIONS].find_one({"user_id": uid}, sort=[("created_at", -1)])
    if partner is None:
        if application is None:
            return None, "Not a partner account"
        if application["status"] == K.APP_REJECTED:
            return None, "Partner application rejected"
    elif partner["status"] != K.P_ACTIVE:
        return None, "Partner suspended"
    db.users.update_one({"_id": user["_id"]}, {"$set": {"last_login": utcnow()}})
    return {
        "user_id": uid, "email": email, "name": user.get("name") or email.split("@")[0],
        "role": "partner", "scope": "partner",
        "partner_id": str(partner["_id"]) if partner else "",
        "organization_id": "", "organization_name": "", "organization_slug": "",
        "org_role": "", "is_platform_admin": False, "platform_role": None,
    }, None


# ── per-request context ──────────────────────────────────────────────────────

def _context_from_session(request: Request) -> PartnerContext:
    claims = session_user(request)
    if not claims:
        _deny(401, "unauthorized", "Sign in required")
    if claims.get("scope") != "partner":
        _deny(401, "unauthorized", "Partner sign-in required")
    if claims.get("impersonated_by"):
        from app.auth.tenant import _check_impersonation
        _check_impersonation(claims)
    db = get_sync_db()
    if db is None:
        _deny(503, "service_unavailable", "Database unavailable")
    user = db.users.find_one({"_id": oid(claims.get("user_id"))}) if oid(claims.get("user_id")) else None
    if not user or (user.get("email") or "").lower() != (claims.get("email") or "").lower():
        _deny(401, "unauthorized", "Account not found")
    if user.get("status") in ("suspended", "disabled", "deactivated"):
        _deny(403, "account_suspended", "Account is suspended")
    uid = str(user["_id"])
    partner = db[K.PARTNERS].find_one({"user_id": uid})
    application = db[K.APPLICATIONS].find_one({"user_id": uid}, sort=[("created_at", -1)])
    if partner is None and application is None:
        _deny(403, "not_a_partner", "This account is not a partner")
    return PartnerContext(
        user_id=uid, email=user["email"], name=user.get("name") or "",
        partner_id=str(partner["_id"]) if partner else "",
        application_id=str(application["_id"]) if application else "",
        application_status=(application or {}).get("status", ""),
        status=(partner or {}).get("status", ""), partner_type=(partner or {}).get("partner_type", ""),
        permissions=effective_permissions(partner), session_id=claims.get("session_id"),
        impersonated_by=claims.get("impersonated_by"))


def _api_key_from(request: Request) -> Optional[str]:
    raw = request.headers.get("x-api-key")
    auth = request.headers.get("authorization") or ""
    if not raw and auth.lower().startswith("bearer "):
        raw = auth.split(" ", 1)[1].strip()
    return raw if raw and raw.startswith(K.API_KEY_PREFIX) else None


def _context_from_api_key(raw: str, ip: Optional[str] = None) -> PartnerContext:
    db = get_sync_db()
    if db is None:
        _deny(503, "service_unavailable", "Database unavailable")
    key = db.api_keys.find_one({"key_hash": hash_key(raw), "is_active": True, "owner_type": "partner"})
    if not key:
        revoked = db.api_keys.find_one({"key_hash": hash_key(raw), "owner_type": "partner"}, {"partner_id": 1, "key_id": 1})
        from app.events.security import log_security_event
        log_security_event("invalid_api_key", "medium", ip=ip,
                           details={"key_prefix": raw[:14], "revoked_key": bool(revoked)})
        from app.partners.fraud import raise_flag
        raise_flag("invalid_api_access", partner_id=(revoked or {}).get("partner_id"), severity="low",
                   subject_type="api_key", subject_id=(revoked or {}).get("key_id") or (ip or "unknown"),
                   details={"key_prefix": raw[:14], "revoked_key": bool(revoked)})
        _deny(401, "invalid_api_key", "Invalid or revoked API key")
    db.api_keys.update_one({"_id": key["_id"]}, {"$inc": {"usage_count": 1}})
    from app.auth.rate_limit import RateLimiter
    limiter = RateLimiter("partner_api_key", int(key.get("rate_limit_per_minute") or 60), 60)
    if not limiter.consume(key["key_id"]):
        raise HTTPException(status_code=429, detail="API rate limit exceeded",
                            headers={"Retry-After": str(max(1, limiter.retry_after(key["key_id"])))})
    partner = db[K.PARTNERS].find_one({"_id": oid(key.get("partner_id"))})
    perms = effective_permissions(partner)
    if not partner or partner.get("status") != K.P_ACTIVE or K.API_ACCESS not in perms:
        _deny(403, "api_access_disabled", "API access is not enabled for this partner")
    db.api_keys.update_one({"_id": key["_id"]}, {"$set": {"last_used_at": utcnow()}})
    return PartnerContext(user_id=str(partner["user_id"]), email=partner["email"],
                          name=partner.get("name") or "", partner_id=str(partner["_id"]),
                          status=partner["status"], partner_type=partner.get("partner_type", ""),
                          permissions=perms, via_api_key=True, api_key_id=key["key_id"])


def get_partner_session(request: Request) -> PartnerContext:
    """Signed-in partner OR applicant (application status pages only)."""
    cached = getattr(request.state, "partner_ctx", None)
    if isinstance(cached, PartnerContext) and not cached.via_api_key:
        return cached
    ctx = _context_from_session(request)
    request.state.partner_ctx = ctx
    return ctx


def get_partner_context(request: Request) -> PartnerContext:
    """An ACTIVE partner, via session or (read-only) API key."""
    cached = getattr(request.state, "partner_ctx", None)
    if isinstance(cached, PartnerContext) and cached.is_active_partner:
        return cached
    raw = _api_key_from(request)
    if raw:
        from app.admin.audit import request_meta
        ctx = _context_from_api_key(raw, request_meta(request).get("ip"))
    else:
        ctx = _context_from_session(request)
    if not ctx.partner_id:
        _deny(403, "partner_not_approved", "Your partner application has not been approved yet")
    if ctx.status != K.P_ACTIVE:
        _deny(403, "partner_suspended", "Your partner account is suspended")
    request.state.partner_ctx = ctx
    return ctx


def _permission_denied(request: Request, ctx: PartnerContext, perm: str):
    try:
        from app.events.security import security_event_from_request
        security_event_from_request(request, "permission_denied", "low",
                                    details={"permission": perm, "partner_id": ctx.partner_id,
                                             "actor": ctx.email})
    except Exception:
        pass
    from app.partners.fraud import raise_flag
    raise_flag("unauthorized_action", partner_id=ctx.partner_id, severity="low", subject_type="permission",
               subject_id=perm, details={"permission": perm, "path": str(request.url.path),
                                         "via_api_key": ctx.via_api_key})
    _deny(403, "permission_denied", f"Your partner account does not have '{perm}'")


def require_partner_permission(perm: Optional[str] = None, *, write: bool = False):
    """Dependency: active partner holding ``perm``. ``write`` endpoints refuse
    API keys (keys are read-only)."""
    def _dep(request: Request, ctx: PartnerContext = Depends(get_partner_context)) -> PartnerContext:
        if write and ctx.via_api_key:
            _deny(403, "api_key_read_only", "API keys are read-only; use the Partner Portal")
        if perm and perm not in ctx.permissions:
            _permission_denied(request, ctx, perm)
        return ctx
    return _dep


def load_partner_doc(ctx: PartnerContext) -> Dict[str, Any]:
    db = get_sync_db()
    p = db[K.PARTNERS].find_one({"_id": oid(ctx.partner_id)}) if db is not None else None
    if not p:
        _deny(404, "partner_not_found", "Partner not found")
    return p


# ── partner API keys (existing api_keys collection) ──────────────────────────

def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()


def create_partner_api_key(partner: Dict[str, Any], name: str) -> tuple:
    db = get_sync_db()
    if db.api_keys.count_documents({"partner_id": str(partner["_id"]), "is_active": True}) >= 5:
        raise HTTPException(status_code=422, detail="You can have at most 5 active API keys")
    raw = K.API_KEY_PREFIX + secrets.token_urlsafe(32)
    now = utcnow()
    doc = {"key_id": f"pkey_{uuid.uuid4().hex[:10]}", "name": (name or "API key").strip()[:60],
           "owner_type": "partner", "partner_id": str(partner["_id"]), "organization_id": None,
           "key_hash": hash_key(raw), "prefix": raw[:14] + "...", "scopes": ["partner:read"],
           "rate_limit_per_minute": 60, "is_active": True, "created_by": partner["email"],
           "last_used_at": None, "created_at": now, "updated_at": now}
    db.api_keys.insert_one(doc)
    return raw, doc


def list_partner_api_keys(partner_id: str) -> List[Dict[str, Any]]:
    from app.partners.service import clean
    db = get_sync_db()
    return [clean(k) for k in db.api_keys.find({"partner_id": str(partner_id)}, {"key_hash": 0})
            .sort("created_at", -1)]


def revoke_partner_api_key(partner_id: str, key_id: str) -> bool:
    db = get_sync_db()
    res = db.api_keys.update_one({"partner_id": str(partner_id), "key_id": key_id, "is_active": True},
                                 {"$set": {"is_active": False, "updated_at": utcnow(),
                                           "revoked_reason": "revoked_by_partner"}})
    return res.modified_count > 0
