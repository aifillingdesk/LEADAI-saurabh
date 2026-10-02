"""
Acceptance tests for sign-in: credential verification for both scopes
(site login = database accounts only; Super Admin = SUPERADMIN_EMAIL /
SUPERADMIN_PASSWORD from the environment, legacy PANEL_ADMIN_* / admin_users),
the signed session cookie (tamper + expiry protection) and the per-IP
brute-force throttle.
"""
import pytest

from app.auth import service
from app.config import get_settings
from app.admin.envvars import clear_cache


@pytest.fixture(autouse=True)
def _restore_settings():
    """Every test here mutates the shared Settings object and _patch() stubs
    the env registry's DB accessor; put both back."""
    import app.admin.envvars as ev
    s = get_settings()
    keys = ("admin_email", "admin_password_hash", "panel_admin_email",
            "panel_admin_password_hash", "superadmin_email", "superadmin_password",
            "session_secret", "session_ttl_days")
    saved = {k: getattr(s, k) for k in keys}
    saved_db = ev.get_sync_db
    yield
    for k, v in saved.items():
        setattr(s, k, v)
    ev.get_sync_db = saved_db
    clear_cache()


def _patch():
    clear_cache()
    settings = get_settings()
    # legacy env site credentials: must NOT grant any login any more
    settings.admin_email = "Admin@gmail.com"
    settings.admin_password_hash = (
        "a36aef5a11c4073fbe60314fc9df530a9d5f986533594d1f5190742ff9e0e408")
    settings.panel_admin_email = "Admin123@gmail.com"
    settings.panel_admin_password_hash = (
        "bc78e58d55cde1346e68f8e5fe588dedf62fa457aa646a500a53347faff6ee24")
    settings.superadmin_email = ""
    settings.superadmin_password = ""
    settings.session_secret = "test-secret"
    settings.session_ttl_days = 7
    # Ensure envvars uses settings values (not DB overrides) during tests
    import app.admin.envvars as ev
    ev.get_sync_db = lambda: None


def _with_password(email, password, **kw):
    """A database site account (users + org + membership) with a password."""
    from app.auth.crypto import hash_password
    from app.db.mongo import get_sync_db
    from tests.conftest import seed_site_account
    user_id, org_id = seed_site_account(email, **kw)
    get_sync_db().users.update_one({"email": email.strip().lower()},
                                   {"$set": {"password_hash": hash_password(password)}})
    return user_id, org_id


# ── Main website login (scope "site"): database accounts only ─────────────

def test_site_correct_credentials():
    _patch()
    _user_id, org_id = _with_password("owner@example.com", "Owner@2026pass")
    user, err = service.verify_saas_user_login("Owner@example.com", "Owner@2026pass")
    assert err is None and user is not None
    assert user["email"] == "owner@example.com"
    assert user["scope"] == "site"
    assert user["organization_id"] == org_id


def test_site_without_account_fails_closed():
    _patch()
    user, err = service.verify_saas_user_login("nobody@example.com", "Owner@2026pass")
    assert user is None and err == "Invalid email or password"


def test_site_credentials_without_active_org_fail_closed():
    _patch()
    _with_password("owner@example.com", "Owner@2026pass", org_status="suspended")
    user, _err = service.verify_saas_user_login("owner@example.com", "Owner@2026pass")
    assert user is None


def test_site_email_case_insensitive():
    _patch()
    _with_password("owner@example.com", "Owner@2026pass")
    user, _err = service.verify_saas_user_login(" OWNER@EXAMPLE.COM ", "Owner@2026pass")
    assert user is not None


def test_site_wrong_password():
    _patch()
    _with_password("owner@example.com", "Owner@2026pass")
    assert service.verify_saas_user_login("owner@example.com", "wrong")[0] is None
    assert service.verify_saas_user_login("owner@example.com", "")[0] is None


def test_env_site_credentials_no_longer_grant_login(site_account):
    """ADMIN_EMAIL / ADMIN_PASSWORD_HASH are not a login path: a database
    account without its own password cannot sign in with the env password."""
    _patch()
    site_account("admin@gmail.com")
    assert not hasattr(service, "verify_site_login")
    assert service.verify_saas_user_login("Admin@gmail.com", "Admin@2026")[0] is None


def test_site_credentials_rejected_for_panel():
    """Website credentials must not unlock the admin portal."""
    _patch()
    _with_password("owner@example.com", "Owner@2026pass")
    assert service.verify_admin_login("owner@example.com", "Owner@2026pass") is None
    assert service.verify_admin_login("Admin@gmail.com", "Admin@2026") is None


# ── Super Admin (scope "admin") ───────────────────────────────────────────

def _superadmin(email="Owner@LeadAI.example", password="Perm@nent-Pass-2026"):
    s = get_settings()
    s.superadmin_email = email
    s.superadmin_password = password


def test_superadmin_env_credentials():
    _patch()
    _superadmin()
    user = service.verify_admin_login(" OWNER@leadai.example ", "Perm@nent-Pass-2026")
    assert user is not None
    assert user["email"] == "owner@leadai.example"
    assert user["scope"] == "admin" and user["role"] == "super_admin"
    assert user["platform_role"] == "super_admin"
    assert service.verify_admin_login("owner@leadai.example", "wrong") is None
    assert service.verify_admin_login("owner@leadai.example", "") is None


def test_superadmin_password_may_be_bcrypt_hash():
    from app.auth.crypto import hash_password
    _patch()
    _superadmin(password=hash_password("Hashed-Pass-2026!"))
    assert service.verify_admin_login("owner@leadai.example", "Hashed-Pass-2026!") is not None
    assert service.verify_admin_login("owner@leadai.example", "wrong") is None


def test_superadmin_replaces_legacy_panel_account():
    """With SUPERADMIN_* set, the deprecated PANEL_ADMIN_* pair is ignored."""
    _patch()
    _superadmin()
    assert service.verify_admin_login("Admin123@gmail.com", "Admin@1234") is None
    from app.auth.roles import effective_role
    assert effective_role("owner@leadai.example") == "super_admin"
    assert effective_role("admin123@gmail.com") == ""


def test_superadmin_cannot_be_shadowed_by_db_record(monkeypatch):
    """A same-email admin_users record cannot sign the Super Admin in with
    another password (the environment is the only source of truth)."""
    from app.auth.crypto import hash_password
    _patch()
    _superadmin()
    monkeypatch.setattr(service, "_admin_user_record", lambda email: {
        "_id": "x", "email": email, "role": "viewer", "enabled": True,
        "password_hash": hash_password("db-password-123")})
    assert service.verify_admin_login("owner@leadai.example", "db-password-123") is None
    user = service.verify_admin_login("owner@leadai.example", "Perm@nent-Pass-2026")
    assert user is not None and user["role"] == "super_admin"


def test_superadmin_config_status():
    from app.auth import superadmin as sa
    _patch()
    get_settings().panel_admin_email = ""
    get_settings().panel_admin_password_hash = ""
    assert sa.config_status()["configured"] is False
    get_settings().superadmin_email = "owner@leadai.example"
    assert sa.config_status() == {"configured": False, "source": "SUPERADMIN_EMAIL",
                                  "problem": "SUPERADMIN_PASSWORD is empty"}
    get_settings().superadmin_password = "short"
    status = sa.config_status()
    assert status["configured"] and status["password_too_short"] and status["email_valid"]
    assert sa.validate_superadmin_config()["configured"] is True


# ── Legacy Super Admin (PANEL_ADMIN_*, only without SUPERADMIN_*) ──────────

def test_panel_correct_credentials():
    _patch()
    user = service.verify_admin_login("Admin123@gmail.com", "Admin@1234")
    assert user is not None
    assert user["email"] == "admin123@gmail.com"
    assert user["scope"] == "admin"
    assert user["role"] == "super_admin"  # env account is the recovery super-admin


def test_panel_email_case_insensitive():
    _patch()
    assert service.verify_admin_login(" ADMIN123@GMAIL.COM ", "Admin@1234") is not None


def test_panel_wrong_password():
    _patch()
    assert service.verify_admin_login("Admin123@gmail.com", "wrong") is None
    assert service.verify_admin_login("Admin123@gmail.com", "") is None


def test_panel_wrong_email():
    _patch()
    assert service.verify_admin_login("hacker@example.com", "Admin@1234") is None


def test_panel_credentials_rejected_for_site():
    """The admin portal credentials must not unlock the main website."""
    _patch()
    assert service.verify_saas_user_login("Admin123@gmail.com", "Admin@1234")[0] is None


# ── Session cookie ────────────────────────────────────────────────────────

def test_cookie_roundtrip():
    _patch()
    value = service.build_session_value({"email": "admin123@gmail.com", "name": "Admin", "role": "admin"})
    user = service.parse_session_value(value)
    assert user is not None
    assert user["email"] == "admin123@gmail.com"


def test_cookie_tamper_detected():
    _patch()
    value = service.build_session_value({"email": "admin@gmail.com", "name": "Admin", "role": "admin"})
    payload, sig = value.rsplit(".", 1)
    import base64 as b64
    tampered = b64.urlsafe_b64encode(
        b64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        .replace(b"admin@gmail.com", b"evil@example.com")).rstrip(b"=").decode("ascii")
    assert service.parse_session_value(f"{tampered}.{sig}") is None


def test_cookie_expiry():
    _patch()
    value = service.build_session_value({"email": "admin@gmail.com", "name": "Admin", "role": "admin"})
    payload, sig = value.rsplit(".", 1)
    import base64 as b64
    import json
    data = json.loads(b64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    data["exp"] = 0
    expired_payload = b64.urlsafe_b64encode(
        json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=").decode("ascii")
    assert service.parse_session_value(f"{expired_payload}.{sig}") is None


# ── Sensitive stripping & Idle timeout ────────────────────────────────────

def test_strip_sensitive():
    from app.db.models import strip_sensitive
    raw = {
        "email": "user@example.com",
        "name": "Test User",
        "password_hash": "secret_hash_123",
        "hashed_password": "secret_hash_456",
        "session_secret": "my-secret",
        "role": "admin",
        "gemini_api_key": "ai-key",
    }
    sanitized = strip_sensitive(raw)
    assert sanitized["email"] == "user@example.com"
    assert sanitized["name"] == "Test User"
    assert sanitized["role"] == "admin"
    assert "password_hash" not in sanitized
    assert "hashed_password" not in sanitized
    assert "session_secret" not in sanitized
    assert "gemini_api_key" not in sanitized
    assert strip_sensitive(None) is None


def test_idle_session_timeout():
    from datetime import datetime, timedelta, timezone
    from app.db.mongo import get_sync_db
    import app.admin.envvars as ev

    _patch()
    db = get_sync_db()
    ev.get_sync_db = lambda: db

    settings = get_settings()
    settings.session_idle_timeout_minutes = 15

    user_dict = {"email": "idle@example.com", "name": "Idle", "role": "admin"}
    tracked = service.create_tracked_session(user_dict)
    cookie_val = service.build_session_value(tracked)

    # 1. Immediately valid
    parsed = service.parse_session_value(cookie_val)
    assert parsed is not None
    assert parsed["email"] == "idle@example.com"

    # 2. Simulate idle passage beyond 15 min (e.g. 20 min ago)
    twenty_min_ago = datetime.now(timezone.utc) - timedelta(minutes=20)
    db["user_sessions"].update_one(
        {"session_id": tracked["session_id"]},
        {"$set": {"last_active_at": twenty_min_ago}}
    )

    # 3. Next parse should detect idle timeout and fail
    expired = service.parse_session_value(cookie_val)
    assert expired is None

    # Check revoked_by in db
    doc = db["user_sessions"].find_one({"session_id": tracked["session_id"]})
    assert doc is not None
    assert doc.get("revoked_by") == "idle_timeout"

