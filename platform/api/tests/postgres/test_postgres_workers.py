"""Two real worker PROCESSES against one real PostgreSQL: every recording is processed exactly once.

This is the multi-worker claim the queue's row locking exists for, run with separate operating-system
processes (not threads) and the placeholder provider (fast, and nothing here is about the models)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.db import make_session_factory
from app.models import ProcessingJob, Recording, Transcript
from tests.conftest import new_web, provision_device
from tests.helpers import add_company, add_member, make_wav
from tests.postgres.test_postgres import pg_app

API_DIR = Path(__file__).resolve().parents[2]


def test_two_worker_processes_share_a_queue_and_process_every_recording_exactly_once(
    settings, pg_engine: Engine, pg_url: str, tmp_path: Path
) -> None:
    app = pg_app(settings, pg_engine, pg_url)
    storage = tmp_path / "recordings"
    app.state.settings.storage_dir = storage  # the workers read the audio from here
    app.state.storage.root = storage
    with make_session_factory(pg_engine)() as db:
        company = add_company(db, "Acme")
        add_member(db, company, "manager@acme.example.com", "manager")
    manager = new_web(app)
    manager.login("manager@acme.example.com")
    _, locket = provision_device(app, manager)
    total = 60  # enough that both processes are claiming at the same time
    for n in range(total):
        assert locket.upload(f"clip-{n:04d}-{uuid.uuid4().hex[:6]}", make_wav(4.0)).status_code == 201

    env = {k: v for k, v in os.environ.items() if not k.startswith("ASK_")}
    env.update(
        {
            "ASK_ENV": "test",
            "ASK_SECRET_KEY": "test-secret-key-0123456789-abcdefghijklmnop",
            "ASK_DATABASE_URL": pg_url,
            "ASK_STORAGE_DIR": str(storage),
            "ASK_AI_PROVIDER": "stub",
            "ASK_EMAIL_BACKEND": "memory",
            "ASK_COOKIE_SECURE": "false",
            "ASK_WORKER_POLL_SECONDS": "0.1",
        }
    )
    logs = [tmp_path / "worker-a.log", tmp_path / "worker-b.log"]
    workers = [
        subprocess.Popen(
            [sys.executable, "-m", "app.worker", "--once"],
            cwd=API_DIR,
            env=env,
            stdout=log.open("w"),
            stderr=subprocess.STDOUT,
        )
        for log in logs
    ]
    for worker in workers:
        assert worker.wait(timeout=240) == 0

    # Which process took which job, from what each one logged as it claimed them.
    claimed = [re.findall(r"processing job (\S+)", log.read_text(errors="replace")) for log in logs]
    print(f"\n  worker A processed {len(claimed[0])}, worker B processed {len(claimed[1])}")
    assert claimed[0] and claimed[1], "one process did everything, so there was no contention to test"
    everything = claimed[0] + claimed[1]
    assert len(everything) == total and len(set(everything)) == total  # none twice, none missed

    with make_session_factory(pg_engine)() as db:
        states = {s for (s,) in db.execute(select(Recording.state))}
        jobs = db.execute(select(ProcessingJob.status, ProcessingJob.attempts)).all()
        assert states == {"completed"}
        assert len(jobs) == total and {(s, a) for s, a in jobs} == {("done", 1)}  # none twice, none missed
        assert db.scalar(select(func.count()).select_from(Transcript)) == total
