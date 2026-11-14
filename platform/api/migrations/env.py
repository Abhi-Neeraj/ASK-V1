"""Alembic environment. The schema is only ever created and changed by migrations in production."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.config import DatabaseSettings
from app.db import Base, make_engine

config = context.config
if config.config_file_name is not None:
    # Do not silence loggers that already exist. By default this would switch off every application
    # logger created before a migration runs, which hides errors (and breaks tests that read the log).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    """Which database objects the drift check compares.

    One exception. SQLite cannot add a constraint to an existing table, so migration 0004 adds
    `recordings.assigned_user_id` with its REFERENCES clause written inline, which SQLite stores and enforces
    (the data test proves ON DELETE SET NULL works). SQLAlchemy can read a constraint's name and ON DELETE
    option
    back from SQLite only when it is a table-level constraint, so it reports a difference that is not real.
    """
    unreadable = (
        type_ == "foreign_key_constraint"
        and context.get_context().dialect.name == "sqlite"
        and getattr(obj.table, "name", None) == "recordings"
        and list(obj.column_keys) == ["assigned_user_id"]
    )
    return not unreadable


def _database_url() -> str:
    # Tests and tooling can pass a URL explicitly; otherwise ASK_DATABASE_URL / .env decides.
    return config.get_main_option("sqlalchemy.url") or DatabaseSettings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _sqlite_foreign_keys(connection: Connection, enabled: bool) -> None:
    """Switch foreign key enforcement on or off for this connection (SQLite only).

    SQLite cannot ALTER most things in place, so batch mode rebuilds a table: copy it, drop the
    original, rename the copy. With foreign keys enforced, dropping the original cascades and deletes
    every row in every table that references it (recordings, credentials, ...). SQLite's own guidance
    is to turn enforcement off for the rebuild and verify afterwards. The pragma does nothing inside
    a transaction, so it goes straight to the driver, ahead of SQLAlchemy's transaction handling.
    """
    cursor = connection.connection.cursor()
    try:
        cursor.execute(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")
    finally:
        cursor.close()


def _sqlite_assert_no_orphans(connection: Connection) -> None:
    cursor = connection.connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_key_check")
        broken = cursor.fetchall()
    finally:
        cursor.close()
    if broken:
        raise RuntimeError(f"The migration left {len(broken)} row(s) pointing at rows that no longer exist.")


def run_migrations_online() -> None:
    engine = make_engine(_database_url())
    with engine.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            _sqlite_foreign_keys(connection, False)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things in place; batch mode rebuilds the table instead.
            render_as_batch=True,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
        if sqlite:
            _sqlite_assert_no_orphans(connection)
            _sqlite_foreign_keys(connection, True)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
