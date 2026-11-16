from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.config import DatabaseSettings, Settings

GOOD_SECRET = "a-long-random-secret-for-tests-0123456789"


def make(**overrides: object) -> Settings:
    values: dict[str, object] = {"secret_key": SecretStr(GOOD_SECRET), "database_url": "sqlite://"}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_secret_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASK_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError, match="secret_key"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_short_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        make(secret_key=SecretStr("too-short"))


def test_placeholder_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="placeholder"):
        make(secret_key=SecretStr("change-me-" + "x" * 30))


def test_wildcard_origin_is_rejected() -> None:
    with pytest.raises(ValidationError, match="explicit origins"):
        make(web_origins=["*"])


def test_lists_can_be_comma_separated_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASK_SECRET_KEY", GOOD_SECRET)
    monkeypatch.setenv("ASK_WEB_ORIGINS", "http://a.test, http://b.test")
    monkeypatch.setenv("ASK_ALLOWED_SAMPLE_RATES", "16000,44100")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.web_origins == ["http://a.test", "http://b.test"]
    assert settings.allowed_sample_rates == [16000, 44100]


def test_cookies_are_secure_exactly_in_production_by_default() -> None:
    assert make().secure_cookies is False
    prod = make(
        env="production",
        database_url="postgresql+psycopg://u:p@db/ask",
        email_backend="smtp",
        smtp_host="smtp.example.test",
        web_origins=["https://ask.example.test"],
        web_base_url="https://ask.example.test",
    )
    assert prod.secure_cookies is True


class TestSimulatedLockets:
    production = {
        "env": "production",
        "database_url": "postgresql+psycopg://u:p@db/ask",
        "email_backend": "smtp",
        "smtp_host": "smtp.example.test",
        "web_origins": ["https://ask.example.test"],
        "web_base_url": "https://ask.example.test",
    }

    def test_allowed_in_development_and_test_by_default(self) -> None:
        assert make().simulated_devices_allowed is True
        assert make(env="test").simulated_devices_allowed is True

    def test_refused_in_production_by_default(self) -> None:
        assert make(**self.production).simulated_devices_allowed is False

    def test_production_can_opt_in_deliberately(self) -> None:
        assert make(**self.production, allow_simulated_devices=True).simulated_devices_allowed is True

    def test_development_can_switch_them_off(self) -> None:
        assert make(allow_simulated_devices=False).simulated_devices_allowed is False

    def test_it_is_read_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ASK_SECRET_KEY", GOOD_SECRET)
        monkeypatch.setenv("ASK_ALLOW_SIMULATED_DEVICES", "false")
        assert Settings(_env_file=None).simulated_devices_allowed is False  # type: ignore[call-arg]


class TestProductionRules:
    base = {
        "env": "production",
        "database_url": "postgresql+psycopg://u:p@db/ask",
        "email_backend": "smtp",
        "smtp_host": "smtp.example.test",
        "web_origins": ["https://ask.example.test"],
        "web_base_url": "https://ask.example.test",
    }

    def test_valid_production_settings(self) -> None:
        assert make(**self.base).env == "production"

    def test_sqlite_is_not_allowed(self) -> None:
        with pytest.raises(ValidationError, match="PostgreSQL"):
            make(**{**self.base, "database_url": "sqlite:///x.db"})

    def test_insecure_cookies_are_not_allowed(self) -> None:
        with pytest.raises(ValidationError, match="secure cookies"):
            make(**{**self.base, "cookie_secure": False})

    def test_console_email_is_not_allowed(self) -> None:
        with pytest.raises(ValidationError, match="smtp"):
            make(**{**self.base, "email_backend": "console"})


def test_smtp_needs_a_host() -> None:
    with pytest.raises(ValidationError, match="SMTP_HOST"):
        make(email_backend="smtp")


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql://u:p@db.example:5432/ask", "postgresql+psycopg://u:p@db.example:5432/ask"),
        ("postgres://u:p@db.example/ask", "postgresql+psycopg://u:p@db.example/ask"),
        ("postgresql+psycopg://u:p@db.example/ask", "postgresql+psycopg://u:p@db.example/ask"),
        ("sqlite://", "sqlite://"),
    ],
)
def test_a_hosted_postgres_url_gets_the_installed_driver(given: str, expected: str) -> None:
    """Hosts such as Render hand out `postgresql://` (or `postgres://`); only psycopg 3 is installed."""
    assert make(database_url=given).database_url == expected
    assert DatabaseSettings(_env_file=None, database_url=given).database_url == expected  # type: ignore[call-arg]
