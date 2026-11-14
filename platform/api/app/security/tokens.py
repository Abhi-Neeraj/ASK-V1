"""Random tokens and how they are stored.

Session, invitation, email and device tokens carry 256 bits of randomness, so a plain SHA-256 is
enough to store them (there is nothing to brute-force) and it survives rotating the application
secret. Pairing codes are short, so they are keyed with the secret and expire quickly.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# No 0/O/1/I/L: pairing codes are read off a screen and typed on a device.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_token(num_bytes: int = 32) -> str:
    return secrets.token_urlsafe(num_bytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_pairing_code() -> str:
    """Ten characters (about 50 bits), shown as XXXXX-XXXXX."""
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def normalize_pairing_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def hash_pairing_code(code: str, secret: str) -> str:
    normalized = normalize_pairing_code(code)
    return hmac.new(secret.encode("utf-8"), f"pairing:{normalized}".encode(), hashlib.sha256).hexdigest()


def csrf_token(session_token: str, secret: str) -> str:
    """Bound to one session: a token from another session or another site's guess will not match."""
    return hmac.new(secret.encode("utf-8"), f"csrf:{session_token}".encode(), hashlib.sha256).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
