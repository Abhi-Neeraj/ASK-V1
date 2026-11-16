"""Engine and session plumbing. The schema itself is created only by Alembic migrations."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from fastapi import Request
from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import CursorResult, Engine, Result
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Stable constraint names keep Alembic migrations reproducible across SQLite and PostgreSQL.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SessionFactory = sessionmaker[Session]


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _is_memory(url: str) -> bool:
    return url in {"sqlite://", "sqlite:///:memory:"}


def make_engine(url: str) -> Engine:
    # hide_parameters: a failed statement's error text otherwise carries the values it was storing (emails,
    # hashes,
    # transcript text) into the log.
    if not url.startswith("sqlite"):
        return create_engine(url, pool_pre_ping=True, hide_parameters=True)

    kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
    if _is_memory(url):
        kwargs["poolclass"] = StaticPool  # one shared in-memory database for the whole process
    else:
        path = url.removeprefix("sqlite:///")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, hide_parameters=True, **kwargs)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")  # SQLite ignores foreign keys unless asked
        cursor.execute("PRAGMA busy_timeout=5000")
        if not _is_memory(url):
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    """Request-scoped session. Handlers commit explicitly, so a response is only sent after the data
    it reports is durable. Anything left uncommitted is rolled back when the session closes."""
    factory: SessionFactory = request.app.state.session_factory
    with factory() as session:
        yield session


def rows_affected(result: Result[Any]) -> int:
    """How many rows an INSERT/UPDATE/DELETE touched (the typed view of `rowcount`)."""
    return int(cast("CursorResult[Any]", result).rowcount)
