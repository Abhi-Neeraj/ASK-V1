"""On free database hosting (Neon bills compute by the hour and pauses an idle database), a worker with
nothing to do must leave the database alone, and still start the moment a recording arrives."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.ai.types import AsrSegment, Provider
from app.config import Settings
from app.services import wake
from app.worker import Worker
from tests.ai_doubles import FakeTranscriber
from tests.test_meeting_parts import upload_part
from tests.test_phone_upload import make_setup
from tests.test_pipeline_summary import FakeSummarizer


def until(check: Callable[[], bool], seconds: float = 8.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return check()


def sleepy_worker(app, wake_file: Path) -> tuple[Worker, list[int]]:  # type: ignore[no-untyped-def]
    settings: Settings = app.state.settings.model_copy(
        update={
            "worker_sleepy_after_seconds": 0.3,
            "worker_sleepy_poll_seconds": 3600.0,
            "worker_poll_seconds": 0.05,
            "worker_wake_file": wake_file,
        }
    )
    provider = Provider(
        name="test",
        placeholder=False,
        transcriber=FakeTranscriber([AsrSegment(1000, 3000, "We will ship on Friday.", (), 0.95)]),
        summarizer=FakeSummarizer("A SUMMARY"),
    )
    worker = Worker(app.state.session_factory, settings, provider, app.state.storage, "worker-sleepy")
    looks: list[int] = []
    real = worker.work_once

    def counted() -> bool:
        looks.append(1)
        return real()

    worker.work_once = counted  # type: ignore[method-assign]
    return worker, looks


def test_an_idle_worker_leaves_the_database_alone_until_signalled(app, tmp_path) -> None:  # type: ignore[no-untyped-def]
    wake_file = tmp_path / "wake"
    worker, looks = sleepy_worker(app, wake_file)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    try:
        assert until(lambda: worker.sleepy), "the worker never went to sleep"
        settled = len(looks)
        time.sleep(1.0)
        assert len(looks) == settled  # a second of sleeping, not one look at the database
        wake.signal(wake_file)
        assert until(lambda: len(looks) > settled), "the signal did not wake the worker"
    finally:
        worker.stop()
        thread.join(timeout=10)


def test_a_recording_arriving_wakes_the_sleeping_worker_and_is_processed(app, acme, tmp_path) -> None:  # type: ignore[no-untyped-def]
    wake_file = tmp_path / "wake"
    wake.configure(wake_file)  # as the API does at start-up when worker_wake_file is set
    try:
        worker, _ = sleepy_worker(app, wake_file)
        setup = make_setup(app)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        try:
            assert until(lambda: worker.sleepy)
            before = wake.stamp(wake_file)
            upload_part(setup, 0, datetime.now(UTC) - timedelta(minutes=5))
            assert wake.stamp(wake_file) > before  # the API signalled once the upload was committed
            page = None

            def processed() -> bool:
                nonlocal page
                page = setup["manager"].get("/api/recordings?pageSize=5").json()
                return bool(page["items"]) and page["items"][0]["state"] in {"completed", "needs_review"}

            assert until(processed), page
        finally:
            worker.stop()
            thread.join(timeout=10)
    finally:
        wake.configure(None)


def test_nothing_changes_without_a_wake_file(app) -> None:  # type: ignore[no-untyped-def]
    provider = Provider(
        name="test", placeholder=False, transcriber=FakeTranscriber([]), summarizer=FakeSummarizer("x")
    )
    worker = Worker(
        app.state.session_factory, app.state.settings, provider, app.state.storage, "worker-plain"
    )
    assert app.state.settings.worker_wake_file is None and not worker.sleepy
    assert worker.run(once=True) == 0 and not worker.sleepy
    assert wake.stamp(None) == 0
    wake.signal(None)  # no file, no error
