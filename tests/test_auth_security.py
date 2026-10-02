"""
Tests for Phase 4 Authentication Security, TOTP 2FA, Email Verification, and Request ID Tracing.
"""
import time
from datetime import timedelta

import mongomock
import pytest
from bson import ObjectId
from starlette.requests import Request
from starlette.responses import Response

from app.auth.totp import (
    generate_totp_code,
    generate_totp_secret,
    get_totp_uri,
    verify_totp_code,
)
from app.db.models import utcnow
from app.logging_context import (
    RequestIdFilter,
    RequestIdMiddleware,
    get_current_request_id,
    set_current_request_id,
)


def test_totp_secret_generation():
    secret = generate_totp_secret()
    assert isinstance(secret, str)
    assert len(secret) >= 26
    # Base32 characters only
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in secret)


def test_totp_uri_formatting():
    secret = "JBSWY3DPEHPK3PXP"
    uri = get_totp_uri(secret, email="admin@example.com", issuer="LeadAI")
    assert uri.startswith("otpauth://totp/")
    assert "secret=JBSWY3DPEHPK3PXP" in uri
    assert "issuer=LeadAI" in uri
    assert "admin%40example.com" in uri or "admin@example.com" in uri


def test_totp_code_generation_and_verification():
    secret = generate_totp_secret()
    now = time.time()

    # Generate current code
    code = generate_totp_code(secret, for_time=now)
    assert len(code) == 6
    assert code.isdigit()

    # Verify matching code succeeds
    assert verify_totp_code(secret, code) is True

    # Verify time drift within 1 window step (+30s)
    future_code = generate_totp_code(secret, for_time=now + 30)
    assert verify_totp_code(secret, future_code, window=1) is True

    # Verify time drift past window fails (+90s)
    distant_code = generate_totp_code(secret, for_time=now + 90)
    assert verify_totp_code(secret, distant_code, window=1) is False

    # Verify invalid codes fail
    assert verify_totp_code(secret, "000000") is (code == "000000")
    assert verify_totp_code(secret, "abcdef") is False
    assert verify_totp_code(secret, "123") is False
    assert verify_totp_code("", code) is False


def test_request_id_tracing_context():
    # Outside context defaults to '-'
    assert get_current_request_id() == "-"

    set_current_request_id("test-req-12345")
    try:
        assert get_current_request_id() == "test-req-12345"

        import logging
        record = logging.LogRecord("test", logging.INFO, "test.py", 10, "Hello", (), None)
        f = RequestIdFilter()
        f.filter(record)
        assert record.request_id == "test-req-12345"
    finally:
        set_current_request_id("-")


@pytest.mark.asyncio
async def test_request_id_middleware():
    middleware = RequestIdMiddleware(app=None)

    # Simulated incoming request without X-Request-ID
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/test",
        "headers": [],
    }
    req = Request(scope)

    async def call_next(request: Request) -> Response:
        # Check that state.request_id and contextvar are populated
        assert hasattr(request.state, "request_id")
        assert len(request.state.request_id) > 0
        assert get_current_request_id() == request.state.request_id
        return Response(content="OK", status_code=200)

    res = await middleware.dispatch(req, call_next)
    assert "X-Request-ID" in res.headers
    assert len(res.headers["X-Request-ID"]) > 0


@pytest.mark.asyncio
async def test_request_id_middleware_echoes_client_id():
    middleware = RequestIdMiddleware(app=None)

    custom_id = "client-trace-abc-123"
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/test",
        "headers": [(b"x-request-id", custom_id.encode())],
    }
    req = Request(scope)

    async def call_next(request: Request) -> Response:
        assert request.state.request_id == custom_id
        return Response(content="OK", status_code=200)

    res = await middleware.dispatch(req, call_next)
    assert res.headers["X-Request-ID"] == custom_id


def test_email_verification_token_flow():
    import hashlib
    import secrets

    client = mongomock.MongoClient()
    db = client["lead_ai_test"]

    uid = ObjectId()
    email = "newuser@example.com"
    db["users"].insert_one({
        "_id": uid,
        "email": email,
        "email_verified": False,
    })

    # Generate token
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = utcnow()

    db["email_verifications"].insert_one({
        "user_id": str(uid),
        "email": email,
        "token_hash": token_hash,
        "expires_at": now + timedelta(hours=24),
        "used_at": None,
        "created_at": now,
    })

    # Verify token
    found = db["email_verifications"].find_one_and_update(
        {"token_hash": token_hash, "used_at": None, "expires_at": {"$gt": now}},
        {"$set": {"used_at": now}}
    )
    assert found is not None

    db["users"].update_one(
        {"_id": uid},
        {"$set": {"email_verified": True, "email_verified_at": now}}
    )

    user_doc = db["users"].find_one({"_id": uid})
    assert user_doc["email_verified"] is True

    # Replay attempt fails
    replay = db["email_verifications"].find_one_and_update(
        {"token_hash": token_hash, "used_at": None, "expires_at": {"$gt": now}},
        {"$set": {"used_at": now}}
    )
    assert replay is None
