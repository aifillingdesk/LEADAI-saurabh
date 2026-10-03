"""
Partner platform — analytics (reconciled with invoices), notifications,
marketing center, Partner API and fraud review, on the real routes.
"""
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin

SUPER = "platform-owner@leadai.example"
PW = "Partner-Pass-2026"
CUST_PW = "Customer-Pass-2026"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def env(tmp_path, monkeypatch):
    as_superadmin(SUPER)
    from app.billing.provider import reset_billing_provider
    reset_billing_provider()
    from app.partners import marketing
    monkeypatch.setattr(marketing, "PRIVATE_DIR", str(tmp_path / "assets"))
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def _login(client, email, password, scope="site"):
    c = TestClient(client.app)
    r = c.post("/api/auth/login", json={"email": email, "password": password, "scope": scope})
    assert r.status_code == 200, (email, r.text)
    return c


def _partner(client, sa, email, *, ptype="affiliate", name="P", permissions=None):
    r = client.post("/api/public/partners/apply", json={
        "name": name, "email": email, "password": PW, "company": name + " Co", "partner_type": ptype,
        "experience": "Ten years of SaaS sales", "promotion_plan": "Newsletter", "accepted_terms": True,
        "payout_info": {"method": "paypal", "paypal_email": "pay@" + email.split("@")[1]}})
    assert r.status_code == 200, r.text
    body = {"partner_type": ptype, **({"permissions": permissions} if permissions is not None else {})}
    r = sa.post(f"/api/super-admin/partners/applications/{r.json()['application']['id']}/approve", json=body)
    assert r.status_code == 200, r.text
    return r.json()["partner"], _login(client, email, PW, scope="partner")


def _customer(client, sa, db, partner, email, company, *, campaign=None, coupon=None, pay=True, ref_code=None):
    v = TestClient(client.app)
    v.get(f"/r/{partner['referral_code']}" + (f"/{campaign}" if campaign else ""), follow_redirects=False)
    body = {"email": email, "password": CUST_PW, "name": "Owner", "company": company, "accepted_terms": True}
    if ref_code:
        body["ref_code"] = ref_code
    r = v.post("/api/auth/signup", json=body)
    assert r.status_code == 200, r.text
    org_id = db.users.find_one({"email": email})["default_organization_id"]
    sa.post(f"/api/super-admin/demo-requests/{r.json()['demo_request_id']}/approve", json={})
    if not pay:
        return {"org_id": org_id}
    owner = _login(client, email, CUST_PW)
    plans = owner.get("/api/billing/plans").json()["plans"]
    plan = next(p for p in plans if (p.get("price_monthly") or 0) > 0 and p["slug"] != "enterprise")
    co = owner.post("/api/billing/checkout", json={"plan_slug": plan["slug"], **({"coupon_code": coupon} if coupon else {})})
    assert co.status_code == 200, co.text
    sid = co.json()["checkout"]["session_id"]
    owner.post(f"/api/billing/checkout/{sid}/mock-pay", json={"succeed": True})
    sub = db.subscriptions.find_one({"checkout_session_id": sid})
    assert sa.post(f"/api/super-admin/subscriptions/{sub['_id']}/confirm").status_code == 200
    return {"org_id": org_id, "sub": db.subscriptions.find_one({"_id": sub["_id"]}), "owner": owner,
            "invoice": db.invoices.find_one({"subscription_id": str(sub["_id"])})}


# ── analytics ────────────────────────────────────────────────────────────────

def test_analytics_reconcile_with_invoices_filters_and_export(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    p, portal = _partner(client, sa, "an@aff.example", name="Ana")
    other, other_portal = _partner(client, sa, "ot@aff.example", name="Oth")
    camp = portal.post("/api/partner/v1/campaigns", json={"name": "Webinar"}).json()["campaign"]
    c1 = _customer(client, sa, db, p, "o1@one.example", "One Co", campaign=camp["slug"])
    _customer(client, sa, db, p, "o2@two.example", "Two Co", pay=False)
    TestClient(client.app).get(f"/r/{p['referral_code']}", follow_redirects=False)        # extra visitor
    gross = float(c1["invoice"]["total"])

    a = portal.get("/api/partner/v1/analytics").json()["analytics"]
    t = a["totals"]
    assert t["clicks"] == 3 and t["visitors"] == 3 and t["referrals"] == 2 and t["demos"] == 2
    assert t["customers"] == 1 and t["active_subscriptions"] == 1 and t["revenue"] == gross
    assert t["commission"] == round(gross * 0.2, 2)
    cur = c1["invoice"]["currency"].upper()
    assert a["commission_by_status"][cur]["pending"] == round(gross * 0.2, 2)
    assert [f["stage"] for f in a["funnel"]] == ["visitors", "signups", "demos", "customers"]
    camp_row = next(c for c in a["campaigns"] if c["id"] == camp["id"])
    assert camp_row["customers"] == 1 and camp_row["revenue"] == gross
    # campaign filter
    f = portal.get(f"/api/partner/v1/analytics?campaign_id={camp['id']}").json()["analytics"]["totals"]
    assert f["referrals"] == 1 and f["revenue"] == gross
    # another partner's campaign is not accepted
    assert other_portal.get(f"/api/partner/v1/analytics?campaign_id={camp['id']}").status_code == 404
    # revenue follows refunds on the invoice
    sa.post(f"/api/super-admin/invoices/{c1['invoice']['_id']}/refund", json={"amount": gross / 2, "reason": "partial"})
    assert portal.get("/api/partner/v1/analytics").json()["analytics"]["totals"]["revenue"] == round(gross / 2, 2)
    assert portal.get("/api/partner/v1/dashboard").json()["dashboard"]["kpis"]["revenue"] == round(gross / 2, 2)
    # CSV export
    r = portal.get("/api/partner/v1/analytics.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "period,clicks,visitors" in r.text and "Webinar" in r.text
    # Super Admin: whole program, one partner, leaderboard, export
    allp = sa.get("/api/super-admin/partners/analytics").json()["analytics"]["totals"]
    assert allp["referrals"] == 2
    one = sa.get(f"/api/super-admin/partners/analytics?partner_id={other['id']}").json()["analytics"]["totals"]
    assert one["referrals"] == 0 and one["revenue"] == 0
    lb = sa.get("/api/super-admin/partners/leaderboard").json()
    assert next(x for x in lb["items"] if x["partner_id"] == p["id"])["customers"] == 1
    assert sa.get("/api/super-admin/partners/analytics.csv").status_code == 200
    # empty range = zeros, invalid range = 422
    assert portal.get("/api/partner/v1/analytics?from=2020-01-01&to=2020-01-31").json()["analytics"]["totals"]["clicks"] == 0
    assert portal.get("/api/partner/v1/analytics?from=2026-12-01&to=2026-01-01").status_code == 422


def test_reconciliation_is_clean_and_detects_tampering(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    p, _ = _partner(client, sa, "rc@aff.example", name="Rec")
    c = _customer(client, sa, db, p, "o@rec.example", "Rec Co")
    sa.post(f"/api/super-admin/invoices/{c['invoice']['_id']}/refund", json={"amount": 1, "reason": "goodwill"})
    rec = sa.get("/api/super-admin/partners/reconciliation").json()["reconciliation"]
    assert rec["ok"] is True, rec["issues"]
    # an out-of-band change to a commission is caught (read-only report)
    db.partner_commissions.update_one({"kind": "commission"}, {"$set": {"paid_amount": 1.0}})
    rec = sa.get("/api/super-admin/partners/reconciliation").json()["reconciliation"]
    assert any(i["type"] == "payment_amount_mismatch" for i in rec["issues"])


# ── notifications ────────────────────────────────────────────────────────────

def test_notifications_cover_the_partner_lifecycle(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    db.partner_settings.update_one({"_id": "program"}, {"$set": {"commission_hold_days": 0, "min_payout": 1}}, upsert=True)
    p, portal = _partner(client, sa, "nt@aff.example", name="Note")
    assert sa.post("/api/super-admin/partners/coupons", json={"partner_id": p["id"], "code": "NOTE10",
                                                              "discount_value": 10}).status_code == 200
    c = _customer(client, sa, db, p, "o@note.example", "Note Co", coupon="NOTE10")
    titles = [n["title"] for n in portal.get("/api/partner/v1/notifications?limit=100").json()["items"]]
    for t in ("Your partner application is approved", "New coupon for you", "New referral signed up",
              "Referral demo approved", "Referral chose a plan", "Referral payment received",
              "Your coupon was redeemed", "Commission earned"):
        assert t in titles, (t, titles)
    sa_titles = [n["title"] for n in sa.get("/api/super-admin/notifications?limit=100").json()["items"]]
    assert "New partner application" in sa_titles
    assert any(t.startswith("Partner fraud alert") for t in sa_titles)     # same-network signals in tests
    # refund + payout lifecycle
    sa.post(f"/api/super-admin/invoices/{c['invoice']['_id']}/refund", json={"amount": 1, "reason": "x"})
    com = db.partner_commissions.find_one({"kind": "commission"})
    sa.post(f"/api/super-admin/partners/commissions/{com['_id']}/approve")
    po = portal.post("/api/partner/v1/payouts", json={}).json()["payout"]
    sa.post(f"/api/super-admin/partners/payouts/{po['id']}/reject", json={"reason": "Bank details unclear"})
    titles = [n["title"] for n in portal.get("/api/partner/v1/notifications?limit=100").json()["items"]]
    for t in ("Referral payment refunded", "Commission approved", "Payout requested", "Payout rejected"):
        assert t in titles, (t, titles)
    # email channel uses the existing outbox
    assert db.email_outbox.find_one({"to": "nt@aff.example", "kind": "partner_partner_payout"}) or \
        db.email_outbox.find_one({"to": "nt@aff.example", "kind": "partner_payout"})


# ── marketing center ─────────────────────────────────────────────────────────

def test_marketing_center_permissions_uploads_and_downloads(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    aff, aff_portal = _partner(client, sa, "mk@aff.example", name="Mk")
    res, res_portal = _partner(client, sa, "mk@res.example", ptype="reseller", name="Rs")
    _, no_mkt = _partner(client, sa, "mk@none.example", name="No", permissions=["dashboard.view"])
    logo = sa.post("/api/super-admin/partners/assets", json={"title": "LeadAI logo", "category": "logos"}).json()["asset"]
    r = sa.post(f"/api/super-admin/partners/assets/{logo['id']}/file",
                files={"file": ("logo.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["asset"]["has_file"] and r.json()["asset"]["file_size"] == len(PNG)
    assert sa.post(f"/api/super-admin/partners/assets/{logo['id']}/file",
                   files={"file": ("x.svg", b"<svg/>", "image/svg+xml")}).status_code == 415
    assert sa.post(f"/api/super-admin/partners/assets/{logo['id']}/file",
                   files={"file": ("fake.png", b"not a png", "image/png")}).status_code == 400
    sa.post("/api/super-admin/partners/assets", json={"title": "Reseller deck", "category": "brochures",
                                                      "partner_types": ["reseller"], "url": "https://cdn.example/deck.pdf"})
    sa.post("/api/super-admin/partners/assets", json={"title": "Intro email", "category": "email_templates",
                                                      "subject": "Meet {company}", "content": "Try LeadAI: {referral_url}"})
    sa.post("/api/super-admin/partners/assets", json={"title": "Draft", "category": "copy", "content": "x", "status": "draft"})
    sa.post("/api/super-admin/partners/assets", json={"title": "VIP only", "category": "banners", "url": "https://x.example/b.png",
                                                      "partner_ids": [res["id"]]})
    a_items = {i["title"]: i for i in aff_portal.get("/api/partner/v1/marketing-assets").json()["items"]}
    r_items = {i["title"]: i for i in res_portal.get("/api/partner/v1/marketing-assets").json()["items"]}
    assert set(a_items) == {"LeadAI logo", "Intro email"}
    assert set(r_items) == {"LeadAI logo", "Intro email", "Reseller deck", "VIP only"}
    tpl = a_items["Intro email"]
    assert tpl["subject"] == "Meet Mk Co" and aff["referral_code"] in tpl["content"]
    assert "partner_ids" not in r_items["VIP only"]
    # category filter
    assert [i["title"] for i in aff_portal.get("/api/partner/v1/marketing-assets?category=logos").json()["items"]] == ["LeadAI logo"]
    # permitted download (counted + audited); private file never under /static
    f = aff_portal.get(f"/api/partner/v1/marketing-assets/{logo['id']}/file?download=true")
    assert f.status_code == 200 and f.content == PNG and "attachment" in f.headers["content-disposition"]
    assert db.partner_marketing_assets.find_one({"_id": ObjectId(logo["id"])})["download_count"] == 1
    assert db.audit_logs.find_one({"action": "partner.asset.downloaded"})
    assert not db.partner_marketing_assets.find_one({"_id": ObjectId(logo["id"])}).get("file_url")
    # not permitted: other partner's restricted asset, no marketing permission, anonymous
    vip = r_items["VIP only"]["id"]
    assert aff_portal.get(f"/api/partner/v1/marketing-assets/{vip}/file").status_code == 404
    assert no_mkt.get("/api/partner/v1/marketing-assets").status_code == 403
    assert TestClient(client.app).get(f"/api/partner/v1/marketing-assets/{logo['id']}/file").status_code == 401


# ── Partner API ──────────────────────────────────────────────────────────────

def test_partner_api_index_notifications_and_invalid_keys(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    p, portal = _partner(client, sa, "api@aff.example", name="Api")
    sa.patch(f"/api/super-admin/partners/{p['id']}", json={"permissions": p["permissions"] + ["api.access"]})
    raw = portal.post("/api/partner/v1/api-keys", json={"name": "BI"}).json()["api_key"]
    api = TestClient(client.app)
    h = {"X-API-Key": raw}
    idx = api.get("/api/partner/v1", headers=h).json()
    assert idx["auth"] == "api_key" and idx["read_only"] and "GET /analytics" in idx["endpoints"]
    for path in ("/profile", "/dashboard", "/referral-links", "/referrals", "/customers", "/commissions", "/wallet",
                 "/payouts", "/analytics", "/analytics.csv", "/campaigns", "/marketing-assets", "/notifications"):
        assert api.get("/api/partner/v1" + path, headers=h).status_code == 200, path
    # coupons need coupons.manage (not granted) — denied and flagged for review
    assert api.get("/api/partner/v1/coupons", headers=h).status_code == 403
    assert db.partner_fraud_flags.find_one({"type": "unauthorized_action", "partner_id": p["id"]})
    assert api.post("/api/partner/v1/notifications/read", json={}, headers=h).status_code == 401   # read-only key
    assert db.api_keys.find_one({"partner_id": p["id"]})["usage_count"] >= 13
    # invalid + revoked keys are logged and flagged
    assert api.get("/api/partner/v1/dashboard", headers={"X-API-Key": "lap_live_bogus"}).status_code == 401
    key_id = db.api_keys.find_one({"partner_id": p["id"]})["key_id"]
    portal.delete(f"/api/partner/v1/api-keys/{key_id}")
    assert api.get("/api/partner/v1/dashboard", headers=h).status_code == 401
    assert db.partner_fraud_flags.find_one({"type": "invalid_api_access", "partner_id": p["id"]})
    assert db.security_events.find_one({"type": "invalid_api_key"})


# ── fraud review ─────────────────────────────────────────────────────────────

def test_fraud_signals_are_flagged_held_and_resolved(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    db.partner_settings.update_one({"_id": "program"}, {"$set": {"commission_hold_days": 0}}, upsert=True)
    a, portal_a = _partner(client, sa, "fa@aff.example", name="Fa")
    b, _ = _partner(client, sa, "fb@aff.example", name="Fb")
    # duplicate customer: same company twice for one partner
    _customer(client, sa, db, a, "x1@dup.example", "Dup Trading", pay=False)
    _customer(client, sa, db, a, "x2@other.example", "Dup Trading Pvt Ltd", pay=False)
    assert db.partner_fraud_flags.find_one({"type": "duplicate_customer", "partner_id": a["id"]})
    # attribution conflict: clicked A's link but typed B's code
    _customer(client, sa, db, a, "x3@conf.example", "Conflict Co", pay=False, ref_code=b["referral_code"])
    conf = db.partner_fraud_flags.find_one({"type": "attribution_conflict"})
    assert conf and conf["details"]["typed_code_partner"] == b["id"]
    # tampered cookie
    v = TestClient(client.app)
    v.cookies.set("leadai_ref", "eyJmdCI6e319.deadbeef")
    v.post("/api/auth/signup", json={"email": "x4@tamper.example", "password": CUST_PW, "name": "T",
                                     "company": "Tamper Co", "accepted_terms": True})
    assert db.partner_fraud_flags.find_one({"type": "attribution_tampering"})
    # partner session probing admin APIs
    assert portal_a.get("/api/super-admin/partners").status_code == 401
    assert portal_a.get("/api/org-admin/overview").status_code == 401
    assert db.partner_fraud_flags.find_one({"type": "privilege_probe"})
    # cross-partner probe
    ref_b = db.partner_referrals.find_one({"partner_id": a["id"]})
    _, portal_b = None, _login(client, "fb@aff.example", PW, scope="partner")
    assert portal_b.get(f"/api/partner/v1/referrals/{ref_b['_id']}").status_code == 404
    assert db.partner_fraud_flags.find_one({"type": "cross_partner_access", "partner_id": b["id"]})
    # a paying flagged customer: the commission is HELD, never changed
    c = _customer(client, sa, db, a, "x5@paid.example", "Paid Co")
    com = db.partner_commissions.find_one({"organization_id": c["org_id"]})
    ref = db.partner_referrals.find_one({"organization_id": c["org_id"]})
    flag = db.partner_fraud_flags.find_one({"referral_id": str(ref["_id"]), "status": "open"})
    assert flag and com["requires_manual_approval"] is True
    from app.partners.commissions import release_matured
    release_matured()
    com2 = db.partner_commissions.find_one({"_id": com["_id"]})
    assert com2["status"] == "qualified" and com2["amount"] == com["amount"]     # held, not modified
    # review queue + resolution
    q = sa.get("/api/super-admin/partners/fraud-flags").json()
    assert q["counts"]["open"] >= 5 and q["total"] >= 5
    assert sa.post(f"/api/super-admin/partners/fraud-flags/{flag['_id']}/resolve",
                   json={"decision": "confirm", "note": ""}).status_code == 422
    # dismiss every open flag on this referral -> hold released -> lifecycle continues
    for f in db.partner_fraud_flags.find({"referral_id": str(ref["_id"]), "status": "open"}):
        assert sa.post(f"/api/super-admin/partners/fraud-flags/{f['_id']}/resolve",
                       json={"decision": "dismiss", "note": "Known customer"}).status_code == 200
    assert db.partner_commissions.find_one({"_id": com["_id"]})["requires_manual_approval"] is False
    release_matured()
    assert db.partner_commissions.find_one({"_id": com["_id"]})["status"] == "payable"
    # invalidate_referral is an explicit, audited financial decision
    dup = db.partner_fraud_flags.find_one({"type": "duplicate_customer", "status": "open"})
    r = sa.post(f"/api/super-admin/partners/fraud-flags/{dup['_id']}/resolve",
                json={"decision": "invalidate_referral", "note": "Same business registered twice"})
    assert r.status_code == 200 and r.json()["flag"]["status"] == "confirmed"
    assert db.partner_referrals.find_one({"_id": ObjectId(dup["referral_id"])})["status"] == "invalid"
    assert db.audit_logs.find_one({"action": "partner.fraud.confirmed"})
    assert sa.post(f"/api/super-admin/partners/fraud-flags/{dup['_id']}/resolve",
                   json={"decision": "dismiss"}).status_code == 409
    # partners never see the fraud queue
    assert portal_a.get("/api/super-admin/partners/fraud-flags").status_code == 401


def test_coupon_abuse_is_flagged_not_blocked(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    p, _ = _partner(client, sa, "ca@aff.example", name="Ca")
    sa.post("/api/super-admin/partners/coupons", json={"partner_id": p["id"], "code": "ABUSE10", "discount_value": 10})
    c = _customer(client, sa, db, p, "o@abuse.example", "Abuse Co", coupon="ABUSE10")
    assert c["sub"]["status"] == "active"                                      # the sale itself goes through
    flag = db.partner_fraud_flags.find_one({"type": "coupon_abuse"})
    assert flag and flag["details"]["code"] == "ABUSE10"
    assert db.partner_commissions.find_one({"organization_id": c["org_id"]})["requires_manual_approval"] is True


def test_lead_exports_use_the_stored_commenter_name():
    """Search leads store ``commenter_name`` (API-ingested ones ``author_name``):
    the Excel export and CRM formatters must handle both."""
    from app.services.crm_connectors import ExcelExportService, HubSpotConnector, ZohoCRMConnector
    search_lead = {"commenter_name": "Rahul Verma", "phone": "+91 98765 43210", "comment_text": "=HYPERLINK(1)"}
    api_lead = {"author_name": "Asha Rao"}
    xml = ExcelExportService.generate_spreadsheet_xml([search_lead, api_lead])
    assert b"Rahul Verma" in xml and b"Asha Rao" in xml
    assert b"'=HYPERLINK(1)" in xml                      # formula injection neutralised
    assert HubSpotConnector().format_lead(search_lead)["properties"]["firstname"] == "Rahul"
    assert ZohoCRMConnector().format_lead(search_lead)["Last_Name"] == "Verma"
