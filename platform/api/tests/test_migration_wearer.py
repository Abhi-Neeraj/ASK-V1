"""Migration 0004: recordings remember who the locket was assigned to when they arrived.

It adds a column to `recordings`, a table that transcripts, segments and processing jobs all point at. Done
carelessly on SQLite that deletes all of them, so this seeds real child rows and proves they survive, that the
backfill keeps who could see what, and that the new column's foreign key is really enforced."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, text

from app.db import make_engine

API_DIR = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 25, 12, 0, 0)


@pytest.fixture
def alembic_config(tmp_path: Path) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{(tmp_path / 'wearer.sqlite3').as_posix()}")
    return config


def url_of(config: Config) -> str:
    return config.get_main_option("sqlalchemy.url") or ""


def seed(config: Config) -> dict[str, str]:
    """A company with two people and two lockets: one worn by Ana, one unassigned. Each has a recording that
    already has a transcript, segments and a processing job."""
    engine = make_engine(url_of(config))  # foreign keys enforced, as in the application
    ids = {name: uuid.uuid4().hex for name in ("company", "ana", "ben", "worn", "spare")}
    try:
        names = (
            "companies",
            "users",
            "devices",
            "recordings",
            "processing_jobs",
            "transcripts",
            "transcript_segments",
        )
        metadata = MetaData()
        t = {name: Table(name, metadata, autoload_with=engine) for name in names}
        with engine.begin() as c:
            c.execute(
                t["companies"]
                .insert()
                .values(id=ids["company"], name="Acme", purge_grace_days=7, created_at=NOW)
            )
            for key, email in (("ana", "ana@acme.example.com"), ("ben", "ben@acme.example.com")):
                c.execute(
                    t["users"]
                    .insert()
                    .values(
                        id=ids[key],
                        email=email,
                        name=key.title(),
                        password_hash="x",
                        is_active=True,
                        created_at=NOW,
                    )
                )
            for key, public_id, owner in (("worn", "ASK-WORN1", ids["ana"]), ("spare", "ASK-SPARE", None)):
                c.execute(
                    t["devices"]
                    .insert()
                    .values(
                        id=ids[key],
                        company_id=ids["company"],
                        public_id=public_id,
                        name=key.title(),
                        status="active",
                        presence="offline",
                        assigned_user_id=owner,
                        created_at=NOW,
                    )
                )
            for key in ("worn", "spare"):
                recording, transcript = uuid.uuid4().hex, uuid.uuid4().hex
                ids[f"recording_{key}"] = recording
                c.execute(
                    t["recordings"]
                    .insert()
                    .values(
                        id=recording,
                        company_id=ids["company"],
                        device_id=ids[key],
                        client_recording_id=f"clip-{key}-0001",
                        started_at=NOW,
                        ended_at=NOW,
                        duration_ms=4000,
                        size_bytes=128044,
                        sha256="c" * 64,
                        sample_rate=16000,
                        channels=1,
                        bits_per_sample=16,
                        format="WAV, 16 kHz, mono, 16-bit",
                        storage_key=f"a/b/{key}.wav",
                        uploaded_at=NOW,
                        state="completed",
                        retry_count=0,
                    )
                )
                c.execute(
                    t["processing_jobs"]
                    .insert()
                    .values(
                        id=uuid.uuid4().hex,
                        recording_id=recording,
                        company_id=ids["company"],
                        status="done",
                        attempts=1,
                        max_attempts=3,
                        run_after=NOW,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                c.execute(
                    t["transcripts"]
                    .insert()
                    .values(
                        id=transcript,
                        recording_id=recording,
                        company_id=ids["company"],
                        full_text="hello",
                        transcription_model="m",
                        transcription_model_version="1",
                        review_status="none",
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                for index in range(3):
                    c.execute(
                        t["transcript_segments"]
                        .insert()
                        .values(
                            id=uuid.uuid4().hex,
                            transcript_id=transcript,
                            company_id=ids["company"],
                            idx=index,
                            start_ms=index * 1000,
                            end_ms=index * 1000 + 900,
                            speaker_label="SPEAKER_00",
                            text="hello",
                            needs_review=False,
                            corrected=False,
                        )
                    )
    finally:
        engine.dispose()
    return ids


def counts(config: Config) -> dict[str, int]:
    engine = create_engine(url_of(config))
    try:
        with engine.connect() as connection:
            return {
                name: connection.execute(text(f"SELECT count(*) FROM {name}")).scalar_one()  # noqa: S608
                for name in ("companies", "users", "devices", "recordings", "processing_jobs", "transcripts")
            } | {
                "transcript_segments": connection.execute(
                    text("SELECT count(*) FROM transcript_segments")
                ).scalar_one()
            }
    finally:
        engine.dispose()


EXPECTED = {
    "companies": 1,
    "users": 2,
    "devices": 2,
    "recordings": 2,
    "processing_jobs": 2,
    "transcripts": 2,
    "transcript_segments": 6,
}


def wearers(config: Config) -> dict[str, str | None]:
    engine = create_engine(url_of(config))
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT client_recording_id, assigned_user_id FROM recordings"))
            return {row.client_recording_id: row.assigned_user_id for row in rows}
    finally:
        engine.dispose()


def test_upgrading_keeps_every_recording_and_everything_that_points_at_one(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "0003")
    seed(alembic_config)
    assert counts(alembic_config) == EXPECTED

    command.upgrade(alembic_config, "0004")

    # Transcripts, segments and jobs would be the first to go if the table had been rebuilt with foreign keys
    # on.
    assert counts(alembic_config) == EXPECTED


def test_existing_recordings_are_attributed_to_whoever_could_already_see_them(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "0003")
    ids = seed(alembic_config)

    command.upgrade(alembic_config, "0004")

    found = wearers(alembic_config)
    # The worn locket's recording goes to its current wearer: exactly who saw it before the migration, so
    # nobody
    # gains or loses access to anything they could already see.
    assert found["clip-worn-0001"] == ids["ana"]
    # The unassigned locket's recording was visible to managers only, and stays that way.
    assert found["clip-spare-0001"] is None


def test_the_new_column_is_really_a_foreign_key_that_clears_when_the_person_goes(
    alembic_config: Config,
) -> None:
    command.upgrade(alembic_config, "0003")
    ids = seed(alembic_config)
    command.upgrade(alembic_config, "0004")

    engine = make_engine(url_of(alembic_config))  # foreign keys enforced
    try:
        # A person who does not exist cannot be recorded as the wearer.
        with engine.begin() as connection, pytest.raises(Exception, match="FOREIGN KEY"):
            connection.execute(
                text("UPDATE recordings SET assigned_user_id = :ghost"), {"ghost": uuid.uuid4().hex}
            )
        with engine.begin() as connection:
            # Removing the person clears the reference. It must not delete the recordings that name them.
            connection.execute(text("DELETE FROM users WHERE id = :u"), {"u": ids["ana"]})
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT id, assigned_user_id FROM recordings WHERE id = :r"),
                {"r": ids["recording_worn"]},
            ).all()
        assert len(rows) == 1 and rows[0].assigned_user_id is None
    finally:
        engine.dispose()


def test_there_is_an_index_for_the_employees_query(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    engine = create_engine(url_of(alembic_config))
    try:
        with engine.connect() as connection:
            indexes = {row.name for row in connection.execute(text("PRAGMA index_list(recordings)"))}
    finally:
        engine.dispose()
    assert "ix_recordings_assigned_user" in indexes


def test_downgrading_removes_the_column_and_still_keeps_every_row(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    seed(alembic_config)
    assert counts(alembic_config) == EXPECTED

    command.downgrade(alembic_config, "0003")  # SQLite removes a column by rebuilding the table

    assert counts(alembic_config) == EXPECTED
    engine = create_engine(url_of(alembic_config))
    try:
        with engine.connect() as connection:
            columns = {row.name for row in connection.execute(text("PRAGMA table_info(recordings)"))}
    finally:
        engine.dispose()
    assert "assigned_user_id" not in columns

    command.upgrade(alembic_config, "head")  # and the way back up still works
    assert counts(alembic_config) == EXPECTED
