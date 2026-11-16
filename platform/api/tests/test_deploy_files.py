"""The deployment files can't be built here (no Docker), but they can be checked against the code they
configure: every variable they set must exist, and the values they produce must pass the production rules."""

from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.config import Settings

PLATFORM = Path(__file__).resolve().parents[2]
DEPLOY = PLATFORM / "deploy"

_VARIABLE = re.compile(r"\$\{([A-Z_0-9]+)(:[?-][^}]*)?\}")


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def filled_in_template() -> dict[str, str]:
    """The template as an operator would complete it."""
    env = parse_env_file(DEPLOY / ".env.example")
    env["POSTGRES_PASSWORD"] = secrets.token_hex(24)
    env["ASK_SECRET_KEY"] = secrets.token_urlsafe(48)
    env["ASK_SMTP_HOST"] = "smtp.example.com"
    return env


def substitute(text: str, env: dict[str, str]) -> str:
    """Docker Compose's ${NAME}, ${NAME:-default} and ${NAME:?message}, enough for these files."""

    def replace(match: re.Match[str]) -> str:
        name, modifier = match.group(1), match.group(2) or ""
        value = env.get(name, "")
        if value:
            return value
        if modifier.startswith(":?"):
            raise AssertionError(f"{name} is required but empty: {modifier[2:]}")
        return modifier[2:] if modifier.startswith(":-") else ""

    return _VARIABLE.sub(replace, text)


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))


def api_environment(compose: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    raw = compose["services"]["api"]["environment"]
    return {key: substitute(str(value), env) for key, value in raw.items()}


def settings_from(environment: dict[str, str]) -> Settings:
    fields = {key.removeprefix("ASK_").lower(): value for key, value in environment.items()}
    return Settings(_env_file=None, **fields)  # type: ignore[call-arg]


class TestVariablesAreReal:
    def test_every_setting_in_the_template_exists(self) -> None:
        # pydantic-settings silently ignores a variable it does not know, so a typo would do nothing.
        fields = set(Settings.model_fields)
        unknown = [
            key
            for key in parse_env_file(DEPLOY / ".env.example")
            if key.startswith("ASK_") and key.removeprefix("ASK_").lower() not in fields
        ]
        assert unknown == []

    def test_every_setting_the_compose_file_sets_exists(self, compose: dict[str, Any]) -> None:
        fields = set(Settings.model_fields)
        for service in ("api", "worker", "migrate"):
            environment = compose["services"][service].get("environment", {})
            for key in environment:
                if key.startswith("ASK_"):
                    assert key.removeprefix("ASK_").lower() in fields, (service, key)

    def test_every_variable_compose_reads_is_in_the_template(self, compose: dict[str, Any]) -> None:
        template = set(parse_env_file(DEPLOY / ".env.example"))
        text = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
        used = {m.group(1) for m in _VARIABLE.finditer(text)}
        assert used - template == set(), "compose reads variables the template never tells anyone to set"

    def test_the_template_mentions_nothing_it_does_not_use(self) -> None:
        text = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
        for key in parse_env_file(DEPLOY / ".env.example"):
            assert key in text, f"{key} is in the template but compose never reads it"


class TestProductionRulesAccept:
    def test_a_completed_template_makes_valid_production_settings(self, compose: dict[str, Any]) -> None:
        settings = settings_from(api_environment(compose, filled_in_template()))
        assert settings.env == "production"
        assert settings.secure_cookies is True
        assert not settings.is_sqlite
        assert settings.database_url.startswith("postgresql+psycopg://ask:")
        assert settings.email_backend == "smtp"
        assert settings.web_origins == ["https://ask.example.com"]
        assert settings.ai_provider == "none"
        assert settings.trust_forwarded_for is True

    def test_the_worker_gets_the_same_settings_as_the_api(self, compose: dict[str, Any]) -> None:
        # Both use one anchor, so they cannot drift: a worker with a different database would be a bug.
        assert compose["services"]["worker"]["environment"] == compose["services"]["api"]["environment"]

    def test_an_empty_secret_or_password_is_refused_before_anything_starts(
        self, compose: dict[str, Any]
    ) -> None:
        for name in ("ASK_SECRET_KEY", "POSTGRES_PASSWORD", "PUBLIC_DOMAIN"):
            env = filled_in_template()
            env[name] = ""
            with pytest.raises(AssertionError, match=name):
                substitute((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"), env)

    def test_the_example_values_alone_are_not_a_working_deployment(self, compose: dict[str, Any]) -> None:
        # An operator who copies the template and forgets the secret gets an error, not a weak default.
        with pytest.raises(AssertionError):
            api_environment(compose, parse_env_file(DEPLOY / ".env.example"))

    def test_the_stub_and_sqlite_and_console_email_stay_refused(self, compose: dict[str, Any]) -> None:
        good = api_environment(compose, filled_in_template())
        for change in (
            {"ASK_AI_PROVIDER": "stub"},
            {"ASK_DATABASE_URL": "sqlite:///x.db"},
            {"ASK_EMAIL_BACKEND": "console"},
            {"ASK_SECRET_KEY": "change-me-" + "x" * 40},
            {"ASK_WEB_ORIGINS": "*"},
        ):
            with pytest.raises(ValidationError):
                settings_from({**good, **change})


class TestComposeShape:
    def test_only_the_proxy_is_reachable_from_outside(self, compose: dict[str, Any]) -> None:
        published = {name: svc["ports"] for name, svc in compose["services"].items() if "ports" in svc}
        assert list(published) == ["proxy"]
        assert sorted(published["proxy"]) == ["443:443", "80:80"]

    def test_the_schema_is_migrated_before_anything_uses_it(self, compose: dict[str, Any]) -> None:
        services = compose["services"]
        assert services["migrate"]["restart"] == "no"
        assert services["migrate"]["depends_on"]["db"]["condition"] == "service_healthy"
        for name in ("api", "worker"):
            assert services[name]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
        assert services["db"]["healthcheck"]["test"]

    def test_the_worker_does_not_start_unless_asked_for(self, compose: dict[str, Any]) -> None:
        # With no provider chosen the worker exits at once, which under "restart: unless-stopped" would loop.
        assert compose["services"]["worker"]["profiles"] == ["processing"]
        assert "profiles" not in compose["services"]["api"]

    def test_the_api_reports_whether_it_is_really_ready(self, compose: dict[str, Any]) -> None:
        check = " ".join(compose["services"]["api"]["healthcheck"]["test"])
        assert "/api/ready" in check

    def test_recordings_and_the_database_live_on_named_volumes(self, compose: dict[str, Any]) -> None:
        assert {"pgdata", "audio"} <= set(compose["volumes"])
        assert "pgdata:/var/lib/postgresql/data" in compose["services"]["db"]["volumes"]
        assert "audio:/data/recordings" in compose["services"]["api"]["volumes"]
        assert "audio:/data/recordings" in compose["services"]["worker"]["volumes"]

    def test_no_password_or_key_is_written_into_the_file(self) -> None:
        text = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
        for line in text.splitlines():
            if re.search(r"(PASSWORD|SECRET|TOKEN)\w*:", line):
                assert "${" in line, f"a value looks hard-coded: {line.strip()}"

    def test_the_web_image_ships_only_the_exported_app(self, compose: dict[str, Any]) -> None:
        # The frontend is the ASK app's static web export, served by a dependency-free script.
        assert compose["services"]["web"]["build"]["context"] == "../web"
        text = (PLATFORM / "web" / "Dockerfile").read_text(encoding="utf-8")
        assert "npm run build:web" in text
        assert "COPY --from=build /app/dist ./dist" in text
        assert "node_modules" not in "".join(re.findall(r"^COPY --from=build .*$", text, re.MULTILINE))


class TestProxy:
    def test_the_live_stream_is_neither_buffered_nor_compressed(self) -> None:
        text = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
        events = text[text.index("handle /api/events") : text.index("handle /api/*")]
        assert "flush_interval -1" in events
        assert "encode" not in events

    def test_the_stream_rule_comes_before_the_general_api_rule(self) -> None:
        text = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
        assert text.index("handle /api/events") < text.index("handle /api/*")

    def test_the_api_and_the_web_app_each_get_their_traffic(self) -> None:
        text = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
        assert "reverse_proxy api:8000" in text and "reverse_proxy web:3000" in text
        assert "Strict-Transport-Security" in text


class TestImages:
    def test_the_api_image_does_not_run_as_root_and_carries_no_secrets(self) -> None:
        text = (PLATFORM / "api" / "Dockerfile").read_text(encoding="utf-8")
        assert re.search(r"^USER ask$", text, re.MULTILINE)
        assert "COPY .env" not in text and ".env" not in re.findall(r"^COPY .*$", text, re.MULTILINE)
        assert "--factory" in text

    def test_the_web_image_does_not_run_as_root(self) -> None:
        assert re.search(
            r"^USER node$", (PLATFORM / "web" / "Dockerfile").read_text(encoding="utf-8"), re.MULTILINE
        )

    def test_local_secrets_stay_out_of_the_build_context(self) -> None:
        for folder in ("api", "web"):
            ignored = (PLATFORM / folder / ".dockerignore").read_text(encoding="utf-8").splitlines()
            assert ".env" in ignored
