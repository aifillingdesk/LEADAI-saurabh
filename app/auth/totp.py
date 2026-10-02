"""
RFC 6238 Time-Based One-Time Password (TOTP) Implementation for LeadAI.

Provides:
- Base32 secret generation (160-bit entropy)
- otpauth:// URI formatting for authenticator apps (Google Authenticator, Authy, 1Password)
- TOTP verification with configurable time window drift (RFC 6238 / RFC 4226)

Implemented strictly using Python standard library (hmac, hashlib, struct, base64, secrets).
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse


def generate_totp_secret() -> str:
    """Generate a random 160-bit (20 byte) base32-encoded secret string."""
    raw = secrets.token_bytes(20)
    # base32 encoding, strip padding for clean user entry
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def get_totp_uri(secret: str, email: str, issuer: str = "LeadAI") -> str:
    """Generate the standard otpauth:// URL for QR code generation."""
    clean_secret = secret.replace(" ", "").upper()
    label = f"{issuer}:{email}"
    params = {
        "secret": clean_secret,
        "issuer": issuer,
        "algorithm": "SHA1",
        "digits": "6",
        "period": "30",
    }
    query = urllib.parse.urlencode(params)
    return f"otpauth://totp/{urllib.parse.quote(label)}?{query}"


def generate_totp_code(secret: str, for_time: float | None = None) -> str:
    """Generate the current 6-digit TOTP code for a given secret at timestamp."""
    if for_time is None:
        for_time = time.time()

    time_step = int(for_time // 30)
    clean_secret = secret.replace(" ", "").upper()
    # Add back base32 padding if needed
    padding_needed = (8 - len(clean_secret) % 8) % 8
    key = base64.b32decode(clean_secret + "=" * padding_needed, casefold=True)

    msg = struct.pack(">Q", time_step)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code_int = struct.unpack(">I", h[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code_int % 1000000:06d}"


def verify_totp_code(secret: str, code: str, window: int = 1) -> bool:
    """Verify a user-submitted TOTP code against the secret within a drift window.

    window=1 checks the current step, 1 step before (-30s), and 1 step after (+30s).
    """
    if not secret or not code:
        return False

    code = str(code).strip()
    if len(code) != 6 or not code.isdigit():
        return False

    now = time.time()
    for step in range(-window, window + 1):
        t = now + (step * 30)
        expected = generate_totp_code(secret, t)
        if hmac.compare_digest(expected, code):
            return True

    return False
