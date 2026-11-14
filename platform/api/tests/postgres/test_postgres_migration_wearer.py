"""Migration 0004 on PostgreSQL: the branch SQLite never takes (a plain ADD COLUMN plus a named foreign key).

The SQLite tests prove existing rows survive and that the backfill keeps who could see what. This is the same
claim on a real PostgreSQL: seed rows at revision 0003, upgrade, and check nothing was lost, the backfill is
right, the foreign key is enforced and clears when the person goes, the index exists, and down and up work."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import MetaData, Table, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from tests.postgres.test_postgres import alembic_config

NOW = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
TABLES = (
    "companies",
    "users",
    "devices",
    "recordings",
    "processing_jobs",
    "transcripts",
    "transcript_segments",
)
EXPECTED = dict(zip(TABLES, (1, 2, 2, 2, 2, 2, 6), strict=True))


def seed(engine: Engine) -> dict[str, uuid.UUID]:
    ids = {name: uuid.uuid4() for name in ("company", "ana", "ben", "worn", "spare")}
    metadata = MetaData()
    t = {name: Table(name, metadata, autoload_with=engine) for name in TABLES}
    with engine.begin() as c:
        c.execute(
            t["companies"].insert().values(id=ids["company"], name="Acme", purge_grace_days=7, created_at=NOW)
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
            recording, transcript = uuid.uuid4(), uuid.uuid4()
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
                    id=uuid.uuid4(),
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
                        id=uuid.uuid4(),
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
    return ids


def counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            name: connection.execute(text(f"SELECT count(*) FROM {name}")).scalar_one()  # noqa: S608
            for name in TABLES
        }


def test_upgrading_keeps_every_row_and_backfills_who_could_already_see_each_recording(
    pg_engine: Engine, pg_url: str
) -> None:
    config = alembic_config(pg_url)
    command.upgrade(config, "0003")
    ids = seed(pg_engine)
    assert counts(pg_engine) == EXPECTED

    command.upgrade(config, "0004")

    assert counts(pg_engine) == EXPECTED
    with pg_engine.connect() as connection:
        found = {
            row.client_recording_id: row.assigned_user_id
            for row in connection.execute(
                text("SELECT client_recording_id, assigned_user_id FROM recordings")
            )
        }
        indexes = {
            row.indexname
            for row in connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'recordings'")
            )
        }
    assert found["clip-worn-0001"] == ids["ana"]  # exactly who could see it before
    assert found["clip-spare-0001"] is None  # managers only, as before
    assert "ix_recordings_assigned_user" in indexes


def test_the_new_column_is_a_real_foreign_key_that_clears_when_the_person_goes(
    pg_engine: Engine, pg_url: str
) -> None:
    config = alembic_config(pg_url)
    command.upgrade(config, "0003")
    ids = seed(pg_engine)
    command.upgrade(config, "0004")

    with pytest.raises(IntegrityError), pg_engine.begin() as connection:  # a person who does not exist
        connection.execute(text("UPDATE recordings SET assigned_user_id = :ghost"), {"ghost": uuid.uuid4()})
    with pg_engine.begin() as connection:  # removing a person clears the reference and keeps the recording
        connection.execute(text("DELETE FROM users WHERE id = :u"), {"u": ids["ana"]})
    with pg_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT assigned_user_id FROM recordings WHERE id = :r"), {"r": ids["recording_worn"]}
        ).all()
    assert len(rows) == 1 and rows[0].assigned_user_id is None


def test_downgrading_keeps_every_row_and_the_way_back_up_still_works(pg_engine: Engine, pg_url: str) -> None:
    config = alembic_config(pg_url)
    command.upgrade(config, "head")
    seed(pg_engine)

    command.downgrade(config, "0003")
    assert counts(pg_engine) == EXPECTED
    with pg_engine.connect() as connection:
        columns = {
            row.column_name
            for row in connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'recordings'")
            )
        }
    assert "assigned_user_id" not in columns

    command.upgrade(config, "head")
    assert counts(pg_engine) == EXPECTED
