"""
LeadAI Compliance & Privacy API Routes (Phase 4).

Endpoints:
  GET    /api/public/compliance-notice     Public Data Processing Notice
  POST   /api/compliance/opt-out           Public / prospect opt-out request
  GET    /api/compliance/blocklist         List organization blocklist
  POST   /api/compliance/blocklist         Add contact to blocklist
  DELETE /api/compliance/blocklist/{id}    Remove contact from blocklist
  POST   /api/compliance/delete-request    Right to be Forgotten deletion
  POST   /api/compliance/purge-pii         Trigger PII retention purge
  GET    /api/compliance/settings          Get PII retention settings
  PUT    /api/compliance/settings          Update PII retention settings
"""
import logging

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.admin.audit import request_meta
from app.auth import permissions as P
from app.auth.rate_limit import RateLimiter
from app.auth.tenant import TenantContext, require_org_permission
from app.compliance import service as cs
from app.db.models import utcnow
from app.db.mongo import get_sync_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["compliance"])

_opt_out_limiter = RateLimiter("compliance_opt_out", 10, 300)


class OptOutRequest(BaseModel):
    contact_type: str = Field(..., description="phone, email, or username")
    contact_value: str = Field(..., description="The contact value to opt out")
    reason: str | None = Field("User requested opt-out", description="Optional reason")


class BlocklistAddRequest(BaseModel):
    contact_type: str = Field(..., description="phone, email, or username")
    contact_value: str = Field(..., description="Contact value to block")
    reason: str | None = Field("Do not contact", description="Reason for block")


class DeleteProspectRequest(BaseModel):
    identifier: str = Field(..., description="Phone, email, or username of prospect")
    identifier_type: str = Field("phone", description="phone, email, or username")
    reason: str | None = Field("Right to be Forgotten", description="Deletion reason")


class RetentionSettingsUpdate(BaseModel):
    pii_retention_days: int = Field(..., ge=0, le=365, description="PII retention in days (0=disabled)")


# ── Public Endpoints ─────────────────────────────────────────────────────────

@router.get("/api/public/compliance-notice")
async def get_public_compliance_notice():
    """Return platform Data Processing Notice and Privacy Notice."""
    return cs.get_compliance_notice()


@router.post("/api/compliance/opt-out")
async def public_opt_out(body: OptOutRequest, request: Request):
    """Public / prospect self-serve opt-out endpoint.

    Adds the identifier to the global compliance blocklist and immediately
    purges/redacts matching records.
    """
    ip = request_meta(request)["ip"]
    if not _opt_out_limiter.consume(ip):
        raise HTTPException(status_code=429, detail="Too many opt-out requests. Please try again later.")

    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        res = cs.add_to_blocklist(
            db,
            contact_type=body.contact_type,
            contact_value=body.contact_value,
            reason=f"Public self-service opt-out: {body.reason or ''}".strip(),
            organization_id=None,  # Platform-wide block
            actor_email=f"optout@{ip}",
            ip=ip,
        )
        return {
            "success": True,
            "message": "Opt-out processed successfully. Contact information blocked from future analysis.",
            "data": res,
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ── Authenticated Organization Compliance Endpoints ─────────────────────────

@router.get("/api/compliance/blocklist")
async def get_org_blocklist(
    type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    ctx: TenantContext = Depends(require_org_permission(P.SETTINGS_MANAGE)),  # noqa: B008
):
    """List blocked contacts for the authenticated organization."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    return cs.list_blocklist(
        db,
        organization_id=ctx.organization_id,
        contact_type=type,
        page=page,
        page_size=page_size,
    )


@router.post("/api/compliance/blocklist")
async def add_org_blocklist_entry(
    body: BlocklistAddRequest,
    request: Request,
    ctx: TenantContext = Depends(require_org_permission(P.SETTINGS_MANAGE)),  # noqa: B008
):
    """Add a contact to the organization's blocklist and purge existing leads."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        res = cs.add_to_blocklist(
            db,
            contact_type=body.contact_type,
            contact_value=body.contact_value,
            reason=body.reason or "Admin blocked",
            organization_id=ctx.organization_id,
            actor_email=ctx.email,
            actor_user_id=ctx.user_id,
            ip=request_meta(request)["ip"],
        )
        return {"success": True, "entry": res}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/api/compliance/blocklist/{id}")
async def remove_org_blocklist_entry(
    id: str,
    request: Request,
    ctx: TenantContext = Depends(require_org_permission(P.SETTINGS_MANAGE)),  # noqa: B008
):
    """Remove a contact from the organization's blocklist."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    ok = cs.remove_from_blocklist(
        db,
        blocklist_id=id,
        organization_id=ctx.organization_id,
        actor_email=ctx.email,
        ip=request_meta(request)["ip"],
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Blocklist entry not found")
    return {"success": True, "deleted_id": id}


@router.post("/api/compliance/delete-request")
async def execute_data_deletion(
    body: DeleteProspectRequest,
    request: Request,
    ctx: TenantContext = Depends(require_org_permission(P.LEADS_MANAGE)),  # noqa: B008
):
    """Right to be Forgotten: permanently delete all data for a prospect."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        res = cs.delete_prospect_data(
            db,
            organization_id=ctx.organization_id,
            identifier=body.identifier,
            identifier_type=body.identifier_type,
            reason=body.reason or "Right to be Forgotten",
            actor_email=ctx.email,
            ip=request_meta(request)["ip"],
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/api/compliance/purge-pii")
async def trigger_pii_purge(
    retention_days: int | None = None,
    ctx: TenantContext = Depends(require_org_permission(P.SETTINGS_MANAGE)),  # noqa: B008
):
    """Manually trigger PII retention sweep for this organization."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    res = cs.purge_expired_pii(
        db,
        organization_id=ctx.organization_id,
        retention_days=retention_days,
        actor_email=ctx.email,
    )
    return res


@router.get("/api/compliance/settings")
async def get_compliance_settings(
    ctx: TenantContext = Depends(require_org_permission(P.SETTINGS_MANAGE)),  # noqa: B008
):
    """Get the organization's PII retention configuration."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    org = db["organizations"].find_one({"_id": ObjectId(str(ctx.organization_id))})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    settings = org.get("settings") or {}
    return {
        "pii_retention_days": int(settings.get("pii_retention_days") or 0),
        "data_processing_notice": cs.get_compliance_notice(),
    }


@router.put("/api/compliance/settings")
async def update_compliance_settings(
    body: RetentionSettingsUpdate,
    ctx: TenantContext = Depends(require_org_permission(P.SETTINGS_MANAGE)),  # noqa: B008
):
    """Update the organization's PII retention window (days)."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    db["organizations"].update_one(
        {"_id": ObjectId(str(ctx.organization_id))},
        {"$set": {"settings.pii_retention_days": body.pii_retention_days, "updated_at": utcnow()}},
    )
    return {
        "success": True,
        "pii_retention_days": body.pii_retention_days,
        "message": f"PII retention policy set to {body.pii_retention_days} days (0 = unlimited).",
    }
