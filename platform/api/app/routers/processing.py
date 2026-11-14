from __future__ import annotations

from fastapi import APIRouter

from app.schemas.processing import ProcessingOut
from app.security.deps import Authed, DbDep, SettingsDep
from app.services.workers import processing_status

router = APIRouter(tags=["processing"])


@router.get("/api/processing", response_model=ProcessingOut)
def processing(ctx: Authed, db: DbDep, settings: SettingsDep) -> ProcessingOut:
    """Is anything processing recordings right now? Everyone in the company can see this, because
    "waiting for a worker" and "being processed" look the same on a recording unless it is said."""
    status = processing_status(db, settings, ctx.company_id)
    return ProcessingOut(
        available=status.available,
        workers=status.workers,
        placeholder=status.placeholder,
        transcription=status.transcription,
        diarization=status.diarization,
        speaker_matching=status.speaker_matching,
        queued=status.queued,
        running=status.running,
        # The notes name settings and software, for the person who can do something about them.
        notes=list(status.notes) if ctx.can_manage else [],
        enrollment_available=status.enrollment_available,
    )
