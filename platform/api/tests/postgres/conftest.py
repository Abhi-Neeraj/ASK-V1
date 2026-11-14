"""Tests that need a REAL PostgreSQL. They are skipped unless you point them at one.

They were run against a portable PostgreSQL 16.15 on Windows (all passing), not yet against the
`postgres:16` Docker image.

    # a throwaway database whose name contains "verify" or "test"; its schema is DROPPED and re-created
    $env:ASK_VERIFY_DATABASE_URL = "postgresql+psycopg://user:password@localhost:5432/ask_verify"
    # the pg_dump / pg_restore test also needs the PostgreSQL tools (PATH, or this folder)
    $env:ASK_PG_BIN = "C:/Program Files/PostgreSQL/16/bin"
    python -m pytest tests/postgres -q

The whole ordinary suite can run on the same kind of database with ASK_TEST_DATABASE_URL
(see tests/conftest.py).

With Docker, one way to get a throwaway server is:
    docker run --rm -p 5432:5432 -e POSTGRES_PASSWORD=verify -e POSTGRES_DB=ask_verify postgres:16-alpine
    $env:ASK_VERIFY_DATABASE_URL = "postgresql+psycopg://postgres:verify@localhost:5432/ask_verify"
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url

from app.db import make_engine


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("ASK_VERIFY_DATABASE_URL"):
        return
    skip = pytest.mark.skip(
        reason="PostgreSQL tests: set ASK_VERIFY_DATABASE_URL (see tests/postgres/conftest.py)"
    )
    for item in items:
        if "tests/postgres" in item.nodeid.replace("\\", "/"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def pg_url() -> str:
    url = os.environ["ASK_VERIFY_DATABASE_URL"]
    parsed = make_url(url)
    if not parsed.get_backend_name().startswith("postgres"):
        pytest.fail("ASK_VERIFY_DATABASE_URL must be a PostgreSQL URL")
    name = (parsed.database or "").lower()
    if "verify" not in name and "test" not in name:
        pytest.fail(
            f"Refusing to use database {name!r}: these tests DROP the schema, so the database name must "
            "contain 'verify' or 'test'."
        )
    return url


@pytest.fixture
def pg_engine(pg_url: str) -> Iterator[Engine]:
    """An engine on a database with an empty schema (nothing created yet)."""
    engine = make_engine(pg_url)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()
