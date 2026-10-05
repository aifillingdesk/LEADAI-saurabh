"""Encryption at rest for secrets customers give us (their own API keys).

Fernet (AES-128-CBC + HMAC-SHA256) from ``cryptography``. The key comes from,
in order:

1. ``API_KEY_ENCRYPTION_KEY``: a Fernet key, or any passphrase (hashed into one).
   Set this in production and keep it outside the database.
2. ``SESSION_SECRET``: derived, so existing deployments are protected without
   new configuration.
3. A key generated once and stored in ``system_settings`` (development
   fallback, logged as a warning: the data and its key then live together).

Plain values are never logged. ``decrypt`` returns None when a value can't be
decrypted (the key changed); callers treat that as "key missing, re-enter it".
"""
import base64
import hashlib
import logging
import os
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_SETTING_ID = "secrets.api_key_encryption"
_PREFIX = "fernet:"


def _derive(material: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(("leadai-api-keys:" + material).encode("utf-8")).digest())


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    explicit = (os.getenv("API_KEY_ENCRYPTION_KEY") or "").strip()
    if explicit:
        try:
            return Fernet(explicit.encode("utf-8"))        # already a Fernet key
        except (ValueError, TypeError):
            return Fernet(_derive(explicit))               # a passphrase
    session = (os.getenv("SESSION_SECRET") or "").strip()
    if not session:
        try:
            from app.config import get_settings
            session = (get_settings().session_secret or "").strip()
        except Exception:
            session = ""
    if session:
        return Fernet(_derive(session))
    # development fallback: one generated key, persisted so restarts can decrypt
    from app.db.mongo import get_sync_db
    db = get_sync_db()
    if db is None:
        raise RuntimeError("No encryption key: set API_KEY_ENCRYPTION_KEY")
    doc = db["system_settings"].find_one({"_id": _SETTING_ID})
    if not doc or not doc.get("value"):
        db["system_settings"].update_one({"_id": _SETTING_ID},
                                         {"$setOnInsert": {"value": Fernet.generate_key().decode()}}, upsert=True)
        doc = db["system_settings"].find_one({"_id": _SETTING_ID})
        logger.warning("API_KEY_ENCRYPTION_KEY and SESSION_SECRET are not set: customers' API keys are encrypted "
                       "with a key stored in the database. Set API_KEY_ENCRYPTION_KEY in production.")
    return Fernet(str(doc["value"]).encode("utf-8"))


def reset_cache() -> None:
    """Tests / key rotation: forget the cached key."""
    _fernet.cache_clear()


def encrypt(plain: str) -> str:
    return _PREFIX + _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    if not token.startswith(_PREFIX):
        return None
    try:
        return _fernet().decrypt(token[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        logger.warning("A stored API key could not be decrypted (encryption key changed?)")
        return None


def is_encrypted(value: Optional[str]) -> bool:
    return bool(value) and str(value).startswith(_PREFIX)

