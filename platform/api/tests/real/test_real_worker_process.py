"""A real worker PROCESS, with real models, killed in the middle of a recording; then a new worker process.

This is the crash-recovery claim tested as a user would meet it: the process is terminated abruptly (as a
power cut or an out-of-memory kill would), not asked to stop. Nothing in it is a test double.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from sqlalchemy import func, select

from app.models import ProcessingJob, Recording, Transcript
from tests.conftest import new_web, provision_device
from tests.real.conftest import MODELS

API_DIR = Path(__file__).resolve().parents[2]


def worker_environment(app, settings) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Environment for a worker process that uses THIS test's database and storage, never the developer's."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("ASK_")}
    env.update(
        {
            "ASK_ENV": "test",
            "ASK_SECRET_KEY": "test-secret-key-0123456789-abcdefghijklmnop",
            "ASK_DATABASE_URL": settings.database_url,
            "ASK_STORAGE_DIR": str(settings.storage_dir),
            "ASK_AI_PROVIDER": "local",
            "ASK_AI_MODEL_DIR": str(MODELS),
            "ASK_AI_ASR_MODEL": settings.ai_asr_model,
            "ASK_AI_DIARIZER": settings.ai_diarizer,
            "ASK_JOB_LOCK_TIMEOUT_SECONDS": "3",
            "ASK_WORKER_POLL_SECONDS": "0.3",
            "ASK_EMAIL_BACKEND": "memory",
            "ASK_COOKIE_SECURE": "false",
            "PYTHONWARNINGS": "ignore",
        }
    )
    return env


def start_worker(env: dict[str, str], *args: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", "app.worker", *args],
        cwd=API_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for(condition, seconds: float, what: str):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out after {seconds:.0f}s waiting for {what}")


def test_a_worker_killed_mid_recording_is_replaced_and_the_recording_still_gets_its_transcript(
    file_app, samples: Path, settings
) -> None:  # type: ignore[no-untyped-def]
    factory = file_app.state.session_factory
    manager = new_web(file_app)
    with factory() as db:
        from tests.helpers import add_company, add_member

        company = add_company(db, "Acme")
        add_member(db, company, "manager@acme.example.com", "manager")
    manager.login("manager@acme.example.com")
    _, locket = provision_device(file_app, manager)
    stored = locket.upload(f"clip-{uuid.uuid4().hex[:12]}", (samples / "conversation.wav").read_bytes())
    assert stored.status_code == 201, stored.text
    recording_id = uuid.UUID(stored.json()["recordingId"])

    def state() -> str:
        with factory() as db:
            recording = db.get(Recording, recording_id)
            return recording.state if recording else ""

    env = worker_environment(file_app, file_app.state.settings)
    first = start_worker(env)
    try:
        # The model takes a while to load; then the recording moves through its stages. Strike while the
        # speaker separation (the slow stage on a CPU) is running, so there is real work to lose.
        wait_for(lambda: state() == "diarizing", 180, "the first worker to reach speaker separation")
        first.kill()  # abrupt: no chance to hand the job back
        first.wait(timeout=30)
    finally:
        if first.poll() is None:
            first.kill()

    with factory() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.recording_id == recording_id))
        assert job is not None and job.status == "running" and job.attempts == 1  # left behind, mid-work
        assert db.scalar(select(func.count()).select_from(Transcript)) == 0  # no half-finished transcript

    time.sleep(4)  # longer than the 3 s lease: the dead worker's claim has lapsed
    second = start_worker(env, "--once")
    try:
        assert second.wait(timeout=300) == 0
    finally:
        if second.poll() is None:
            second.kill()

    with factory() as db:
        db.expire_all()
        recording = db.get(Recording, recording_id)
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.recording_id == recording_id))
        assert recording is not None and job is not None
        assert recording.state in ("completed", "needs_review")
        assert (job.status, job.attempts) == ("done", 2)
        assert db.scalar(select(func.count()).select_from(Transcript)) == 1
    detail = manager.get(f"/api/recordings/{recording_id}").json()
    text = " ".join(s["text"] for s in detail["transcript"]["segments"]).lower()
    assert "delivery" in text and "handouts" in text
    assert detail["transcript"]["placeholder"] is False


def test_a_worker_that_cannot_load_its_model_exits_with_the_reason_instead_of_idling(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ASK_")}
    env.update(
        {
            "ASK_ENV": "test",
            "ASK_SECRET_KEY": "test-secret-key-0123456789-abcdefghijklmnop",
            "ASK_DATABASE_URL": f"sqlite:///{(tmp_path / 'x.sqlite3').as_posix()}",
            "ASK_STORAGE_DIR": str(tmp_path / "rec"),
            "ASK_AI_PROVIDER": "local",
            "ASK_AI_MODEL_DIR": str(tmp_path / "no-weights-here"),
            "ASK_AI_OFFLINE": "true",  # forbidden to download, and nothing is there
            "ASK_AI_ASR_MODEL": "tiny.en",
            "ASK_EMAIL_BACKEND": "memory",
            "ASK_COOKIE_SECURE": "false",
            "PYTHONWARNINGS": "ignore",
        }
    )
    done = subprocess.run(
        [sys.executable, "-m", "app.worker", "--once"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 2
    assert "cannot start" in done.stderr and "speech recognition" in done.stderr
