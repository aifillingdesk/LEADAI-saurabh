"""
Per-request database hot paths (every page load pays them, and on a hosted
MongoDB each is a network round trip): the session check, the settings read
and the plan lookup behind the usage summary.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from bson import ObjectId
from starlette.requests import Request

from app.admin import settings as admin_settings
from app.auth import service
from app.billing.entitlements import EntitlementService
from app.billing.plans import ensure_default_plans
from app.db.models import utcnow
from app.db.mongo import get_sync_db


def _request(cookie: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [
        (b"cookie", f"{service.COOKIE_NAME}={cookie}".encode())]})


def _counting(db, coll):
    calls = {"find_one": 0, "update_one": 0}
    real = db[coll]

    class Proxy:
        def __getattr__(self, name):
            attr = getattr(real, name)
            if name in calls:
                def wrapped(*a, **k):
                    calls[name] += 1
                    return attr(*a, **k)
                return wrapped
            return attr
    return calls, Proxy()


def test_session_is_checked_once_per_request_and_activity_written_at_most_once_a_minute():
    db = get_sync_db()
    tracked = service.create_tracked_session({"email": "hot@path.test", "name": "Hot", "role": "admin"})
    cookie = service.build_session_value(tracked)
    calls, proxy = _counting(db, "user_sessions")
    with patch.object(type(db), "__getitem__", lambda self, name: proxy if name == "user_sessions" else
                      db.get_collection(name)):
        req = _request(cookie)
        first = service.session_user(req)
        second = service.session_user(req)          # middleware + dependency on the same request
        assert first == second and first["email"] == "hot@path.test"
        assert first is not second                  # callers get their own copy
        assert calls["find_one"] == 1
        assert calls["update_one"] == 0             # touched seconds ago at sign-in
        service.session_user(_request(cookie))      # a new request checks again
        assert calls["find_one"] == 2
    # stale activity (> 60 s) is refreshed
    db.user_sessions.update_one({"session_id": tracked["session_id"]},
                                {"$set": {"last_active_at": datetime.now(timezone.utc) - timedelta(minutes=2)}})
    service.session_user(_request(cookie))
    seen = db.user_sessions.find_one({"session_id": tracked["session_id"]})["last_active_at"]
    seen = seen if seen.tzinfo else seen.replace(tzinfo=timezone.utc)
    assert (datetime.now(timezone.utc) - seen).total_seconds() < 30
    # a revoked session is refused on the next request
    service.revoke_session(tracked["session_id"], revoked_by="test")
    assert service.session_user(_request(cookie)) is None


def test_settings_reads_are_cached_and_writes_apply_immediately():
    db = get_sync_db()
    admin_settings.clear_settings_cache()
    assert admin_settings.is_maintenance_enabled() is False
    calls, proxy = _counting(db, admin_settings.COLLECTION)
    with patch.object(type(db), "__getitem__", lambda self, name: proxy if name == admin_settings.COLLECTION else
                      db.get_collection(name)):
        for _ in range(5):
            admin_settings.is_maintenance_enabled()
        assert calls["find_one"] == 0               # served from the cache
    admin_settings.set_setting("maintenance.enabled", True)
    assert admin_settings.is_maintenance_enabled() is True      # write clears the cache
    admin_settings.delete_setting("maintenance.enabled")
    assert admin_settings.is_maintenance_enabled() is False


@pytest.mark.asyncio
async def test_usage_summary_resolves_the_plan_once_and_matches_get_limit():
    db = get_sync_db()
    await ensure_default_plans()
    org_id = str(db.organizations.insert_one({"name": "Hot Org", "slug": "hot-org", "status": "active",
                                              "plan_id": "free", "created_at": utcnow()}).inserted_id)
    db.temporary_entitlements.insert_many([
        {"organization_id": org_id, "entitlement_type": "credit", "key": "monthly_searches", "value": 50},
        {"organization_id": org_id, "entitlement_type": "credit", "key": "monthly_searches", "value": 5,
         "expires_at": utcnow() + timedelta(days=1)},
        {"organization_id": org_id, "entitlement_type": "credit", "key": "monthly_searches", "value": 999,
         "expires_at": utcnow() - timedelta(days=1)},                  # expired: ignored
    ])
    real = EntitlementService.get_effective_plan
    n = {"plan": 0}

    async def counting(organization_id, db=None):
        n["plan"] += 1
        return await real(organization_id, db=db)
    with patch.object(EntitlementService, "get_effective_plan", staticmethod(counting)):
        summary = await EntitlementService.get_usage_summary(org_id)
    assert n["plan"] == 1
    for metric, row in summary["metrics"].items():
        if metric == "team_members":
            continue
        assert row["limit"] == await EntitlementService.get_limit(org_id, metric), metric
    assert summary["metrics"]["monthly_searches"]["limit"] == \
        int((await real(org_id)).get("limits", {}).get("monthly_searches") or 0) + 55
    assert ObjectId.is_valid(org_id)


def test_every_response_carries_its_duration_and_slow_requests_are_logged(caplog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app import logging_context as lc
    app = FastAPI()
    app.add_middleware(lc.RequestIdMiddleware)

    @app.get("/x")
    def x():
        return {"ok": True}
    with patch.object(lc, "_SLOW_REQUEST_MS", 0.0), caplog.at_level("WARNING", logger="app.slow_requests"):
        r = TestClient(app).get("/x")
    assert r.headers["Server-Timing"].startswith("app;dur=")
    assert any("slow request GET /x -> 200" in m for m in caplog.messages)
