"""
Administrators changing other people's sign-in details (email / password):

* the Organization Admin, for the members of their own organization
  (owner protected, admins only by the owner, never accounts used elsewhere)
* the Super Admin, for organization owners / admins / users, partners and
  platform staff (admin_users)
* an administrator-set password is temporary: the person must choose their
  own before anything else works, in every portal
"""
from datetime import datetime
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin

SUPER = "platform-owner@leadai.example"
PW = "Member-Pass-2026"
NEW_PW = "Temp-Pass-2026x"


@pytest.fixture
def env():
    as_superadmin(SUPER)
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def _org(db, name):
    return str(db.organizations.insert_one({"name": name, "slug": name.lower().replace(" ", "-"), "status": "active",
                                            "admin_portal_enabled": True, "settings": {},
                                            "created_at": datetime.utcnow()}).inserted_id)


def _user(db, email, org_id, role):
    uid = str(db.users.insert_one({"email": email, "name": email.split("@")[0], "status": "active",
                                   "password_hash": hash_password(PW), "default_organization_id": org_id,
                                   "created_at": datetime.utcnow()}).inserted_id)
    db.organization_members.insert_one({"organization_id": org_id, "user_id": uid, "email": email, "role": role,
                                        "status": "active", "permissions_override": {},
                                        "created_at": datetime.utcnow()})
    return uid


def _login(client, email, password, scope="site", expect=200):
    c = TestClient(client.app)
    r = c.post("/api/auth/login", json={"email": email, "password": password, "scope": scope})
    assert r.status_code == expect, (email, r.text)
    return c, (r.json() if r.status_code == 200 else None)


@pytest.fixture
def team(env):
    client, db = env
    org = _org(db, "Acme")
    ids = {k: _user(db, f"{k}@acme.example", org, role) for k, role in
           (("owner", "owner"), ("admin", "admin"), ("admin2", "admin"), ("member", "member"))}
    return client, db, org, ids


def test_org_admin_sets_a_temporary_password_and_the_member_must_choose_their_own(team):
    client, db, org, ids = team
    member_old, _ = _login(client, "member@acme.example", PW)
    admin, _ = _login(client, "admin@acme.example", PW)
    # weak passwords are refused
    assert admin.post(f"/api/org-admin/users/{ids['member']}/password", json={"password": "short"}).status_code == 422
    r = admin.post(f"/api/org-admin/users/{ids['member']}/password", json={"password": NEW_PW})
    assert r.status_code == 200, r.text
    assert r.json()["must_change_password"] is True and r.json()["sessions_revoked"] >= 1
    assert "password" not in {k for k in r.json() if k != "must_change_password"}
    # signed out everywhere; the old password is dead
    assert member_old.get("/api/auth/me").status_code == 401
    _login(client, "member@acme.example", PW, expect=401)
    # the notice never contains the password
    mail = db.email_outbox.find_one({"to": "member@acme.example", "subject": "Your LeadAI password was changed"})
    assert mail and NEW_PW not in str(mail)
    # signing in with the temporary password: only the change-password page works
    m, body = _login(client, "member@acme.example", NEW_PW)
    assert body["must_change_password"] is True and body["redirect"] == "/change-password"
    assert m.get("/api/auth/me").json()["user"]["must_change_password"] is True
    blocked = m.get("/api/search/history")
    assert blocked.status_code == 403 and blocked.json()["error"] == "password_change_required"
    page = m.get("/dashboard", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/change-password"
    assert m.get("/change-password").status_code == 200
    # same password again is refused; then they choose their own
    assert m.post("/api/auth/password/change", json={"current_password": NEW_PW,
                                                      "new_password": NEW_PW}).status_code == 422
    r = m.post("/api/auth/password/change", json={"current_password": NEW_PW, "new_password": "Own-Choice-2026"})
    assert r.status_code == 200, r.text
    assert m.get("/api/search/history").status_code == 200          # same session, unrestricted now
    assert not db.users.find_one({"_id": ObjectId(ids["member"])}).get("must_change_password")
    assert db.audit_logs.find_one({"action": "member.password_set", "resource_id": ids["member"]})


def test_org_admin_can_set_a_permanent_password_without_the_change_step(team):
    client, db, org, ids = team
    admin, _ = _login(client, "admin@acme.example", PW)
    r = admin.post(f"/api/org-admin/users/{ids['member']}/password",
                   json={"password": NEW_PW, "must_change": False, "notify": False})
    assert r.status_code == 200 and r.json()["must_change_password"] is False
    m, body = _login(client, "member@acme.example", NEW_PW)
    assert "must_change_password" not in body
    assert m.get("/api/search/history").status_code == 200


def test_org_admin_changes_a_members_sign_in_email(team):
    client, db, org, ids = team
    db.users.update_one({"_id": ObjectId(ids["member"])}, {"$set": {"email_verified": True}})
    admin, _ = _login(client, "admin@acme.example", PW)
    assert admin.patch(f"/api/org-admin/users/{ids['member']}/email", json={"email": "nope"}).status_code == 422
    assert admin.patch(f"/api/org-admin/users/{ids['member']}/email",
                       json={"email": "owner@acme.example"}).status_code == 409          # in use
    assert admin.patch(f"/api/org-admin/users/{ids['member']}/email",
                       json={"email": SUPER}).status_code == 409                          # the Super Admin's
    r = admin.patch(f"/api/org-admin/users/{ids['member']}/email", json={"email": "New.Member@Acme.example"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "new.member@acme.example"
    _login(client, "member@acme.example", PW, expect=401)
    _login(client, "new.member@acme.example", PW)
    u = db.users.find_one({"_id": ObjectId(ids["member"])})
    assert u["email_verified"] is False
    assert db.organization_members.find_one({"user_id": ids["member"]})["email"] == "new.member@acme.example"
    told = {m["to"] for m in db.email_outbox.find({"subject": "Your LeadAI sign-in email was changed"})}
    assert {"member@acme.example", "new.member@acme.example"} <= told
    assert db.audit_logs.find_one({"action": "member.email_changed", "resource_id": ids["member"]})


def test_org_admin_limits_owner_admins_self_and_shared_accounts(team):
    client, db, org, ids = team
    admin, _ = _login(client, "admin@acme.example", PW)
    owner, _ = _login(client, "owner@acme.example", PW)
    for target in ("owner", "admin2", "admin"):            # the owner; another admin; themselves
        assert admin.post(f"/api/org-admin/users/{ids[target]}/password", json={"password": NEW_PW}).status_code == 403
        assert admin.patch(f"/api/org-admin/users/{ids[target]}/email", json={"email": "x@y.example"}).status_code == 403
    # only the owner manages admins
    assert owner.post(f"/api/org-admin/users/{ids['admin2']}/password", json={"password": NEW_PW}).status_code == 200
    # an account that is also used elsewhere: another org, a partner, platform staff
    other = _org(db, "Other Co")
    db.organization_members.insert_one({"organization_id": other, "user_id": ids["member"], "role": "member",
                                        "status": "active"})
    r = admin.post(f"/api/org-admin/users/{ids['member']}/password", json={"password": NEW_PW})
    assert r.status_code == 409 and "outside your organization" in r.json()["detail"]
    db.organization_members.delete_one({"organization_id": other, "user_id": ids["member"]})
    db.partners.insert_one({"user_id": ids["member"], "email": "member@acme.example", "status": "active"})
    assert admin.patch(f"/api/org-admin/users/{ids['member']}/email", json={"email": "z@z.example"}).status_code == 409
    db.partners.delete_many({})
    # another organization's member is invisible
    stranger = _user(db, "stranger@other.example", other, "member")
    assert admin.post(f"/api/org-admin/users/{stranger}/password", json={"password": NEW_PW}).status_code == 404
    # a plain member has no access at all
    m, _ = _login(client, "member@acme.example", PW)
    assert m.post(f"/api/org-admin/users/{ids['admin']}/password", json={"password": NEW_PW}).status_code == 403


def test_super_admin_manages_owners_partners_and_staff(team):
    client, db, org, ids = team
    sa, _ = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    # an organization owner (the org admin can't touch them; the Super Admin can)
    r = sa.post(f"/api/super-admin/users/{ids['owner']}/password", json={"password": NEW_PW, "reason": "lost access"})
    assert r.status_code == 200, r.text
    _, body = _login(client, "owner@acme.example", NEW_PW)
    assert body["must_change_password"] is True
    r = sa.patch(f"/api/super-admin/users/{ids['owner']}/email", json={"email": "boss@acme.example"})
    assert r.status_code == 200 and r.json()["email"] == "boss@acme.example"
    assert db.audit_logs.find_one({"action": "user.password_set", "resource_id": ids["owner"]})
    # 2FA seeds never leave the server
    db.users.update_one({"_id": ObjectId(ids["owner"])}, {"$set": {"totp_secret": "JBSWY3DPEHPK3PXP"}})
    assert "totp_secret" not in sa.get(f"/api/super-admin/users/{ids['owner']}").json()["user"]
    assert all("totp_secret" not in u for u in sa.get("/api/super-admin/users").json()["users"])
    # a partner: their Partner Portal sign-in, and every copy of their email
    from app.partners import service as PS
    PS.ensure_program_defaults(db)
    app_ref = PS.submit_application({"name": "Pat", "email": "pat@partner.example", "password": PW,
                                     "accepted_terms": True}, ip="1.1.1.1")
    partner = PS.approve_application(app_ref["id"], actor="test")
    r = sa.patch(f"/api/super-admin/partners/{partner['id']}/email", json={"email": "pat.new@partner.example"})
    assert r.status_code == 200, r.text
    assert db.partners.find_one({"_id": ObjectId(partner["id"])})["email"] == "pat.new@partner.example"
    assert db.partner_applications.find_one({"_id": ObjectId(app_ref["id"])})["email"] == "pat.new@partner.example"
    assert sa.post(f"/api/super-admin/partners/{partner['id']}/password",
                   json={"password": NEW_PW}).status_code == 200
    p, body = _login(client, "pat.new@partner.example", NEW_PW, scope="partner")
    assert body["must_change_password"] is True
    assert p.get("/api/partner/v1/dashboard").status_code == 403
    assert p.post("/api/auth/password/change", json={"current_password": NEW_PW,
                                                      "new_password": "Partner-Own-2026"}).status_code == 200
    assert p.get("/api/partner/v1/dashboard").status_code == 200
    # platform staff (admin_users): listed, password + email, sign-in honours the flag
    sid = str(db.admin_users.insert_one({"email": "ops@leadai.example", "name": "Ops", "role": "manager",
                                         "enabled": True, "password_hash": hash_password(PW),
                                         "created_at": datetime.utcnow()}).inserted_id)
    staff = sa.get("/api/super-admin/staff").json()["items"]
    assert any(s["id"] == sid for s in staff) and all("password_hash" not in s for s in staff)
    assert sa.post(f"/api/super-admin/staff/{sid}/password", json={"password": NEW_PW}).status_code == 200
    st, body = _login(client, "ops@leadai.example", NEW_PW, scope="admin")
    assert body["must_change_password"] is True
    assert st.get("/api/admin/security").status_code == 403
    assert st.post("/api/auth/password/change", json={"current_password": NEW_PW,
                                                       "new_password": "Staff-Own-2026"}).status_code == 200
    assert st.get("/api/admin/security").status_code == 200
    r = sa.patch(f"/api/super-admin/staff/{sid}/email", json={"email": "ops2@leadai.example"})
    assert r.status_code == 200 and db.admin_users.find_one({"_id": ObjectId(sid)})["email"] == "ops2@leadai.example"
    assert st.get("/api/admin/security").status_code == 401          # signed out by the email change


def test_nobody_can_change_the_env_super_admin_or_reach_these_routes_without_rights(team):
    client, db, org, ids = team
    from app.auth.credentials import change_email
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        change_email("user", ids["member"], SUPER, actor_email="x@y.example", by_label="test")
    assert e.value.status_code == 409
    owner, _ = _login(client, "owner@acme.example", PW)
    assert owner.post(f"/api/super-admin/users/{ids['member']}/password", json={"password": NEW_PW}).status_code in (401, 403)
    assert TestClient(client.app).post(f"/api/org-admin/users/{ids['member']}/password",
                                       json={"password": NEW_PW}).status_code == 401
