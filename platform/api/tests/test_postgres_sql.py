"""What can be checked about PostgreSQL without a PostgreSQL server.

Nothing here connects to a database or runs a statement. It generates the SQL that PostgreSQL would be
sent and reads it. That catches a migration that cannot even be written in that dialect, but it does NOT
show the SQL runs, performs, or behaves the same as on SQLite: that needs a real server."""

from __future__ import annotations

import io
import re
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.db import Base

API_DIR = Path(__file__).resolve().parents[1]
POSTGRES_URL = "postgresql+psycopg://user:password@localhost:5432/ask"


def offline_sql(direction: str) -> str:
    buffer = io.StringIO()
    config = Config(str(API_DIR / "alembic.ini"), output_buffer=buffer)
    config.set_main_option("sqlalchemy.url", POSTGRES_URL)
    config.set_main_option("script_location", str(API_DIR / "migrations"))
    if direction == "up":
        command.upgrade(config, "base:head", sql=True)
    else:
        command.downgrade(config, "head:base", sql=True)
    return buffer.getvalue()


def test_every_migration_can_be_written_for_postgresql() -> None:
    sql = offline_sql("up")
    created = set(re.findall(r"^CREATE TABLE (\w+)", sql, re.MULTILINE))
    assert created - {"alembic_version"} == set(Base.metadata.tables)


def test_postgresql_gets_its_own_column_types() -> None:
    sql = offline_sql("up")
    assert "JSONB" in sql  # structured details are indexable on PostgreSQL
    assert "UUID" in sql  # not strings
    assert "TIMESTAMP WITH TIME ZONE" in sql  # never naive times
    assert "BYTEA" in sql  # voice prints
    # Nothing SQLite-specific leaks into the PostgreSQL script.
    assert "PRAGMA" not in sql.upper()
    assert "sqlite" not in sql.lower()


def test_the_schema_can_also_be_taken_back_down() -> None:
    sql = offline_sql("down")
    dropped = set(re.findall(r"^DROP TABLE (\w+)", sql, re.MULTILINE))
    assert set(Base.metadata.tables) <= dropped


def test_existing_rows_are_never_rebuilt_away_on_postgresql() -> None:
    # SQLite changes some tables by copying and dropping them; PostgreSQL alters in place. A DROP TABLE of a
    # table that other tables reference, in the upgrade script, would be that SQLite pattern leaking over.
    up = offline_sql("up")
    assert not re.search(r"^DROP TABLE", up, re.MULTILINE)
    assert "_alembic_tmp_" not in up
