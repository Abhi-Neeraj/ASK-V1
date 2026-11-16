"""Migration 0006: Bluetooth lockets.

It adds a column to `devices` and one to `transcripts` (tables other tables point at), and new tables. This
seeds real rows at 0005 and proves they all survive, that who-is-on-which-locket and who-sees-which-recording
are carried over exactly, and that a locket's product ID can no longer be changed in the database."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError

from tests.test_migration_wearer import EXPECTED, counts, seed, url_of

API_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic_config(tmp_path: Path) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{(tmp_path / 'lockets.sqlite3').as_posix()}")
    return config


def rows(config: Config, sql: str) -> list[tuple[object, ...]]:
    engine = create_engine(url_of(config))
    try:
        with engine.connect() as connection:
            return [tuple(r) for r in connection.execute(text(sql))]
    finally:
        engine.dispose()


def wear(config: Config, recording_id: str, user_id: str) -> None:
    """At 0005 a wearer is set on arrival; the shared seed inserts rows directly, so set it here."""
    engine = create_engine(url_of(config))
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE recordings SET assigned_user_id = :u WHERE id = :r"),
                {"u": user_id, "r": recording_id},
            )
    finally:
        engine.dispose()


def test_upgrade_keeps_devices_recordings_and_backfills_members(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "0005")
    ids = seed(alembic_config)
    wear(alembic_config, ids["recording_worn"], ids["ana"])
    command.upgrade(alembic_config, "0006")

    assert counts(alembic_config) == EXPECTED
    assert rows(alembic_config, "SELECT device_id, user_id FROM device_members") == [
        (ids["worn"], ids["ana"])
    ]
    assert rows(alembic_config, "SELECT recording_id, user_id FROM recording_access") == [
        (ids["recording_worn"], ids["ana"])
    ]
    assert sorted(rows(alembic_config, "SELECT max_users FROM devices")) == [(1,), (1,)]
    assert rows(alembic_config, "SELECT summary_text FROM transcripts") == [(None,), (None,)]


def test_public_id_cannot_change_after_upgrade(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "0005")
    seed(alembic_config)
    command.upgrade(alembic_config, "0006")

    engine = create_engine(url_of(alembic_config))
    try:
        with pytest.raises(DatabaseError, match="product ID is permanent"), engine.begin() as connection:
            connection.execute(
                text("UPDATE devices SET public_id = 'ASK-ZZZZZZ' WHERE public_id = 'ASK-WORN1'")
            )
        with engine.begin() as connection:  # other columns still change freely
            connection.execute(text("UPDATE devices SET name = 'Renamed' WHERE public_id = 'ASK-WORN1'"))
    finally:
        engine.dispose()
    assert ("ASK-WORN1",) in rows(alembic_config, "SELECT public_id FROM devices")


def test_down_and_up_again(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "0005")
    ids = seed(alembic_config)
    command.upgrade(alembic_config, "0006")
    command.downgrade(alembic_config, "0005")
    assert counts(alembic_config) == EXPECTED
    command.upgrade(alembic_config, "0006")
    assert counts(alembic_config) == EXPECTED
    assert rows(alembic_config, "SELECT device_id, user_id FROM device_members") == [
        (ids["worn"], ids["ana"])
    ]
