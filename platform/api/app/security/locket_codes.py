"""The locket secret and the six-digit pairing code.

A locket's secret is never stored: it is HMAC-SHA256(ASK_LOCKET_MASTER_KEY, product ID), recomputed when
needed and written into the locket once at manufacture. When someone pairs, the locket hands the phone a
fresh 16-byte number (the nonce); the server derives the code from the secret and that number and emails it.
The locket derives the same code itself, with no internet, and accepts it as the Bluetooth passkey.

The firmware implements exactly the same formula (platform/firmware/locket_ble), checked against the same
test vectors (tests/test_locket_codes.py).
"""

from __future__ import annotations

import hashlib
import hmac
import re

from app.errors import ApiError

NONCE_BYTES = 16
_NONCE = re.compile(r"^[0-9a-fA-F]{32}$")
_CODE_LABEL = b"ASK-PAIR"
_PROOF_LABEL = b"ASK-BONDED"
_PROOF = re.compile(r"^[0-9a-f]{64}$")


def locket_secret(master_key: str, product_id: str) -> bytes:
    """The locket's 32-byte secret. Never log or return it except once, at registration."""
    return hmac.new(master_key.encode("utf-8"), product_id.encode("utf-8"), hashlib.sha256).digest()


def pairing_code(secret: bytes, nonce: bytes) -> str:
    """Six digits from the secret and the locket's nonce: the first four bytes of the HMAC, big-endian,
    modulo one million, zero-padded."""
    digest = hmac.new(secret, _CODE_LABEL + nonce, hashlib.sha256).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


def bond_proof(secret: bytes, nonce: bytes) -> str:
    """What the locket shows the phone, on an encrypted characteristic, once Bluetooth pairing with this nonce
    succeeded: HMAC-SHA256(secret, "ASK-BONDED" + nonce) as 64 hex characters. Only the real locket can make
    it, so the server knows the person really paired instead of just asking for a code."""
    return hmac.new(secret, _PROOF_LABEL + nonce, hashlib.sha256).hexdigest()


def proof_matches(secret: bytes, nonce: bytes, proof: str) -> bool:
    return bool(_PROOF.match(proof or "")) and hmac.compare_digest(proof, bond_proof(secret, nonce))


def parse_nonce(value: str) -> bytes:
    """The nonce as the app sends it: exactly 32 hexadecimal characters."""
    if not isinstance(value, str) or not _NONCE.match(value):
        raise ApiError(
            422,
            "validation_error",
            "The locket's pairing number must be 32 hexadecimal characters.",
            {"nonce": "Must be 32 hexadecimal characters."},
        )
    return bytes.fromhex(value)
