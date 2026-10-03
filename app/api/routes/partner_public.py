"""Public partner program endpoints (no session).

  GET  /api/public/partners/program    program summary for the /partners page
  POST /api/public/partners/apply      partner application (rate limited per IP)
  GET  /r/{code}  |  /r/{code}/{campaign}   referral click -> attribution cookie -> landing
"""
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.admin.audit import request_meta
from app.admin.envvars import get_envvar_bool
from app.auth.rate_limit import RateLimiter
from app.auth.service import session_user
from app.config import get_settings
from app.events.security import log_security_event
from app.partners import constants as K
from app.partners import referrals as R
from app.partners import service as S

router = APIRouter(tags=["partner-public"])
_apply_limiter = RateLimiter("partner_apply_ip", 5, 3600)


@router.get("/api/public/partners/program")
def program():
    db = S.db_or_503()
    S.ensure_program_defaults(db)
    settings = S.get_program_settings(db)
    rule = db[K.RULES].find_one({"scope": "global", "status": "active"}, sort=[("created_at", -1)])
    return {"success": True, "program": {
        "applications_open": settings["applications_open"],
        "partner_types": [{"key": K.AFFILIATE, "name": "Affiliate partner",
                           "summary": "Share your referral link and earn commission on every customer you bring."},
                          {"key": K.RESELLER, "name": "Reseller",
                           "summary": "Onboard and manage customers directly, with commission on their subscriptions."}],
        "commission": {"type": rule["commission_type"], "value": rule["value"],
                       "recurring": rule.get("recurring"), "duration_months": rule.get("duration_months")}
        if rule else None,
        "attribution_window_days": settings["attribution_window_days"],
        "min_payout": settings["min_payout"], "payout_methods": settings["payout_methods"],
        "terms_version": settings["terms_version"],
        "tiers": [{"name": t["name"], "description": t.get("description"), "benefits": t.get("benefits") or []}
                  for t in S.list_tiers(db) if t.get("status") == "active"],
    }}


class ApplyBody(BaseModel):
    name: str
    email: str
    password: str = ""
    company: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    website: Optional[str] = None
    business_type: Optional[str] = None
    partner_type: str = K.AFFILIATE
    experience: Optional[str] = None
    promotion_plan: Optional[str] = None
    social_profiles: Optional[Dict[str, str]] = None
    tax_info: Optional[Dict[str, Any]] = None
    payout_info: Optional[Dict[str, Any]] = None
    accepted_terms: bool = False


@router.post("/api/public/partners/apply")
def apply(body: ApplyBody, request: Request):
    ip = request_meta(request).get("ip") or "unknown"
    if not _apply_limiter.consume(ip):
        log_security_event("partner_apply_rate_limited", "low", ip=ip, path=str(request.url.path))
        raise HTTPException(status_code=429, detail="Too many applications from this network. Try again later.")
    S.ensure_program_defaults()
    res = S.submit_application(body.model_dump(), ip=ip)
    return {"success": True, "application": res,
            "message": "Application received. We'll email you when it has been reviewed — you can "
                       "sign in to the Partner Portal any time to check its status."}


def _referral_redirect(code: str, campaign: Optional[str], request: Request):
    meta = request_meta(request)
    target, cookie, vid = R.record_click(
        code, campaign, ip=meta.get("ip"), user_agent=meta.get("user_agent"),
        referer=request.headers.get("referer"),
        existing_cookie=request.cookies.get(K.REF_COOKIE),
        visitor_id=request.cookies.get(K.VISITOR_COOKIE),
        session_claims=session_user(request))
    plan = re.sub(r"[^a-z0-9_-]", "", (request.query_params.get("plan") or "").lower())[:40]
    if plan:  # plan-specific share links from the partner sales kit
        target += ("&" if "?" in target else "?") + "plan=" + plan
    resp = RedirectResponse(target, status_code=302)
    secure = get_envvar_bool("SESSION_COOKIE_SECURE", get_settings().session_cookie_secure)
    resp.set_cookie(K.VISITOR_COOKIE, vid, max_age=365 * 86400, httponly=True, samesite="lax",
                    secure=secure, path="/")
    if cookie:
        days = int(S.get_program_settings().get("attribution_window_days") or 30)
        resp.set_cookie(K.REF_COOKIE, cookie, max_age=days * 86400, httponly=True, samesite="lax",
                        secure=secure, path="/")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


@router.get("/r/{code}", include_in_schema=False)
def referral_link(code: str, request: Request):
    return _referral_redirect(code, None, request)


@router.get("/r/{code}/{campaign}", include_in_schema=False)
def referral_campaign_link(code: str, campaign: str, request: Request):
    return _referral_redirect(code, campaign, request)
