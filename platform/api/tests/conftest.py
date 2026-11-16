from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base, make_engine, make_session_factory
from app.main import create_app
from app.services.email import MemoryEmailSender
from tests.helpers import DeviceClient, WebClient, add_company, add_member

# Run the ordinary suite on a real PostgreSQL instead of in-memory SQLite:
#   ASK_TEST_DATABASE_URL=postgresql+psycopg://user:pw@localhost:5432/ask_verify python -m pytest
# The database's schema is DROPPED before and after every test, so its name must contain "verify" or "test".
# A few tests are about SQLite itself (a database file, SQLite's migration rebuilds) and are skipped there.
PG_URL = os.environ.get("ASK_TEST_DATABASE_URL")
if PG_URL and not any(word in (make_url(PG_URL).database or "").lower() for word in ("verify", "test")):
    raise SystemExit(
        "ASK_TEST_DATABASE_URL must name a throwaway database (its name must contain 'verify' or 'test')"
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]  # never read a developer's real .env in tests
        env="test",
        secret_key=SecretStr("test-secret-key-0123456789-abcdefghijklmnop"),
        locket_master_key=SecretStr("test-master-key-0123456789abcdef0123456789"),
        database_url=PG_URL or "sqlite://",
        storage_dir=tmp_path / "recordings",
        email_backend="memory",
        run_background_tasks=False,  # tests call housekeeping directly so results are deterministic
        web_origins=["http://localhost:3000"],
        web_base_url="http://localhost:3000",
        cookie_secure=False,
        # Ordinary tests never depend on whether a sentence model happens to be installed on this machine.
        analysis_engine="lexical",
    )


def _fresh_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = make_engine(PG_URL or "sqlite://")
    if PG_URL:
        _fresh_schema(engine)
    Base.metadata.create_all(engine)
    yield engine
    if PG_URL:
        engine.dispose()
        engine = make_engine(PG_URL)
        _fresh_schema(engine)
    engine.dispose()


@pytest.fixture
def outbox() -> MemoryEmailSender:
    return MemoryEmailSender()


@pytest.fixture
def app(settings: Settings, engine: Engine, outbox: MemoryEmailSender):  # type: ignore[no-untyped-def]
    return create_app(settings, engine=engine, email=outbox)


@pytest.fixture
def db(app) -> Iterator[Session]:  # type: ignore[no-untyped-def]
    with make_session_factory(app.state.engine)() as session:
        yield session


@pytest.fixture
def web(app) -> Iterator[WebClient]:  # type: ignore[no-untyped-def]
    with TestClient(app) as client:
        yield WebClient(client)


def new_web(app) -> WebClient:  # type: ignore[no-untyped-def]
    """A second, independent browser (its own cookie jar) talking to the same app."""
    return WebClient(TestClient(app))


def provision_device(app, manager: WebClient, name: str = "Locket One"):  # type: ignore[no-untyped-def]
    """Create a device as `manager` and pair it. Returns (device json, paired DeviceClient)."""
    created = manager.post("/api/devices", json={"name": name})
    assert created.status_code == 201, created.text
    locket = DeviceClient(TestClient(app))
    paired = locket.pair(created.json()["code"])
    assert paired.status_code == 200, paired.text
    locket.credential = paired.json()["credential"]
    listing = manager.get("/api/devices").json()
    device = next(d for d in listing if d["deviceId"] == created.json()["deviceId"])
    return device, locket


@pytest.fixture
def file_app(tmp_path: Path, settings: Settings, outbox: MemoryEmailSender) -> Iterator[Any]:
    """The application on a real SQLite file, for tests where a second thread writes while the first
    reads (the shared in-memory database used elsewhere is one connection and unsafe for that).
    Timers are short so streams and leases can be watched in a fraction of a second."""
    url = f"sqlite:///{(tmp_path / 'live.sqlite3').as_posix()}"
    file_engine = make_engine(url)
    Base.metadata.create_all(file_engine)
    quick = settings.model_copy(
        update={
            "database_url": url,
            "event_poll_interval_seconds": 0.05,
            "event_keepalive_seconds": 0.2,
            "event_stream_max_seconds": 2.0,
        }
    )
    yield create_app(quick, engine=file_engine, email=outbox)
    file_engine.dispose()


@pytest.fixture
def acme(db: Session):  # type: ignore[no-untyped-def]
    """Company A with an administrator, a manager and an employee."""
    company = add_company(db, "Acme")
    return {
        "company": company,
        "admin": add_member(db, company, "admin@acme.example.com", "admin"),
        "manager": add_member(db, company, "manager@acme.example.com", "manager"),
        "employee": add_member(db, company, "employee@acme.example.com", "employee"),
    }


@pytest.fixture
def globex(db: Session):  # type: ignore[no-untyped-def]
    """Company B: a different tenant that must never be reachable from Acme."""
    company = add_company(db, "Globex")
    return {
        "company": company,
        "admin": add_member(db, company, "admin@globex.example.com", "admin"),
        "employee": add_member(db, company, "employee@globex.example.com", "employee"),
    }
