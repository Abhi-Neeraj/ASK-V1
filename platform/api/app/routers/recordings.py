from __future__ import annotations

import logging
import re
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from app.errors import ApiError, not_found
from app.models import ProcessingJob, Recording
from app.schemas.common import Page, UtcDatetime
from app.schemas.recordings import (
    JobOut,
    RecordingDetailOut,
    RecordingOut,
    TranscriptOut,
    TranscriptPatchIn,
)
from app.security.deps import Authed, DbDep, Manager, SettingsDep
from app.services import audio_archive, textfiles
from app.services import insights as insights_service
from app.services import meeting as meetings
from app.services import recordings as svc
from app.services.audit import record_audit
from app.services.storage import Storage

# "bytes=0-" or "bytes=0-1023": asking for the clip from its first byte.
_FROM_THE_START = re.compile(r"^\s*bytes\s*=\s*0+-", re.IGNORECASE)

logger = logging.getLogger("ask.recordings")

router = APIRouter(tags=["recordings"])

SortField = Literal["startedAt", "uploadedAt", "durationMs"]


def _storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


@router.get("/api/recordings", response_model=Page[RecordingOut])
def list_recordings(
    ctx: Authed,
    db: DbDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    device_id: Annotated[uuid.UUID | None, Query(alias="deviceId")] = None,
    state: Annotated[str | None, Query(max_length=24)] = None,
    person_id: Annotated[uuid.UUID | None, Query(alias="personId")] = None,
    date_from: Annotated[UtcDatetime | None, Query(alias="dateFrom")] = None,
    date_to: Annotated[UtcDatetime | None, Query(alias="dateTo")] = None,
    min_duration_ms: Annotated[int | None, Query(alias="minDurationMs", ge=0)] = None,
    max_duration_ms: Annotated[int | None, Query(alias="maxDurationMs", ge=0)] = None,
    session_ref: Annotated[str | None, Query(alias="sessionRef", max_length=64)] = None,
    sort: SortField = "startedAt",
    order: Literal["asc", "desc"] = "desc",
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
) -> Page[RecordingOut]:
    filters = svc.Filters(
        q=q,
        device_id=device_id,
        state=state or None,
        person_id=person_id,
        date_from=date_from,
        date_to=date_to,
        min_duration_ms=min_duration_ms,
        max_duration_ms=max_duration_ms,
        session_ref=session_ref,
        sort=sort,
        descending=order == "desc",
        page=page,
        page_size=page_size,
    )
    rows, total = svc.search(db, ctx, filters)
    return Page[RecordingOut](items=svc.recording_outs(db, rows), total=total, page=page, page_size=page_size)


@router.get("/api/recordings/{recording_id}", response_model=RecordingDetailOut)
def get_recording(recording_id: uuid.UUID, ctx: Authed, db: DbDep) -> RecordingDetailOut:
    recording = svc.get_visible_recording(db, ctx, recording_id)
    detail = svc.recording_detail(db, recording, can_manage=ctx.can_manage)
    # Why it failed (or is being retried) is for the people who can fix it. The text is already cleaned of
    # paths and tokens when it is stored; employees get the fixed sentence in `error` and nothing more.
    if ctx.can_manage and recording.state in {"failed", "retrying"}:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.recording_id == recording.id))
        if job is not None and job.last_error:
            detail = detail.model_copy(update={"failure_detail": job.last_error})
    return detail


@router.get("/api/recordings/{recording_id}/audio")
def get_audio(
    recording_id: uuid.UUID,
    request: Request,
    ctx: Authed,
    db: DbDep,
    download: bool = False,
) -> FileResponse:
    """Playback (supports range requests) for anyone who can see the recording. Saving a copy needs a
    manager or administrator, and is written to the audit log."""
    recording = svc.get_visible_recording(db, ctx, recording_id)
    if download and not ctx.can_manage:
        raise ApiError(403, "forbidden", "Only managers and administrators can download audio.")
    storage = _storage(request)
    # The file on the server's disk, or, when the disk does not keep it (a free host restarts with an empty
    # one), the compressed copy in the database, decoded to a temporary WAV that is removed after it is sent.
    local = storage.exists(recording.storage_key)
    temporary = None
    if not local:
        temporary = storage.new_temp_path()
        if not audio_archive.restore(db, recording.id, temporary):
            temporary.unlink(missing_ok=True)
            raise not_found("audio file")
    # Playback and saving stream the same bytes, so playback is recorded too. A player asks for a clip in many
    # pieces as it plays or is scrubbed; only the request for the start of the clip is an access worth a row.
    range_header = request.headers.get("range")
    starts_the_clip = range_header is None or _FROM_THE_START.match(range_header) is not None
    if download or starts_the_clip:
        record_audit(
            db,
            action="recording.downloaded" if download else "recording.played",
            company_id=ctx.company_id,
            actor_user_id=ctx.user.id,
            object_type="recording",
            object_id=recording.id,
            ip=ctx.ip,
        )
        db.commit()
    disposition = "attachment" if download else "inline"
    return FileResponse(
        storage.path_for(recording.storage_key) if temporary is None else temporary,
        background=None if temporary is None else BackgroundTask(temporary.unlink, missing_ok=True),
        media_type="audio/wav",
        # The filename is built from the ID, never from the stored path.
        headers={
            "Content-Disposition": f'{disposition}; filename="recording-{recording.id}.wav"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/recordings/{recording_id}/transcript", response_model=TranscriptOut)
def get_transcript(recording_id: uuid.UUID, ctx: Authed, db: DbDep) -> TranscriptOut:
    recording = svc.get_visible_recording(db, ctx, recording_id)
    transcript = svc.find_transcript(db, recording)
    if transcript is None:
        raise not_found("transcript")
    return svc.transcript_out(db, transcript)


def _text_file(text: str, filename: str) -> Response:
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/api/recordings/{recording_id}/transcription.txt")
def get_transcription_txt(recording_id: uuid.UUID, ctx: Authed, db: DbDep) -> Response:
    """The transcript as plain text, one line per segment: `[HH:MM:SS] Speaker 1: ...`."""
    recording = svc.get_visible_recording(db, ctx, recording_id)
    return _text_file(textfiles.transcription_file(db, recording), "transcription.txt")


@router.get("/api/recordings/{recording_id}/summary.txt")
def get_summary_txt(recording_id: uuid.UUID, ctx: Authed, db: DbDep) -> Response:
    """The written summary as plain text."""
    recording = svc.get_visible_recording(db, ctx, recording_id)
    return _text_file(textfiles.summary_file(db, recording), "summary.txt")


@router.patch("/api/recordings/{recording_id}/transcript", response_model=RecordingDetailOut)
def correct_transcript(
    recording_id: uuid.UUID, body: TranscriptPatchIn, ctx: Authed, db: DbDep, settings: SettingsDep
) -> RecordingDetailOut:
    recording = svc.get_visible_recording(db, ctx, recording_id)
    svc.apply_corrections(db, ctx, recording, body.edits)
    transcript = svc.find_transcript(db, recording)
    if transcript is not None:
        # The summary was built from the old words. Rebuild it from the corrected ones; if that cannot be done
        # the correction still stands and the old summary is kept.
        try:
            insights_service.refresh(db, settings, transcript)
        except Exception:
            logger.exception("could not rebuild the summary after a correction")
    db.commit()
    return svc.recording_detail(db, recording, can_manage=ctx.can_manage)


@router.delete("/api/recordings/{recording_id}", status_code=204)
def delete_recording(recording_id: uuid.UUID, ctx: Manager, db: DbDep, meeting: bool = False) -> Response:
    """Delete one recording, or with `meeting=true` every part of the meeting it belongs to (a meeting of
    hours is hundreds of one-minute parts). Only parts the caller may see are deleted."""
    recording = svc.get_visible_recording(db, ctx, recording_id)
    targets = [recording]
    if meeting:
        parts = meetings.session_parts(db, recording)
        allowed = {
            r.id
            for r in db.scalars(svc.visible_recordings(ctx).where(Recording.id.in_([p.id for p in parts])))
        }
        targets = [p for p in parts if p.id in allowed]
    for target in targets:
        if target.deleted_at is None:
            svc.delete_recording(db, ctx, target)
    db.commit()
    return Response(status_code=204)


@router.post("/api/recordings/{recording_id}/reprocess", response_model=RecordingOut, status_code=202)
def reprocess_recording(recording_id: uuid.UUID, request: Request, ctx: Manager, db: DbDep) -> RecordingOut:
    recording = svc.get_visible_recording(db, ctx, recording_id)
    if not _storage(request).exists(recording.storage_key):
        # The audio was removed after processing (ASK_DELETE_AUDIO_AFTER_PROCESSING): nothing to work from.
        raise ApiError(
            409,
            "audio_not_kept",
            "The audio of this recording is no longer on the server, so it cannot be processed again.",
        )
    svc.reprocess(db, ctx, recording)
    db.commit()
    return svc.recording_outs(db, [recording])[0]


@router.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, ctx: Authed, db: DbDep) -> JobOut:
    job = db.get(ProcessingJob, job_id)
    # A job is visible exactly when its recording is.
    if job is None or db.scalar(svc.visible_recordings(ctx).where(Recording.id == job.recording_id)) is None:
        raise not_found("job")
    return JobOut(
        id=job.id,
        recording_id=job.recording_id,
        status=job.status,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        # The cleaned-up cause is for the people who can fix it; it names settings, which others do not need.
        last_error=job.last_error if ctx.can_manage else None,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )
