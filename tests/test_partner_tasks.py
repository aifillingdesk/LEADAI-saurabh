"""
Partner tasks: the Super Admin assigns work, the partner does it in the
Partner Portal and reports back, the Super Admin approves or sends it back.
The whole journey starts from a real application on the public website.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin

SUPER = "platform-owner@leadai.example"
PW = "Partner-Pass-2026"
TOMORROW = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()


@pytest.fixture
def env():
    as_superadmin(SUPER)
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


def _partner(client, sa, email, *, ptype="affiliate", name="P"):
    r = client.post("/api/public/partners/apply", json={
        "name": name, "email": email, "password": PW, "company": name + " Co", "partner_type": ptype,
        "experience": "Ten years of SaaS sales", "promotion_plan": "Newsletter", "accepted_terms": True})
    assert r.status_code == 200, r.text
    r = sa.post(f"/api/super-admin/partners/applications/{r.json()['application']['id']}/approve",
                json={"partner_type": ptype})
    assert r.status_code == 200, r.text
    return r.json()["partner"], _login(client, email, PW, scope="partner")


def test_apply_approve_assign_do_and_approve_the_task(env):
    client, db = env
    # 1. someone applies on the website and signs in: no tasks before approval
    r = client.post("/api/public/partners/apply", json={
        "name": "Raj", "email": "raj@resell.example", "password": PW, "company": "Raj Digital",
        "partner_type": "reseller", "experience": "Agency", "promotion_plan": "Clients", "accepted_terms": True})
    assert r.status_code == 200, r.text
    app_id = r.json()["application"]["id"]
    applicant = _login(client, "raj@resell.example", PW, scope="partner")
    assert applicant.get("/api/partner/v1/tasks").status_code == 403
    # 2. the Super Admin approves
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    r = sa.post(f"/api/super-admin/partners/applications/{app_id}/approve", json={"partner_type": "reseller"})
    assert r.status_code == 200, r.text
    pid = r.json()["partner"]["id"]
    # 3. the Super Admin assigns a task
    r = sa.post("/api/super-admin/partners/tasks", json={
        "audience": "partner", "partner_id": pid, "title": "Sell 3 Starter plans", "details": "Use your link.",
        "due_at": TOMORROW, "priority": "high", "section": "sell"})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    tid = r.json()["tasks"][0]["id"]
    # 4. the partner (same session as before approval) sees it, with a notification and a nav count
    me = applicant.get("/api/partner/v1/me").json()["me"]
    assert me["open_tasks"] == 1
    items = applicant.get("/api/partner/v1/tasks").json()["items"]
    assert [t["title"] for t in items] == ["Sell 3 Starter plans"]
    assert items[0]["priority"] == "high" and items[0]["section"] == "sell" and not items[0]["overdue"]
    assert items[0]["due_date"] == TOMORROW   # the day picked, independent of timezone
    notes = applicant.get("/api/partner/v1/notifications").json()["items"]
    assert any(n["title"] == "New task from LeadAI" for n in notes)
    # 5. start, then submit (a note is required)
    assert applicant.post(f"/api/partner/v1/tasks/{tid}/start", json={}).json()["task"]["status"] == "in_progress"
    assert applicant.post(f"/api/partner/v1/tasks/{tid}/submit", json={"note": ""}).status_code == 422
    r = applicant.post(f"/api/partner/v1/tasks/{tid}/submit", json={"note": "Sold to Green Gym and 2 more"})
    assert r.status_code == 200 and r.json()["task"]["status"] == "submitted"
    assert db.notifications.find_one({"audience": "super_admin", "type": "partner_task"})
    # 6. the Super Admin sends it back, the partner resubmits, the Super Admin approves
    assert sa.post(f"/api/super-admin/partners/tasks/{tid}/send_back", json={"note": ""}).status_code == 422
    r = sa.post(f"/api/super-admin/partners/tasks/{tid}/send_back", json={"note": "Add the customer names"})
    assert r.json()["task"]["status"] == "in_progress"
    applicant.post(f"/api/partner/v1/tasks/{tid}/submit", json={"note": "Green Gym, Blue Cafe, Red Salon"})
    r = sa.post(f"/api/super-admin/partners/tasks/{tid}/approve", json={"note": "Great work"})
    assert r.status_code == 200 and r.json()["task"]["status"] == "done"
    t = applicant.get(f"/api/partner/v1/tasks/{tid}").json()["task"]
    assert [h["status"] for h in t["history"]] == ["open", "in_progress", "submitted", "in_progress", "submitted", "done"]
    assert t["completed_at"] and t["review_note"] == "Great work"
    assert applicant.get("/api/partner/v1/me").json()["me"]["open_tasks"] == 0
    # 7. a closed task can't move again; every step is in the audit log
    assert applicant.post(f"/api/partner/v1/tasks/{tid}/submit", json={"note": "again"}).status_code == 409
    actions = {a["action"] for a in db.audit_logs.find({"details.partner_id": pid, "action": {"$regex": "^partner.task"}})}
    assert actions == {"partner.task.assigned", "partner.task.started", "partner.task.submitted",
                       "partner.task.sent_back", "partner.task.approved"}
    detail = sa.get(f"/api/super-admin/partners/{pid}").json()["partner"]
    assert detail["tasks"]["done"] == 1


def test_broadcast_edit_cancel_and_overdue(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    _, pa = _partner(client, sa, "aff@tasks.example", name="Aff")
    _, pr = _partner(client, sa, "res@tasks.example", ptype="reseller", name="Res")
    r = sa.post("/api/super-admin/partners/tasks", json={"audience": "reseller", "title": "Upload your logo"})
    assert r.json()["created"] == 1
    assert pr.get("/api/partner/v1/tasks").json()["total"] == 1 and pa.get("/api/partner/v1/tasks").json()["total"] == 0
    r = sa.post("/api/super-admin/partners/tasks", json={"audience": "all", "title": "Read the new pricing"})
    assert r.json()["created"] == 2 and r.json()["batch_id"]
    tid = pa.get("/api/partner/v1/tasks").json()["items"][0]["id"]
    # edit while open
    r = sa.patch(f"/api/super-admin/partners/tasks/{tid}", json={"title": "Read the October pricing", "due_at": TOMORROW})
    assert r.status_code == 200 and r.json()["task"]["title"] == "Read the October pricing"
    # validation
    assert sa.post("/api/super-admin/partners/tasks", json={"audience": "all", "title": ""}).status_code == 422
    assert sa.post("/api/super-admin/partners/tasks", json={"audience": "all", "title": "x",
                                                            "due_at": "2001-01-01"}).status_code == 422
    assert sa.post("/api/super-admin/partners/tasks", json={"audience": "partner", "title": "x"}).status_code == 404
    # overdue = past due and not yet submitted

    db.partner_tasks.update_one({"_id": __import__("bson").ObjectId(tid)},
                                {"$set": {"due_at": datetime(2020, 1, 1, tzinfo=timezone.utc)}})
    assert pa.get(f"/api/partner/v1/tasks/{tid}").json()["task"]["overdue"] is True
    listing = sa.get("/api/super-admin/partners/tasks?overdue=true").json()
    assert listing["total"] == 1 and listing["counts"]["overdue"] == 1
    # cancel needs a reason; a cancelled task is closed for the partner
    assert sa.post(f"/api/super-admin/partners/tasks/{tid}/cancel", json={"note": ""}).status_code == 422
    assert sa.post(f"/api/super-admin/partners/tasks/{tid}/cancel", json={"note": "No longer needed"}).status_code == 200
    assert pa.post(f"/api/partner/v1/tasks/{tid}/start", json={}).status_code == 409
    assert sa.get("/api/super-admin/partners/overview").json()["overview"]["tasks_open"] == 2


def test_tasks_are_private_to_each_partner_and_read_only_by_api_key(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    a, pa = _partner(client, sa, "one@tasks.example", name="One")
    b, pb = _partner(client, sa, "two@tasks.example", name="Two")
    tid = sa.post("/api/super-admin/partners/tasks", json={"audience": "partner", "partner_id": a["id"],
                                                           "title": "Private"}).json()["tasks"][0]["id"]
    # partner B can't see or act on partner A's task — and the probe is flagged
    assert pb.get(f"/api/partner/v1/tasks/{tid}").status_code == 404
    assert pb.post(f"/api/partner/v1/tasks/{tid}/submit", json={"note": "mine"}).status_code == 404
    assert db.partner_fraud_flags.find_one({"type": "cross_partner_access", "partner_id": b["id"]})
    assert pb.get("/api/partner/v1/tasks").json()["total"] == 0
    # customers and staff can't reach the endpoints
    assert TestClient(client.app).get("/api/partner/v1/tasks").status_code == 401
    # a partner API key can read tasks but not change them
    sa.patch(f"/api/super-admin/partners/{a['id']}", json={"permissions": a["permissions"] + ["api.access"]})
    key = pa.post("/api/partner/v1/api-keys", json={"name": "k"}).json()["api_key"]
    k = TestClient(client.app)
    assert k.get("/api/partner/v1/tasks", headers={"X-API-Key": key}).json()["total"] == 1
    assert k.post(f"/api/partner/v1/tasks/{tid}/start", json={}, headers={"X-API-Key": key}).status_code == 403
    # suspended partners can't be assigned work
    sa.post(f"/api/super-admin/partners/{b['id']}/suspend", json={"reason": "test"})
    assert sa.post("/api/super-admin/partners/tasks", json={"audience": "partner", "partner_id": b["id"],
                                                            "title": "x"}).status_code == 409
