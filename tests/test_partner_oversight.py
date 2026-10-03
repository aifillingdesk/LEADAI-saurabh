"""
Partner sales kit, deal registration, and the Super Admin's oversight of
everything partners do (activity log, sessions, deals) — on the real routes.
"""
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin

SUPER = "platform-owner@leadai.example"
PW = "Partner-Pass-2026"


@pytest.fixture
def env():
    as_superadmin(SUPER)
    from app.billing.provider import reset_billing_provider
    reset_billing_provider()
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def _login(client, email, password, scope="site", expect=200):
    c = TestClient(client.app)
    r = c.post("/api/auth/login", json={"email": email, "password": password, "scope": scope})
    assert r.status_code == expect, (email, r.text)
    return c


def _partner(client, sa, email, *, ptype="affiliate", name="P", permissions=None):
    r = client.post("/api/public/partners/apply", json={
        "name": name, "email": email, "password": PW, "company": name + " Co", "partner_type": ptype,
        "experience": "Ten years of SaaS sales", "promotion_plan": "Newsletter", "accepted_terms": True})
    assert r.status_code == 200, r.text
    body = {"partner_type": ptype, **({"permissions": permissions} if permissions is not None else {})}
    r = sa.post(f"/api/super-admin/partners/applications/{r.json()['application']['id']}/approve", json=body)
    assert r.status_code == 200, r.text
    return r.json()["partner"], _login(client, email, PW, scope="partner")


def test_sales_kit_shows_live_plans_customer_price_commission_and_share_links(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    p, portal = _partner(client, sa, "seller@kit.example", name="Seller")
    assert "sales.view" in p["effective_permissions"] and "deals.manage" in p["effective_permissions"]
    sa.post("/api/super-admin/partners/pricing", json={"name": "Seller deal", "scope": "partner", "partner_id": p["id"],
                                                       "discount_type": "percentage", "discount_value": 10})
    kit = portal.get("/api/partner/v1/sales").json()["sales"]
    paid = [x for x in kit["plans"] if not x["free"]]
    assert paid, kit
    plan_doc = db.plans.find_one({"slug": paid[0]["slug"]})
    m = paid[0]["cycles"]["monthly"]
    assert m["list_price"] == float(plan_doc["price_monthly"])
    assert m["customer_price"] == round(float(plan_doc["price_monthly"]) * 0.9, 2)     # partner pricing
    assert m["commission_first_payment"] == round(m["customer_price"] * 0.20, 2)       # default 20 % rule
    assert paid[0]["share_url"].endswith(f"/r/{p['referral_code']}?plan={paid[0]['slug']}")
    # the plan link lands on the demo request with the plan pre-selected, and attributes
    v = TestClient(client.app)
    r = v.get(f"/r/{p['referral_code']}?plan={paid[0]['slug']}", follow_redirects=False)
    assert r.status_code == 302 and f"plan={paid[0]['slug']}" in r.headers["location"] and "ref=" in r.headers["location"]
    assert v.cookies.get("leadai_ref")
    # without the permission the kit is refused
    sa.patch(f"/api/super-admin/partners/{p['id']}", json={"permissions": ["dashboard.view"]})
    assert portal.get("/api/partner/v1/sales").status_code == 403


def test_deal_registration_review_link_and_conflict(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    a, pa = _partner(client, sa, "alpha@deals.example", name="Alpha")
    b, pb = _partner(client, sa, "beta@deals.example", name="Beta")
    r = pa.post("/api/partner/v1/deals", json={"company": "Big Retail", "contact_name": "Ravi",
                                              "contact_email": "ravi@bigretail.example", "expected_value": 5000})
    assert r.status_code == 200, r.text
    deal = r.json()["deal"]
    assert deal["status"] == "registered" and "conflict" not in deal
    assert pa.post("/api/partner/v1/deals", json={"company": "Big Retail", "contact_email": "ravi@bigretail.example"}).status_code == 409
    assert pa.post("/api/partner/v1/deals", json={"company": "Me", "contact_email": "alpha@deals.example"}).status_code == 422
    # Super Admin sees it (with the competing-claim check) and is notified
    lst = sa.get("/api/super-admin/partners/deals?status=registered").json()
    assert lst["total"] == 1 and lst["items"][0]["partner_name"] == "Alpha Co"
    assert any(n.get("type") == "partner_deal" for n in sa.get("/api/super-admin/notifications").json()["items"])
    # Beta registering the same prospect is flagged as a conflict for review
    rb = pb.post("/api/partner/v1/deals", json={"company": "Big Retail Pvt Ltd", "contact_email": "buyer@bigretail.example"})
    assert rb.status_code == 200
    assert db.partner_deals.find_one({"_id": ObjectId(rb.json()["deal"]["id"])})["conflict"]["kind"] == "deal"
    # approve -> protected; reject needs a reason
    assert sa.post(f"/api/super-admin/partners/deals/{rb.json()['deal']['id']}/reject", json={"note": ""}).status_code == 422
    r = sa.post(f"/api/super-admin/partners/deals/{deal['id']}/approve", json={"note": "ok"})
    assert r.status_code == 200 and r.json()["deal"]["protected_until"]
    # isolation: Beta can't touch Alpha's deal
    assert pb.patch(f"/api/partner/v1/deals/{deal['id']}", json={"notes": "mine"}).status_code == 404
    assert pb.get("/api/partner/v1/deals").json()["total"] == 1
    # the prospect signs up through BETA's link -> linked nowhere for Beta, conflict flag (money untouched)
    v = TestClient(client.app)
    v.get(f"/r/{b['referral_code']}", follow_redirects=False)
    v.post("/api/auth/signup", json={"email": "ravi@bigretail.example", "password": "Shop-Owner-2026", "name": "Ravi",
                                     "company": "Big Retail", "accepted_terms": True})
    ref = db.partner_referrals.find_one({"email": "ravi@bigretail.example"})
    assert ref["partner_id"] == b["id"] and ref["suspicious"] is True
    flag = db.partner_fraud_flags.find_one({"type": "attribution_conflict", "subject_id": str(ref["_id"])})
    assert flag and flag["details"]["deal_id"] == deal["id"]
    # a registered prospect signing up through the partner's OWN link links the deal
    d2 = pa.post("/api/partner/v1/deals", json={"company": "Corner Bakery", "contact_name": "Asha",
                                               "contact_email": "asha@cornerbakery.example"}).json()["deal"]
    v2 = TestClient(client.app)
    v2.get(f"/r/{a['referral_code']}", follow_redirects=False)
    v2.post("/api/auth/signup", json={"email": "asha@cornerbakery.example", "password": "Shop-Owner-2026",
                                      "name": "Asha", "company": "Corner Bakery", "accepted_terms": True})
    assert db.partner_deals.find_one({"_id": ObjectId(d2["id"])})["referral_id"] is not None
    row = next(x for x in pa.get("/api/partner/v1/deals").json()["items"] if x["id"] == d2["id"])
    assert row["referral_stage"] == "signed_up"


def test_reseller_converts_a_deal_into_a_customer(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    _, portal = _partner(client, sa, "res@deals.example", ptype="reseller", name="Res")
    d = portal.post("/api/partner/v1/deals", json={"company": "Gym Plus", "contact_name": "Gita",
                                                  "contact_email": "gita@gymplus.example"}).json()["deal"]
    r = portal.post(f"/api/partner/v1/deals/{d['id']}/convert")
    assert r.status_code == 200, r.text
    org_id = r.json()["customer"]["organization_id"]
    assert db.organizations.find_one({"_id": ObjectId(org_id)})["status"] == "pending"   # normal demo flow
    saved = db.partner_deals.find_one({"_id": ObjectId(d["id"])})
    assert saved["organization_id"] == org_id and saved["referral_id"]
    assert portal.post(f"/api/partner/v1/deals/{d['id']}/convert").status_code == 409


def test_super_admin_sees_every_partner_activity(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    p, portal = _partner(client, sa, "watched@act.example", name="Watched")
    # failed sign-in, page + API use, a business action, sign-out
    _login(client, "watched@act.example", "wrong-Pass-1", scope="partner", expect=401)
    portal.get("/partner")
    portal.get("/api/partner/v1/dashboard")
    portal.get("/api/partner/v1/notifications?unread=true&limit=1")    # badge poll: not logged
    portal.post("/api/partner/v1/campaigns", json={"name": "Spring"})
    portal.get("/api/partner/v1/commissions?status=bogus")             # 422
    feed = sa.get(f"/api/super-admin/partners/activity?partner_id={p['id']}&limit=200").json()
    acts = [(i["kind"], i["action"], i.get("success")) for i in feed["items"]]
    assert ("auth", "login", True) in acts and ("auth", "login", False) in acts
    assert ("request", "GET /partner", True) in acts
    assert ("request", "GET /api/partner/v1/dashboard", True) in acts
    assert ("request", "POST /api/partner/v1/campaigns", True) in acts
    assert ("request", "GET /api/partner/v1/commissions", False) in acts
    assert ("action", "partner.campaign.created", True) in acts
    assert ("action", "partner.created", True) in acts              # Super Admin's own action on the partner
    assert not any(a[1] == "GET /api/partner/v1/notifications" for a in acts)
    req = next(i for i in feed["items"] if i["action"] == "GET /api/partner/v1/dashboard")
    assert req["status"] == 200 and req["email"] == "watched@act.example" and req["duration_ms"] is not None
    assert "ip" not in req and req["ip_hash"]                       # IPs are hashed, never stored raw
    # filters + summary + CSV
    assert sa.get(f"/api/super-admin/partners/activity?partner_id={p['id']}&kind=auth").json()["total"] >= 2
    assert sa.get("/api/super-admin/partners/activity?success=false&kind=auth").json()["total"] >= 1
    assert feed["summary_24h"]["failed_logins"] >= 1
    csv = sa.get(f"/api/super-admin/partners/activity.csv?partner_id={p['id']}")
    assert csv.status_code == 200 and "partner.campaign.created" in csv.text
    detail = sa.get(f"/api/super-admin/partners/{p['id']}").json()["partner"]
    assert detail["activity"]["last_login_at"] and detail["activity"]["failed_logins_30d"] >= 1
    assert detail["active_sessions"] >= 1
    # API-key calls are logged too
    perms = p["permissions"] + ["api.access"]
    sa.patch(f"/api/super-admin/partners/{p['id']}", json={"permissions": perms})
    raw = portal.post("/api/partner/v1/api-keys", json={"name": "BI"}).json()["api_key"]
    TestClient(client.app).get("/api/partner/v1/dashboard", headers={"X-API-Key": raw})
    api_rows = sa.get(f"/api/super-admin/partners/activity?partner_id={p['id']}&via_api_key=true").json()
    assert api_rows["total"] == 1 and api_rows["items"][0]["api_key_id"]
    # partners can never read the activity log
    assert portal.get("/api/super-admin/partners/activity").status_code == 401


def test_super_admin_controls_partner_sessions(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    p, portal = _partner(client, sa, "sess@act.example", name="Sess")
    second = _login(client, "sess@act.example", PW, scope="partner")
    sessions = sa.get(f"/api/super-admin/partners/sessions?partner_id={p['id']}").json()
    assert sessions["total"] == 2 and sessions["items"][0]["partner_name"] == "Sess Co"
    # end one session
    one = sessions["items"][0]["id"]
    assert sa.post(f"/api/super-admin/partners/sessions/{one}/revoke", json={"reason": "lost laptop"}).status_code == 200
    alive = [c for c in (portal, second) if c.get("/api/partner/v1/me").status_code == 200]
    assert len(alive) == 1
    # force sign-out everywhere
    r = sa.post(f"/api/super-admin/partners/{p['id']}/sessions/revoke", json={"reason": "security review"})
    assert r.status_code == 200 and r.json()["revoked"] == 1
    assert all(c.get("/api/partner/v1/me").status_code == 401 for c in (portal, second))
    acts = [i["action"] for i in sa.get(f"/api/super-admin/partners/activity?partner_id={p['id']}&kind=action").json()["items"]]
    assert "partner.session.revoked" in acts and "partner.session.revoked_all" in acts
    # partner can sign in again (not suspended), and is notified
    _login(client, "sess@act.example", PW, scope="partner")
    assert db.notifications.find_one({"type": "partner_security", "title": "You were signed out by LeadAI"})


def test_customer_billing_views_never_expose_the_referring_partner(env):
    """A customer who paid with a partner coupon sees the discount, never the
    partner's internal ids; applicant-era sign-ins join the partner history."""
    client, db = env
    from app.billing.subscriptions import customer_view
    sub = {"_id": ObjectId(), "organization_id": "o1", "status": "active",
           "coupon": {"code": "RIYA10", "discount_amount": 4.9, "partner_id": "p1", "coupon_id": "c1"},
           "partner_pricing": {"name": "Deal", "discount_amount": 3.0, "partner_id": "p1", "pricing_rule_id": "r1"}}
    out = customer_view(sub)
    assert out["coupon"] == {"code": "RIYA10", "discount_amount": 4.9}
    assert out["partner_pricing"] == {"name": "Deal", "discount_amount": 3.0}
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    r = client.post("/api/public/partners/apply", json={"name": "Early Bird", "email": "early@p.example", "password": PW,
                                                        "partner_type": "affiliate", "accepted_terms": True})
    _login(client, "early@p.example", PW, scope="partner")       # signs in while still an applicant
    p = sa.post(f"/api/super-admin/partners/applications/{r.json()['application']['id']}/approve", json={}).json()["partner"]
    rows = sa.get(f"/api/super-admin/partners/activity?partner_id={p['id']}&kind=auth").json()["items"]
    assert any(x["action"] == "login" and x["success"] for x in rows)
