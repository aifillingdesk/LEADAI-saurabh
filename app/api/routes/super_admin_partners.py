"""Super Admin — Partner Management (/api/super-admin/partners)

Reads need ``partners.view``; every mutation needs ``partners.manage`` (the
Super Admin holds both) and is audited (category "partner").

  GET   /partners/overview
  GET   /partners/applications            ?status&q&page&limit&sort
  GET   /partners/applications/{id}
  POST  /partners/applications/{id}/approve           {partner_type, tier_id, permissions, note}
  POST  /partners/applications/{id}/reject            {reason}
  POST  /partners/applications/{id}/request-changes   {note}
  GET   /partners                         ?status&partner_type&q&page&limit&sort
  GET   /partners/{id}                    full detail (payout info unmasked) + stats
  PATCH /partners/{id}                    type, tier, permissions, profile, payout/tax, notes
  POST  /partners/{id}/suspend | /reactivate
  GET   /partners/{id}/referrals | /commissions | /payouts | /clicks | /audit
  POST  /partners/{id}/adjustments        {amount, currency, reason}
  POST  /partners/{id}/notify             {title, message}
  POST  /partners/notify                  broadcast {title, message, partner_type?}
  GET   /partners/commissions             ?status&partner_id
  POST  /partners/commissions/{id}/approve | /reverse {reason}
  GET   /partners/payouts                 ?status&partner_id
  POST  /partners/payouts/{id}/{approve|mark_paid|reject}   {reason, reference}
  GET|POST /partners/tiers   PATCH /partners/tiers/{id}
  GET|POST /partners/commission-rules   PATCH /partners/commission-rules/{id}
  GET|PUT  /partners/settings
  GET|POST /partners/coupons   PATCH /partners/coupons/{id}
  GET|POST /partners/assets    PATCH /partners/assets/{id}
  GET   /partners/meta                    permission catalog, types, statuses
"""
import re
from typing import Any, Dict, List, Optional

import json
import os
from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel

from app.admin.audit import request_meta
from app.auth.permissions import PARTNERS_MANAGE, PARTNERS_VIEW
from app.auth.tenant import TenantContext, require_platform_permission, require_platform_role
from app.db.models import utcnow
from app.partners import commissions as C
from app.partners import constants as K
from app.partners import coupons as CP
from app.partners import referrals as R
from app.partners import service as S
from app.partners import stats as ST

router = APIRouter(prefix="/api/super-admin/partners", tags=["super-admin-partners"])
VIEW = require_platform_permission(PARTNERS_VIEW)
MANAGE = require_platform_permission(PARTNERS_MANAGE)


def _ip(request: Request) -> Optional[str]:
    return request_meta(request).get("ip")


def _actor(ctx: TenantContext) -> Dict[str, Any]:
    return ctx.audit_user()


def _search(q: Optional[str], fields: List[str]) -> Dict[str, Any]:
    if not q:
        return {}
    rx = {"$regex": re.escape(q.strip()[:80]), "$options": "i"}
    return {"$or": [{f: rx} for f in fields]}


def _page(coll, query, *, page: int, limit: int, sort: str, allowed: tuple, out) -> Dict[str, Any]:
    field = sort.lstrip("-")
    if field not in allowed:
        field, sort = "created_at", "-created_at"
    direction = -1 if sort.startswith("-") else 1
    total = coll.count_documents(query)
    items = [out(d) for d in coll.find(query).sort(field, direction).skip((page - 1) * limit).limit(limit)]
    return {"items": items, "total": total, "page": page, "limit": limit,
            "pages": max(1, -(-total // limit))}


@router.get("/meta")
def meta(ctx: TenantContext = Depends(VIEW)):
    return {"success": True, "permissions": K.PERMISSION_LABELS,
            "default_permissions": K.DEFAULT_PERMISSIONS, "reseller_only": sorted(K.RESELLER_ONLY),
            "partner_types": K.PARTNER_TYPES, "application_statuses": K.APPLICATION_STATUSES,
            "commission_statuses": K.COMMISSION_STATUSES, "payout_statuses": K.PAYOUT_STATUSES,
            "attribution_models": K.ATTRIBUTION_MODELS, "window_choices": K.WINDOW_CHOICES,
            "can_manage": ctx.is_super_admin or PARTNERS_MANAGE in ctx.permissions}


@router.get("/overview")
def overview(ctx: TenantContext = Depends(VIEW)):
    return {"success": True, "overview": ST.admin_overview(S.db_or_503())}


# ── applications ─────────────────────────────────────────────────────────────

@router.get("/applications")
def list_applications(status: Optional[str] = None, q: Optional[str] = None,
                      partner_type: Optional[str] = None,
                      page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
                      sort: str = "-created_at", ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    query: Dict[str, Any] = _search(q, ["name", "email", "company", "phone", "country"])
    if status:
        query["status"] = status
    if partner_type:
        query["partner_type"] = partner_type
    res = _page(db[K.APPLICATIONS], query, page=page, limit=limit, sort=sort,
                allowed=("created_at", "name", "company", "status", "updated_at"),
                out=lambda a: S.public_application(a))
    res["counts"] = {s: db[K.APPLICATIONS].count_documents({"status": s}) for s in K.APPLICATION_STATUSES}
    return {"success": True, **res}


@router.get("/applications/{app_id}")
def get_application(app_id: str, ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    a = db[K.APPLICATIONS].find_one({"_id": S.oid(app_id)}) if S.oid(app_id) else None
    if not a:
        raise HTTPException(status_code=404, detail="Application not found")
    out = S.public_application(a, admin=ctx.is_super_admin or PARTNERS_MANAGE in ctx.permissions)
    user = db.users.find_one({"_id": S.oid(a["user_id"])}, {"email": 1, "status": 1,
                                                          "default_organization_id": 1, "created_at": 1})
    out["account"] = S.clean(user) if user else None
    out["is_existing_customer"] = bool(user and db.organization_members.find_one(
        {"user_id": a["user_id"], "status": "active"}))
    out["previous_applications"] = db[K.APPLICATIONS].count_documents(
        {"user_id": a["user_id"], "_id": {"$ne": a["_id"]}})
    return {"success": True, "application": out}


class ApproveBody(BaseModel):
    partner_type: Optional[str] = None
    tier_id: Optional[str] = None
    permissions: Optional[List[str]] = None
    note: Optional[str] = None


class ReasonBody(BaseModel):
    reason: str = ""


class NoteBody(BaseModel):
    note: str = ""


@router.post("/applications/{app_id}/approve")
def approve_application(app_id: str, body: ApproveBody, request: Request,
                        ctx: TenantContext = Depends(MANAGE)):
    partner = S.approve_application(app_id, actor=_actor(ctx), partner_type=body.partner_type,
                                    tier_id=body.tier_id, permissions=body.permissions,
                                    note=body.note, ip=_ip(request))
    return {"success": True, "partner": partner}


@router.post("/applications/{app_id}/reject")
def reject_application(app_id: str, body: ReasonBody, request: Request,
                       ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "application": S.reject_application(app_id, actor=_actor(ctx),
                                                                 reason=body.reason.strip()[:1000],
                                                                 ip=_ip(request))}


@router.post("/applications/{app_id}/request-changes")
def request_changes(app_id: str, body: NoteBody, request: Request, ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "application": S.request_application_changes(app_id, actor=_actor(ctx),
                                                                           note=body.note, ip=_ip(request))}


# ── commissions / payouts (global queues; declared before /{partner_id}) ─────

@router.get("/commissions")
def all_commissions(status: Optional[str] = None, partner_id: Optional[str] = None,
                    manual: Optional[bool] = None, page: int = Query(1, ge=1),
                    limit: int = Query(25, ge=1, le=200), sort: str = "-created_at",
                    ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    if partner_id:
        query["partner_id"] = partner_id
    if manual is not None:
        query["requires_manual_approval"] = manual
    names = {}

    def out(c):
        row = ST.commission_out(c)
        pid = c["partner_id"]
        if pid not in names:
            p = db[K.PARTNERS].find_one({"_id": S.oid(pid)}, {"name": 1, "company": 1})
            names[pid] = (p or {}).get("company") or (p or {}).get("name")
        row.update({"partner_id": pid, "partner_name": names[pid]})
        return row
    res = _page(db[K.COMMISSIONS], query, page=page, limit=limit, sort=sort,
                allowed=("created_at", "amount", "status"), out=out)
    res["counts"] = {s: db[K.COMMISSIONS].count_documents({"status": s}) for s in K.COMMISSION_STATUSES}
    return {"success": True, **res}


@router.post("/commissions/{commission_id}/approve")
def approve_commission(commission_id: str, request: Request, ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "commission": C.approve_commission(commission_id, actor=_actor(ctx),
                                                                ip=_ip(request))}


@router.post("/commissions/{commission_id}/payable")
def payable_commission(commission_id: str, request: Request, ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "commission": C.make_payable(commission_id, actor=_actor(ctx), ip=_ip(request))}


class ReverseBody(BaseModel):
    reason: str = ""
    amount: Optional[float] = None   # partial reversal; empty = everything still reversible


@router.post("/commissions/{commission_id}/reverse")
def reverse_commission(commission_id: str, body: ReverseBody, request: Request,
                       ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "commission": C.reverse_commission(commission_id, actor=_actor(ctx),
                                                                reason=body.reason, amount=body.amount,
                                                                ip=_ip(request))}


@router.post("/commissions/run-lifecycle")
def run_commission_lifecycle(request: Request, ctx: TenantContext = Depends(MANAGE)):
    """Run the qualification / approval / payout-schedule pass now."""
    n = C.release_matured()
    S.paudit("partner.commission.lifecycle_run", None, actor=_actor(ctx), ip=_ip(request),
             details={"qualified": n}, resource_type="partner_commission", resource_id=None)
    return {"success": True, "qualified": n}


@router.get("/reversals")
def reversals(page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
              source: Optional[str] = None, ctx: TenantContext = Depends(VIEW)):
    """Reversed / partially reversed commissions and clawbacks (refunds,
    chargebacks, cancellations, manual)."""
    db = S.db_or_503()
    query: Dict[str, Any] = {"$or": [{"reversed_amount": {"$gt": 0}}, {"kind": "clawback"},
                                     {"status": K.C_REVERSED}]}
    if source == "chargeback":
        query["chargeback"] = True
    elif source:
        query["reversal_source"] = source
    names: Dict[str, Any] = {}

    def out(c):
        row = ST.commission_out(c)
        pid = c["partner_id"]
        if pid not in names:
            p = db[K.PARTNERS].find_one({"_id": S.oid(pid)}, {"name": 1, "company": 1})
            names[pid] = (p or {}).get("company") or (p or {}).get("name")
        row.update({"partner_id": pid, "partner_name": names[pid], "reversal_source": c.get("reversal_source"),
                    "chargeback": c.get("chargeback"), "clawback_of": c.get("clawback_of")})
        return row
    return {"success": True, **_page(db[K.COMMISSIONS], query, page=page, limit=limit, sort="-updated_at",
                                     allowed=("updated_at",), out=out)}


@router.get("/wallets")
def wallets(q: Optional[str] = None, page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
            ctx: TenantContext = Depends(VIEW)):
    """Every partner's balances (derived from commissions) per currency."""
    db = S.db_or_503()
    query: Dict[str, Any] = _search(q, ["name", "email", "company", "referral_code"])

    def out(p):
        return {"id": str(p["_id"]), "name": p.get("company") or p.get("name"), "email": p.get("email"),
                "status": p.get("status"), "partner_type": p.get("partner_type"),
                "has_payout_info": bool(p.get("payout_info")),
                "balances": C.compute_balances(str(p["_id"]), db)}
    return {"success": True, **_page(db[K.PARTNERS], query, page=page, limit=limit, sort="name",
                                     allowed=("name",), out=out)}


@router.get("/referrals")
def all_referrals(partner_id: Optional[str] = None, stage: Optional[str] = None,
                  status: Optional[str] = None, suspicious: Optional[bool] = None, q: Optional[str] = None,
                  page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
                  ctx: TenantContext = Depends(VIEW)):
    """Customer / referral relationships across all partners."""
    db = S.db_or_503()
    query: Dict[str, Any] = _search(q, ["company", "email"])
    for k, v in (("partner_id", partner_id), ("stage", stage), ("status", status)):
        if v:
            query[k] = v
    if suspicious is not None:
        query["suspicious"] = suspicious
    names: Dict[str, Any] = {}

    def out(r):
        row = S.clean({k: v for k, v in r.items() if k != "payments_seen"})
        pid = r["partner_id"]
        if pid not in names:
            p = db[K.PARTNERS].find_one({"_id": S.oid(pid)}, {"name": 1, "company": 1})
            names[pid] = (p or {}).get("company") or (p or {}).get("name")
        row["partner_name"] = names[pid]
        org = db.organizations.find_one({"_id": S.oid(r["organization_id"])}, {"status": 1})
        row["organization_status"] = (org or {}).get("status")
        return row
    res = _page(db[K.REFERRALS], query, page=page, limit=limit, sort="-signed_up_at",
                allowed=("signed_up_at",), out=out)
    res["counts"] = {s_: db[K.REFERRALS].count_documents({"stage": s_}) for s_ in K.STAGES}
    return {"success": True, **res}


class ReassignBody(BaseModel):
    partner_id: str
    reason: str = ""


@router.post("/referrals/{referral_id}/reassign")
def reassign_referral(referral_id: str, body: ReassignBody, request: Request, ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "referral": R.reassign_referral(referral_id, body.partner_id, actor=_actor(ctx),
                                                             reason=body.reason, ip=_ip(request))}


class ReviewBody(BaseModel):
    decision: str            # clear | invalidate
    reason: str = ""


@router.post("/referrals/{referral_id}/review")
def review_referral(referral_id: str, body: ReviewBody, request: Request, ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "referral": R.review_referral(referral_id, body.decision, actor=_actor(ctx),
                                                           reason=body.reason, ip=_ip(request))}


@router.get("/fraud")
def fraud_review(ctx: TenantContext = Depends(VIEW)):
    """Review queue: flagged referrals, commissions held for review,
    chargebacks, click floods and blocked self-referrals."""
    db = S.db_or_503()

    def pname(pid):
        p = db[K.PARTNERS].find_one({"_id": S.oid(pid)}, {"name": 1, "company": 1})
        return (p or {}).get("company") or (p or {}).get("name")
    flagged = []
    for r_ in db[K.REFERRALS].find({"suspicious": True, "status": "active"}).sort("signed_up_at", -1).limit(100):
        row = ST.referral_out(r_)
        row.update({"partner_id": r_["partner_id"], "partner_name": pname(r_["partner_id"]),
                    "self_referral_flag": r_.get("self_referral_flag")})
        flagged.append(row)
    held = []
    for c in db[K.COMMISSIONS].find({"requires_manual_approval": True,
                                     "status": {"$in": [K.C_PENDING, K.C_QUALIFIED]}}).sort("created_at", -1).limit(100):
        row = ST.commission_out(c)
        row.update({"partner_id": c["partner_id"], "partner_name": pname(c["partner_id"])})
        held.append(row)
    floods = []
    for row in db[K.CLICKS].aggregate([{"$match": {"suspicious": True}},
                                       {"$group": {"_id": "$partner_id", "n": {"$sum": 1},
                                                   "last": {"$max": "$created_at"}}},
                                       {"$sort": {"n": -1}}, {"$limit": 50}]):
        floods.append({"partner_id": row["_id"], "partner_name": pname(row["_id"]), "clicks": row["n"],
                       "last": S.clean(row["last"])})
    blocked = [S.clean({k: v for k, v in e.items() if k in ("_id", "at", "created_at", "details", "actor_email",
                                                           "organization_id")})
               for e in db.security_events.find({"type": {"$in": ["partner_self_referral", "cross_partner_access"]}})
               .sort("_id", -1).limit(50)]
    chargebacks = db[K.COMMISSIONS].count_documents({"chargeback": True})
    open_flags = db["partner_fraud_flags"].count_documents({"status": "open"})
    return {"success": True, "flagged_referrals": flagged, "held_commissions": held, "click_floods": floods,
            "security_events": blocked, "chargebacks": chargebacks, "open_flags": open_flags}


@router.get("/pricing")
def pricing(ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    return {"success": True, "items": [S.clean(x) for x in db[K.PRICING].find().sort("created_at", -1)]}


@router.post("/pricing")
def create_pricing(body: Dict[str, Any], request: Request, ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "rule": CP.save_pricing_rule(body, actor=_actor(ctx), ip=_ip(request))}


@router.patch("/pricing/{rule_id}")
def update_pricing(rule_id: str, body: Dict[str, Any], request: Request, ctx: TenantContext = Depends(MANAGE)):
    db = S.db_or_503()
    cur = db[K.PRICING].find_one({"_id": S.oid(rule_id)}) if S.oid(rule_id) else None
    if not cur:
        raise HTTPException(status_code=404, detail="Pricing rule not found")
    merged = {**{k: v for k, v in cur.items() if k != "_id"}, **body}
    return {"success": True, "rule": CP.save_pricing_rule(merged, actor=_actor(ctx), rule_id=rule_id,
                                                          ip=_ip(request))}


@router.post("/tiers/evaluate")
def evaluate_tiers(request: Request, ctx: TenantContext = Depends(MANAGE)):
    """Apply tier requirements to every (unlocked) partner now."""
    from app.partners.tiers import evaluate_tiers as run
    changes = run(force=True, actor=ctx.email)
    S.paudit("partner.tier.evaluated", None, actor=_actor(ctx), ip=_ip(request),
             details={"changes": len(changes)}, resource_type="partner_tier", resource_id=None)
    return {"success": True, "changes": changes}


@router.get("/payouts")
def all_payouts(status: Optional[str] = None, partner_id: Optional[str] = None,
                page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
                sort: str = "-created_at", ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    if partner_id:
        query["partner_id"] = partner_id
    admin = ctx.is_super_admin or PARTNERS_MANAGE in ctx.permissions

    def out(p):
        row = C.payout_out(p, admin=admin)
        partner = db[K.PARTNERS].find_one({"_id": S.oid(p["partner_id"])}, {"name": 1, "company": 1, "email": 1})
        row["partner_name"] = (partner or {}).get("company") or (partner or {}).get("name")
        row["partner_email"] = (partner or {}).get("email")
        return row
    res = _page(db[K.PAYOUTS], query, page=page, limit=limit, sort=sort,
                allowed=("created_at", "amount", "status"), out=out)
    res["counts"] = {s: db[K.PAYOUTS].count_documents({"status": s}) for s in K.PAYOUT_STATUSES}
    return {"success": True, **res}


class PayoutActionBody(BaseModel):
    reason: str = ""
    reference: str = ""


@router.post("/payouts/{payout_id}/{action}")
def payout_action(payout_id: str, action: str, body: PayoutActionBody, request: Request,
                  ctx: TenantContext = Depends(MANAGE)):
    if action not in K.PAYOUT_ACTIONS:
        raise HTTPException(status_code=404, detail="Unknown action")
    return {"success": True, "payout": C.admin_payout_action(payout_id, action, actor=_actor(ctx),
                                                             reason=body.reason, reference=body.reference,
                                                             ip=_ip(request))}


# ── program configuration ────────────────────────────────────────────────────

@router.get("/settings")
def get_settings(ctx: TenantContext = Depends(VIEW)):
    return {"success": True, "settings": S.get_program_settings(S.db_or_503())}


@router.put("/settings")
def put_settings(body: Dict[str, Any], request: Request, ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "settings": S.update_program_settings(body, actor=_actor(ctx), ip=_ip(request))}


@router.get("/tiers")
def tiers(ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    items = S.list_tiers(db)
    for t in items:
        t["partners"] = db[K.PARTNERS].count_documents({"tier_id": t["id"]})
    return {"success": True, "items": items}


@router.post("/tiers")
def create_tier(body: Dict[str, Any], request: Request, ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "tier": S.save_tier(body, actor=_actor(ctx), ip=_ip(request))}


@router.patch("/tiers/{tier_id}")
def update_tier(tier_id: str, body: Dict[str, Any], request: Request, ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "tier": S.save_tier(body, tier_id=tier_id, actor=_actor(ctx), ip=_ip(request))}


@router.get("/commission-rules")
def rules(ctx: TenantContext = Depends(VIEW)):
    return {"success": True, "items": S.list_rules()}


@router.post("/commission-rules")
def create_rule(body: Dict[str, Any], request: Request, ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "rule": S.save_rule(body, actor=_actor(ctx), ip=_ip(request))}


@router.patch("/commission-rules/{rule_id}")
def update_rule(rule_id: str, body: Dict[str, Any], request: Request, ctx: TenantContext = Depends(MANAGE)):
    db = S.db_or_503()
    current = db[K.RULES].find_one({"_id": S.oid(rule_id)}) if S.oid(rule_id) else None
    if not current:
        raise HTTPException(status_code=404, detail="Commission rule not found")
    merged = {**{k: v for k, v in current.items() if k != "_id"}, **body}
    return {"success": True, "rule": S.save_rule(merged, rule_id=rule_id, actor=_actor(ctx), ip=_ip(request))}


@router.get("/coupons")
def coupons(partner_id: Optional[str] = None, status: Optional[str] = None, q: Optional[str] = None,
            page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
            ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    query: Dict[str, Any] = _search(q.upper() if q else None, ["code"])
    if partner_id:
        query["partner_id"] = partner_id
    if status:
        query["status"] = status
    return {"success": True, **_page(db[K.COUPONS], query, page=page, limit=limit, sort="-created_at",
                                     allowed=("created_at",), out=S.clean)}


@router.post("/coupons")
def create_coupon(body: Dict[str, Any], request: Request, ctx: TenantContext = Depends(MANAGE)):
    pid = str(body.get("partner_id") or "")
    return {"success": True, "coupon": CP.save_coupon(body, partner_id=pid, actor=_actor(ctx), ip=_ip(request))}


@router.patch("/coupons/{coupon_id}")
def update_coupon(coupon_id: str, body: Dict[str, Any], request: Request, ctx: TenantContext = Depends(MANAGE)):
    db = S.db_or_503()
    c = db[K.COUPONS].find_one({"_id": S.oid(coupon_id)}) if S.oid(coupon_id) else None
    if not c:
        raise HTTPException(status_code=404, detail="Coupon not found")
    merged = {**{k: v for k, v in c.items() if k not in ("_id", "code")}, **body}
    return {"success": True, "coupon": CP.save_coupon(merged, partner_id=c["partner_id"], actor=_actor(ctx),
                                                      coupon_id=coupon_id, ip=_ip(request))}


@router.get("/assets")
def assets(category: Optional[str] = None, ctx: TenantContext = Depends(VIEW)):
    from app.partners import marketing as M
    db = S.db_or_503()
    items = [M.asset_out(a) for a in db[K.ASSETS].find().sort("created_at", -1)]
    if category:
        items = [i for i in items if i["category"] == category]
    return {"success": True, "items": items, "categories": M.CATEGORIES}


@router.post("/assets")
def create_asset(body: Dict[str, Any], request: Request, ctx: TenantContext = Depends(MANAGE)):
    from app.partners import marketing as M
    db = S.db_or_503()
    doc = {**M.clean_asset(body, db), "download_count": 0, "created_at": utcnow(), "created_by": ctx.email}
    aid = str(db[K.ASSETS].insert_one(doc).inserted_id)
    S.paudit("partner.asset.created", None, actor=_actor(ctx), ip=_ip(request),
             details={"title": doc["title"], "category": doc["category"]}, resource_type="partner_asset",
             resource_id=aid)
    return {"success": True, "asset": M.asset_out(db[K.ASSETS].find_one({"_id": S.oid(aid)}))}


@router.patch("/assets/{asset_id}")
def update_asset(asset_id: str, body: Dict[str, Any], request: Request, ctx: TenantContext = Depends(MANAGE)):
    from app.partners import marketing as M
    db = S.db_or_503()
    a = db[K.ASSETS].find_one({"_id": S.oid(asset_id)}) if S.oid(asset_id) else None
    if not a:
        raise HTTPException(status_code=404, detail="Asset not found")
    doc = M.clean_asset({**a, **body}, db)
    if body.get("remove_file"):
        doc.update({"storage_key": None, "file_url": None, "file_name": None, "file_type": None, "file_size": None})
    db[K.ASSETS].update_one({"_id": a["_id"]}, {"$set": doc})
    S.paudit("partner.asset.updated", None, actor=_actor(ctx), ip=_ip(request),
             details={"title": doc["title"], "status": doc["status"], "file_removed": bool(body.get("remove_file"))},
             resource_type="partner_asset", resource_id=asset_id)
    return {"success": True, "asset": M.asset_out(db[K.ASSETS].find_one({"_id": a["_id"]}))}


@router.post("/assets/{asset_id}/file")
async def upload_asset_file(asset_id: str, request: Request, file: UploadFile = File(...),
                            ctx: TenantContext = Depends(MANAGE)):
    """Upload / replace the asset's file (images, PDF, MP4/WebM video, ZIP).
    Type is verified from the bytes; stored privately with the local backend."""
    import asyncio
    from app.partners import marketing as M
    db = S.db_or_503()
    a = db[K.ASSETS].find_one({"_id": S.oid(asset_id)}) if S.oid(asset_id) else None
    if not a:
        raise HTTPException(status_code=404, detail="Asset not found")
    content = await file.read(M.MAX_BYTES + 1)
    mime = (file.content_type or "").split(";")[0].strip().lower()
    ext = M.sniff(content, mime)
    stored = await M.store_file(content, ext)
    upd = {**stored, "file_name": S.text(file.filename, 200) or ("asset" + ext), "file_type": mime,
           "file_size": len(content), "updated_at": utcnow()}
    await asyncio.to_thread(db[K.ASSETS].update_one, {"_id": a["_id"]}, {"$set": upd})
    S.paudit("partner.asset.file_uploaded", None, actor=_actor(ctx), ip=_ip(request),
             details={"title": a.get("title"), "type": mime, "size": len(content)},
             resource_type="partner_asset", resource_id=asset_id)
    return {"success": True, "asset": M.asset_out(db[K.ASSETS].find_one({"_id": a["_id"]}))}


@router.get("/assets/{asset_id}/file")
def admin_asset_file(asset_id: str, ctx: TenantContext = Depends(VIEW)):
    return _serve_asset_file(asset_id)


def _serve_asset_file(asset_id: str, *, download: bool = False):
    from fastapi.responses import FileResponse, RedirectResponse
    from app.partners import marketing as M
    db = S.db_or_503()
    a = db[K.ASSETS].find_one({"_id": S.oid(asset_id)}) if S.oid(asset_id) else None
    if not a:
        raise HTTPException(status_code=404, detail="Asset not found")
    if a.get("file_url"):
        return RedirectResponse(a["file_url"], status_code=302)
    path = M.local_path(a)
    if not path:
        raise HTTPException(status_code=404, detail="This asset has no file")
    inline = not download and (a.get("file_type") or "").startswith(("image/", "video/", "application/pdf"))
    resp = FileResponse(path, media_type=a.get("file_type") or "application/octet-stream",
                        filename=a.get("file_name") or os.path.basename(path),
                        content_disposition_type="inline" if inline else "attachment")
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# ── analytics / reconciliation / fraud flags ─────────────────────────────────

@router.get("/analytics")
def program_analytics(partner_id: Optional[str] = None, campaign_id: Optional[str] = None,
                      date_from: Optional[str] = Query(None, alias="from"),
                      date_to: Optional[str] = Query(None, alias="to"), unit: str = "day",
                      ctx: TenantContext = Depends(VIEW)):
    """Whole program, or one partner (and/or one campaign)."""
    db = S.db_or_503()
    start, end = ST.parse_range(date_from, date_to)
    ids = [partner_id] if partner_id else None
    return {"success": True, "analytics": ST.analytics(db, None, start, end, unit, campaign_id=campaign_id,
                                                       partner_ids=ids)}


@router.get("/analytics.csv")
def program_analytics_csv(request: Request, partner_id: Optional[str] = None, campaign_id: Optional[str] = None,
                          date_from: Optional[str] = Query(None, alias="from"),
                          date_to: Optional[str] = Query(None, alias="to"), unit: str = "day",
                          ctx: TenantContext = Depends(VIEW)):
    from fastapi.responses import Response
    db = S.db_or_503()
    start, end = ST.parse_range(date_from, date_to)
    data = ST.analytics(db, None, start, end, unit, campaign_id=campaign_id,
                        partner_ids=[partner_id] if partner_id else None)
    S.paudit("partner.analytics.exported", partner_id, actor=_actor(ctx), ip=_ip(request),
             details={"campaign_id": campaign_id, "from": start.isoformat(), "to": end.isoformat()},
             resource_type="partner_analytics", resource_id=partner_id)
    return Response(ST.analytics_csv(data), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="partner-analytics-{start:%Y%m%d}-{end:%Y%m%d}.csv"'})


@router.get("/leaderboard")
def leaderboard(date_from: Optional[str] = Query(None, alias="from"), date_to: Optional[str] = Query(None, alias="to"),
                partner_type: Optional[str] = None, page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=100),
                ctx: TenantContext = Depends(VIEW)):
    start, end = ST.parse_range(date_from, date_to)
    return {"success": True, **ST.partner_leaderboard(S.db_or_503(), start, end, page=page, limit=limit,
                                                      partner_type=partner_type)}


@router.get("/reconciliation")
def reconciliation(partner_id: Optional[str] = None, ctx: TenantContext = Depends(VIEW)):
    """Read-only cross-check of commissions / payouts / wallets / referral
    revenue against invoices and payments."""
    return {"success": True, "reconciliation": ST.reconciliation(S.db_or_503(), partner_id)}


# ── partner activity log + sessions (Super Admin oversight) ─────────────────

def _activity_query(partner_id, kind, q, email, status, via_api_key, success, date_from, date_to):
    from app.partners.stats import parse_range
    query: Dict[str, Any] = {}
    if partner_id:
        query["partner_id"] = partner_id
    if kind:
        query["kind"] = kind
    if email:
        query["email"] = email.strip().lower()
    if status:
        query["status"] = int(status)
    if via_api_key is not None:
        query["via_api_key"] = via_api_key
    if success is not None:
        query["success"] = success
    if q:
        query["action"] = {"$regex": re.escape(q.strip()[:80]), "$options": "i"}
    if date_from or date_to:
        start, end = parse_range(date_from, date_to, default_days=3650)
        query["at"] = {"$gte": start, "$lte": end}
    return query


@router.get("/activity")
def activity(partner_id: Optional[str] = None, kind: Optional[str] = None, q: Optional[str] = None,
             email: Optional[str] = None, status: Optional[int] = None, via_api_key: Optional[bool] = None,
             success: Optional[bool] = None, date_from: Optional[str] = Query(None, alias="from"),
             date_to: Optional[str] = Query(None, alias="to"), page: int = Query(1, ge=1),
             limit: int = Query(50, ge=1, le=200), ctx: TenantContext = Depends(VIEW)):
    """Everything partners do: sign-ins, every portal / API request, every
    audited action (theirs and the Super Admin's on them)."""
    from app.partners.activity import COLL
    db = S.db_or_503()
    query = _activity_query(partner_id, kind, q, email, status, via_api_key, success, date_from, date_to)
    names: Dict[str, Any] = {}

    def out(a):
        row = S.clean({k: v for k, v in a.items() if k != "expires_at"})
        pid = a.get("partner_id")
        if pid and pid not in names:
            p = db[K.PARTNERS].find_one({"_id": S.oid(pid)}, {"name": 1, "company": 1})
            names[pid] = (p or {}).get("company") or (p or {}).get("name")
        row["partner_name"] = names.get(pid)
        return row
    res = _page(db[COLL], query, page=page, limit=limit, sort="-at", allowed=("at",), out=out)
    since = utcnow() - timedelta(hours=24)
    res["summary_24h"] = {k: db[COLL].count_documents({"kind": k, "at": {"$gte": since}}) for k in ("auth", "request", "action")}
    res["summary_24h"]["failed_logins"] = db[COLL].count_documents({"kind": "auth", "success": False, "at": {"$gte": since}})
    res["summary_24h"]["denied"] = db[COLL].count_documents({"kind": "request", "status": {"$in": [401, 403]},
                                                             "at": {"$gte": since}})
    res["summary_24h"]["active_partners"] = len(db[COLL].distinct("partner_id", {"at": {"$gte": since},
                                                                                 "partner_id": {"$ne": None}}))
    return {"success": True, **res}


@router.get("/activity.csv")
def activity_csv(request: Request, partner_id: Optional[str] = None, kind: Optional[str] = None,
                 q: Optional[str] = None, email: Optional[str] = None, status: Optional[int] = None,
                 via_api_key: Optional[bool] = None, success: Optional[bool] = None,
                 date_from: Optional[str] = Query(None, alias="from"), date_to: Optional[str] = Query(None, alias="to"),
                 ctx: TenantContext = Depends(VIEW)):
    import csv
    import io
    from fastapi.responses import Response
    from app.partners.activity import COLL
    db = S.db_or_503()
    query = _activity_query(partner_id, kind, q, email, status, via_api_key, success, date_from, date_to)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["at", "kind", "action", "partner_id", "email", "actor", "method", "path", "status", "api_key",
                "success", "ip_hash", "duration_ms", "details"])

    def safe(v):
        s_ = "" if v is None else str(v)
        return "'" + s_ if s_[:1] in ("=", "+", "-", "@", chr(9), chr(13)) else s_
    for a in db[COLL].find(query).sort("at", -1).limit(50000):
        w.writerow([S.clean(a.get("at")), a.get("kind"), safe(a.get("action")), a.get("partner_id"), a.get("email"),
                    safe(a.get("actor")), a.get("method"), safe(a.get("path")), a.get("status"),
                    a.get("api_key_id") or "", a.get("success"), a.get("ip_hash"), a.get("duration_ms"),
                    safe(json.dumps(S.clean(a.get("details") or {}))[:500])])
    S.paudit("partner.activity.exported", partner_id, actor=_actor(ctx), ip=_ip(request),
             details={"filters": {k: v for k, v in (("kind", kind), ("q", q), ("email", email)) if v}},
             resource_type="partner_activity", resource_id=partner_id)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="partner-activity.csv"'})


@router.get("/sessions")
def partner_sessions(partner_id: Optional[str] = None, active: bool = True, page: int = Query(1, ge=1),
                     limit: int = Query(50, ge=1, le=200), ctx: TenantContext = Depends(VIEW)):
    """Partner Portal sessions (the existing session store, scope=partner)."""
    db = S.db_or_503()
    query: Dict[str, Any] = {"scope": "partner"}
    if active:
        query["revoked_at"] = None
        query["expires_at"] = {"$gt": utcnow()}
    if partner_id:
        p = S.load_partner(partner_id, db)
        query["user_id"] = str(p["user_id"])
    users: Dict[str, Any] = {}

    def out(s_):
        uid = s_.get("user_id")
        if uid not in users:
            p = db[K.PARTNERS].find_one({"user_id": uid}, {"name": 1, "company": 1})
            users[uid] = {"partner_id": str(p["_id"]) if p else None,
                          "partner_name": ((p or {}).get("company") or (p or {}).get("name"))}
        return {"id": (s_.get("session_id") or "")[:12], "email": s_.get("email"), **users[uid],
                "user_agent": s_.get("user_agent"), "ip_hash": s_.get("ip_hash"),
                "created_at": S.clean(s_.get("created_at")), "last_active_at": S.clean(s_.get("last_active_at")),
                "expires_at": S.clean(s_.get("expires_at")), "revoked_at": S.clean(s_.get("revoked_at")),
                "revoked_by": s_.get("revoked_by")}
    return {"success": True, **_page(db.user_sessions, query, page=page, limit=limit, sort="-last_active_at",
                                     allowed=("last_active_at", "created_at"), out=out)}


class RevokeBody(BaseModel):
    reason: str = ""


@router.post("/sessions/{short_id}/revoke")
def revoke_partner_session(short_id: str, body: RevokeBody, request: Request, ctx: TenantContext = Depends(MANAGE)):
    db = S.db_or_503()
    if len(short_id) < 8:
        raise HTTPException(status_code=422, detail="Invalid session id")
    s_ = db.user_sessions.find_one({"scope": "partner", "revoked_at": None,
                                    "session_id": {"$regex": "^" + re.escape(short_id)}})
    if not s_:
        raise HTTPException(status_code=404, detail="Session not found")
    db.user_sessions.update_one({"_id": s_["_id"]}, {"$set": {"revoked_at": utcnow(),
                                                             "revoked_by": f"super_admin:{ctx.email}"}})
    p = db[K.PARTNERS].find_one({"user_id": s_.get("user_id")}, {"_id": 1})
    S.paudit("partner.session.revoked", str(p["_id"]) if p else None, actor=_actor(ctx), ip=_ip(request),
             details={"session": short_id, "email": s_.get("email"), "reason": body.reason},
             resource_type="session", resource_id=short_id)
    return {"success": True}


@router.post("/{partner_id}/sessions/revoke")
def revoke_all_partner_sessions(partner_id: str, body: RevokeBody, request: Request,
                                ctx: TenantContext = Depends(MANAGE)):
    """Force sign-out everywhere (the partner may sign in again unless suspended)."""
    db = S.db_or_503()
    p = S.load_partner(partner_id, db)
    res = db.user_sessions.update_many({"user_id": str(p["user_id"]), "scope": "partner", "revoked_at": None},
                                       {"$set": {"revoked_at": utcnow(), "revoked_by": f"super_admin:{ctx.email}"}})
    S.paudit("partner.session.revoked_all", partner_id, actor=_actor(ctx), ip=_ip(request),
             details={"sessions": res.modified_count, "reason": body.reason})
    S.notify_partner(p, "partner_security", "You were signed out by LeadAI",
                     body.reason or "All your Partner Portal sessions were ended by the LeadAI team.",
                     severity="warning", email=True)
    return {"success": True, "revoked": res.modified_count}


class ImpersonateBody(BaseModel):
    reason: str = ""


@router.post("/{partner_id}/impersonate")
def impersonate_partner(partner_id: str, body: ImpersonateBody, request: Request, response: Response,
                        ctx: TenantContext = Depends(require_platform_role("super_admin"))):
    """Super Admin opens the Partner Portal AS this partner, with the partner's
    full rights. Time-limited, reason required, every request and action is
    recorded under the Super Admin's name. Exit: /api/super-admin/impersonate/exit."""
    from app.auth.service import create_tracked_session, set_session_cookie
    from app.auth.tenant import impersonation_expiry
    reason = (body.reason or "").strip()
    if len(reason) < 5:
        raise HTTPException(status_code=422, detail="A reason (at least 5 characters) is required")
    db = S.db_or_503()
    p = S.load_partner(partner_id, db)
    user = db.users.find_one({"_id": S.oid(p["user_id"])}) or {}
    claims = {"user_id": str(p["user_id"]), "email": p["email"], "name": user.get("name") or p.get("name"),
              "role": "partner", "scope": "partner", "partner_id": partner_id,
              "organization_id": "", "organization_name": "", "organization_slug": "", "org_role": "",
              "is_platform_admin": False, "platform_role": None,
              "impersonated_by": ctx.email, "impersonator_user_id": ctx.user_id,
              "impersonation_reason": reason[:300], "impersonation_expires_at": impersonation_expiry()}
    meta = request_meta(request)
    tracked = create_tracked_session(claims, ip=meta.get("ip") or "unknown", user_agent=meta.get("user_agent") or "unknown",
                                     impersonated_by=ctx.email, impersonation_reason=reason[:300])
    set_session_cookie(response, tracked)
    S.paudit("partner.impersonation.start", partner_id, actor=_actor(ctx), ip=_ip(request),
             details={"reason": reason[:300], "expires_at": claims["impersonation_expires_at"]})
    return {"success": True, "redirect": "/partner#/dashboard", "expires_at": claims["impersonation_expires_at"],
            "message": f"Now viewing the Partner Portal as {p.get('company') or p.get('name')}"}


# ── deals ───────────────────────────────────────────────────────────────────

@router.get("/deals")
def all_deals(status: Optional[str] = None, q: Optional[str] = None, partner_id: Optional[str] = None,
              conflict: Optional[bool] = None, page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
              ctx: TenantContext = Depends(VIEW)):
    from app.partners import sales as SL
    db = S.db_or_503()
    query = SL.list_query(status, q)
    if partner_id:
        query["partner_id"] = partner_id
    if conflict is not None:
        query["conflict"] = {"$ne": None} if conflict else None
    names: Dict[str, Any] = {}

    def out(d):
        row = SL.deal_out(d, admin=True)
        pid = d["partner_id"]
        if pid not in names:
            p = db[K.PARTNERS].find_one({"_id": S.oid(pid)}, {"name": 1, "company": 1})
            names[pid] = (p or {}).get("company") or (p or {}).get("name")
        row["partner_name"] = names[pid]
        row["referral_stage"] = SL.deal_stage(db, d)
        return row
    res = _page(db[SL.DEALS], query, page=page, limit=limit, sort="-created_at", allowed=("created_at",), out=out)
    res["counts"] = {s_: db[SL.DEALS].count_documents({"status": s_}) for s_ in SL.DEAL_STATUSES}
    pipeline_value: Dict[str, float] = {}
    for d in db[SL.DEALS].find({"status": {"$in": ["registered", "approved"]}}, {"expected_value": 1, "currency": 1}):
        cur = d.get("currency") or "USD"
        pipeline_value[cur] = round(pipeline_value.get(cur, 0.0) + float(d.get("expected_value") or 0), 2)
    res["open_pipeline_value"] = pipeline_value
    return {"success": True, **res}


class DealDecision(BaseModel):
    note: str = ""


@router.post("/deals/{deal_id}/{decision}")
def decide_deal(deal_id: str, decision: str, body: DealDecision, request: Request,
                ctx: TenantContext = Depends(MANAGE)):
    from app.partners.sales import review_deal
    if decision not in ("approve", "reject", "won", "lost"):
        raise HTTPException(status_code=404, detail="Unknown action")
    return {"success": True, "deal": review_deal(deal_id, decision, actor=_actor(ctx), note=body.note,
                                                 ip=_ip(request))}


# ── tasks: work assigned to partners ─────────────────────────────────────────

@router.get("/tasks")
def all_tasks(status: Optional[str] = None, q: Optional[str] = None, partner_id: Optional[str] = None,
              overdue: Optional[bool] = None, page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
              ctx: TenantContext = Depends(VIEW)):
    from app.partners import tasks as T
    db = S.db_or_503()
    query = T.list_query(status, q, overdue)
    base: Dict[str, Any] = {}
    if partner_id:
        query["partner_id"] = base["partner_id"] = partner_id
    names: Dict[str, Any] = {}

    def out(t):
        row = T.task_out(t)
        pid = t["partner_id"]
        if pid not in names:
            p = db[K.PARTNERS].find_one({"_id": S.oid(pid)}, {"name": 1, "company": 1})
            names[pid] = (p or {}).get("company") or (p or {}).get("name")
        row["partner_name"] = names[pid]
        return row
    res = _page(db[T.TASKS], query, page=page, limit=limit, sort="-created_at", allowed=("created_at",), out=out)
    res["counts"] = {s_: db[T.TASKS].count_documents({**base, "status": s_}) for s_ in T.TASK_STATUSES}
    res["counts"]["overdue"] = db[T.TASKS].count_documents({**base, **T.list_query(None, None, True)})
    return {"success": True, **res}


class TaskAssign(BaseModel):
    title: str = ""
    details: Optional[str] = None
    due_at: Optional[str] = None
    priority: str = "normal"
    section: Optional[str] = None
    audience: str = "partner"
    partner_id: Optional[str] = None
    email: bool = True


@router.post("/tasks")
def assign_task(body: TaskAssign, request: Request, ctx: TenantContext = Depends(MANAGE)):
    from app.partners.tasks import assign
    res = assign(body.model_dump(), actor=_actor(ctx), ip=_ip(request))
    return {"success": True, **res, "message": f"Task assigned to {res['created']} partner(s)"}


class TaskEdit(BaseModel):
    title: Optional[str] = None
    details: Optional[str] = None
    due_at: Optional[str] = None
    priority: Optional[str] = None
    section: Optional[str] = None


@router.patch("/tasks/{task_id}")
def edit_task(task_id: str, body: TaskEdit, request: Request, ctx: TenantContext = Depends(MANAGE)):
    from app.partners.tasks import admin_edit
    return {"success": True, "task": admin_edit(task_id, body.model_dump(exclude_unset=True), actor=_actor(ctx),
                                                ip=_ip(request))}


@router.post("/tasks/{task_id}/{decision}")
def decide_task(task_id: str, decision: str, body: DealDecision, request: Request,
                ctx: TenantContext = Depends(MANAGE)):
    from app.partners.tasks import review
    return {"success": True, "task": review(task_id, decision, actor=_actor(ctx), note=body.note, ip=_ip(request))}


@router.get("/fraud-flags")
def fraud_flags(status: Optional[str] = "open", flag_type: Optional[str] = Query(None, alias="type"),
                partner_id: Optional[str] = None, severity: Optional[str] = None,
                page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200), ctx: TenantContext = Depends(VIEW)):
    from app.partners.fraud import FLAG_TYPES, FLAGS
    db = S.db_or_503()
    query: Dict[str, Any] = {}
    for k, v in (("status", status), ("type", flag_type), ("partner_id", partner_id), ("severity", severity)):
        if v:
            query[k] = v
    names: Dict[str, Any] = {}

    def out(f):
        row = S.clean(f)
        pid = f.get("partner_id")
        if pid and pid not in names:
            p = db[K.PARTNERS].find_one({"_id": S.oid(pid)}, {"name": 1, "company": 1})
            names[pid] = (p or {}).get("company") or (p or {}).get("name")
        row["partner_name"] = names.get(pid)
        return row
    res = _page(db[FLAGS], query, page=page, limit=limit, sort="-created_at", allowed=("created_at",), out=out)
    res["counts"] = {s_: db[FLAGS].count_documents({"status": s_}) for s_ in ("open", "confirmed", "dismissed")}
    res["types"] = FLAG_TYPES
    return {"success": True, **res}


class FlagDecision(BaseModel):
    decision: str
    note: str = ""


@router.post("/fraud-flags/{flag_id}/resolve")
def resolve_fraud_flag(flag_id: str, body: FlagDecision, request: Request, ctx: TenantContext = Depends(MANAGE)):
    from app.partners.fraud import resolve_flag
    return {"success": True, "flag": resolve_flag(flag_id, body.decision, actor=_actor(ctx), note=body.note,
                                                  ip=_ip(request))}


class NotifyBody(BaseModel):
    title: str
    message: str
    partner_type: Optional[str] = None
    email: bool = False


@router.post("/notify")
def broadcast(body: NotifyBody, request: Request, ctx: TenantContext = Depends(MANAGE)):
    db = S.db_or_503()
    title, message = S.text(body.title, 200), S.text(body.message, 1000)
    if not title or not message:
        raise HTTPException(status_code=422, detail="Title and message are required")
    query: Dict[str, Any] = {"status": K.P_ACTIVE}
    if body.partner_type:
        query["partner_type"] = body.partner_type
    n = 0
    for p in db[K.PARTNERS].find(query):
        S.notify_partner(p, "partner_announcement", title, message, email=body.email)
        n += 1
    S.paudit("partner.notification.broadcast", None, actor=_actor(ctx), ip=_ip(request),
             details={"title": title, "recipients": n, "partner_type": body.partner_type},
             resource_type="partner_notification", resource_id=None)
    return {"success": True, "sent": n}


# ── partners ─────────────────────────────────────────────────────────────────

@router.get("")
def list_partners(status: Optional[str] = None, partner_type: Optional[str] = None,
                  q: Optional[str] = None, tier_id: Optional[str] = None,
                  page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
                  sort: str = "-created_at", ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    query: Dict[str, Any] = _search(q, ["name", "email", "company", "referral_code", "partner_code"])
    if status:
        query["status"] = status
    if partner_type:
        query["partner_type"] = partner_type
    if tier_id:
        query["tier_id"] = tier_id
    res = _page(db[K.PARTNERS], query, page=page, limit=limit, sort=sort,
                allowed=("created_at", "name", "company", "status"),
                out=lambda p: S.public_partner(p))
    stats = R.referral_stats_for(db, [p["id"] for p in res["items"]])
    for p in res["items"]:
        p["stats"] = stats.get(p["id"], {})
    res["counts"] = {"active": db[K.PARTNERS].count_documents({"status": K.P_ACTIVE}),
                     "suspended": db[K.PARTNERS].count_documents({"status": K.P_SUSPENDED}),
                     "affiliate": db[K.PARTNERS].count_documents({"partner_type": K.AFFILIATE}),
                     "reseller": db[K.PARTNERS].count_documents({"partner_type": K.RESELLER})}
    return {"success": True, **res}


@router.get("/{partner_id}")
def get_partner(partner_id: str, ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    p = S.load_partner(partner_id, db)
    out = S.public_partner(p, admin=ctx.is_super_admin or PARTNERS_MANAGE in ctx.permissions)
    out["stats"] = R.referral_stats_for(db, [partner_id]).get(partner_id, {})
    out["stats"]["clicks"] = db[K.CLICKS].count_documents({"partner_id": partner_id})
    out["balances"] = C.compute_balances(partner_id, db)
    out["rule"] = S.clean(S.resolve_rule(p, None, db))
    out["coupons"] = [S.clean(c) for c in db[K.COUPONS].find({"partner_id": partner_id})]
    from app.partners.activity import summary as activity_summary
    out["activity"] = S.clean(activity_summary(db, partner_id))
    out["active_sessions"] = db.user_sessions.count_documents({"user_id": str(p["user_id"]), "scope": "partner",
                                                               "revoked_at": None, "expires_at": {"$gt": utcnow()}})
    out["deals"] = {s_: db["partner_deals"].count_documents({"partner_id": partner_id, "status": s_})
                    for s_ in ("registered", "approved", "won", "lost")}
    out["tasks"] = {s_: db["partner_tasks"].count_documents({"partner_id": partner_id, "status": s_})
                    for s_ in ("open", "in_progress", "submitted", "done")}
    out["campaigns"] = [S.clean(c) for c in db[K.CAMPAIGNS].find({"partner_id": partner_id})]
    out["api_keys"] = [S.clean(k) for k in db.api_keys.find({"partner_id": partner_id}, {"key_hash": 0})]
    app = db[K.APPLICATIONS].find_one({"_id": S.oid(p.get("application_id"))}) if p.get("application_id") else None
    out["application"] = S.public_application(app, admin=True) if app else None
    return {"success": True, "partner": out}


@router.patch("/{partner_id}")
def update_partner(partner_id: str, body: Dict[str, Any], request: Request,
                   ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "partner": S.admin_update_partner(partner_id, body, actor=_actor(ctx),
                                                               ip=_ip(request))}


@router.post("/{partner_id}/suspend")
def suspend(partner_id: str, body: ReasonBody, request: Request, ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "partner": S.set_partner_status(partner_id, K.P_SUSPENDED, actor=_actor(ctx),
                                                             reason=body.reason.strip()[:500], ip=_ip(request))}


@router.post("/{partner_id}/reactivate")
def reactivate(partner_id: str, body: ReasonBody, request: Request, ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "partner": S.set_partner_status(partner_id, K.P_ACTIVE, actor=_actor(ctx),
                                                             reason=body.reason.strip()[:500], ip=_ip(request))}


# ── the partner's sign-in (their users account) ─────────────────────────────

class PartnerPasswordBody(BaseModel):
    password: str
    must_change: bool = True
    notify: bool = True
    reason: str = ""


class PartnerEmailBody(BaseModel):
    email: str
    reason: str = ""


def _partner_user_id(partner_id: str) -> str:
    p = S.load_partner(partner_id)
    if not p.get("user_id"):
        raise HTTPException(status_code=409, detail="This partner has no sign-in account")
    return str(p["user_id"])


@router.post("/{partner_id}/password")
def set_partner_password(partner_id: str, body: PartnerPasswordBody, request: Request,
                         ctx: TenantContext = Depends(MANAGE)):
    """Set the partner's Partner Portal password. Signed out everywhere; by
    default they choose their own at the next sign-in."""
    from app.auth.credentials import set_password
    res = set_password("user", _partner_user_id(partner_id), body.password, actor_email=ctx.email,
                       by_label=f"super_admin:{ctx.email}", must_change=body.must_change, notify=body.notify)
    S.paudit("partner.password_set", partner_id, actor=_actor(ctx), ip=_ip(request),
             details={"must_change_password": res["must_change_password"],
                      "sessions_revoked": res["sessions_revoked"], "reason": body.reason[:300]})
    return {"success": True, **res,
            "message": "Password set. The partner was signed out everywhere"
                       + (" and must choose their own password at the next sign-in." if body.must_change else ".")}


@router.patch("/{partner_id}/email")
def change_partner_email(partner_id: str, body: PartnerEmailBody, request: Request,
                         ctx: TenantContext = Depends(MANAGE)):
    """Change the partner's sign-in email (also the partner record and the
    application). Both addresses are told; signed out everywhere."""
    from app.auth.credentials import change_email
    res = change_email("user", _partner_user_id(partner_id), body.email, actor_email=ctx.email,
                       by_label=f"super_admin:{ctx.email}")
    if res["changed"]:
        S.paudit("partner.email_changed", partner_id, actor=_actor(ctx), ip=_ip(request),
                 details={"before": res["before"], "after": res["after"],
                          "sessions_revoked": res["sessions_revoked"], "reason": body.reason[:300]})
    return {"success": True, "email": res["after"], "sessions_revoked": res["sessions_revoked"],
            "message": (f"Sign-in email changed to {res['after']}. The partner was signed out everywhere."
                        if res["changed"] else "Email unchanged")}


@router.get("/{partner_id}/referrals")
def partner_referrals(partner_id: str, page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
                      stage: Optional[str] = None, ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    query: Dict[str, Any] = {"partner_id": partner_id}
    if stage:
        query["stage"] = stage

    def out(r):
        row = S.clean({k: v for k, v in r.items() if k not in ("payments_seen",)})
        org = db.organizations.find_one({"_id": S.oid(r["organization_id"])}, {"status": 1, "name": 1})
        row["organization_status"] = (org or {}).get("status")
        return row
    return {"success": True, **_page(db[K.REFERRALS], query, page=page, limit=limit, sort="-signed_up_at",
                                     allowed=("signed_up_at",), out=out)}


@router.get("/{partner_id}/commissions")
def partner_commissions(partner_id: str, page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
                        status: Optional[str] = None, ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    query: Dict[str, Any] = {"partner_id": partner_id}
    if status:
        query["status"] = status
    return {"success": True, **_page(db[K.COMMISSIONS], query, page=page, limit=limit, sort="-created_at",
                                     allowed=("created_at",), out=ST.commission_out)}


@router.get("/{partner_id}/payouts")
def partner_payouts(partner_id: str, page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
                    ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    admin = ctx.is_super_admin or PARTNERS_MANAGE in ctx.permissions
    return {"success": True, **_page(db[K.PAYOUTS], {"partner_id": partner_id}, page=page, limit=limit,
                                     sort="-created_at", allowed=("created_at",),
                                     out=lambda p: C.payout_out(p, admin=admin))}


@router.get("/{partner_id}/clicks")
def partner_clicks(partner_id: str, page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
                   suspicious: Optional[bool] = None, ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    query: Dict[str, Any] = {"partner_id": partner_id}
    if suspicious is not None:
        query["suspicious"] = suspicious
    return {"success": True, **_page(db[K.CLICKS], query, page=page, limit=limit, sort="-created_at",
                                     allowed=("created_at",), out=S.clean)}


@router.get("/{partner_id}/ledger")
def partner_ledger(partner_id: str, page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
                   ctx: TenantContext = Depends(VIEW)):
    """Partner financial history: every wallet movement with its customer,
    subscription, invoice, commission and payout references."""
    db = S.db_or_503()
    S.load_partner(partner_id, db)
    total = db[K.WALLET_TX].count_documents({"partner_id": partner_id})
    return {"success": True, "items": C.recent_ledger(partner_id, limit, db, skip=(page - 1) * limit),
            "total": total, "page": page, "limit": limit, "pages": max(1, -(-total // limit)),
            "balances": C.compute_balances(partner_id, db)}


@router.get("/{partner_id}/audit")
def partner_audit(partner_id: str, page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
                  ctx: TenantContext = Depends(VIEW)):
    db = S.db_or_503()
    q = S.partner_audit_query(partner_id)
    total = db.audit_logs.count_documents(q)
    items = [S.clean(a) for a in db.audit_logs.find(q).sort("at", -1).skip((page - 1) * limit).limit(limit)]
    return {"success": True, "items": items, "total": total, "page": page, "limit": limit,
            "pages": max(1, -(-total // limit))}


class AdjustmentBody(BaseModel):
    amount: float
    currency: str = "USD"
    reason: str


@router.post("/{partner_id}/adjustments")
def adjustment(partner_id: str, body: AdjustmentBody, request: Request, ctx: TenantContext = Depends(MANAGE)):
    return {"success": True, "adjustment": C.create_adjustment(partner_id, amount=body.amount,
                                                               currency=body.currency, reason=body.reason,
                                                               actor=_actor(ctx), ip=_ip(request))}


@router.post("/{partner_id}/notify")
def notify_one(partner_id: str, body: NotifyBody, request: Request, ctx: TenantContext = Depends(MANAGE)):
    p = S.load_partner(partner_id)
    title, message = S.text(body.title, 200), S.text(body.message, 1000)
    if not title or not message:
        raise HTTPException(status_code=422, detail="Title and message are required")
    S.notify_partner(p, "partner_message", title, message, email=body.email)
    S.paudit("partner.notification.sent", partner_id, actor=_actor(ctx), ip=_ip(request),
             details={"title": title})
    return {"success": True}
