"""The schema is created only by migrations. These tests prove they work and match the models."""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, create_engine, inspect, text

from app.db import Base, make_engine

API_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic_config(tmp_path: Path) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{(tmp_path / 'migrate.sqlite3').as_posix()}")
    return config


def table_names(config: Config) -> set[str]:
    engine = create_engine(config.get_main_option("sqlalchemy.url") or "")
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_running_migrations_does_not_silence_the_applications_loggers(alembic_config: Config) -> None:
    """Alembic's logging setup disables every logger that already exists unless told not to, which hid
    real errors from anything that migrates in the same process as it runs."""
    probe = logging.getLogger("ask.migration_probe")
    assert not probe.disabled
    command.upgrade(alembic_config, "head")
    assert not probe.disabled


def test_upgrade_creates_every_model_table(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    expected = set(Base.metadata.tables) | {"alembic_version"}
    assert table_names(alembic_config) == expected


def test_models_and_migrations_have_not_drifted(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    # Raises if autogenerate would produce changes: a model was edited without a migration.
    command.check(alembic_config)


def seed_a_device_with_children(config: Config) -> dict[str, int]:
    """Real rows in a table that other tables point at, plus rows pointing at it."""
    engine = make_engine(
        config.get_main_option("sqlalchemy.url") or ""
    )  # foreign keys enforced, as in the app
    try:
        metadata = MetaData()
        tables = {
            name: Table(name, metadata, autoload_with=engine)
            for name in ("companies", "devices", "device_credentials", "device_events", "device_pairings")
        }
        company, device = uuid.uuid4().hex, uuid.uuid4().hex
        now = datetime(2026, 10, 25, 12, 0, 0)
        with engine.begin() as connection:
            connection.execute(
                tables["companies"]
                .insert()
                .values(id=company, name="Acme", purge_grace_days=7, created_at=now)
            )
            connection.execute(
                tables["devices"]
                .insert()
                .values(
                    id=device,
                    company_id=company,
                    public_id="ASK-TEST01",
                    name="Front desk",
                    status="active",
                    presence="offline",
                    created_at=now,
                )
            )
            connection.execute(
                tables["device_credentials"]
                .insert()
                .values(id=uuid.uuid4().hex, device_id=device, secret_hash="a" * 64, created_at=now)
            )
            connection.execute(
                tables["device_events"]
                .insert()
                .values(
                    id=uuid.uuid4().hex, company_id=company, device_id=device, type="paired", created_at=now
                )
            )
            connection.execute(
                tables["device_pairings"]
                .insert()
                .values(
                    id=uuid.uuid4().hex,
                    device_id=device,
                    code_hash="b" * 64,
                    expires_at=now,
                    created_at=now,
                )
            )
        return dict.fromkeys(("device_credentials", "device_events", "device_pairings"), 1)
    finally:
        engine.dispose()


def row_counts(config: Config, names: list[str]) -> dict[str, int]:
    engine = create_engine(config.get_main_option("sqlalchemy.url") or "")
    try:
        with engine.connect() as connection:
            return {
                name: connection.execute(text(f"SELECT count(*) FROM {name}")).scalar_one()  # noqa: S608
                for name in names
            }
    finally:
        engine.dispose()


def seed_a_transcript(config: Config) -> None:
    """A recording with a transcript and segments, the tables the processing-worker migration alters."""
    engine = make_engine(config.get_main_option("sqlalchemy.url") or "")
    try:
        metadata = MetaData()
        names = ("companies", "devices", "recordings", "transcripts", "transcript_segments")
        tables = {name: Table(name, metadata, autoload_with=engine) for name in names}
        company, device, recording, transcript = (uuid.uuid4().hex for _ in range(4))
        now = datetime(2026, 10, 25, 12, 0, 0)
        with engine.begin() as connection:
            connection.execute(
                tables["companies"]
                .insert()
                .values(id=company, name="Acme", purge_grace_days=7, created_at=now)
            )
            connection.execute(
                tables["devices"]
                .insert()
                .values(
                    id=device,
                    company_id=company,
                    public_id="ASK-TEST02",
                    name="Front desk",
                    status="active",
                    presence="offline",
                    created_at=now,
                )
            )
            connection.execute(
                tables["recordings"]
                .insert()
                .values(
                    id=recording,
                    company_id=company,
                    device_id=device,
                    client_recording_id="clip-0001-aaaa",
                    started_at=now,
                    ended_at=now,
                    duration_ms=4000,
                    size_bytes=128044,
                    sha256="c" * 64,
                    sample_rate=16000,
                    channels=1,
                    bits_per_sample=16,
                    format="WAV, 16 kHz, mono, 16-bit",
                    storage_key="a/b/c.wav",
                    uploaded_at=now,
                    state="completed",
                    retry_count=0,
                )
            )
            connection.execute(
                tables["transcripts"]
                .insert()
                .values(
                    id=transcript,
                    recording_id=recording,
                    company_id=company,
                    full_text="hello",
                    transcription_model="m",
                    transcription_model_version="1",
                    review_status="none",
                    created_at=now,
                    updated_at=now,
                )
            )
            for index in range(3):
                connection.execute(
                    tables["transcript_segments"]
                    .insert()
                    .values(
                        id=uuid.uuid4().hex,
                        transcript_id=transcript,
                        company_id=company,
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


def test_the_processing_worker_migration_keeps_existing_transcripts_and_marks_them_real(
    alembic_config: Config,
) -> None:
    command.upgrade(alembic_config, "0002")
    seed_a_transcript(alembic_config)

    command.upgrade(alembic_config, "head")

    assert row_counts(alembic_config, ["recordings", "transcripts", "transcript_segments"]) == {
        "recordings": 1,
        "transcripts": 1,
        "transcript_segments": 3,
    }
    engine = create_engine(alembic_config.get_main_option("sqlalchemy.url") or "")
    try:
        with engine.connect() as connection:
            row = connection.execute(text("SELECT is_placeholder, language FROM transcripts")).one()
    finally:
        engine.dispose()
    assert not row.is_placeholder  # transcripts made before placeholders existed were never placeholders
    assert row.language is None


def test_upgrading_keeps_every_row_in_tables_that_reference_a_changed_table(alembic_config: Config) -> None:
    """SQLite changes some tables by rebuilding them. Done carelessly, that deletes the rows of every
    table pointing at the rebuilt one (a migration here once wiped recordings and device credentials)."""
    command.upgrade(alembic_config, "0001")
    expected = seed_a_device_with_children(alembic_config)

    command.upgrade(alembic_config, "head")

    assert row_counts(alembic_config, [*expected, "devices", "companies"]) == {
        **expected,
        "devices": 1,
        "companies": 1,
    }


def test_downgrading_keeps_those_rows_too(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    expected = seed_a_device_with_children(alembic_config)

    command.downgrade(alembic_config, "0001")  # removes a column, which SQLite does by rebuilding the table

    assert row_counts(alembic_config, [*expected, "devices"]) == {**expected, "devices": 1}


def test_rebuilding_a_table_in_a_migration_is_safe_whatever_the_migration_does(
    alembic_config: Config, tmp_path: Path
) -> None:
    """The safety net lives in env.py, so even a migration that forces a full rebuild is harmless."""
    command.upgrade(alembic_config, "head")
    expected = seed_a_device_with_children(alembic_config)

    head = ScriptDirectory.from_config(alembic_config).get_current_head()
    scripts = tmp_path / "scripts"
    shutil.copytree(API_DIR / "migrations", scripts)
    # A throwaway revision on top of the real history, so this keeps working as migrations are added.
    (scripts / "versions" / "9999_force_rebuild.py").write_text(
        '"""force a rebuild"""\n'
        "from alembic import op\n"
        "import sqlalchemy as sa\n"
        "revision = '9999'\n"
        f"down_revision = '{head}'\n"
        "branch_labels = None\n"
        "depends_on = None\n\n"
        "def upgrade():\n"
        "    with op.batch_alter_table('devices', recreate='always') as batch:\n"
        "        batch.alter_column('name', existing_type=sa.String(120), type_=sa.String(200))\n\n"
        "def downgrade():\n"
        "    pass\n",
        encoding="utf-8",
    )
    alembic_config.set_main_option("script_location", str(scripts))
    command.upgrade(alembic_config, "head")

    assert row_counts(alembic_config, [*expected, "devices"]) == {**expected, "devices": 1}


def test_downgrade_removes_everything_and_upgrade_works_again(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    assert table_names(alembic_config) <= {"alembic_version"}
    command.upgrade(alembic_config, "head")
    assert "recordings" in table_names(alembic_config)
