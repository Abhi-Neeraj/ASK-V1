"""The operator CLI. Every test points the CLI at a temporary directory, never at real files."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app import cli
from app.config import API_DIR, Settings, get_settings, reset_settings_cache
from app.db import make_engine, make_session_factory
from app.models import AuditEvent, AuthSession, Membership, User
from app.services import accounts

STRONG_PASSWORD = "correct-horse-battery-staple-42"


@pytest.fixture
def local_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throwaway database and storage directory, selected through the environment like production."""
    database = tmp_path / "cli.sqlite3"
    monkeypatch.setenv("ASK_SECRET_KEY", "cli-test-secret-key-0123456789-abcdefghij")
    monkeypatch.setenv("ASK_DATABASE_URL", f"sqlite:///{database.as_posix()}")
    monkeypatch.setenv("ASK_STORAGE_DIR", str(tmp_path / "recordings"))
    monkeypatch.setenv("ASK_ENV", "test")
    reset_settings_cache()
    yield database
    reset_settings_cache()


def migrate(database: Path) -> None:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "head")


def test_generate_env_writes_a_strong_secret_and_never_prints_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / ".env"
    monkeypatch.setattr(cli, "ENV_FILE", target)

    assert cli.main(["generate-env"]) == 0

    content = target.read_text(encoding="utf-8")
    secret = next(line for line in content.splitlines() if line.startswith("ASK_SECRET_KEY=")).split("=", 1)[
        1
    ]
    assert len(secret) >= 32
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    # The generated file is accepted by the real settings loader.
    loaded = Settings(_env_file=target)
    assert loaded.secret_key.get_secret_value() == secret


def test_generate_env_does_not_overwrite_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".env"
    target.write_text("ASK_SECRET_KEY=keep-me-keep-me-keep-me-keep-me-keep-me\n", encoding="utf-8")
    monkeypatch.setattr(cli, "ENV_FILE", target)

    assert cli.main(["generate-env"]) == 1
    assert "keep-me" in target.read_text(encoding="utf-8")

    assert cli.main(["generate-env", "--force"]) == 0
    assert "keep-me" not in target.read_text(encoding="utf-8")


def test_check_reports_missing_migrations_then_passes(local_env: Path) -> None:
    assert cli.main(["check"]) == 1
    migrate(local_env)
    assert cli.main(["check"]) == 0


def test_bootstrap_creates_company_admin_and_audit_entry(
    local_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate(local_env)
    monkeypatch.setattr("sys.stdin", io.StringIO(STRONG_PASSWORD + "\n"))

    code = cli.main(
        [
            "bootstrap",
            "--company",
            "Acme Works",
            "--name",
            "Ada Admin",
            "--email",
            "Ada@Acme.Example.com",
            "--password-stdin",
        ]
    )
    assert code == 0

    engine = make_engine(get_settings().database_url)
    with make_session_factory(engine)() as db:
        user = db.scalar(select(User))
        assert user is not None
        assert user.email == "ada@acme.example.com"
        assert user.password_hash != STRONG_PASSWORD
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
        assert membership is not None and membership.role == "admin"
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "company.created")) is not None
    engine.dispose()


def test_bootstrap_refuses_a_weak_password_and_a_duplicate_email(
    local_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    migrate(local_env)
    args = [
        "bootstrap",
        "--company",
        "Acme",
        "--name",
        "Ada",
        "--email",
        "ada@acme.example.com",
        "--password-stdin",
    ]

    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert cli.main(args) == 1

    monkeypatch.setattr("sys.stdin", io.StringIO(STRONG_PASSWORD + "\n"))
    assert cli.main(args) == 0

    monkeypatch.setattr("sys.stdin", io.StringIO(STRONG_PASSWORD + "\n"))
    assert cli.main(args) == 1

    output = capsys.readouterr()
    assert STRONG_PASSWORD not in output.out + output.err


def bootstrap_args(email: str = "ada@acme.example.com") -> list[str]:
    return ["bootstrap", "--company", "Acme", "--name", "Ada", "--email", email, "--password-stdin"]


def test_a_byte_order_mark_from_powershell_pipes_is_not_part_of_the_password(
    local_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate(local_env)
    monkeypatch.setattr("sys.stdin", io.StringIO("﻿" + STRONG_PASSWORD + "\r\n"))
    assert cli.main(bootstrap_args()) == 0

    engine = make_engine(get_settings().database_url)
    with make_session_factory(engine)() as db:
        assert accounts.authenticate(db, "ada@acme.example.com", STRONG_PASSWORD) is not None
    engine.dispose()


def test_reset_password_changes_the_password_ends_sessions_and_is_audited(
    local_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate(local_env)
    monkeypatch.setattr("sys.stdin", io.StringIO(STRONG_PASSWORD + "\n"))
    assert cli.main(bootstrap_args()) == 0

    engine = make_engine(get_settings().database_url)
    factory = make_session_factory(engine)
    with factory() as db:
        user = db.scalar(select(User))
        assert user is not None
        membership = accounts.primary_membership(db, user.id)
        assert membership is not None
        accounts.start_session(db, get_settings(), user, membership.company_id, "127.0.0.1", "pytest")
        db.commit()

    new_password = "a-completely-different-passphrase-77"
    monkeypatch.setattr("sys.stdin", io.StringIO(new_password + "\n"))
    assert cli.main(["reset-password", "--email", "ADA@acme.example.com", "--password-stdin"]) == 0

    with factory() as db:
        assert accounts.authenticate(db, "ada@acme.example.com", new_password) is not None
        assert accounts.authenticate(db, "ada@acme.example.com", STRONG_PASSWORD) is None
        live = db.scalars(select(AuthSession).where(AuthSession.revoked_at.is_(None))).all()
        assert live == []
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.password_reset_by_operator"))
        assert audit is not None
        assert new_password not in str(audit.detail)
    engine.dispose()


def test_reset_password_refuses_unknown_accounts_and_weak_passwords(
    local_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate(local_env)
    monkeypatch.setattr("sys.stdin", io.StringIO(STRONG_PASSWORD + "\n"))
    assert cli.main(bootstrap_args()) == 0

    monkeypatch.setattr("sys.stdin", io.StringIO(STRONG_PASSWORD + "\n"))
    assert cli.main(["reset-password", "--email", "nobody@acme.example.com", "--password-stdin"]) == 1

    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert cli.main(["reset-password", "--email", "ada@acme.example.com", "--password-stdin"]) == 1

    engine = make_engine(get_settings().database_url)
    with make_session_factory(engine)() as db:
        assert accounts.authenticate(db, "ada@acme.example.com", STRONG_PASSWORD) is not None
    engine.dispose()


def test_housekeeping_runs_and_reports_each_step(local_env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    migrate(local_env)
    assert cli.main(["housekeeping"]) == 0
    printed = capsys.readouterr().out
    assert "devices_marked_offline: 0" in printed
    assert "recordings_purged: 0" in printed


def test_register_locket_prints_the_product_id_and_secret_once(
    local_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.models import Company, Device
    from app.security.locket_codes import locket_secret

    master = "cli-locket-master-key-0123456789abcdefghij"
    monkeypatch.setenv("ASK_LOCKET_MASTER_KEY", master)
    reset_settings_cache()
    migrate(local_env)
    engine = make_engine(get_settings().database_url)
    with make_session_factory(engine)() as db:
        company = Company(name="Acme")
        db.add(company)
        db.commit()
        company_id = company.id

    assert cli.main(["register-locket", "--company-id", str(company_id), "--name", "Factory 1"]) == 0
    printed = capsys.readouterr().out
    with make_session_factory(engine)() as db:
        device = db.scalar(select(Device))
        assert device is not None and device.company_id == company_id and device.status == "active"
        assert device.public_id in printed
        assert locket_secret(master, device.public_id).hex() in printed
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "locket.registered")) is not None
    engine.dispose()


def test_register_locket_needs_a_known_company(local_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASK_LOCKET_MASTER_KEY", "cli-locket-master-key-0123456789abcdefghij")
    reset_settings_cache()
    migrate(local_env)
    unknown = "00000000-0000-0000-0000-000000000000"
    assert cli.main(["register-locket", "--company-id", unknown, "--name", "x"]) == 1


BOOTSTRAP_ENV = {
    "ASK_BOOTSTRAP_COMPANY": "Acme Works",
    "ASK_BOOTSTRAP_NAME": "Ada Admin",
    "ASK_BOOTSTRAP_EMAIL": "ada@acme.example.com",
    "ASK_BOOTSTRAP_PASSWORD": STRONG_PASSWORD,
}


def _set_bootstrap_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for key, value in {**BOOTSTRAP_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)


def _users(database: Path) -> list[User]:
    engine = make_engine(f"sqlite:///{database.as_posix()}")
    try:
        with make_session_factory(engine)() as db:
            return list(db.scalars(select(User)))
    finally:
        engine.dispose()


def test_bootstrap_from_env_creates_the_first_company_once(
    local_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A host with no console (Render free) creates its first administrator at start-up from its settings."""
    migrate(local_env)
    _set_bootstrap_env(monkeypatch)
    assert cli.main(["bootstrap-from-env"]) == 0
    assert [u.email for u in _users(local_env)] == ["ada@acme.example.com"]
    assert cli.main(["bootstrap-from-env"]) == 0  # every later start: nothing to do
    assert len(_users(local_env)) == 1
    assert STRONG_PASSWORD not in capsys.readouterr().out + capsys.readouterr().err


def test_bootstrap_from_env_never_adds_a_company_beside_an_existing_one(
    local_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrate(local_env)
    _set_bootstrap_env(monkeypatch)
    assert cli.main(["bootstrap-from-env"]) == 0
    _set_bootstrap_env(monkeypatch, ASK_BOOTSTRAP_EMAIL="other@elsewhere.example.com")
    assert cli.main(["bootstrap-from-env"]) == 0
    assert [u.email for u in _users(local_env)] == ["ada@acme.example.com"]


def test_bootstrap_from_env_does_nothing_without_its_settings(local_env: Path) -> None:
    migrate(local_env)
    for key in BOOTSTRAP_ENV:
        assert key not in __import__("os").environ
    assert cli.main(["bootstrap-from-env"]) == 0
    assert _users(local_env) == []


def test_bootstrap_from_env_refuses_a_weak_password(local_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    migrate(local_env)
    _set_bootstrap_env(monkeypatch, ASK_BOOTSTRAP_PASSWORD="short")
    assert cli.main(["bootstrap-from-env"]) == 1
    assert _users(local_env) == []
