"""A way for the API to tell the worker (another process in the same container) that a job was queued,
without the worker asking the database every half second to find out.

The API touches a small file right after it commits a new processing job; a sleeping worker watches that
file's modification time, which costs the database nothing. Off unless `worker_wake_file` is set.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models.recordings import ProcessingJob

logger = logging.getLogger("ask.wake")

_path: Path | None = None


def configure(path: Path | None) -> None:
    """Where to signal (None: nowhere). Called when the API starts."""
    global _path
    _path = path


def stamp(path: Path | None) -> int:
    """When the file was last touched (0 when it does not exist or there is no file)."""
    if path is None:
        return 0
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def signal(path: Path | None) -> None:
    """Touch the file so its modification time is newer than any seen before. Never raises: a missed signal
    only means the worker finds the job at its next look."""
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        now = max(time.time_ns(), stamp(path) + 1)
        if not path.exists():
            path.touch()
        os.utime(path, ns=(now, now))
    except OSError:
        logger.warning("could not signal the worker", exc_info=True)


@event.listens_for(Session, "before_flush")
def _note_new_jobs(session: Session, _context: Any, _instances: Any) -> None:
    if _path is not None and any(isinstance(item, ProcessingJob) for item in session.new):
        session.info["ask_wake"] = True


@event.listens_for(Session, "after_commit")
def _signal_after_commit(session: Session) -> None:
    # Only after the commit: a worker that looked earlier would not see the job yet and go back to sleep.
    if session.info.pop("ask_wake", False):
        signal(_path)


@event.listens_for(Session, "after_rollback")
def _forget_signal(session: Session) -> None:
    session.info.pop("ask_wake", None)
