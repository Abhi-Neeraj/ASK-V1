"""The processing worker: a separate process that works through the job queue.

    python -m app.worker                 run until stopped (Ctrl+C or SIGTERM finishes the current job first)
    python -m app.worker --once          process whatever is due, then exit (for scripts and tests)

It needs the same `.env` as the API (same database, same storage) and a configured provider
(`ASK_AI_PROVIDER=stub` for development placeholders, `local` for real models). It refuses to start
without one, with a message saying what to do, rather than idling and pretending to work.

Any number of workers may run at once; the queue makes sure each job goes to exactly one.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading
import uuid

from app.ai.registry import build_provider, warm_up
from app.ai.types import Provider, ProviderUnavailableError
from app.config import Settings, get_settings
from app.db import SessionFactory, make_engine, make_session_factory
from app.services import jobs, workers
from app.services.jobs import sanitize_error
from app.services.pipeline import Pipeline
from app.services.storage import Storage

logger = logging.getLogger("ask.worker")


def new_worker_id() -> str:
    """Unique per process start, so a restarted worker is clearly a new one."""
    return f"{socket.gethostname()[:30]}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class Worker:
    def __init__(
        self,
        factory: SessionFactory,
        settings: Settings,
        provider: Provider,
        storage: Storage,
        worker_id: str | None = None,
    ) -> None:
        self.factory = factory
        self.settings = settings
        self.provider = provider
        self.worker_id = worker_id or new_worker_id()
        self.stopping = threading.Event()
        self.pipeline = Pipeline(
            factory, settings, provider, storage, self.worker_id, should_stop=self.stopping.is_set
        )

    # --- reporting in ---------------------------------------------------------------------------

    def heartbeat(self) -> None:
        with self.factory() as db:
            workers.beat(db, self.worker_id, self.provider.capabilities)

    def _heartbeat_loop(self) -> None:
        """Keeps reporting in during a long job, when the main loop is busy inside a model."""
        interval = max(self.settings.worker_online_seconds / 3, 0.05)
        while not self.stopping.wait(interval):
            try:
                self.heartbeat()
            except Exception:
                logger.exception("could not report in")

    # --- one pass ---------------------------------------------------------------------------------

    def work_once(self) -> bool:
        """Recover stalled jobs, then claim and process at most one. True if a job was processed."""
        with self.factory() as db:
            recovered = jobs.recover_stale(db, self.settings)
            if recovered:
                logger.warning("took back %s job(s) from a worker that stopped responding", recovered)
        with self.factory() as db:
            job = jobs.claim_next(db, self.worker_id)
        if job is None:
            return False
        logger.info("processing job %s (attempt %s of %s)", job.id, job.attempts, job.max_attempts)
        outcome = self.pipeline.run(job)
        logger.info("job %s: %s", job.id, outcome)
        return True

    def run(self, *, once: bool = False) -> int:
        """Process jobs until stopped, or with `once` until none are due. Returns how many ran."""
        processed = 0
        self.heartbeat()
        beater = threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True)
        beater.start()
        logger.info("worker %s started with provider %s", self.worker_id, self.provider.name)
        try:
            while not self.stopping.is_set():
                if self.work_once():
                    processed += 1
                    continue
                if once:
                    break
                self.stopping.wait(self.settings.worker_poll_seconds)
        finally:
            self.stopping.set()
            beater.join(timeout=5)
            # Say goodbye promptly, so the dashboard does not wait out the heartbeat window to notice.
            with self.factory() as db:
                workers.forget(db, self.worker_id)
            logger.info("worker %s stopped after %s job(s)", self.worker_id, processed)
        return processed

    def stop(self) -> None:
        self.stopping.set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker", description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="process what is due, then exit")
    parser.add_argument(
        "--no-warm-up",
        action="store_true",
        help="load models on the first recording instead of at start-up (slower first job; for tests)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if os.name != "nt":
        os.umask(0o077)  # as for the API: files this process creates are private to its account
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        provider = build_provider(settings)
        if not args.no_warm_up:
            provider = warm_up(provider, logger.info)
    except ProviderUnavailableError as error:
        logger.error("the worker cannot start: %s", error)
        cause = error.__cause__
        if (
            cause is not None
        ):  # what the library itself said, cleaned of paths and tokens, for whoever fixes it
            logger.error("the library reported: %s: %s", type(cause).__name__, sanitize_error(str(cause)))
        return 2

    engine = make_engine(settings.database_url)
    worker = Worker(make_session_factory(engine), settings, provider, Storage(settings.storage_dir))
    if provider.placeholder:
        logger.warning(
            "PLACEHOLDER MODE: transcripts will be marked as placeholders. No speech model is running."
        )
    for note in provider.notes:
        logger.warning("%s", note)

    def _stop(signum: int, _frame: object) -> None:
        logger.info("received signal %s; finishing the current job and stopping", signum)
        worker.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        worker.run(once=args.once)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
