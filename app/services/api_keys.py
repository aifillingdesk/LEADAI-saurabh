"""
LeadAI Public REST API Key Management & Authentication (Phase 6 Product Feature).

Guarantees:
- Keys prefixed with `lai_live_` or `lai_test_`
- Hashed at rest using SHA-256 (raw secret returned only on creation)
- Multi-tenant scoped (`organization_id`)
- Scopes: `leads:read`, `leads:write`, `search:create`, `webhooks:manage`
- Rate-limited and logged
"""
import hashlib
import logging
import secrets
import uuid
from typing import Any

from bson import ObjectId
from fastapi import HTTPException
from fastapi.security import APIKeyHeader

from app.db.models import utcnow

logger = logging.getLogger(__name__)

COLL_API_KEYS = "api_keys"
COLL_API_USAGE = "api_key_usage"

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()


def create_api_key(
    db,
    *,
    organization_id: str,
    name: str,
    scopes: list[str] | None = None,
    rate_limit_per_minute: int = 60,
    created_by: str | None = None,
    test_mode: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Generate a new API key. Raw key is returned once."""
    prefix = "lai_test_" if test_mode else "lai_live_"
    secret_part = secrets.token_urlsafe(32)
    raw_key = f"{prefix}{secret_part}"
    key_hash = _hash_key(raw_key)
    now = utcnow()

    doc = {
        "_id": ObjectId(),
        "key_id": f"key_{uuid.uuid4().hex[:10]}",
        "name": name,
        "organization_id": str(organization_id),
        "key_hash": key_hash,
        "prefix": raw_key[:12] + "...",
        "scopes": scopes or ["leads:read", "search:create"],
        "rate_limit_per_minute": rate_limit_per_minute,
        "is_active": True,
        "created_by": created_by,
        "last_used_at": None,
        "created_at": now,
        "updated_at": now,
    }

    db[COLL_API_KEYS].insert_one(doc)
    doc["id"] = str(doc["_id"])
    logger.info("Generated API key '%s' (%s) for org %s", name, doc["prefix"], organization_id)
    return raw_key, doc


def list_api_keys(db, organization_id: str) -> list[dict[str, Any]]:
    """List all API keys for an organization (never returns hashes)."""
    keys = list(db[COLL_API_KEYS].find(
        {"organization_id": str(organization_id)},
        {"key_hash": 0},
    ).sort("created_at", -1))
    for k in keys:
        k["id"] = str(k["_id"])
    return keys


def revoke_api_key(db, key_id: str, organization_id: str) -> bool:
    """Revoke an API key."""
    q = {"organization_id": str(organization_id)}
    if ObjectId.is_valid(key_id):
        q["$or"] = [{"_id": ObjectId(key_id)}, {"key_id": key_id}]
    else:
        q["key_id"] = key_id

    res = db[COLL_API_KEYS].update_one(q, {"$set": {"is_active": False, "updated_at": utcnow()}})
    return res.modified_count > 0


def authenticate_api_key(
    db,
    raw_key: str,
    required_scope: str | None = None,
) -> dict[str, Any]:
    """Validate a raw API key and scope."""
    if not raw_key or not raw_key.startswith(("lai_live_", "lai_test_")):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    key_hash = _hash_key(raw_key)
    key_doc = db[COLL_API_KEYS].find_one({"key_hash": key_hash, "is_active": True})
    # partner keys (owner_type="partner") never authenticate against the org API
    if not key_doc or key_doc.get("owner_type") == "partner" or not key_doc.get("organization_id"):
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")

    # Scope check
    if required_scope:
        scopes = key_doc.get("scopes", [])
        if required_scope not in scopes and "*" not in scopes:
            raise HTTPException(status_code=403, detail=f"API key missing required scope: '{required_scope}'")

    now = utcnow()
    db[COLL_API_KEYS].update_one({"_id": key_doc["_id"]}, {"$set": {"last_used_at": now}})

    return {
        "key_id": key_doc.get("key_id"),
        "organization_id": key_doc["organization_id"],
        "name": key_doc.get("name"),
        "scopes": key_doc.get("scopes", []),
        "rate_limit_per_minute": key_doc.get("rate_limit_per_minute", 60),
    }
