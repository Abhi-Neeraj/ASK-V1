"""Worker heartbeats, and what the dashboard is told about processing.

The API never assumes a worker exists. A worker reports in while it runs; if none has recently, the
dashboard says recordings are waiting for one, rather than implying work is happening.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import timeutil
from app.ai.registry import capabilities as configured_capabilities
from app.ai.types import Capabilities
from app.config import Settings
from app.db import rows_affected
from app.models import ProcessingJob, Worker

RETAIN_FOR = timedelta(days=1)


def beat(db: Session, worker_id: str, capabilities: Capabilities, now: datetime | None = None) -> None:
    """Report that this worker is alive. Only the worker itself writes its own row."""
    now = now or timeutil.utcnow()
    row = db.scalar(select(Worker).where(Worker.worker_id == worker_id))
    details = {
        "transcription": capabilities.transcription,
        "diarization": capabilities.diarization,
        "speakerMatching": capabilities.speaker_matching,
        "notes": list(capabilities.notes),
    }
    if row is None:
        db.add(
            Worker(
                worker_id=worker_id,
                provider=capabilities.provider,
                placeholder=capabilities.placeholder,
                capabilities=details,
                started_at=now,
                last_seen_at=now,
            )
        )
    else:
        row.last_seen_at = now
        row.capabilities = details
    db.commit()


def forget(db: Session, worker_id: str) -> None:
    """A worker that is stopping on purpose says so, so the dashboard does not wait to notice."""
    db.execute(delete(Worker).where(Worker.worker_id == worker_id))
    db.commit()


def prune(db: Session, now: datetime | None = None) -> int:
    """Forget workers that have not been heard from for a day."""
    cutoff = (now or timeutil.utcnow()) - RETAIN_FOR
    removed = rows_affected(db.execute(delete(Worker).where(Worker.last_seen_at < cutoff)))
    db.commit()
    return removed


@dataclass(frozen=True)
class ProcessingStatus:
    """What the dashboard shows about processing for one company."""

    # A worker has reported in recently, so queued recordings will actually be picked up.
    available: bool
    workers: int
    # Output is placeholder text, not real transcripts (the development stub).
    placeholder: bool
    # What the running worker really does. Empty when none is running.
    transcription: bool
    diarization: bool
    speaker_matching: bool
    queued: int
    running: int
    notes: tuple[str, ...]
    # The API itself can enroll voices (it has a speaker model). Separate from the worker, because
    # enrollment runs in the API process.
    enrollment_available: bool


def processing_status(db: Session, settings: Settings, company_id: uuid.UUID) -> ProcessingStatus:
    now = timeutil.utcnow()
    online = db.scalars(
        select(Worker)
        .where(Worker.last_seen_at >= now - timedelta(seconds=settings.worker_online_seconds))
        .order_by(Worker.last_seen_at.desc())
    ).all()

    # (`dict(rows)` does not work with SQLAlchemy 2.1's Row, so the pairs are picked out by hand.)
    counts = {
        row[0]: row[1]
        for row in db.execute(
            select(ProcessingJob.status, func.count())
            .where(ProcessingJob.company_id == company_id, ProcessingJob.status.in_(("queued", "running")))
            .group_by(ProcessingJob.status)
        )
    }
    mine = configured_capabilities(settings)
    newest = online[0] if online else None
    caps = newest.capabilities if newest else {}
    return ProcessingStatus(
        available=bool(online),
        workers=len(online),
        placeholder=any(w.placeholder for w in online),
        transcription=bool(caps.get("transcription", False)),
        diarization=bool(caps.get("diarization", False)),
        speaker_matching=bool(caps.get("speakerMatching", False)),
        queued=counts.get("queued", 0),
        running=counts.get("running", 0),
        notes=tuple(str(n) for n in caps.get("notes", ())) if newest else mine.notes,
        enrollment_available=mine.speaker_matching and not mine.placeholder,
    )
