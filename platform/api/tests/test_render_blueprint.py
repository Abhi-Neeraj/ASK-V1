"""render.yaml (the Render Blueprint) can't be deployed here, but it can be checked against the code it
configures: every ASK_* variable it sets must be a real setting, and the address handling must stay on the
safe value."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.config import Settings

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def env_vars() -> dict[str, dict[str, Any]]:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    [service] = [s for s in blueprint["services"] if s["name"] == "ask-api"]
    return {item["key"]: item for item in service["envVars"]}


def test_every_ask_variable_in_the_blueprint_is_a_real_setting(env_vars: dict[str, dict[str, Any]]) -> None:
    known = {name.upper() for name in Settings.model_fields}
    for key in env_vars:
        if key.startswith("ASK_") and not key.startswith("ASK_BOOTSTRAP_"):
            name = key.removeprefix("ASK_")
            assert name in known, f"{key} in render.yaml is not a setting the server reads"


def test_the_server_reads_the_client_address_from_the_last_forwarded_entry(
    env_vars: dict[str, dict[str, Any]],
) -> None:
    assert env_vars["ASK_TRUST_FORWARDED_FOR"]["value"] == "true"
    # One hop reads the entry the proxy in front of the service wrote, which a caller cannot choose. A higher
    # number may only be set after seeing what the header really holds behind Vercel (docs/SECURITY.md).
    assert env_vars["ASK_TRUSTED_PROXY_HOPS"]["value"] == "1"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        secret_key="x" * 40,  # type: ignore[arg-type]
        trust_forwarded_for=True,
        trusted_proxy_hops=int(env_vars["ASK_TRUSTED_PROXY_HOPS"]["value"]),
    )
    assert settings.trusted_proxy_hops == 1


def test_no_secret_is_written_into_the_blueprint(env_vars: dict[str, dict[str, Any]]) -> None:
    # Every secret, and the database address (it holds a password), is entered by the owner.
    for key in (
        "ASK_GOOGLE_STT_API_KEY",
        "ASK_GEMINI_API_KEY",
        "ASK_SECRET_KEY",
        "ASK_LOCKET_MASTER_KEY",
        "ASK_DATABASE_URL",
    ):
        assert env_vars[key].get("sync") is False and "value" not in env_vars[key]
    assert not any(
        key.startswith("ASK_BOOTSTRAP_") for key in env_vars
    )  # the data is restored, not bootstrapped


def test_the_database_is_not_a_render_database() -> None:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    assert "databases" not in blueprint  # Render's free database is deleted after 30 days: Neon holds it
    [service] = [s for s in blueprint["services"] if s["name"] == "ask-api"]
    assert service["region"] == "singapore" and service["plan"] == "free"
