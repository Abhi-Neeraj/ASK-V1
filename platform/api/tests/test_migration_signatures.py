"""Migration 0005: voice signatures and recording analyses.

It only adds two tables, so nothing that exists can be lost; this seeds real recordings with transcripts and
segments anyway and proves they all survive, that the new tables work with foreign keys enforced, and that
they
go when the person or transcript they belong to goes."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db import make_engine
from tests.test_migration_wearer import EXPECTED, counts, seed, url_of

API_DIR = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 12, 12, 0, 0)


@pytest.fixture
def alembic_config(tmp_path: Path) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{(tmp_path / 'signatures.sqlite3').as_posix()}")
    return config


def tables(config: Config) -> set[str]:
    engine = create_engine(url_of(config))
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrading_keeps_every_recording_transcript_and_segment(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "0004")
    seed(alembic_config)
    before = counts(alembic_config)
    assert before == EXPECTED

    command.upgrade(alembic_config, "0005")

    assert counts(alembic_config) == EXPECTED
    assert {"person_signatures", "recording_analyses"} <= tables(alembic_config)


def test_the_new_tables_are_enforced_and_cascade_with_what_they_belong_to(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "0004")
    ids = seed(alembic_config)
    command.upgrade(alembic_config, "0005")

    engine = make_engine(url_of(alembic_config))  # foreign keys enforced, as in the application
    try:
        metadata = MetaData()
        people = Table("people", metadata, autoload_with=engine)
        signatures = Table("person_signatures", metadata, autoload_with=engine)
        analyses = Table("recording_analyses", metadata, autoload_with=engine)
        transcripts = Table("transcripts", metadata, autoload_with=engine)
        person = uuid.uuid4().hex
        with engine.begin() as c:
            c.execute(
                people.insert().values(
                    id=person,
                    company_id=ids["company"],
                    display_name="Ana",
                    consent_status="granted",
                    created_at=NOW,
                )
            )
            c.execute(
                signatures.insert().values(
                    id=uuid.uuid4().hex,
                    person_id=person,
                    company_id=ids["company"],
                    timbre=b"\x00" * 96,
                    version="dsp-1",
                    pitch_base_hz=190.0,
                    pitch_low_hz=150.0,
                    pitch_high_hz=240.0,
                    voiced_seconds=12.0,
                    sample_count=3,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            transcript = c.execute(transcripts.select().limit(1)).first()
            assert transcript is not None
            c.execute(
                analyses.insert().values(
                    id=uuid.uuid4().hex,
                    transcript_id=transcript.id,
                    company_id=ids["company"],
                    engine="lexical",
                    version=1,
                    content={"items": []},
                    created_at=NOW,
                )
            )

        # A signature must belong to a real person, and an analysis to a real transcript.
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.execute(
                signatures.insert().values(
                    id=uuid.uuid4().hex,
                    person_id=uuid.uuid4().hex,
                    company_id=ids["company"],
                    timbre=b"\x00",
                    version="dsp-1",
                    pitch_base_hz=1.0,
                    pitch_low_hz=1.0,
                    pitch_high_hz=1.0,
                    voiced_seconds=1.0,
                    sample_count=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        # One signature per person, one analysis per transcript.
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.execute(
                signatures.insert().values(
                    id=uuid.uuid4().hex,
                    person_id=person,
                    company_id=ids["company"],
                    timbre=b"\x00",
                    version="dsp-1",
                    pitch_base_hz=1.0,
                    pitch_low_hz=1.0,
                    pitch_high_hz=1.0,
                    voiced_seconds=1.0,
                    sample_count=1,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )

        with engine.begin() as c:
            c.execute(people.delete().where(people.c.id == person))
            c.execute(text("DELETE FROM transcript_segments WHERE transcript_id = :t"), {"t": transcript.id})
            c.execute(transcripts.delete().where(transcripts.c.id == transcript.id))
        with engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM person_signatures")).scalar_one() == 0
            assert c.execute(text("SELECT count(*) FROM recording_analyses")).scalar_one() == 0
    finally:
        engine.dispose()


def test_going_back_removes_only_the_new_tables(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "0005")
    seed(alembic_config)

    command.downgrade(alembic_config, "0004")

    assert counts(alembic_config) == EXPECTED
    assert not ({"person_signatures", "recording_analyses"} & tables(alembic_config))
