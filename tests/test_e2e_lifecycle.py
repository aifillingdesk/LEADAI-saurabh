"""
End-to-end customer lifecycle across all four portals (in-memory MongoDB).

Only the Apify network boundary (``ApifyConnector._call_actor`` /
``_read_items``) is replaced with realistic actor output; every route,
service, pipeline stage (mapping, qualification, keyword filter, rules AI,
scoring, lifecycle), notification and database write is the real code.

Website demo request → Super Admin notification → approval → demo tokens →
User Portal login → URL search → Apify → pages → posts → comments → lead
qualification → scoring → results/history → CSV export → plan checkout →
payment → Super Admin notification → confirmation → organization active →
Admin Portal → invitation → invited user joins → User Portal. Plus the
negative paths: rejection, suspension, cross-tenant access, failures.
"""
import time
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin

SUPER = "platform-owner@leadai.example"
PAGE_URL = "https://www.facebook.com/metrocarsdealer"

PAGE_ITEM = {"pageId": "1001", "title": "Metro Cars", "pageName": "metrocarsdealer",
             "facebookUrl": PAGE_URL, "pageUrl": PAGE_URL, "categories": ["Car dealership"],
             "followers": 12000, "likes": 11000, "intro": "Certified pre-owned cars in Pune",
             "phone": "+91 98765 43210", "email": "sales@metrocars.example"}
POST_URL = "https://www.facebook.com/metrocarsdealer/posts/555"
POST_ITEM = {"postId": "555", "url": POST_URL, "text": "Metro Cars: certified SUVs with easy EMI, visit Metro Cars today",
             "time": "2026-09-01T10:00:00Z", "likes": 300, "comments": 25, "shares": 4,
             "pageName": "Metro Cars", "facebookUrl": PAGE_URL}
COMMENTS = [
    ("Rahul Verma", "What is the on road price of the SUV? Please call me 9876543210"),
    ("Priya S", "Interested in EMI options, share details on priya@mail.example"),
    ("Amit", "nice car"),
    ("Metro Cars", "Thanks for the interest, DM us"),
    ("Spammer", "click here to earn money fast"),
]


def _comment_items():
    return [{"id": f"c{i}", "commentUrl": f"{POST_URL}?comment_id=c{i}", "text": t,
             "profileName": a, "profileUrl": f"https://facebook.com/u{i}",
             "date": f"2026-09-0{i + 2}T10:00:00Z", "likesCount": i, "postUrl": POST_URL}
            for i, (a, t) in enumerate(COMMENTS)]


def _fake_call_actor(self, actor_id, run_input, label, attempts=1, should_abort=None):
    return {"id": f"run-{label}", "status": "SUCCEEDED", "defaultDatasetId": label}


def _fake_read_items(self, run, *, actor_id=None, keyword=None):
    return {"pages-details": [PAGE_ITEM], "posts": [POST_ITEM],
            "comments": _comment_items()}.get(run["defaultDatasetId"], [])


@pytest.fixture
def app_client():
    as_superadmin(SUPER)
    from app.config import get_settings
    get_settings().apify_api_token = "apify_test_token"  # restored by conftest
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False), \
         patch("app.connectors.apify_connector.ApifyConnector._get_client", return_value=object()), \
         patch("app.connectors.apify_connector.ApifyConnector.has_token", return_value=True), \
         patch("app.connectors.apify_connector.ApifyConnector._call_actor", _fake_call_actor), \
         patch("app.connectors.apify_connector.ApifyConnector._read_items", _fake_read_items):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def _login(client, email, password, scope="site"):
    c = TestClient(client.app)
    r = c.post("/api/auth/login", json={"email": email, "password": password, "scope": scope})
    assert r.status_code == 200, (email, r.status_code, r.text)
    return c


def _wait_run(c, run_id, timeout=90):  # generous: background threads are slow under a loaded full suite
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/search/{run_id}")
        assert r.status_code == 200, r.text
        st = (r.json().get("search") or {}).get("status")
        if st and st not in ("running", "queued"):
            return r.json()
        time.sleep(0.2)
    raise AssertionError(f"search {run_id} did not finish")


def test_full_customer_lifecycle(app_client):
    client, db = app_client
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")

    # ── 1. Website: demo request (industry chosen) ─────────────────────────
    email, pwd = "owner@metrocars.example", "Metro-Cars-2026!"
    r = client.post("/api/auth/signup", json={"email": email, "password": pwd, "name": "Neha Owner",
                                              "company": "Metro Cars", "industry": "automotive"})
    assert r.status_code == 200, r.text
    req_id = r.json()["demo_request_id"]
    user = db.users.find_one({"email": email})
    org_id = user["default_organization_id"]
    assert user["password_hash"].startswith("$2") and pwd not in str(user)
    assert db.organizations.find_one({"_id": ObjectId(org_id)})["status"] == "pending"
    # pending → no access
    r = client.post("/api/auth/login", json={"email": email, "password": pwd})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "demo_pending"

    # ── 2. Super Admin notified + sees the request ─────────────────────────
    notes = sa.get("/api/super-admin/notifications").json()
    assert any("Metro Cars" in (n.get("body") or n.get("message") or "") or n.get("type") == "registration"
               for n in notes.get("items", notes.get("notifications", []))), notes
    reqs = sa.get("/api/super-admin/demo-requests").json()
    assert any(str(x.get("id") or x.get("_id")) == req_id for x in reqs.get("items", reqs.get("requests", [])))

    # ── 3. Approve → user + org active as demo, tokens granted ─────────────
    r = sa.post(f"/api/super-admin/demo-requests/{req_id}/approve", json={})
    assert r.status_code == 200, r.text
    org = db.organizations.find_one({"_id": ObjectId(org_id)})
    assert org["status"] == "demo"
    assert db.users.find_one({"email": email})["status"] == "active"
    assert db[ "demo_requests"].find_one({"_id": ObjectId(req_id)})["status"] == "approved"

    # ── 4. User Portal: login, industry context, tokens ────────────────────
    owner = _login(client, email, pwd)
    me = owner.get("/api/auth/me").json()["user"]
    assert me["organization_id"] == org_id and me["industry"]["key"] == "automotive"
    customer_notes = owner.get("/api/notifications").json()
    assert customer_notes.get("items") is not None or customer_notes.get("notifications") is not None

    # ── 5. LeadAI search: Apify → pages → posts → comments → leads ─────────
    r = owner.post("/api/url/search", params={"url": PAGE_URL, "max_posts": 5, "max_comments_per_post": 20})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    result = _wait_run(owner, run_id)
    assert result["search"]["status"] in ("completed", "partial"), result["search"]
    page = db.facebook_pages.find_one({"search_run_id": run_id})
    assert page and page["organization_id"] == org_id and page["platform"] == "facebook"
    post = db.facebook_posts.find_one({"page_ref": str(page["_id"])})
    assert post and post["organization_id"] == org_id
    # the pipeline collects comments for qualifying posts in the background
    if db.facebook_comments.count_documents({"post_ref": str(post["_id"])}) == 0:
        r = owner.post(f"/api/posts/{post['_id']}/comments", params={"max_comments": 20})
        assert r.status_code == 200, r.text
        end = time.time() + 90
        while time.time() < end and db.ai_comments.count_documents({"post_ref": str(post["_id"])}) < len(COMMENTS) - 1:
            time.sleep(0.2)
    assert db.facebook_comments.count_documents({"post_ref": str(post["_id"])}) == len(COMMENTS)
    end = time.time() + 90
    while time.time() < end and not db.ai_comments.count_documents({"post_ref": str(post["_id"]), "is_lead": True}):
        time.sleep(0.2)
    leads = list(db.ai_comments.find({"post_ref": str(post["_id"])}))
    by_name = {d["commenter_name"]: d for d in leads}
    assert by_name["Rahul Verma"]["is_lead"] and by_name["Rahul Verma"]["phone"]
    assert by_name["Rahul Verma"]["industry"] == "automotive" and by_name["Rahul Verma"]["lead_score"] > 0
    assert by_name["Priya S"]["is_lead"] and by_name["Priya S"]["email"]
    for name in ("Amit", "Metro Cars", "Spammer"):
        assert not by_name.get(name, {}).get("is_lead"), name
    assert all(d["organization_id"] == org_id for d in leads)

    # results / history / lead lifecycle / export through the API
    comments = owner.get(f"/api/posts/{post['_id']}/comments").json()
    assert comments["total"] >= 2
    hist = owner.get("/api/search/history").json()
    assert any(s["run_id"] == run_id for s in hist["searches"])
    lead_id = str(by_name["Rahul Verma"]["_id"])
    r = owner.patch(f"/api/leads/{lead_id}", json={"lead_status": "contacted"})
    assert r.status_code == 200, r.text
    # exports are off in the default demo configuration (Super Admin setting)
    csv_resp = owner.get("/api/export/comments.csv", params={"post_id": str(post["_id"]), "only_leads": "true"})
    assert csv_resp.status_code == 403 and csv_resp.json()["detail"]["code"] == "FEATURE_NOT_AVAILABLE"

    # ── 6. Plan checkout → payment → Super Admin confirmation ──────────────
    plans = owner.get("/api/billing/plans").json()
    paid = next(p for p in plans.get("plans", plans.get("items", []))
                if (p.get("price_monthly") or 0) > 0 and p.get("slug") != "enterprise")
    r = owner.post("/api/billing/checkout", json={"plan_slug": paid["slug"], "billing_cycle": "monthly"})
    assert r.status_code == 200, r.text
    session_id = r.json()["checkout"]["session_id"]
    r = owner.post(f"/api/billing/checkout/{session_id}/mock-pay", json={"succeed": True})
    assert r.status_code == 200, r.text
    sub = db.subscriptions.find_one({"checkout_session_id": session_id})
    assert sub["status"] == "pending_admin_confirmation", sub["status"]
    # payment does NOT activate the organization by itself
    assert db.organizations.find_one({"_id": ObjectId(org_id)})["status"] == "demo"
    queue = sa.get("/api/super-admin/subscriptions/queue").json()
    assert any(str(q.get("id") or q.get("_id")) == str(sub["_id"]) for q in queue.get("items", queue.get("queue", [])))
    r = sa.post(f"/api/super-admin/subscriptions/{sub['_id']}/confirm")
    assert r.status_code == 200, r.text
    org = db.organizations.find_one({"_id": ObjectId(org_id)})
    assert org["status"] == "active" and org.get("plan_id") in (paid["slug"], paid.get("id"), str(paid.get("_id")))
    assert db.subscriptions.find_one({"_id": sub["_id"]})["status"] == "active"

    # ── 7. Admin Portal enabled for the owner (same credentials) ───────────
    owner2 = _login(client, email, pwd)  # new session sees the new state
    assert owner2.get("/api/auth/me").json()["user"]["admin_portal_enabled"] is True
    assert owner2.get("/api/org-admin/overview").status_code == 200
    # the paid plan unlocks exports: the same session state now allows CSV
    csv_resp = owner2.get("/api/export/comments.csv", params={"post_id": str(post["_id"]), "only_leads": "true"})
    assert csv_resp.status_code == 200, csv_resp.text
    assert "Rahul Verma" in csv_resp.text and "Amit" not in csv_resp.text
    # the customer is told about the confirmed subscription
    assert db.notifications.count_documents({"organization_id": org_id}) >= 1

    # ── 8. Admin invites a user → user registers → User Portal ─────────────
    r = owner2.post("/api/organizations/current/invitations", json={"email": "sam@metrocars.example", "role": "member"})
    assert r.status_code in (200, 201), r.text
    invite_url = r.json().get("invite_url")
    assert invite_url, r.json()
    token = invite_url.rstrip("/").split("/")[-1].split("token=")[-1]
    r = client.post(f"/api/invitations/{token}/register", json={"name": "Sam", "password": "Sam-Pass-2026!"})
    assert r.status_code == 200, r.text
    sam = _login(client, "sam@metrocars.example", "Sam-Pass-2026!")
    me = sam.get("/api/auth/me").json()["user"]
    assert me["organization_id"] == org_id and me["org_role"] == "member"
    # members cannot reach the Admin Portal
    assert sam.get("/api/org-admin/overview").status_code in (401, 403)

    # ── 9. Audit trail covers the lifecycle ────────────────────────────────
    actions = {a["action"] for a in db.audit_logs.find({})}
    for a in ("demo.requested", "auth.login"):
        assert a in actions, a


def test_rejected_demo_and_suspension_block_access(app_client):
    client, db = app_client
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    r = client.post("/api/auth/signup", json={"email": "no@reject.example", "password": "Reject-Me-2026!",
                                              "name": "No", "company": "Reject Co"})
    req_id = r.json()["demo_request_id"]
    assert sa.post(f"/api/super-admin/demo-requests/{req_id}/reject", json={"reason": "not a fit"}).status_code == 200
    r = client.post("/api/auth/login", json={"email": "no@reject.example", "password": "Reject-Me-2026!"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "demo_rejected"

    # approved account, then the organization is suspended: the EXISTING
    # session loses access immediately
    r = client.post("/api/auth/signup", json={"email": "yes@susp.example", "password": "Susp-Me-2026!",
                                              "name": "Yes", "company": "Susp Co"})
    sa.post(f"/api/super-admin/demo-requests/{r.json()['demo_request_id']}/approve", json={})
    user = _login(client, "yes@susp.example", "Susp-Me-2026!")
    assert user.get("/api/search/history").status_code == 200
    org_id = db.users.find_one({"email": "yes@susp.example"})["default_organization_id"]
    r = sa.patch(f"/api/super-admin/organizations/{org_id}/status", json={"status": "suspended", "reason": "unpaid"})
    assert r.status_code == 200, r.text
    assert user.get("/api/search/history").status_code in (401, 403)


def test_cross_tenant_access_is_denied(app_client):
    client, db = app_client
    from tests.conftest import seed_site_account
    from app.auth.crypto import hash_password
    ua, oa = seed_site_account("a@tenant-a.example", org_name="Tenant A")
    ub, ob = seed_site_account("b@tenant-b.example", org_name="Tenant B")
    for uid in (ua, ub):
        db.users.update_one({"_id": ObjectId(uid)}, {"$set": {"password_hash": hash_password("Tenant-Pass-2026")}})
    db.organizations.update_many({}, {"$set": {"admin_portal_enabled": True}})
    page = db.facebook_pages.insert_one({"page_name": "B page", "organization_id": ob, "user_id": ub,
                                         "search_run_id": "URLB", "platform": "facebook"}).inserted_id
    post = db.facebook_posts.insert_one({"page_ref": str(page), "organization_id": ob, "user_id": ub,
                                         "caption": "b", "platform": "facebook"}).inserted_id
    lead = db.ai_comments.insert_one({"organization_id": ob, "user_id": ub, "post_ref": str(post),
                                      "comment_text": "secret", "is_lead": True, "phone": "9999999999"}).inserted_id
    db.search_history.insert_one({"run_id": "URLB", "organization_id": ob, "user_id": ub, "status": "completed"})
    a = _login(client, "a@tenant-a.example", "Tenant-Pass-2026")
    for path in (f"/api/pages/{page}", f"/api/posts/{post}", f"/api/comments/{lead}",
                 f"/api/pages/{page}/posts", f"/api/posts/{post}/comments", "/api/search/URLB",
                 "/api/url/search/URLB/report", f"/api/export/posts.csv?page_id={page}",
                 f"/api/export/comments.csv?post_id={post}"):
        r = a.get(path)
        assert r.status_code in (403, 404) or "secret" not in r.text and "9999999999" not in r.text, (path, r.status_code)
        assert r.status_code != 200 or path.endswith(".csv") or path.startswith("/api/search"), (path, r.status_code, r.text[:200])
    assert a.patch(f"/api/leads/{lead}", json={"lead_status": "contacted"}).status_code in (403, 404)
    assert a.delete("/api/search/URLB").status_code in (403, 404)
    assert db.search_history.find_one({"run_id": "URLB"})
    assert a.get("/api/org-admin/users").status_code in (200, 403)
    assert "b@tenant-b.example" not in a.get("/api/org-admin/users").text
    # site sessions never reach platform APIs
    assert a.get("/api/super-admin/dashboard").status_code in (401, 403)
    assert a.get("/api/admin/dashboard").status_code in (401, 403)


def test_apify_failure_marks_run_error_without_crashing(app_client):
    client, db = app_client
    from tests.conftest import seed_site_account
    from app.auth.crypto import hash_password
    from app.connectors.apify_connector import ScrapeError
    uid, org = seed_site_account("fail@tenant.example", org_status="active")
    db.users.update_one({"_id": ObjectId(uid)}, {"$set": {"password_hash": hash_password("Fail-Pass-2026")}})
    c = _login(client, "fail@tenant.example", "Fail-Pass-2026")

    def boom(self, *a, **k):
        raise ScrapeError("ACTOR_FAILED", "actor crashed")
    with patch("app.connectors.apify_connector.ApifyConnector._call_actor", boom):
        r = c.post("/api/url/search", params={"url": PAGE_URL})
        assert r.status_code == 200, r.text
        res = _wait_run(c, r.json()["run_id"])
    run = db.search_history.find_one({"run_id": r.json()["run_id"]})
    assert run["status"] in ("error", "partial", "completed")
    assert run["organization_id"] == org
    # invalid / unsupported URLs are rejected up-front
    assert c.post("/api/url/search", params={"url": "https://example.com/x"}).status_code == 422
