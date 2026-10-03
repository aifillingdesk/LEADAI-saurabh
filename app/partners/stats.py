"""Partner dashboard + analytics, computed from the real records
(clicks, referrals, commissions, payouts, organizations)."""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.db.models import utcnow
from app.partners import constants as K
from app.partners.commissions import compute_balances
from app.partners.service import clean, oid


def parse_range(date_from: Optional[str], date_to: Optional[str],
                default_days: int = 30) -> Tuple[datetime, datetime]:
    def parse(v: str, end: bool) -> datetime:
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail="Dates must be YYYY-MM-DD")
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        if end and len(v) <= 10:
            d = d + timedelta(days=1) - timedelta(microseconds=1)
        return d
    end = parse(date_to, True) if date_to else utcnow()
    start = parse(date_from, False) if date_from else end - timedelta(days=default_days)
    if start > end:
        raise HTTPException(status_code=422, detail="'from' must be before 'to'")
    if (end - start).days > 731:
        raise HTTPException(status_code=422, detail="Date range is limited to two years")
    return start, end


def _rng(start, end) -> Dict[str, Any]:
    return {"$gte": start, "$lte": end}


def dashboard(db, partner: Dict[str, Any], start: datetime, end: datetime) -> Dict[str, Any]:
    pid = str(partner["_id"])
    clicks = db[K.CLICKS].count_documents({"partner_id": pid, "created_at": _rng(start, end)})
    unique_clicks = db[K.CLICKS].count_documents({"partner_id": pid, "unique": True,
                                                  "suspicious": {"$ne": True},
                                                  "created_at": _rng(start, end)})
    signups = db[K.REFERRALS].count_documents({"partner_id": pid, "signed_up_at": _rng(start, end)})
    # click conversion counts only signups that came from a recorded click
    # (reseller-onboarded, code-entry and coupon customers have none)
    click_signups = db[K.REFERRALS].count_documents({"partner_id": pid, "signed_up_at": _rng(start, end),
                                                     "click_id": {"$nin": [None, ""]}})
    demos = db[K.REFERRALS].count_documents({"partner_id": pid, "demo_at": _rng(start, end)})
    customers = db[K.REFERRALS].count_documents({"partner_id": pid, "converted_at": _rng(start, end)})
    total_customers = db[K.REFERRALS].count_documents({"partner_id": pid, "stage": K.STAGE_CUSTOMER})
    org_ids = [oid(r["organization_id"]) for r in db[K.REFERRALS].find(
        {"partner_id": pid, "stage": K.STAGE_CUSTOMER}, {"organization_id": 1})]
    active_subs = db.organizations.count_documents(
        {"_id": {"$in": [o for o in org_ids if o]}, "status": "active"}) if org_ids else 0
    revenue = sum(i["net"] for i in invoice_revenue(db, referred_org_ids(db, [pid]), start, end))
    earned = 0.0
    for c in db[K.COMMISSIONS].find({"partner_id": pid, "kind": "commission",
                                     "status": {"$ne": K.C_REVERSED},
                                     "created_at": _rng(start, end)}, {"amount": 1}):
        earned += float(c.get("amount") or 0)
    open_payouts = list(db[K.PAYOUTS].find({"partner_id": pid,
                                            "status": {"$in": list(K.OPEN_PAYOUT_STATUSES)}},
                                           {"amount": 1, "currency": 1}))
    balances = compute_balances(pid, db)
    base = unique_clicks or clicks
    return {
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "kpis": {
            "clicks": clicks, "unique_clicks": unique_clicks, "referrals": signups,
            "demos": demos, "customers": customers, "total_customers": total_customers,
            "active_subscriptions": active_subs, "revenue": round(revenue, 2),
            "commission_earned": round(earned, 2),
            "conversion_rate": round(min(100.0, click_signups * 100.0 / base), 2) if base else 0.0,
            "customer_rate": round(customers * 100.0 / signups, 2) if signups else 0.0,
            "pending_payouts": round(sum(float(p["amount"]) for p in open_payouts), 2),
            "pending_payout_count": len(open_payouts),
        },
        "balances": balances,
        "recent_referrals": [referral_out(r) for r in db[K.REFERRALS].find({"partner_id": pid})
                             .sort("signed_up_at", -1).limit(5)],
        "recent_customers": [referral_out(r) for r in db[K.REFERRALS].find(
            {"partner_id": pid, "stage": K.STAGE_CUSTOMER}).sort("converted_at", -1).limit(5)],
        "recent_commissions": [commission_out(c) for c in db[K.COMMISSIONS].find({"partner_id": pid})
                               .sort("created_at", -1).limit(5)],
    }


def referral_out(r: Dict[str, Any]) -> Dict[str, Any]:
    """What a partner may see about a referred organization (no customer data
    beyond company + masked contact)."""
    email = r.get("email") or ""
    local, _, domain = email.partition("@")
    masked = (local[:2] + "•••@" + domain) if domain else None
    return clean({"_id": r["_id"], "organization_id": r.get("organization_id"),
                  "company": r.get("company"), "email": masked, "source": r.get("source"),
                  "managed": r.get("managed", False), "campaign_id": r.get("campaign_id"),
                  "stage": r.get("stage"), "status": r.get("status"),
                  "signed_up_at": r.get("signed_up_at"), "demo_at": r.get("demo_at"),
                  "subscription_at": r.get("subscription_at"), "payment_at": r.get("payment_at"),
                  "converted_at": r.get("converted_at"), "churned_at": r.get("churned_at"),
                  "plan_id": r.get("plan_id"),
                  "revenue_total": round(float(r.get("revenue_total") or 0), 2),
                  "commission_total": round(float(r.get("commission_total") or 0), 2)})


def commission_out(c: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("_id", "referral_id", "organization_id", "company", "event", "kind", "plan_id",
            "base_amount", "paid_amount", "currency", "rate_type", "rate_value", "rate_fixed", "amount",
            "original_amount", "reversed_amount", "clawed_back", "status", "requires_manual_approval",
            "hold_until", "payout_id", "note", "created_at", "qualified_at", "approved_at", "payable_at",
            "paid_at", "reversal_reason", "reversal_source", "chargeback", "clawback_of", "period_key",
            "invoice_id", "subscription_id", "coupon_code", "rule_name")
    return clean({k: c.get(k) for k in keep if k in c})


def _bucket(d: datetime, unit: str) -> str:
    if unit == "month":
        return d.strftime("%Y-%m")
    if unit == "week":
        return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
    return d.strftime("%Y-%m-%d")


def _labels(start: datetime, end: datetime, unit: str) -> List[str]:
    labels, d = [], start
    while d <= end and len(labels) < 800:
        lbl = _bucket(d, unit)
        if not labels or labels[-1] != lbl:
            labels.append(lbl)
        d += timedelta(days=1)
    return labels


def _scope(partner_ids: Optional[List[str]]) -> Dict[str, Any]:
    return {} if partner_ids is None else {"partner_id": {"$in": [str(p) for p in partner_ids]}}


def referred_org_ids(db, partner_ids: Optional[List[str]], campaign_id: Optional[str] = None) -> List[str]:
    q: Dict[str, Any] = {**_scope(partner_ids), "status": "active"}
    if campaign_id:
        q["campaign_id"] = campaign_id
    return [r["organization_id"] for r in db[K.REFERRALS].find(q, {"organization_id": 1})]


def invoice_revenue(db, org_ids: List[str], start: Optional[datetime] = None,
                    end: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Paid invoices of referred organizations (the billing system's own
    records), net of refunds. Single source for "revenue generated"."""
    if not org_ids:
        return []
    q: Dict[str, Any] = {"organization_id": {"$in": org_ids},
                         "status": {"$in": ["paid", "partially_refunded", "refunded"]}}
    if start is not None:
        q["created_at"] = _rng(start, end)
    out = []
    for inv in db.invoices.find(q, {"total": 1, "amount": 1, "refunded_amount": 1, "currency": 1,
                                    "created_at": 1, "organization_id": 1}):
        gross = float(inv.get("total") if inv.get("total") is not None else inv.get("amount") or 0)
        out.append({"at": inv.get("created_at"), "net": round(gross - float(inv.get("refunded_amount") or 0), 2),
                    "gross": round(gross, 2), "currency": (inv.get("currency") or "USD").upper(),
                    "organization_id": inv.get("organization_id")})
    return out


def analytics(db, partner: Optional[Dict[str, Any]], start: datetime, end: datetime, unit: str = "day",
              *, campaign_id: Optional[str] = None, partner_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Real-data analytics for one partner (portal) or many / all (Super Admin:
    ``partner=None`` and ``partner_ids`` None = whole program)."""
    if unit not in ("day", "week", "month"):
        unit = "day"
    if partner is not None:
        partner_ids = [str(partner["_id"])]
    scope = _scope(partner_ids)
    labels = _labels(start, end, unit)
    keys = ("clicks", "visitors", "referrals", "demos", "customers", "revenue", "commission", "payouts")
    series = {k: dict.fromkeys(labels, 0.0) for k in keys}
    visitors_per_bucket: Dict[str, set] = {lbl: set() for lbl in labels}

    def add(key, when, value=1.0):
        if when is None:
            return
        when = _aware(when)
        if not start <= when <= end:
            return
        b = _bucket(when, unit)
        if b in series[key]:
            series[key][b] += value

    cq: Dict[str, Any] = {**scope, "created_at": _rng(start, end)}
    rq: Dict[str, Any] = dict(scope)
    if campaign_id:
        cq["campaign_id"] = campaign_id
        rq["campaign_id"] = campaign_id
    all_visitors = set()
    for c in db[K.CLICKS].find(cq, {"created_at": 1, "visitor_id": 1, "suspicious": 1}):
        add("clicks", c["created_at"])
        if c.get("visitor_id") and not c.get("suspicious"):
            all_visitors.add(c["visitor_id"])
            b = _bucket(_aware(c["created_at"]), unit)
            if b in visitors_per_bucket:
                visitors_per_bucket[b].add(c["visitor_id"])
    for lbl, vs in visitors_per_bucket.items():
        series["visitors"][lbl] = float(len(vs))
    for r in db[K.REFERRALS].find({**rq, "$or": [{"signed_up_at": _rng(start, end)}, {"demo_at": _rng(start, end)},
                                                 {"converted_at": _rng(start, end)}]},
                                  {"signed_up_at": 1, "demo_at": 1, "converted_at": 1}):
        add("referrals", r.get("signed_up_at"))
        add("demos", r.get("demo_at"))
        add("customers", r.get("converted_at"))
    org_ids = referred_org_ids(db, partner_ids, campaign_id)
    revenue_by_cur: Dict[str, float] = {}
    for inv in invoice_revenue(db, org_ids, start, end):
        add("revenue", inv["at"], inv["net"])
        revenue_by_cur[inv["currency"]] = round(revenue_by_cur.get(inv["currency"], 0) + inv["net"], 2)
    com_q: Dict[str, Any] = {**scope, "created_at": _rng(start, end)}
    if campaign_id:
        com_q["organization_id"] = {"$in": org_ids}
    by_status: Dict[str, Dict[str, float]] = {}
    for c in db[K.COMMISSIONS].find(com_q, {"created_at": 1, "amount": 1, "status": 1, "currency": 1, "kind": 1}):
        if c.get("kind") == "commission" and c.get("status") != K.C_REVERSED:
            add("commission", c["created_at"], float(c.get("amount") or 0))
        cur = (c.get("currency") or "USD").upper()
        b = by_status.setdefault(cur, dict.fromkeys(K.COMMISSION_STATUSES, 0.0))
        b[c["status"]] = round(b.get(c["status"], 0.0) + float(c.get("amount") or 0), 2)
    payouts = {"count": 0, "by_status": {}}
    if not campaign_id:
        for po in db[K.PAYOUTS].find({**scope, "created_at": _rng(start, end)}, {"created_at": 1, "amount": 1,
                                                                                "status": 1, "currency": 1}):
            payouts["count"] += 1
            s = payouts["by_status"].setdefault(po["status"], {"count": 0, "amount": 0.0})
            s["count"] += 1
            s["amount"] = round(s["amount"] + float(po.get("amount") or 0), 2)
            if po["status"] == K.PO_PAID:
                add("payouts", po["created_at"], float(po.get("amount") or 0))
    active_subs = db.organizations.count_documents(
        {"_id": {"$in": [oid(o) for o in org_ids if oid(o)]}, "status": "active"}) if org_ids else 0
    totals = {k: round(sum(v.values()), 2) for k, v in series.items()}
    totals["visitors"] = len(all_visitors)
    totals["active_subscriptions"] = active_subs
    # visitor conversion counts only signups that came from a recorded click
    click_signups = db[K.REFERRALS].count_documents({**rq, "signed_up_at": _rng(start, end),
                                                     "click_id": {"$nin": [None, ""]}})
    totals["click_signups"] = click_signups
    totals["conversion_rate"] = (round(min(100.0, click_signups * 100.0 / totals["visitors"]), 2)
                                 if totals["visitors"] else 0.0)
    totals["customer_rate"] = round(totals["customers"] * 100.0 / totals["referrals"], 2) if totals["referrals"] else 0.0

    campaigns = []
    camp_q: Dict[str, Any] = dict(scope)
    if campaign_id:
        camp_q["_id"] = oid(campaign_id)
    for camp in db[K.CAMPAIGNS].find(camp_q).limit(500):
        cid = str(camp["_id"])
        cl = list(db[K.CLICKS].find({"campaign_id": cid, "created_at": _rng(start, end)}, {"visitor_id": 1}))
        refs = db[K.REFERRALS].count_documents({"campaign_id": cid, "signed_up_at": _rng(start, end)})
        demos = db[K.REFERRALS].count_documents({"campaign_id": cid, "demo_at": _rng(start, end)})
        custs = db[K.REFERRALS].count_documents({"campaign_id": cid, "converted_at": _rng(start, end)})
        rev = sum(i["net"] for i in invoice_revenue(db, referred_org_ids(db, None, cid), start, end))
        visitors = len({c.get("visitor_id") for c in cl if c.get("visitor_id")})
        campaigns.append({"id": cid, "partner_id": camp.get("partner_id"), "name": camp.get("name"),
                          "slug": camp.get("slug"), "kind": camp.get("kind") or "referral",
                          "status": camp.get("status"), "clicks": len(cl), "visitors": visitors,
                          "referrals": refs, "demos": demos, "customers": custs, "revenue": round(rev, 2),
                          "conversion_rate": round(refs * 100.0 / visitors, 2) if visitors else 0.0})
    campaigns.sort(key=lambda c: (-c["referrals"], -c["clicks"]))
    sources: Dict[str, int] = {}
    for r in db[K.REFERRALS].find({**rq, "signed_up_at": _rng(start, end)}, {"source": 1}):
        sources[r.get("source") or "link"] = sources.get(r.get("source") or "link", 0) + 1
    return {"unit": unit, "labels": labels,
            "range": {"from": start.isoformat(), "to": end.isoformat()},
            "series": {k: [round(v[lbl], 2) for lbl in labels] for k, v in series.items()},
            "totals": totals, "revenue_by_currency": revenue_by_cur, "commission_by_status": by_status,
            "payouts": payouts, "campaigns": campaigns,
            "sources": [{"source": k, "count": v} for k, v in sorted(sources.items())],
            "funnel": [{"stage": s, "count": int(totals[k])} for s, k in
                       (("visitors", "visitors"), ("signups", "referrals"), ("demos", "demos"),
                        ("customers", "customers"))]}


def partner_leaderboard(db, start: datetime, end: datetime, *, page: int = 1, limit: int = 25,
                        partner_type: Optional[str] = None) -> Dict[str, Any]:
    """Super Admin: per-partner performance over the period (paged)."""
    q: Dict[str, Any] = {}
    if partner_type:
        q["partner_type"] = partner_type
    total = db[K.PARTNERS].count_documents(q)
    rows = []
    for p in db[K.PARTNERS].find(q).sort("created_at", -1).skip((page - 1) * limit).limit(limit):
        pid = str(p["_id"])
        orgs = referred_org_ids(db, [pid])
        earned = 0.0
        for c in db[K.COMMISSIONS].find({"partner_id": pid, "kind": "commission", "status": {"$ne": K.C_REVERSED},
                                         "created_at": _rng(start, end)}, {"amount": 1}):
            earned += float(c.get("amount") or 0)
        rows.append({"partner_id": pid, "name": p.get("company") or p.get("name"), "partner_type": p.get("partner_type"),
                     "status": p.get("status"),
                     "clicks": db[K.CLICKS].count_documents({"partner_id": pid, "created_at": _rng(start, end)}),
                     "referrals": db[K.REFERRALS].count_documents({"partner_id": pid, "signed_up_at": _rng(start, end)}),
                     "customers": db[K.REFERRALS].count_documents({"partner_id": pid, "converted_at": _rng(start, end)}),
                     "revenue": round(sum(i["net"] for i in invoice_revenue(db, orgs, start, end)), 2),
                     "commission": round(earned, 2)})
    return {"items": rows, "total": total, "page": page, "limit": limit, "pages": max(1, -(-total // limit))}


def analytics_csv(data: Dict[str, Any]) -> str:
    """Time series + campaign table as CSV (spreadsheet-formula safe)."""
    import csv
    import io

    def safe(v):
        s = str(v if v is not None else "")
        return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") and not _is_number(s) else s
    buf = io.StringIO()
    w = csv.writer(buf)
    keys = list(data["series"].keys())
    w.writerow(["period"] + keys)
    for i, lbl in enumerate(data["labels"]):
        w.writerow([lbl] + [data["series"][k][i] for k in keys])
    w.writerow([])
    w.writerow(["campaign", "kind", "status", "clicks", "visitors", "referrals", "demos", "customers", "revenue",
                "conversion_rate"])
    for c in data["campaigns"]:
        w.writerow([safe(c["name"]), c["kind"], c["status"], c["clicks"], c["visitors"], c["referrals"], c["demos"],
                    c["customers"], c["revenue"], c["conversion_rate"]])
    return buf.getvalue()


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def reconciliation(db, partner_id: Optional[str] = None) -> Dict[str, Any]:
    """Cross-check partner money against the billing records. Read-only:
    reports differences for review, never changes anything."""
    issues: List[Dict[str, Any]] = []
    scope = {"partner_id": partner_id} if partner_id else {}
    checked = {"commissions": 0, "payouts": 0, "wallets": 0, "referrals": 0}
    for c in db[K.COMMISSIONS].find({**scope, "kind": "commission"}).limit(5000):
        checked["commissions"] += 1
        inv = db.invoices.find_one({"_id": oid(c.get("invoice_id"))}) if c.get("invoice_id") else None
        if not inv:
            issues.append({"type": "commission_without_invoice", "commission_id": str(c["_id"]),
                           "partner_id": c["partner_id"], "subscription_id": c.get("subscription_id")})
            continue
        if str(inv.get("organization_id")) != str(c.get("organization_id")):
            issues.append({"type": "invoice_organization_mismatch", "commission_id": str(c["_id"]),
                           "invoice_id": str(inv["_id"])})
        total = float(inv.get("total") if inv.get("total") is not None else inv.get("amount") or 0)
        if c.get("paid_amount") is not None and abs(float(c["paid_amount"]) - total) > 0.01:
            issues.append({"type": "payment_amount_mismatch", "commission_id": str(c["_id"]),
                           "invoice_id": str(inv["_id"]), "commission_paid_amount": c["paid_amount"],
                           "invoice_total": total})
        refunded = float(inv.get("refunded_amount") or 0)
        if refunded > 0 and total > 0:
            expected = round(float(c.get("original_amount") or c.get("amount") or 0) * min(1.0, refunded / total), 2)
            done = round(float(c.get("reversed_amount") or 0) + float(c.get("clawed_back") or 0), 2)
            if done + 0.02 < expected and c.get("status") != K.C_REVERSED:
                issues.append({"type": "refund_not_reflected", "commission_id": str(c["_id"]),
                               "invoice_id": str(inv["_id"]), "expected_reversal": expected, "reversed": done})
    for po in db[K.PAYOUTS].find({**scope, "status": K.PO_PAID}).limit(5000):
        checked["payouts"] += 1
        held = sum(float(c.get("amount") or 0) for c in db[K.COMMISSIONS].find({"payout_id": str(po["_id"])},
                                                                               {"amount": 1}))
        if abs(held - float(po.get("amount") or 0)) > 0.01:
            issues.append({"type": "payout_total_mismatch", "payout_id": str(po["_id"]), "partner_id": po["partner_id"],
                           "payout_amount": po.get("amount"), "commissions_total": round(held, 2)})
    from app.partners.commissions import compute_balances
    for w in db[K.WALLETS].find(scope).limit(5000):
        checked["wallets"] += 1
        if (w.get("balances") or {}) != compute_balances(w["partner_id"], db):
            issues.append({"type": "wallet_cache_stale", "partner_id": w["partner_id"]})
    for r in db[K.REFERRALS].find({**scope, "status": "active", "revenue_total": {"$gt": 0}}).limit(5000):
        checked["referrals"] += 1
        net = sum(i["net"] for i in invoice_revenue(db, [r["organization_id"]]))
        if abs(net - float(r.get("revenue_total") or 0)) > 0.01:
            issues.append({"type": "referral_revenue_mismatch", "referral_id": str(r["_id"]),
                           "partner_id": r["partner_id"], "referral_revenue": r.get("revenue_total"),
                           "invoice_net_revenue": round(net, 2)})
    return {"ok": not issues, "checked": checked, "issues": issues[:200], "issue_count": len(issues)}


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def admin_overview(db) -> Dict[str, Any]:
    def count(coll, q):
        return db[coll].count_documents(q)
    sums: Dict[str, Dict[str, float]] = {}
    for c in db[K.COMMISSIONS].find({}, {"status": 1, "amount": 1, "currency": 1}):
        cur = (c.get("currency") or "USD").upper()
        b = sums.setdefault(cur, {s: 0.0 for s in K.COMMISSION_STATUSES})
        b[c["status"]] = round(b.get(c["status"], 0.0) + float(c.get("amount") or 0), 2)
    return {
        "applications_pending": count(K.APPLICATIONS, {"status": K.APP_PENDING}),
        "applications_changes": count(K.APPLICATIONS, {"status": K.APP_CHANGES}),
        "partners_active": count(K.PARTNERS, {"status": K.P_ACTIVE}),
        "partners_suspended": count(K.PARTNERS, {"status": K.P_SUSPENDED}),
        "affiliates": count(K.PARTNERS, {"partner_type": K.AFFILIATE}),
        "resellers": count(K.PARTNERS, {"partner_type": K.RESELLER}),
        "referrals": count(K.REFERRALS, {}),
        "customers": count(K.REFERRALS, {"stage": K.STAGE_CUSTOMER}),
        "commissions_pending_review": count(K.COMMISSIONS, {"status": {"$in": [K.C_PENDING, K.C_QUALIFIED]},
                                                            "requires_manual_approval": True}),
        "flagged_referrals": count(K.REFERRALS, {"suspicious": True, "status": "active"}),
        "payouts_open": count(K.PAYOUTS, {"status": {"$in": list(K.OPEN_PAYOUT_STATUSES)}}),
        "tasks_to_review": count("partner_tasks", {"status": "submitted"}),
        "tasks_open": count("partner_tasks", {"status": {"$in": ["open", "in_progress"]}}),
        "commission_totals": sums,
    }
