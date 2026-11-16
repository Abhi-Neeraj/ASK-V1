"""The locket secret and the six-digit pairing code. The same formula runs in the firmware, so these exact
values are shared with it: changing one side without the other breaks pairing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.errors import ApiError
from app.security.locket_codes import bond_proof, locket_secret, pairing_code, parse_nonce

MASTER = "test-master-key-0123456789abcdef0123456789"
SECRET_KEY = "k" * 40


@pytest.mark.parametrize(
    ("product_id", "nonce", "secret_hex", "code"),
    [
        (
            "ASK-7KQ2MX",
            "00112233445566778899aabbccddeeff",
            "89fbcaf408d79a1e3879d5f3559f7b38d63d1b45c74450ae3cefea6b2b798b04",
            "403836",
        ),
        (
            "ASK-ABCDEF",
            "ffffffffffffffffffffffffffffffff",
            "1f2bbb094260a90004e01c966a9c063fd7f81c44354820394347fc68c5fad0fa",
            "400246",
        ),
        (
            "ASK-7KQ2MX",
            "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
            "89fbcaf408d79a1e3879d5f3559f7b38d63d1b45c74450ae3cefea6b2b798b04",
            "778647",
        ),
    ],
)
def test_known_vectors(product_id: str, nonce: str, secret_hex: str, code: str) -> None:
    secret = locket_secret(MASTER, product_id)
    assert secret.hex() == secret_hex
    assert pairing_code(secret, bytes.fromhex(nonce)) == code


def test_code_is_always_six_digits() -> None:
    secret = locket_secret(MASTER, "ASK-7KQ2MX")
    for i in range(200):
        code = pairing_code(secret, i.to_bytes(16, "big"))
        assert len(code) == 6 and code.isdigit()


def test_parse_nonce_accepts_32_hex_characters() -> None:
    assert parse_nonce("00112233445566778899AABBCCDDEEFF") == bytes.fromhex(
        "00112233445566778899aabbccddeeff"
    )


@pytest.mark.parametrize("value", ["00" * 15, "00" * 17, "zz" * 16, "", " " + "00" * 16])
def test_parse_nonce_rejects_wrong_length_and_non_hex(value: str) -> None:
    with pytest.raises(ApiError) as caught:
        parse_nonce(value)
    assert caught.value.status == 422


def test_short_master_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(secret_key=SECRET_KEY, locket_master_key="short")


def test_master_key_must_differ_from_the_secret_key() -> None:
    with pytest.raises(ValidationError):
        Settings(secret_key=SECRET_KEY, locket_master_key=SECRET_KEY)


def test_google_provider_is_a_valid_choice() -> None:
    assert Settings(secret_key=SECRET_KEY, ai_provider="google").ai_provider == "google"


def test_production_google_needs_both_keys(tmp_path) -> None:  # type: ignore[no-untyped-def]
    base = {
        "secret_key": SECRET_KEY,
        "env": "production",
        "database_url": "postgresql+psycopg://u:p@db/ask",
        "email_backend": "smtp",
        "smtp_host": "smtp.example.com",
        "web_origins": "https://ask.example.com",
        "web_base_url": "https://ask.example.com",
        "ai_provider": "google",
    }
    with pytest.raises(ValidationError, match="ASK_GOOGLE_APPLICATION_CREDENTIALS"):
        Settings(**base, gemini_api_key="g" * 30)
    credentials = tmp_path / "service-account.json"
    credentials.write_text("{}", encoding="utf-8")
    with pytest.raises(ValidationError, match="ASK_GEMINI_API_KEY"):
        Settings(**base, google_application_credentials=credentials)
    ok = Settings(**base, google_application_credentials=credentials, gemini_api_key="g" * 30)
    assert ok.gemini_model


@pytest.mark.parametrize(
    ("secret_hex", "nonce", "proof"),
    [
        (
            "89fbcaf408d79a1e3879d5f3559f7b38d63d1b45c74450ae3cefea6b2b798b04",
            "00112233445566778899aabbccddeeff",
            "055ce7fed48fcd43c8693f2c210191a87603d1bc1ffe0afcb40772aa9ba6e21e",
        ),
        (
            "1f2bbb094260a90004e01c966a9c063fd7f81c44354820394347fc68c5fad0fa",
            "ffffffffffffffffffffffffffffffff",
            "d1cfced0427579ea3f74a396c731eaf581aab4f4dfe2720c154ab1dc431385ea",
        ),
    ],
)
def test_bond_proof_vectors(secret_hex: str, nonce: str, proof: str) -> None:
    """After Bluetooth pairing the locket shows the phone this proof (on an encrypted characteristic); only
    the real locket can make it, so the server knows the pairing really happened."""
    assert bond_proof(bytes.fromhex(secret_hex), bytes.fromhex(nonce)) == proof
