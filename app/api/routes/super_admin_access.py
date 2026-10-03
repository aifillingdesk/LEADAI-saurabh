"""Super Admin — every API key and outbound webhook on the platform.

  GET  /api/super-admin/api-keys              organization + partner keys (never hashes)
  POST /api/super-admin/api-keys                issue a key for any organization / partner
  POST /api/super-admin/api-keys/{key_id}/revoke
  GET  /api/super-admin/webhooks              every organization's outbound webhooks (secrets masked)
  POST /api/super-admin/webhooks/{webhook_id}/disable | /enable

Organization admins manage their own keys / webhooks in the Org Admin portal
and partners theirs in the Partner Portal; the Super Admin sees and controls
all of them here. Every change is audited and the owner is notified.
"""
import re
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.admin.audit import aaudit, request_meta
from app.auth.tenant import TenantContext, require_platform_role
from app.db.models import utcnow
from app.db.mongo import get_async_db

router = APIRouter(prefix="/api/super-admin", tags=["super-admin-access"])
SUPER = require_platform_role("super_admin")


def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _iso(v):
    from app.partners.service import clean
    return clean(v)


async def _owner_names(db, docs):
    org_ids = {d.get("organization_id") for d in docs if d.get("organization_id")}
    partner_ids = {d.get("partner_id") for d in docs if d.get("partner_id")}
    orgs = {str(o["_id"]): o.get("name") async for o in db.organizations.find(
        {"_id": {"$in": [ObjectId(i) for i in org_ids if ObjectId.is_valid(i)]}}, {"name": 1})}
    partners = {str(p["_id"]): (p.get("company") or p.get("name")) async for p in db.partners.find(
        {"_id": {"$in": [ObjectId(i) for i in partner_ids if ObjectId.is_valid(i)]}}, {"name": 1, "company": 1})}
    return orgs, partners


@router.get("/api-keys")
async def all_api_keys(owner_type: Optional[str] = None, active: Optional[bool] = None, q: Optional[str] = None,
                       page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
                       ctx: TenantContext = Depends(SUPER)):
    db = _db()
    query: Dict[str, Any] = {}
    if owner_type == "partner":
        query["owner_type"] = "partner"
    elif owner_type == "organization":
        query["owner_type"] = {"$ne": "partner"}
    if active is not None:
        query["is_active"] = active
    if q:
        rx = {"$regex": re.escape(q.strip()[:80]), "$options": "i"}
        query["$or"] = [{"name": rx}, {"prefix": rx}, {"key_id": rx}]
    total = await db.api_keys.count_documents(query)
    docs = [d async for d in db.api_keys.find(query, {"key_hash": 0}).sort("created_at", -1)
            .skip((page - 1) * limit).limit(limit)]
    orgs, partners = await _owner_names(db, docs)
    items = []
    for d in docs:
        is_partner = d.get("owner_type") == "partner"
        items.append({"key_id": d.get("key_id"), "name": d.get("name"), "prefix": d.get("prefix"),
                      "owner_type": "partner" if is_partner else "organization",
                      "owner_id": d.get("partner_id") if is_partner else d.get("organization_id"),
                      "owner_name": partners.get(d.get("partner_id")) if is_partner else orgs.get(d.get("organization_id")),
                      "scopes": d.get("scopes", []), "rate_limit_per_minute": d.get("rate_limit_per_minute"),
                      "is_active": d.get("is_active", False), "usage_count": d.get("usage_count", 0),
                      "created_by": d.get("created_by"), "created_at": _iso(d.get("created_at")),
                      "last_used_at": _iso(d.get("last_used_at")), "revoked_reason": d.get("revoked_reason")})
    return {"success": True, "items": items, "total": total, "page": page, "limit": limit,
            "pages": max(1, -(-total // limit)),
            "counts": {"active": await db.api_keys.count_documents({"is_active": True}),
                       "partner": await db.api_keys.count_documents({"owner_type": "partner"}),
                       "organization": await db.api_keys.count_documents({"owner_type": {"$ne": "partner"}})}}


class ReasonBody(BaseModel):
    reason: str = ""


ORG_SCOPES = ("leads:read", "leads:write", "search:create", "webhooks:manage")


class IssueKeyBody(BaseModel):
    owner_type: str                       # organization | partner
    owner_id: str
    name: str = "Super Admin key"
    scopes: Optional[list] = None         # organization keys only
    reason: str = ""
    grant_api_access: bool = False        # partner keys: also grant the partner "api.access"


@router.post("/api-keys")
async def issue_api_key(body: IssueKeyBody, request: Request, ctx: TenantContext = Depends(SUPER)):
    """Issue a Customer API key for any organization or a Partner API key for
    any partner (support, integrations, migrations). The raw key is returned
    once; the owner is notified and the action audited."""
    import asyncio
    reason = (body.reason or "").strip()[:300]
    if len(reason) < 5:
        raise HTTPException(status_code=422, detail="A reason (at least 5 characters) is required")
    name = (body.name or "Super Admin key").strip()[:60]
    db = _db()
    if body.owner_type == "organization":
        org = await db.organizations.find_one({"_id": ObjectId(body.owner_id)}) if ObjectId.is_valid(body.owner_id) else None
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")
        scopes = sorted(set(body.scopes or ["leads:read"]))
        bad = [s for s in scopes if s not in ORG_SCOPES]
        if bad:
            raise HTTPException(status_code=422, detail=f"Unknown scope(s): {', '.join(bad)}")
        from app.db.mongo import get_sync_db
        from app.services.api_keys import create_api_key
        raw, doc = await asyncio.to_thread(create_api_key, get_sync_db(), organization_id=str(org["_id"]), name=name,
                                           scopes=scopes, created_by=f"super_admin:{ctx.email}")
        await db.api_keys.update_one({"_id": doc["_id"]}, {"$set": {"issued_by_super_admin": True,
                                                                    "issue_reason": reason}})
        meta = request_meta(request)
        await aaudit("api_key.issued_by_super_admin", "security", user=ctx.audit_user(),
                     organization_id=str(org["_id"]), resource_type="api_key", resource_id=doc["key_id"],
                     details={"owner_type": "organization", "scopes": scopes, "name": name, "reason": reason},
                     ip=meta["ip"], user_agent=meta["user_agent"])
        from app.events.notifications import notify_org_admins
        notify_org_admins(str(org["_id"]), "security_event", "LeadAI issued an API key",
                          f"The LeadAI team created the API key “{name}” ({', '.join(scopes)}) for your "
                          f"organization: {reason}", severity="info", link="/org-admin#organization")
    elif body.owner_type == "partner":
        from app.partners import constants as PK
        from app.partners.auth import create_partner_api_key
        from app.partners.service import effective_permissions, load_partner, notify_partner, paudit
        partner = await asyncio.to_thread(load_partner, body.owner_id)
        if partner.get("status") != PK.P_ACTIVE:
            raise HTTPException(status_code=409, detail="The partner is not active")
        if PK.API_ACCESS not in effective_permissions(partner):
            if not body.grant_api_access:
                raise HTTPException(status_code=422, detail={
                    "code": "api_access_disabled",
                    "message": "This partner doesn't have API access. Tick “grant API access” to enable it."})
            await db.partners.update_one({"_id": partner["_id"]}, {"$addToSet": {"permissions": PK.API_ACCESS},
                                                                  "$set": {"updated_at": utcnow()}})
            paudit("partner.permissions.changed", str(partner["_id"]), actor=ctx.audit_user(),
                   details={"added": [PK.API_ACCESS], "removed": [], "by": "super_admin", "reason": reason})
        raw, doc = await asyncio.to_thread(create_partner_api_key, partner, name)
        await db.api_keys.update_one({"_id": doc["_id"]}, {"$set": {"issued_by_super_admin": True,
                                                                    "issue_reason": reason,
                                                                    "created_by": f"super_admin:{ctx.email}"}})
        paudit("partner.api_key.created", str(partner["_id"]), actor=ctx.audit_user(),
               details={"key_id": doc["key_id"], "name": name, "by": "super_admin", "reason": reason},
               resource_type="api_key", resource_id=doc["key_id"])
        notify_partner(partner, "partner_security", "LeadAI issued an API key",
                       f"The LeadAI team created the API key “{name}” for your partner account: {reason}",
                       severity="info", email=True, link="/partner#/api")
    else:
        raise HTTPException(status_code=422, detail="owner_type must be organization or partner")
    return {"success": True, "api_key": raw, "key_id": doc["key_id"], "prefix": doc["prefix"],
            "message": "Copy this key now — it is shown only once."}


@router.post("/api-keys/{key_id}/revoke")
async def revoke_any_api_key(key_id: str, body: ReasonBody, request: Request, ctx: TenantContext = Depends(SUPER)):
    db = _db()
    key = await db.api_keys.find_one({"key_id": key_id})
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    if not key.get("is_active"):
        raise HTTPException(status_code=409, detail="This key is already revoked")
    reason = (body.reason or "").strip()[:300] or "revoked by super admin"
    await db.api_keys.update_one({"_id": key["_id"]}, {"$set": {"is_active": False, "updated_at": utcnow(),
                                                               "revoked_reason": reason, "revoked_by": ctx.email}})
    meta = request_meta(request)
    await aaudit("api_key.revoked_by_super_admin", "security", user=ctx.audit_user(),
                 organization_id=key.get("organization_id"), resource_type="api_key", resource_id=key_id,
                 details={"owner_type": key.get("owner_type") or "organization", "partner_id": key.get("partner_id"),
                          "reason": reason}, ip=meta["ip"], user_agent=meta["user_agent"])
    if key.get("owner_type") == "partner":
        from app.partners.service import load_partner, notify_partner, paudit
        paudit("partner.api_key.revoked", key.get("partner_id"), actor=ctx.audit_user(),
               details={"key_id": key_id, "by": "super_admin", "reason": reason}, resource_type="api_key",
               resource_id=key_id)
        try:
            notify_partner(load_partner(key["partner_id"]), "partner_security", "An API key was revoked",
                           f"LeadAI revoked your API key “{key.get('name')}”: {reason}", severity="warning", email=True)
        except HTTPException:
            pass
    elif key.get("organization_id"):
        from app.events.notifications import notify_org_admins
        notify_org_admins(key["organization_id"], "security_event", "An API key was revoked",
                          f"LeadAI revoked the API key “{key.get('name')}”: {reason}", severity="warning",
                          link="/org-admin#organization")
    return {"success": True}


@router.get("/webhooks")
async def all_webhooks(active: Optional[bool] = None, organization_id: Optional[str] = None,
                       page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
                       ctx: TenantContext = Depends(SUPER)):
    db = _db()
    query: Dict[str, Any] = {}
    if active is not None:
        query["is_active"] = active
    if organization_id:
        query["organization_id"] = organization_id
    total = await db.outbound_webhooks.count_documents(query)
    docs = [d async for d in db.outbound_webhooks.find(query, {"secret": 0}).sort("created_at", -1)
            .skip((page - 1) * limit).limit(limit)]
    orgs, _ = await _owner_names(db, docs)
    items = []
    for d in docs:
        last = await db.webhook_deliveries.find_one({"webhook_id": d.get("webhook_id")}, sort=[("created_at", -1)])
        items.append({"webhook_id": d.get("webhook_id"), "url": d.get("url"), "events": d.get("events", []),
                      "organization_id": d.get("organization_id"), "organization_name": orgs.get(d.get("organization_id")),
                      "is_active": d.get("is_active", False), "created_by": d.get("created_by"),
                      "created_at": _iso(d.get("created_at")), "disabled_reason": d.get("disabled_reason"),
                      "last_delivery": {"status": (last or {}).get("status"), "at": _iso((last or {}).get("created_at"))}
                      if last else None})
    return {"success": True, "items": items, "total": total, "page": page, "limit": limit,
            "pages": max(1, -(-total // limit))}


@router.post("/webhooks/{webhook_id}/{action}")
async def toggle_webhook(webhook_id: str, action: str, body: ReasonBody, request: Request,
                         ctx: TenantContext = Depends(SUPER)):
    if action not in ("disable", "enable"):
        raise HTTPException(status_code=404, detail="Unknown action")
    db = _db()
    w = await db.outbound_webhooks.find_one({"webhook_id": webhook_id})
    if not w:
        raise HTTPException(status_code=404, detail="Webhook not found")
    active = action == "enable"
    reason = (body.reason or "").strip()[:300]
    await db.outbound_webhooks.update_one({"_id": w["_id"]}, {"$set": {
        "is_active": active, "updated_at": utcnow(), "disabled_reason": None if active else (reason or "disabled by super admin"),
        "disabled_by": None if active else ctx.email}})
    meta = request_meta(request)
    await aaudit(f"webhook.{action}d_by_super_admin", "security", user=ctx.audit_user(),
                 organization_id=w.get("organization_id"), resource_type="webhook", resource_id=webhook_id,
                 details={"url": w.get("url"), "reason": reason}, ip=meta["ip"], user_agent=meta["user_agent"])
    if w.get("organization_id"):
        from app.events.notifications import notify_org_admins
        notify_org_admins(w["organization_id"], "security_event", f"A webhook was {action}d",
                          f"LeadAI {action}d the webhook to {w.get('url')}" + (f": {reason}" if reason else "."),
                          severity="warning" if not active else "info", link="/org-admin#organization")
    return {"success": True, "is_active": active}
