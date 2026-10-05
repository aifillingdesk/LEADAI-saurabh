"""The API keys LeadAI itself provides to customers (Apify, Gemini).

Every organization whose coverage says "leadai" for an API runs on these keys
(see docs/API_COVERAGE.md). The Super Admin replaces, tests or resets them here:

- A key saved here overrides the server environment (APIFY_API_TOKEN /
  GEMINI_API_KEY) and is stored encrypted (app/services/secret_box.py).
- A new key is tested with the provider before it is saved; a key that fails
  is refused unless the Super Admin forces it.
- Resetting removes the saved key, so the environment value applies again.
  Refused when the environment has none (every customer on LeadAI-provided
  would stop) unless forced.
- Responses carry only a masked hint, never a key.
"""
import os
from typing import Any, Dict, Optional

from app.db.models import utcnow
from app.services.tenant_api_keys import (PROVIDER_NAMES, iso_utc, mask_key, platform_key, test_apify_token_connection,
                                          test_gemini_key_connection)

ENV_NAMES = {"apify": "APIFY_API_TOKEN", "gemini": "GEMINI_API_KEY"}
STATUS_COLLECTION = "platform_api_key_status"


def environment_key(provider: str) -> str:
    """The key from the server environment (.env / process), ignoring overrides."""
    from app.config import get_settings
    s = get_settings()
    value = s.apify_api_token if provider == "apify" else s.gemini_api_key
    return (value or os.environ.get(ENV_NAMES[provider]) or "").strip()


async def _override_doc(db, provider: str) -> Optional[Dict[str, Any]]:
    if provider == "apify":
        doc = await db.system_settings.find_one({"_id": "apify.token"}, {"value": 1, "updated_at": 1, "updated_by": 1})
    else:
        doc = await db.env_overrides.find_one({"_id": "GEMINI_API_KEY"}, {"value": 1, "updated_at": 1,
                                                                           "updated_by": 1})
    return doc if doc and doc.get("value") else None


async def _test(provider: str, key: str) -> Dict[str, Any]:
    test = await (test_apify_token_connection(key) if provider == "apify" else test_gemini_key_connection(key))
    return {k: v for k, v in test.items() if k in ("valid", "detail", "message", "username", "models_count")}


async def _record_test(db, provider: str, key: str, test: Dict[str, Any]) -> None:
    await db[STATUS_COLLECTION].update_one({"_id": provider}, {"$set": {
        "valid": bool(test.get("valid")), "detail": test.get("detail") or test.get("message"),
        "hint": mask_key(key), "tested_at": utcnow()}}, upsert=True)


async def platform_key_status(db) -> Dict[str, Any]:
    """{"apify": {...}, "gemini": {...}}: hint, where it comes from, last test,
    and how many organizations run on it."""
    out: Dict[str, Any] = {}
    for provider in ENV_NAMES:
        key = platform_key(provider)
        doc = await _override_doc(db, provider)
        env_key = environment_key(provider)
        last = await db[STATUS_COLLECTION].find_one({"_id": provider}) or {}
        # a test of an older key says nothing about the current one
        last_test = ({"valid": last.get("valid"), "detail": last.get("detail"), "tested_at": iso_utc(last.get("tested_at"))}
                     if last and last.get("hint") == mask_key(key) else None)
        using = await db.organizations.count_documents({f"api_coverage.{provider}": {"$ne": "own"},
                                                        "status": {"$nin": ["deleted"]}})
        out[provider] = {
            "name": PROVIDER_NAMES[provider], "env_name": ENV_NAMES[provider],
            "set": bool(key), "hint": mask_key(key) if key else "",
            "source": "saved" if doc else ("environment" if env_key else "not_set"),
            "environment_set": bool(env_key), "environment_hint": mask_key(env_key) if env_key else "",
            "updated_at": iso_utc((doc or {}).get("updated_at")), "updated_by": (doc or {}).get("updated_by"),
            "last_test": last_test, "organizations_using": using,
        }
    return out


async def replace_platform_key(db, provider: str, key: str, *, actor_email: str, force: bool = False) -> Dict[str, Any]:
    """Test, then save (encrypted). Returns {"saved": bool, "test": {...}}."""
    from app.admin import envvars
    from app.admin.settings import aset_setting
    key = (key or "").strip()
    test = await _test(provider, key) if key else {"valid": False, "detail": "Enter the key"}
    if not key or (not test.get("valid") and not force):
        return {"saved": False, "test": test}
    if provider == "apify":
        ok = await aset_setting("apify.token", key, by=actor_email)
        envvars._CACHE.pop("APIFY_API_TOKEN", None)
    else:
        ok = await envvars.aset_envvar_override("GEMINI_API_KEY", key, by=actor_email)
        # the shared circuit breaker may still be open from the old key
        from app.pipeline.comment_ai import reset_circuit_breaker
        reset_circuit_breaker()
    if ok:
        await _record_test(db, provider, key, test)
    return {"saved": bool(ok), "test": test}


async def test_platform_key(db, provider: str) -> Dict[str, Any]:
    key = platform_key(provider)
    if not key:
        return {"valid": False, "detail": f"No {PROVIDER_NAMES[provider]} key is set"}
    test = await _test(provider, key)
    await _record_test(db, provider, key, test)
    if provider == "gemini" and test.get("valid"):
        from app.pipeline.comment_ai import reset_circuit_breaker
        reset_circuit_breaker()
    return test


def reset_platform_key(provider: str) -> bool:
    """Remove the key saved here; the environment's applies again."""
    from app.admin import envvars
    return envvars.delete_envvar_override(ENV_NAMES[provider])
