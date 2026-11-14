"""What only a real PostgreSQL can show: the migrations, the job queue's row locking, and a concurrent upload.

Run against PostgreSQL 16.15 (see conftest.py); skipped when no server is configured.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select
from sqlalchemy.engine import Engine

from app.config import Settings
from app.db import Base, make_session_factory
from app.main import create_app
from app.models import ProcessingJob, Recording, Transcript
from app.services import jobs
from app.services.email import MemoryEmailSender
from app.worker import Worker
from tests.ai_doubles import make_provider
from tests.conftest import new_web, provision_device
from tests.helpers import DeviceClient, add_company, add_member, make_wav

API_DIR = Path(__file__).resolve().parents[2]


def alembic_config(url: str) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def pg_app(settings: Settings, engine: Engine, url: str):  # type: ignore[no-untyped-def]
    """The application on PostgreSQL, with the schema made by the real migrations."""
    command.upgrade(alembic_config(url), "head")
    return create_app(
        settings.model_copy(update={"database_url": url}), engine=engine, email=MemoryEmailSender()
    )


def sign_in_manager(app):  # type: ignore[no-untyped-def]
    with make_session_factory(app.state.engine)() as db:
        company = add_company(db, "Acme")
        add_member(db, company, "manager@acme.example.com", "manager")
    web = new_web(app)
    web.login("manager@acme.example.com")
    return web


def test_migrations_apply_roll_back_and_apply_again_and_match_the_models(
    pg_engine: Engine, pg_url: str
) -> None:
    config = alembic_config(pg_url)
    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    command.check(config)  # no drift between the models and what the migrations made
    command.downgrade(config, "base")
    assert set(inspect(pg_engine).get_table_names()) == {"alembic_version"}
    command.upgrade(config, "head")
    assert set(inspect(pg_engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}


def test_four_workers_racing_for_the_queue_each_take_every_job_exactly_once(
    settings: Settings, pg_engine: Engine, pg_url: str
) -> None:
    app = pg_app(settings, pg_engine, pg_url)
    with TestClient(app):
        manager = sign_in_manager(app)
        _, locket = provision_device(app, manager)
        total = 12
        for n in range(total):
            assert locket.upload(f"clip-{n:04d}-{uuid.uuid4().hex[:6]}", make_wav(1.0)).status_code == 201

        factory = make_session_factory(pg_engine)
        claimed: list[uuid.UUID] = []
        lock = threading.Lock()
        errors: list[BaseException] = []

        def work(name: str) -> None:
            try:
                while True:
                    with factory() as db:
                        job = jobs.claim_next(db, name)
                    if job is None:
                        return
                    with lock:
                        claimed.append(job.id)
            except BaseException as error:  # reported to the main thread below
                errors.append(error)

        threads = [threading.Thread(target=work, args=(f"worker-{i}",)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert not errors, errors
        assert len(claimed) == total and len(set(claimed)) == total  # none missed, none taken twice
        with factory() as db:
            assert (
                db.scalar(
                    select(func.count()).select_from(ProcessingJob).where(ProcessingJob.status == "running")
                )
                == total
            )
            assert db.scalar(select(func.max(ProcessingJob.attempts))) == 1


def test_a_recording_is_uploaded_processed_and_shown_on_postgres(
    settings: Settings, pg_engine: Engine, pg_url: str
) -> None:
    app = pg_app(settings, pg_engine, pg_url)
    with TestClient(app):
        manager = sign_in_manager(app)
        _, locket = provision_device(app, manager)
        stored = locket.upload(f"clip-{uuid.uuid4().hex[:12]}", make_wav(9.0))
        assert stored.status_code == 201, stored.text
        recording_id = uuid.UUID(stored.json()["recordingId"])

        worker = Worker(
            app.state.session_factory, app.state.settings, make_provider(), app.state.storage, "w"
        )
        assert worker.work_once() is True

        detail = manager.get(f"/api/recordings/{recording_id}").json()
        assert detail["state"] in ("completed", "needs_review") and detail["transcript"]["segments"]
        with app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(Transcript)) == 1


def test_the_same_clip_sent_twice_at_once_is_stored_once(
    settings: Settings, pg_engine: Engine, pg_url: str
) -> None:
    app = pg_app(settings, pg_engine, pg_url)
    with TestClient(app):
        manager = sign_in_manager(app)
        _, locket = provision_device(app, manager)
        wav = make_wav(2.0)
        clip = f"clip-{uuid.uuid4().hex[:12]}"
        answers: list[int] = []

        def send() -> None:
            copy = DeviceClient(TestClient(app), locket.credential)
            answers.append(copy.upload(clip, wav).status_code)

        threads = [threading.Thread(target=send) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert sorted(answers).count(201) == 1 and set(answers) <= {200, 201}, answers
        with app.state.session_factory() as db:
            assert db.scalar(select(func.count()).select_from(Recording)) == 1
