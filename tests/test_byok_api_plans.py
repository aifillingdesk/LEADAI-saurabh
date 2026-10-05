"""
API coverage plans: per external API (Apify, Gemini) an organization either
uses LeadAI's ("leadai", included in the plan price) or brings its own key
("own", cheaper). Any mix; the price follows the choice.

Covers pricing, checkout/confirmation, encrypted write-only keys, no silent
fallback to LeadAI's keys, the switching rules, token charging, per-org
isolation of Gemini failures, Super Admin controls, the data migration and
the plan editor.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin

SUPER = "platform-owner@leadai.example"
PW = "Member-Pass-2026"
RAW_APIFY = "apify_api_SECRETvalue1234567890"
RAW_GEMINI = "AIzaSySECRETgeminiKEY0987654321"


@pytest.fixture
def env():
    as_superadmin(SUPER)
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            from app.services import secret_box
            secret_box.reset_cache()
            yield client, get_sync_db()


def _org(db, name="Acme", plan="starter", status="active"):
    return str(db.organizations.insert_one({"name": name, "slug": name.lower(), "status": status, "plan_id": plan,
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


def _login(client, email, password, scope="site"):
    c = TestClient(client.app)
    r = c.post("/api/auth/login", json={"email": email, "password": password, "scope": scope})
    assert r.status_code == 200, r.text
    return c


def _paid_sub(db, org_id, amount=49.0, coverage=None, plan="starter"):
    now = datetime.now(timezone.utc)
    return db.subscriptions.insert_one({
        "organization_id": org_id, "plan_id": plan, "status": "active", "billing_cycle": "monthly",
        "amount": amount, "currency": "USD", "api_coverage": coverage or {"apify": "leadai", "gemini": "leadai"},
        "current_period_start": now - timedelta(days=29), "current_period_end": now - timedelta(minutes=5),
        "created_at": now}).inserted_id


VALID = AsyncMock(return_value={"valid": True, "message": "ok"})


# ── pricing ──────────────────────────────────────────────────────────────────

def test_price_grid_follows_who_provides_each_api(env):
    from app.billing.plans import DEFAULT_PLANS, coverage_options, price_for
    plans = {p["slug"]: p for p in DEFAULT_PLANS}
    grid = {(o["key"]): (o["price_monthly"], o["price_yearly"]) for o in coverage_options(plans["starter"])}
    assert grid == {"all_included": (49.0, 490.0), "own_apify": (37.0, 370.0),
                    "own_gemini": (41.0, 410.0), "own_both": (29.0, 290.0)}
    assert price_for(plans["pro"], {"apify": "own", "gemini": "own"}, "monthly") == 89.0
    assert price_for(plans["enterprise"], {"apify": "own"}, "yearly") == 7590.0
    # Free has no own-key options and never gets cheaper
    assert [o["key"] for o in coverage_options(plans["free"])] == ["all_included"]
    assert price_for(plans["free"], {"apify": "own", "gemini": "own"}, "monthly") == 0.0
    # the public pricing API carries every option
    rows = {p["slug"]: p for p in env[0].get("/api/public/pricing").json()["plans"]}
    assert len(rows["starter"]["coverage_options"]) == 4 and rows["starter"]["allows_own_keys"] is True


def test_checkout_charges_the_choice_and_confirmation_applies_it(env):
    from app.billing.subscriptions import confirm_subscription, start_checkout
    client, db = env
    org = _org(db, status="demo")
    started = asyncio.run(start_checkout(org, "starter", "monthly", actor={"email": "o@x.example"},
                                         api_coverage={"apify": "own", "gemini": "leadai"}))
    sub = started["subscription"]
    assert sub["amount"] == 37.0 and sub["api_coverage"] == {"apify": "own", "gemini": "leadai"}
    from app.billing.subscriptions import record_payment_verified
    asyncio.run(record_payment_verified(str(sub["_id"]), provider="mock", provider_payment_id="pay_1", amount=37.0,
                                         currency="USD", source="test"))
    asyncio.run(confirm_subscription(str(sub["_id"]), actor={"email": SUPER}))
    o = db.organizations.find_one({"_id": ObjectId(org)})
    assert o["api_coverage"] == {"apify": "own", "gemini": "leadai"} and "api_mode" not in o
    # the older all-or-nothing switch still works: "byok" = own for both
    legacy = asyncio.run(start_checkout(org, "pro", "yearly", actor={"email": "o@x.example"}, api_mode="byok"))
    assert legacy["subscription"]["amount"] == 890.0
    # a plan without own-key options refuses them
    db.plans.update_one({"slug": "starter"}, {"$set": {"allows_byok": False}})
    from app.billing.plans import invalidate_plan_cache
    invalidate_plan_cache()
    with pytest.raises(Exception) as e:
        asyncio.run(start_checkout(org, "starter", "monthly", actor={"email": "o@x.example"},
                                   api_coverage={"apify": "own"}))
    assert getattr(e.value, "status_code", None) == 422


# ── keys: encrypted, write-only ──────────────────────────────────────────────

def test_keys_are_encrypted_and_never_leave_the_server(env):
    client, db = env
    org = _org(db)
    _user(db, "owner@acme.example", org, "owner")
    owner = _login(client, "owner@acme.example", PW)
    with patch("app.services.tenant_api_keys.test_apify_token_connection", VALID), \
            patch("app.services.tenant_api_keys.test_gemini_key_connection", VALID):
        assert owner.put("/api/org-admin/integrations/api-keys/apify", json={"key": RAW_APIFY}).status_code == 200
        r = owner.put("/api/org-admin/integrations/api-keys/gemini", json={"key": RAW_GEMINI})
    assert r.status_code == 200 and r.json()["config"]["keys"]["gemini"]["verified"] is True
    doc = db.organizations.find_one({"_id": ObjectId(org)})
    stored = json.dumps(doc, default=str)
    assert RAW_APIFY not in stored and RAW_GEMINI not in stored
    assert doc["custom_api_keys"]["apify"]["ciphertext"].startswith("fernet:")
    # no response anywhere carries the key or its ciphertext
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    bodies = [owner.get("/api/org-admin/integrations/api-keys").text, owner.get("/api/organizations/current").text,
              owner.get("/api/org-admin/context").text, sa.get(f"/api/super-admin/organizations/{org}").text,
              sa.get(f"/api/super-admin/organizations/{org}/api-coverage").text]
    for b in bodies:
        assert RAW_APIFY not in b and RAW_GEMINI not in b and "fernet:" not in b, b[:200]
    info = owner.get("/api/org-admin/integrations/api-keys").json()["integrations"]
    assert info["keys"]["apify"]["hint"].startswith("apif") and info["keys"]["apify"]["configured"] is True
    # audit entries name the API, never the key
    audit = json.dumps(list(db.audit_logs.find({"action": "api_key.saved"})), default=str)
    assert "apify" in audit and RAW_APIFY not in audit
    # a member can't manage keys
    _user(db, "member@acme.example", org, "member")
    member = _login(client, "member@acme.example", PW)
    assert member.put("/api/org-admin/integrations/api-keys/apify", json={"key": RAW_APIFY}).status_code == 403


def test_own_key_is_used_and_never_replaced_by_leadais(env):
    from app.services.tenant_api_keys import resolve_api_key, save_key, set_coverage
    client, db = env
    org = _org(db)
    with patch("app.admin.settings.get_apify_token", return_value="PLATFORM-APIFY"):
        assert resolve_api_key(org, "apify") == ("PLATFORM-APIFY", "platform")
        asyncio.run(set_coverage(org, {"apify": "own", "gemini": "leadai"}))
        assert resolve_api_key(org, "apify") == ("", "organization_missing")      # no fallback
        with patch("app.services.tenant_api_keys.test_apify_token_connection", VALID):
            asyncio.run(save_key(org, "apify", RAW_APIFY, actor_email="o@x.example"))
        assert resolve_api_key(org, "apify") == (RAW_APIFY, "organization")
    # Gemini own but missing: comments fall back to rules (no LeadAI key), admins are told
    _user(db, "owner@acme.example", org, "owner")
    asyncio.run(set_coverage(org, {"apify": "own", "gemini": "own"}))
    from app.pipeline import comment_ai
    with patch.object(comment_ai, "_get_gemini_client") as gc, \
            patch("app.admin.envvars.get_envvar_str", return_value="PLATFORM-GEMINI"):
        res = comment_ai.analyze_comment_ai("What is the price? Please call me 9876543210", organization_id=org)
        assert gc.call_count == 0
    assert res["analyzed_by"] == "rules" and "own Gemini key is missing" in res["reason"]
    assert db.notifications.find_one({"type": "api_key_problem"})


def test_one_organizations_gemini_failure_does_not_pause_ai_for_everyone(env):
    from app.pipeline import comment_ai
    from app.services.tenant_api_keys import save_key, set_coverage
    client, db = env
    org = _org(db)
    _user(db, "owner@acme.example", org, "owner")
    asyncio.run(set_coverage(org, {"gemini": "own"}))
    with patch("app.services.tenant_api_keys.test_gemini_key_connection", VALID):
        asyncio.run(save_key(org, "gemini", RAW_GEMINI, actor_email="o@x.example"))
    resp = MagicMock(status_code=429)
    client_mock = MagicMock()
    client_mock.post.return_value = resp
    before = comment_ai._GEMINI_DISABLED_UNTIL
    with patch.object(comment_ai, "_get_gemini_client", return_value=client_mock):
        with pytest.raises(RuntimeError):
            comment_ai._call_gemini("sys", "user", organization_id=org)
    assert comment_ai._GEMINI_DISABLED_UNTIL == before                 # LeadAI's shared breaker untouched
    assert comment_ai._ORG_GEMINI_COOLDOWN.get(org, 0) > 0            # only this org pauses
    sent_key = client_mock.post.call_args.kwargs["headers"]["x-goog-api-key"]
    assert sent_key == RAW_GEMINI
    doc = db.organizations.find_one({"_id": ObjectId(org)})
    assert doc["custom_api_keys"]["gemini"]["last_error_code"] == "OWN_GEMINI_QUOTA_EXCEEDED"
    comment_ai._ORG_GEMINI_COOLDOWN.pop(org, None)


# ── switching ────────────────────────────────────────────────────────────────

def test_switching_rules(env):
    from app.billing.api_coverage import renewal_amount
    client, db = env
    org = _org(db)
    _user(db, "owner@acme.example", org, "owner")
    owner = _login(client, "owner@acme.example", PW)
    url = "/api/org-admin/integrations/api-coverage"
    # own key needs a verified key first
    r = owner.put(url, json={"apify": "own"})
    assert r.status_code == 409 and "Add and verify" in r.json()["detail"]
    with patch("app.services.tenant_api_keys.test_apify_token_connection", VALID):
        owner.put("/api/org-admin/integrations/api-keys/apify", json={"key": RAW_APIFY})
    # no paid subscription: applies at once
    r = owner.put(url, json={"apify": "own"})
    assert r.status_code == 200 and r.json()["status"] == "applied"
    assert r.json()["integrations"]["coverage"] == {"apify": "own", "gemini": "leadai"}
    owner.put(url, json={"apify": "leadai"})
    # paid subscription (all included, 49): own key now, lower price at renewal
    sub_id = _paid_sub(db, org, 49.0)
    r = owner.put(url, json={"apify": "own"})
    assert r.json()["status"] == "scheduled" and "37.00" in r.json()["message"]
    assert db.organizations.find_one({"_id": ObjectId(org)})["api_coverage"]["apify"] == "own"
    sub = db.subscriptions.find_one({"_id": sub_id})
    assert renewal_amount(db, sub, 49.0) == 37.0
    sub = db.subscriptions.find_one({"_id": sub_id})
    assert sub["api_coverage"] == {"apify": "own", "gemini": "leadai"} and sub["amount"] == 37.0
    # back to LeadAI-provided costs more: checkout, nothing changes until paid
    r = owner.put(url, json={"apify": "leadai"})
    body = r.json()
    assert body["status"] == "checkout_required" and body["new_price"] == 49.0
    assert db.organizations.find_one({"_id": ObjectId(org)})["api_coverage"]["apify"] == "own"
    assert db.audit_logs.find_one({"action": "organization.api_coverage_changed"})
    # renewal through the lifecycle keeps a partner/coupon discount (only the difference comes off)
    sub2 = _paid_sub(db, _org(db, "Other"), 44.10)
    other = str(db.subscriptions.find_one({"_id": sub2})["organization_id"])
    db.organizations.update_one({"_id": ObjectId(other)}, {"$set": {"api_coverage": {"apify": "own", "gemini": "own"}}})
    assert renewal_amount(db, db.subscriptions.find_one({"_id": sub2}), 44.10) == 24.10


def test_tokens_charge_only_what_leadai_pays_for(env):
    from app.billing.api_coverage import token_cost_for
    from app.services.tenant_api_keys import set_coverage
    client, db = env
    org = _org(db)
    assert token_cost_for(org, "search", 10) == 10
    asyncio.run(set_coverage(org, {"apify": "own", "gemini": "leadai"}))
    assert token_cost_for(org, "search", 10) == 4          # Gemini share only
    assert token_cost_for(org, "collect", 10) == 0         # Apify only, theirs
    asyncio.run(set_coverage(org, {"apify": "own", "gemini": "own"}))
    assert token_cost_for(org, "search", 10) == 0
    assert token_cost_for(org, "export", 3) == 3           # no external API


# ── Super Admin, migration, plan editor ──────────────────────────────────────

def test_super_admin_controls(env):
    client, db = env
    org = _org(db)
    _user(db, "owner@acme.example", org, "owner")
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    url = f"/api/super-admin/organizations/{org}/api-coverage"
    assert sa.put(url, json={"apify": "own", "force": True}).status_code == 422          # reason required
    # even a courtesy switch can't put an API on "own" with no key saved (searches would stop)
    assert sa.put(url, json={"apify": "own", "force": True, "reason": "courtesy setup"}).status_code == 409
    with patch("app.services.tenant_api_keys.test_apify_token_connection", VALID),             patch("app.services.tenant_api_keys.test_gemini_key_connection", VALID):
        from app.services.tenant_api_keys import save_key
        asyncio.run(save_key(org, "apify", RAW_APIFY, actor_email="o@x.example"))
        asyncio.run(save_key(org, "gemini", RAW_GEMINI, actor_email="o@x.example"))
    r = sa.put(url, json={"apify": "own", "gemini": "own", "force": True, "reason": "courtesy setup"})
    assert r.status_code == 200 and r.json()["coverage"] == {"apify": "own", "gemini": "own"}
    key_url = f"/api/super-admin/organizations/{org}/api-keys/apify"
    assert sa.request("DELETE", key_url, json={}).status_code == 422                       # reason required
    r = sa.request("DELETE", key_url, json={"reason": "leaked"})
    assert r.status_code == 200 and r.json()["config"]["keys"]["apify"]["configured"] is False
    assert db.notifications.find_one({"type": "api_key_problem", "title": {"$regex": "removed"}})
    dash = sa.get("/api/super-admin/dashboard").json()["dashboard"]["api_coverage"]
    assert dash["own_both"] == 1
    _paid_sub(db, org, 29.0, {"apify": "own", "gemini": "own"})
    subs = sa.get("/api/super-admin/subscriptions?coverage=own_both").json()["subscriptions"]
    assert len(subs) == 1 and subs[0]["api_coverage_label"] == "Bring both keys"
    owner = _login(client, "owner@acme.example", PW)
    assert owner.put(url, json={"apify": "leadai", "reason": "x"}).status_code in (401, 403)


def test_older_draft_data_is_migrated_encrypted(env):
    from app.services.tenant_api_keys import migrate_tenant_api_keys, resolve_api_key
    client, db = env
    oid = db.organizations.insert_one({"name": "Old", "status": "active", "api_mode": "byok",
                                       "custom_api_keys": {"apify_token": RAW_APIFY, "apify_verified": True}}).inserted_id
    assert migrate_tenant_api_keys(db) == 1
    doc = db.organizations.find_one({"_id": oid})
    assert RAW_APIFY not in json.dumps(doc, default=str) and "api_mode" not in doc
    assert doc["api_coverage"] == {"apify": "own", "gemini": "leadai"}
    assert resolve_api_key(str(oid), "apify") == (RAW_APIFY, "organization")
    assert migrate_tenant_api_keys(db) == 0                                  # idempotent


def test_plan_editor_sets_and_validates_api_prices(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    pid = str(db.plans.find_one({"slug": "pro"})["_id"])
    r = sa.patch(f"/api/super-admin/plans/{pid}", json={"api_addons": {"apify": {"monthly": 100, "yearly": 0},
                                                                         "gemini": {"monthly": 60, "yearly": 0}}})
    assert r.status_code == 422                                              # 160 > 149
    r = sa.patch(f"/api/super-admin/plans/{pid}", json={"api_addons": {"apify": {"monthly": 50, "yearly": 500},
                                                                         "gemini": {"monthly": 30, "yearly": 300}}})
    assert r.status_code == 200, r.text
    from app.billing.plans import invalidate_plan_cache, price_for
    invalidate_plan_cache()
    plan = db.plans.find_one({"slug": "pro"})
    assert price_for(plan, {"apify": "own", "gemini": "own"}, "monthly") == 69.0
    assert db.audit_logs.find_one({"action": "plan.updated", "resource_id": "pro"})


def test_courtesy_switch_next_price_matches_what_renewal_charges(env):
    """A Super Admin courtesy switch back to LeadAI-provided covers the rest of the
    paid period; the renewal charges the new price, and "next price" says so."""
    from app.billing.api_coverage import coverage_summary, renewal_amount
    client, db = env
    org = _org(db)
    sub_id = _paid_sub(db, org, 29.0, {"apify": "own", "gemini": "own"})
    db.organizations.update_one({"_id": ObjectId(org)}, {"$set": {"api_coverage": {"apify": "own", "gemini": "own"}}})
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    url = f"/api/super-admin/organizations/{org}/api-coverage"
    r = sa.put(url, json={"gemini": "leadai", "reason": "goodwill"})
    assert r.json()["status"] == "checkout_required"                       # costs more: not without force
    r = sa.put(url, json={"gemini": "leadai", "force": True, "reason": "goodwill"})
    assert r.status_code == 200 and r.json()["integrations"]["subscription"]["next_price"] == 37.0
    assert r.json()["integrations"]["subscription"]["current_period_end"].endswith("+00:00")
    from app.db.mongo import get_async_db
    summary = asyncio.run(coverage_summary(get_async_db(), org))
    assert renewal_amount(db, db.subscriptions.find_one({"_id": sub_id}), 29.0) == summary["subscription"]["next_price"]


def test_admin_organization_responses_never_carry_stored_keys(env):
    from app.services import secret_box
    client, db = env
    org = _org(db)
    db.organizations.update_one({"_id": ObjectId(org)}, {"$set": {"custom_api_keys": {
        "apify": {"ciphertext": secret_box.encrypt(RAW_APIFY), "hint": "apif…7890", "configured": True},
        "gemini_api_key": RAW_GEMINI}}})                                        # a not-yet-migrated plaintext key
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    for path in (f"/api/super-admin/organizations/{org}", "/api/super-admin/organizations",
                 f"/api/admin/organizations/{org}", "/api/admin/organizations"):
        r = sa.get(path)
        assert r.status_code == 200, path
        text = r.text
        assert RAW_APIFY not in text and RAW_GEMINI not in text and "fernet:" not in text, path
        assert "custom_api_keys" not in text, path


# ── Super Admin: LeadAI's own keys and organizations' keys ──────────────────────

def _clear_platform_overrides(db):
    from app.admin.envvars import _CACHE
    from app.admin.settings import clear_settings_cache
    db.system_settings.delete_one({"_id": "apify.token"})
    db.env_overrides.delete_one({"_id": "GEMINI_API_KEY"})
    db.platform_api_key_status.delete_many({})
    _CACHE.clear()
    clear_settings_cache()


def test_super_admin_replaces_leadais_keys_encrypted_and_tested(env):
    from app.services.tenant_api_keys import platform_key
    client, db = env
    _clear_platform_overrides(db)
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    url = "/api/super-admin/provider-keys/platform/apify"
    try:
        assert sa.put(url, json={"key": RAW_APIFY}).status_code == 422                    # reason required
        bad = AsyncMock(return_value={"valid": False, "detail": "Invalid token"})
        with patch("app.services.platform_api_keys.test_apify_token_connection", bad):
            r = sa.put(url, json={"key": RAW_APIFY, "reason": "rotate"})
        assert r.status_code == 422 and "Nothing was changed" in r.json()["detail"]
        assert db.system_settings.find_one({"_id": "apify.token"}) is None              # refused, not saved
        with patch("app.services.platform_api_keys.test_apify_token_connection", VALID):
            r = sa.put(url, json={"key": RAW_APIFY, "reason": "rotate"})
        assert r.status_code == 200, r.text
        assert RAW_APIFY not in r.text
        stored = db.system_settings.find_one({"_id": "apify.token"})["value"]
        assert stored.startswith("fernet:") and RAW_APIFY not in stored                  # encrypted at rest
        assert platform_key("apify") == RAW_APIFY                                         # used decrypted
        info = r.json()["platform"]["apify"]
        assert info["source"] == "saved" and info["hint"] == "apif…7890" and info["last_test"]["valid"]
        # Gemini: same, stored in the environment overrides
        with patch("app.services.platform_api_keys.test_gemini_key_connection", VALID):
            r = sa.put("/api/super-admin/provider-keys/platform/gemini", json={"key": RAW_GEMINI, "reason": "rotate"})
        assert r.status_code == 200
        assert db.env_overrides.find_one({"_id": "GEMINI_API_KEY"})["value"].startswith("fernet:")
        assert platform_key("gemini") == RAW_GEMINI
        overview = sa.get("/api/super-admin/provider-keys")
        assert overview.status_code == 200 and RAW_APIFY not in overview.text and RAW_GEMINI not in overview.text
        assert db.audit_logs.find_one({"action": "platform_api_key.replaced"})
        # reset: refused when the environment has no key (every customer would stop)
        with patch("app.services.platform_api_keys.environment_key", return_value=""):
            r = sa.request("DELETE", url, json={"reason": "back to env"})
        assert r.status_code == 409
        with patch("app.services.platform_api_keys.environment_key", return_value="apify_api_ENVvalue000000000"):
            r = sa.request("DELETE", url, json={"reason": "back to env"})
        assert r.status_code == 200 and db.system_settings.find_one({"_id": "apify.token"}) is None
    finally:
        _clear_platform_overrides(db)


def test_undecryptable_platform_key_falls_back_to_the_environment(env):
    from app.admin.settings import clear_settings_cache, get_apify_token
    from app.config import get_settings
    client, db = env
    db.system_settings.update_one({"_id": "apify.token"}, {"$set": {"value": "fernet:not-a-real-token"}}, upsert=True)
    clear_settings_cache()
    try:
        assert get_apify_token() == (get_settings().apify_api_token or "")
    finally:
        _clear_platform_overrides(db)


def test_super_admin_sets_and_tests_an_organizations_own_key(env):
    client, db = env
    org = _org(db)
    _user(db, "owner@acme.example", org, "owner")
    db.organizations.update_one({"_id": ObjectId(org)}, {"$set": {"api_coverage": {"apify": "own", "gemini": "leadai"}}})
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    listing = sa.get("/api/super-admin/provider-keys?show=problems").json()["organizations"]
    assert [o["problems"] for o in listing if o["id"] == org] == [["apify"]]                 # own, no key
    url = f"/api/super-admin/organizations/{org}/api-keys/apify"
    assert sa.put(url, json={"key": RAW_APIFY}).status_code == 422                           # reason required
    with patch("app.services.tenant_api_keys.test_apify_token_connection", VALID):
        r = sa.put(url, json={"key": RAW_APIFY, "reason": "customer emailed the new token"})
    assert r.status_code == 200 and RAW_APIFY not in r.text
    assert r.json()["integrations"]["keys"]["apify"]["verified"] is True
    doc = db.organizations.find_one({"_id": ObjectId(org)})
    assert doc["custom_api_keys"]["apify"]["ciphertext"].startswith("fernet:")
    assert db.notifications.find_one({"type": "api_key_updated"})
    assert db.audit_logs.find_one({"action": "api_key.saved", "details.via": "super_admin"})
    with patch("app.services.tenant_api_keys.test_apify_token_connection", VALID):
        r = sa.post(f"{url}/test")
    assert r.status_code == 200 and r.json()["test"]["valid"] is True
    listing = sa.get("/api/super-admin/provider-keys?show=problems").json()["organizations"]
    assert org not in [o["id"] for o in listing]                                             # fixed
    owner = _login(client, "owner@acme.example", PW)
    assert owner.put(url, json={"key": RAW_APIFY, "reason": "xyz"}).status_code in (401, 403)


def test_platform_key_times_carry_their_zone(env):
    client, db = env
    _clear_platform_overrides(db)
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    try:
        with patch("app.services.platform_api_keys.test_gemini_key_connection", VALID):
            r = sa.put("/api/super-admin/provider-keys/platform/gemini", json={"key": RAW_GEMINI, "reason": "rotate"})
        g = r.json()["platform"]["gemini"]
        assert g["updated_at"].endswith("+00:00") and g["last_test"]["tested_at"].endswith("+00:00")
    finally:
        _clear_platform_overrides(db)
