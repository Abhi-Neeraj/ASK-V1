"""The durable work queue behind processing: claiming, leasing, retrying, and recovering from crashes.

The queue is the `processing_jobs` table, so it survives restarts and needs nothing besides the
database. A worker *claims* a job (an atomic status change, so two workers can never take the same
one), then keeps a *lease* by touching `locked_at` while it works. A worker that dies simply stops
touching it, and after `job_lock_timeout_seconds` the job is taken back and tried again.

Attempts are counted when a job is claimed, not when it fails, so a job that crashes its worker every
time still runs out of attempts instead of looping forever.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import timeutil
from app.config import Settings
from app.db import rows_affected
from app.models import ProcessingJob, Recording
from app.services.audit import publish_event, record_audit, recording_event

PUBLIC_FAILURE = "Processing failed. An administrator can see the details in the server log."
_MAX_ERROR_CHARS = 500

_HF_TOKEN = re.compile(r"hf_[A-Za-z0-9]{10,}")
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s'\"<>|]*")  # a drive letter, not "http://"
_POSIX_PATH = re.compile(r"(?<![\w.:/])/(?:[\w.\-~]+/)+[\w.\-~]*")  # not the slashes inside a URL


@dataclass(frozen=True)
class ClaimedJob:
    """What a worker needs to run one job, detached from any database session."""

    id: uuid.UUID
    recording_id: uuid.UUID
    company_id: uuid.UUID
    attempts: int
    max_attempts: int


class LostLeaseError(Exception):
    """This worker no longer owns the job (it was taken back after a stall). Stop and write nothing."""


def sanitize_error(text: str, storage_root: Path | None = None) -> str:
    """Make an error safe to store and show: no file paths, no tokens, and a bounded length.

    Raw audio paths and secrets must never be returned by the API, and error text from model libraries
    is full of both.
    """
    # A database error quotes the statement and the values; keep the first line, which says what went wrong.
    cleaned = re.sub(r"\[(?:SQL|parameters):.*", "", text, flags=re.DOTALL)
    if storage_root is not None:
        # The root and everything under it: the folders below it name companies and dates.
        cleaned = re.sub(re.escape(str(storage_root)) + r"[^\s'\"<>|]*", "[storage]", cleaned)
    cleaned = _HF_TOKEN.sub("[token]", cleaned)
    cleaned = _WINDOWS_PATH.sub("[path]", cleaned)
    cleaned = _POSIX_PATH.sub("[path]", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned[: _MAX_ERROR_CHARS - 1] + "…" if len(cleaned) > _MAX_ERROR_CHARS else cleaned


# --- claiming and leasing ---------------------------------------------------------------------------


def claim_next(db: Session, worker_id: str, now: datetime | None = None) -> ClaimedJob | None:
    """Take the oldest job that is due. Returns None if there is nothing to do.

    Safe with any number of workers: the final UPDATE only succeeds while the job is still queued, so
    when two workers pick the same candidate exactly one wins and the other simply finds nothing.
    """
    now = now or timeutil.utcnow()
    candidate = db.scalar(
        select(ProcessingJob.id)
        .join(Recording, Recording.id == ProcessingJob.recording_id)
        .where(
            ProcessingJob.status == "queued",
            ProcessingJob.run_after <= now,
            Recording.deleted_at.is_(None),
        )
        .order_by(ProcessingJob.run_after, ProcessingJob.created_at)
        .limit(1)
        # PostgreSQL: skip rows another worker is looking at. SQLite has no row locks and ignores this.
        .with_for_update(skip_locked=True, of=ProcessingJob)
    )
    if candidate is None:
        db.rollback()
        return None

    taken = rows_affected(
        db.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == candidate, ProcessingJob.status == "queued")
            .values(
                status="running",
                locked_by=worker_id,
                locked_at=now,
                attempts=ProcessingJob.attempts + 1,
                updated_at=now,
            )
        )
    )
    if not taken:
        db.rollback()
        return None
    db.commit()

    job = db.scalar(
        select(ProcessingJob).where(ProcessingJob.id == candidate).execution_options(populate_existing=True)
    )
    assert job is not None  # noqa: S101  (we just updated it)
    return ClaimedJob(job.id, job.recording_id, job.company_id, job.attempts, job.max_attempts)


def touch(db: Session, job: ClaimedJob, worker_id: str, now: datetime | None = None) -> bool:
    """Extend the lease. False means the job is no longer this worker's."""
    now = now or timeutil.utcnow()
    renewed = rows_affected(
        db.execute(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job.id,
                ProcessingJob.status == "running",
                ProcessingJob.locked_by == worker_id,
            )
            .values(locked_at=now, updated_at=now)
        )
    )
    db.commit()
    return bool(renewed)


def require_lease(db: Session, job: ClaimedJob, worker_id: str) -> ProcessingJob:
    """The job row, locked for update, or LostLeaseError if another worker has it now."""
    row = db.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.id == job.id,
            ProcessingJob.status == "running",
            ProcessingJob.locked_by == worker_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        db.rollback()
        raise LostLeaseError(str(job.id))
    return row


def release(db: Session, job: ClaimedJob, worker_id: str, now: datetime | None = None) -> None:
    """Hand a job back untouched (the worker is shutting down). The attempt it used is not held against it."""
    now = now or timeutil.utcnow()
    db.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.id == job.id,
            ProcessingJob.status == "running",
            ProcessingJob.locked_by == worker_id,
        )
        .values(
            status="queued",
            locked_by=None,
            locked_at=None,
            attempts=ProcessingJob.attempts - 1,
            run_after=now,
            updated_at=now,
        )
    )
    recording = db.get(Recording, job.recording_id)
    if recording is not None and recording.state not in {"completed", "needs_review", "failed"}:
        recording.state = "queued"
        _announce(db, recording)
    db.commit()


# --- failing, retrying, recovering ---------------------------------------------------------------------


def _announce(db: Session, recording: Recording) -> None:
    publish_event(
        db,
        recording.company_id,
        "recording.state",
        recording_event(recording, state=recording.state),
    )


def retry_delay(settings: Settings, attempts: int) -> timedelta:
    """30 s, 60 s, 120 s, ... after the first, second, third failed attempt (base is configurable)."""
    return timedelta(seconds=settings.job_retry_base_seconds * 2 ** max(attempts - 1, 0))


def _give_up(
    db: Session, job: ProcessingJob, recording: Recording | None, message: str, now: datetime
) -> None:
    job.status = "failed"
    job.completed_at = now
    job.locked_by = None
    job.locked_at = None
    job.updated_at = now
    if recording is not None:
        recording.state = "failed"
        recording.error = message
        _announce(db, recording)
        record_audit(
            db,
            action="recording.processing_failed",
            company_id=recording.company_id,
            object_type="recording",
            object_id=recording.id,
            detail={"attempts": job.attempts},
        )


def _schedule_retry(
    db: Session, settings: Settings, job: ProcessingJob, recording: Recording | None, now: datetime
) -> None:
    job.status = "queued"
    job.run_after = now + retry_delay(settings, job.attempts)
    job.locked_by = None
    job.locked_at = None
    job.updated_at = now
    if recording is not None:
        recording.state = "retrying"
        recording.retry_count += 1
        recording.error = None
        _announce(db, recording)


def record_failure(
    db: Session,
    settings: Settings,
    job: ClaimedJob,
    worker_id: str,
    message: str,
    *,
    permanent: bool,
    storage_root: Path | None = None,
    now: datetime | None = None,
) -> str:
    """Note that an attempt failed. Returns "retry", "failed", or "lost" (someone else owns it now).

    Temporary failures are retried with growing delays until the job's attempts run out. A failure that
    retrying cannot fix (the audio is unusable) fails immediately. Either way the recording's own
    `error` shows a fixed, safe sentence; the detail goes in `last_error`, scrubbed of paths and tokens.
    """
    now = now or timeutil.utcnow()
    try:
        row = require_lease(db, job, worker_id)
    except LostLeaseError:
        return "lost"
    recording = db.get(Recording, row.recording_id)
    row.last_error = sanitize_error(message, storage_root)

    if not permanent and row.attempts < row.max_attempts:
        _schedule_retry(db, settings, row, recording, now)
        outcome = "retry"
    else:
        public = sanitize_error(message, storage_root) if permanent else PUBLIC_FAILURE
        _give_up(db, row, recording, public, now)
        outcome = "failed"
    db.commit()
    return outcome


def recover_stale(db: Session, settings: Settings, now: datetime | None = None) -> int:
    """Take back jobs whose worker went quiet (crashed, killed, machine slept). Returns how many."""
    now = now or timeutil.utcnow()
    cutoff = now - timedelta(seconds=settings.job_lock_timeout_seconds)
    stale = db.scalars(
        select(ProcessingJob)
        .where(ProcessingJob.status == "running", ProcessingJob.locked_at < cutoff)
        .with_for_update(skip_locked=True)
    ).all()
    for job in stale:
        recording = db.get(Recording, job.recording_id)
        job.last_error = "The worker stopped responding while processing this recording."
        if job.attempts < job.max_attempts:
            _schedule_retry(db, settings, job, recording, now)
            job.run_after = now  # the delay was for a failure; this was a crash, so go again at once
        else:
            _give_up(db, job, recording, PUBLIC_FAILURE, now)
    if stale:
        db.commit()
    return len(stale)


def finish_unneeded(
    db: Session, job: ClaimedJob, worker_id: str, reason: str, now: datetime | None = None
) -> None:
    """Close a job whose recording no longer exists or was deleted. Nothing to process, nothing failed."""
    now = now or timeutil.utcnow()
    try:
        row = require_lease(db, job, worker_id)
    except LostLeaseError:
        return
    row.status = "failed"
    row.last_error = reason
    row.completed_at = now
    row.locked_by = None
    row.locked_at = None
    row.updated_at = now
    db.commit()
