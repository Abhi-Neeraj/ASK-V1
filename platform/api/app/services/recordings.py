"""The recording library: who can see what, searching, transcripts, deletion and retention."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app import timeutil
from app.errors import ApiError, not_found
from app.models import (
    Company,
    Device,
    Person,
    ProcessingJob,
    Recording,
    RecordingAccess,
    Transcript,
    TranscriptSegment,
)
from app.schemas.recordings import (
    PersonRef,
    RecordingDetailOut,
    RecordingOut,
    SegmentEdit,
    SegmentOut,
    TranscriptOut,
)
from app.security.deps import Ctx
from app.services import insights
from app.services.audit import publish_event, record_audit, recording_event
from app.services.storage import Storage

UNKNOWN_SPEAKER = "Unknown speaker"
ACTIVE_STATES = (
    "uploaded",
    "queued",
    "validating",
    "transcribing",
    "diarizing",
    "identifying_speakers",
    "aligning",
    "retrying",
)
SORT_COLUMNS = {
    "startedAt": Recording.started_at,
    "uploadedAt": Recording.uploaded_at,
    "durationMs": Recording.duration_ms,
}


# --- visibility -------------------------------------------------------------------------------


def visible_recordings(ctx: Ctx) -> Select[Recording]:
    """Every recording query starts here, so the company boundary cannot be forgotten.

    Managers see every recording in the company. An employee sees the recordings that arrived while the locket
    was assigned to them: what a locket recorded stays with whoever wore it, and passing the locket on does
    not
    pass its history on."""
    query = select(Recording).where(Recording.company_id == ctx.company_id, Recording.deleted_at.is_(None))
    if not ctx.can_manage:
        # Written when the clip arrived: everyone on its locket then (see app.services.uploads).
        query = query.where(
            exists().where(
                RecordingAccess.recording_id == Recording.id, RecordingAccess.user_id == ctx.user.id
            )
        )
    return query


def get_visible_recording(db: Session, ctx: Ctx, recording_id: uuid.UUID) -> Recording:
    recording = db.scalar(visible_recordings(ctx).where(Recording.id == recording_id))
    if recording is None:
        raise not_found("recording")  # identical for "missing", "deleted" and "someone else's"
    return recording


# --- searching ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Filters:
    q: str | None = None
    device_id: uuid.UUID | None = None
    state: str | None = None
    person_id: uuid.UUID | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    min_duration_ms: int | None = None
    max_duration_ms: int | None = None
    session_ref: str | None = None
    sort: str = "startedAt"
    descending: bool = True
    page: int = 1
    page_size: int = 20


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def apply_filters(query: Select[Recording], filters: Filters) -> Select[Recording]:
    if filters.device_id is not None:
        query = query.where(Recording.device_id == filters.device_id)
    if filters.state:
        query = query.where(Recording.state == filters.state)
    if filters.date_from is not None:
        query = query.where(Recording.started_at >= filters.date_from)
    if filters.date_to is not None:
        query = query.where(Recording.started_at <= filters.date_to)
    if filters.min_duration_ms is not None:
        query = query.where(Recording.duration_ms >= filters.min_duration_ms)
    if filters.max_duration_ms is not None:
        query = query.where(Recording.duration_ms <= filters.max_duration_ms)
    if filters.session_ref:
        query = query.where(Recording.session_ref == filters.session_ref)

    segment_match = (
        select(TranscriptSegment.id)
        .join(Transcript, Transcript.id == TranscriptSegment.transcript_id)
        .where(Transcript.recording_id == Recording.id)
    )
    if filters.person_id is not None:
        query = query.where(exists(segment_match.where(TranscriptSegment.person_id == filters.person_id)))
    if filters.q:
        pattern = _like(filters.q.strip())
        by_text = TranscriptSegment.text.ilike(pattern, escape="\\")
        by_speaker = exists(
            select(Person.id).where(
                Person.id == TranscriptSegment.person_id, Person.display_name.ilike(pattern, escape="\\")
            )
        )
        query = query.where(exists(segment_match.where(or_(by_text, by_speaker))))
    return query


def search(db: Session, ctx: Ctx, filters: Filters) -> tuple[list[Recording], int]:
    base = apply_filters(visible_recordings(ctx), filters)
    total = db.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
    column = SORT_COLUMNS.get(filters.sort, Recording.started_at)
    ordered = base.order_by(column.desc() if filters.descending else column.asc(), Recording.id)
    page = db.scalars(ordered.limit(filters.page_size).offset((filters.page - 1) * filters.page_size)).all()
    return list(page), total


# --- turning rows into responses ----------------------------------------------------------------


def _summaries(
    db: Session, recordings: list[Recording]
) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, list[str]], set[uuid.UUID]]:
    """First line of each transcript, the distinct speakers, and which transcripts are placeholders,
    fetched for the whole page at once."""
    if not recordings:
        return {}, {}, set()
    ids = [r.id for r in recordings]
    rows = db.execute(
        select(
            Transcript.recording_id,
            Transcript.is_placeholder,
            TranscriptSegment.idx,
            TranscriptSegment.text,
            Person.display_name,
        )
        .join(TranscriptSegment, TranscriptSegment.transcript_id == Transcript.id)
        .outerjoin(Person, Person.id == TranscriptSegment.person_id)
        .where(Transcript.recording_id.in_(ids))
        .order_by(Transcript.recording_id, TranscriptSegment.idx)
    )
    snippets: dict[uuid.UUID, str] = {}
    speakers: dict[uuid.UUID, list[str]] = defaultdict(list)
    placeholders: set[uuid.UUID] = set()
    for recording_id, is_placeholder, _idx, text, name in rows:
        snippets.setdefault(recording_id, text if len(text) <= 160 else text[:157].rstrip() + "…")
        label = name or UNKNOWN_SPEAKER
        if label not in speakers[recording_id]:
            speakers[recording_id].append(label)
        if is_placeholder:
            placeholders.add(recording_id)
    # A placeholder transcript with no segments at all (an empty clip) is still a placeholder.
    for recording_id in db.scalars(
        select(Transcript.recording_id).where(Transcript.recording_id.in_(ids), Transcript.is_placeholder)
    ):
        placeholders.add(recording_id)
    return snippets, speakers, placeholders


def _part_counts(
    db: Session, recordings: list[Recording]
) -> dict[tuple[uuid.UUID, str], tuple[int, int, datetime]]:
    """For each meeting on this page, keyed by (locket, session reference): how many parts have arrived, their
    total length in milliseconds and when the first began. A session reference is only meaningful for the
    locket that chose it, so two lockets never share a count."""
    keys: set[tuple[uuid.UUID, str]] = {
        (r.device_id, r.session_ref) for r in recordings if r.session_ref is not None and r.session_ref != ""
    }
    if not keys:
        return {}
    rows = db.execute(
        select(
            Recording.device_id,
            Recording.session_ref,
            func.count(),
            func.sum(Recording.duration_ms),
            func.min(Recording.started_at),
        )
        .where(
            Recording.device_id.in_({device for device, _ in keys}),
            Recording.session_ref.in_({ref for _, ref in keys}),
            Recording.deleted_at.is_(None),
        )
        .group_by(Recording.device_id, Recording.session_ref)
    )
    return {
        (device, ref): (count, int(total or 0), first)
        for device, ref, count, total, first in rows
        if ref is not None and (device, ref) in keys
    }


def _meeting(
    recording: Recording, counts: dict[tuple[uuid.UUID, str], tuple[int, int, datetime]]
) -> tuple[int, int, datetime] | None:
    """(parts, total milliseconds, first start) when the recording is one of several parts of a meeting."""
    if not recording.session_ref:
        return None
    found = counts.get((recording.device_id, recording.session_ref))
    return found if found is not None and found[0] > 1 else None


def _part_label(
    recording: Recording, counts: dict[tuple[uuid.UUID, str], tuple[int, int, datetime]]
) -> int | None:
    """The part number, shown only when it means something: a lone first part is just a recording."""
    if not recording.session_ref or recording.sequence is None:
        return None
    if recording.sequence > 1 or _meeting(recording, counts) is not None:
        return recording.sequence
    return None


def recording_outs(db: Session, recordings: list[Recording]) -> list[RecordingOut]:
    snippets, speakers, placeholders = _summaries(db, recordings)
    device_ids = {r.device_id for r in recordings}
    names: dict[uuid.UUID, str] = {}
    simulated: set[uuid.UUID] = set()
    if device_ids:
        for row in db.execute(
            select(Device.id, Device.name, Device.is_simulated).where(Device.id.in_(device_ids))
        ):
            names[row.id] = row.name
            if row.is_simulated:
                simulated.add(row.id)
    parts = _part_counts(db, recordings)
    return [
        RecordingOut(
            id=r.id,
            device_id=r.device_id,
            device_name=names.get(r.device_id, "Unknown locket"),
            simulated=r.device_id in simulated,
            started_at=r.started_at,
            ended_at=r.ended_at,
            duration_ms=r.duration_ms,
            size_bytes=r.size_bytes,
            format=r.format,
            uploaded_at=r.uploaded_at,
            part=_part_label(r, parts),
            session_ref=r.session_ref or None,
            part_count=meeting[0] if (meeting := _meeting(r, parts)) else None,
            meeting_duration_ms=meeting[1] if meeting else None,
            meeting_started_at=meeting[2] if meeting else None,
            state=r.state,
            retry_count=r.retry_count,
            error=r.error if r.state == "failed" else None,
            snippet=snippets.get(r.id),
            speakers=speakers.get(r.id, []),
            placeholder=r.id in placeholders,
        )
        for r in recordings
    ]


def transcript_out(db: Session, transcript: Transcript) -> TranscriptOut:
    rows = db.execute(
        select(TranscriptSegment, Person.display_name)
        .outerjoin(Person, Person.id == TranscriptSegment.person_id)
        .where(TranscriptSegment.transcript_id == transcript.id)
        .order_by(TranscriptSegment.idx)
    )
    segments = [
        SegmentOut(
            id=s.id,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            speaker_label=s.speaker_label,
            person=PersonRef(id=s.person_id, name=name) if s.person_id and name else None,
            confidence=s.confidence if s.person_id else None,
            text=s.text,
            needs_review=s.needs_review,
            corrected=s.corrected,
        )
        for s, name in rows
    ]
    return TranscriptOut(
        transcription_model=transcript.transcription_model,
        transcription_model_version=transcript.transcription_model_version,
        diarization_model=transcript.diarization_model,
        diarization_model_version=transcript.diarization_model_version,
        review_status=transcript.review_status,
        placeholder=transcript.is_placeholder,
        separation_experimental="experimental" in (transcript.diarization_model or "").lower(),
        language=transcript.language,
        segments=segments,
    )


def find_transcript(db: Session, recording: Recording) -> Transcript | None:
    return db.scalar(select(Transcript).where(Transcript.recording_id == recording.id))


def recording_detail(db: Session, recording: Recording, *, can_manage: bool = False) -> RecordingDetailOut:
    base = recording_outs(db, [recording])[0]
    transcript = find_transcript(db, recording)
    # No summary for placeholder text: a summary of words nobody said would mislead.
    analysis = (
        insights.analysis_out(db, transcript, recording.duration_ms, can_manage=can_manage)
        if transcript is not None and not transcript.is_placeholder
        else None
    )
    return RecordingDetailOut(
        **base.model_dump(),
        transcript=transcript_out(db, transcript) if transcript else None,
        audio_url=f"/api/recordings/{recording.id}/audio",
        analysis=analysis,
    )


# --- editing a transcript ----------------------------------------------------------------------


def apply_corrections(db: Session, ctx: Ctx, recording: Recording, edits: list[SegmentEdit]) -> None:
    transcript = find_transcript(db, recording)
    if transcript is None:
        raise ApiError(409, "no_transcript", "This recording has no transcript yet.")
    wanted = {edit.segment_id for edit in edits}
    segments = {
        s.id: s
        for s in db.scalars(
            select(TranscriptSegment).where(
                TranscriptSegment.transcript_id == transcript.id, TranscriptSegment.id.in_(wanted)
            )
        )
    }
    if len(segments) != len(wanted):
        raise ApiError(
            422,
            "validation_error",
            "One of the segments is not part of this transcript.",
            {"edits": "Unknown segment."},
        )

    changed = 0
    for edit in edits:
        segment = segments[edit.segment_id]
        text = edit.text.strip()
        if not text:
            raise ApiError(422, "validation_error", "A segment cannot be empty.", {"edits": "Empty text."})
        if text != segment.text:
            if segment.original_text is None:
                segment.original_text = segment.text
            segment.text = text
            segment.corrected = True
            changed += 1
        # Looking at a flagged segment and confirming it also resolves the flag.
        segment.needs_review = False

    db.flush()
    refresh_review_state(db, transcript, recording)
    record_audit(
        db,
        action="transcript.corrected",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="recording",
        object_id=recording.id,
        detail={"segments": len(wanted), "changed": changed},  # counts only, never the text
        ip=ctx.ip,
    )


def refresh_review_state(db: Session, transcript: Transcript, recording: Recording) -> None:
    rows = db.scalars(select(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript.id)).all()
    ordered = sorted(rows, key=lambda s: s.idx)
    transcript.full_text = " ".join(s.text for s in ordered)
    transcript.updated_at = timeutil.utcnow()
    if any(s.needs_review for s in ordered):
        transcript.review_status = "needs_review"
        if recording.state == "completed":
            recording.state = "needs_review"
    else:
        transcript.review_status = (
            "reviewed" if any(s.corrected for s in ordered) or recording.state == "needs_review" else "none"
        )
        if recording.state == "needs_review":
            recording.state = "completed"


# --- deleting, retention and purging ----------------------------------------------------------


def grace_period(db: Session, company_id: uuid.UUID) -> timedelta:
    company = db.get(Company, company_id)
    return timedelta(days=company.purge_grace_days if company else 7)


def soft_delete(db: Session, recording: Recording, *, now: datetime | None = None) -> None:
    now = now or timeutil.utcnow()
    recording.deleted_at = now
    recording.purge_after = now + grace_period(db, recording.company_id)
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.recording_id == recording.id))
    if job is not None and job.status in {"queued", "running"}:
        job.status = "failed"
        job.last_error = "The recording was deleted."
        job.updated_at = now


def delete_recording(db: Session, ctx: Ctx, recording: Recording) -> None:
    soft_delete(db, recording)
    record_audit(
        db,
        action="recording.deleted",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="recording",
        object_id=recording.id,
        detail={"purge_after": recording.purge_after.isoformat() if recording.purge_after else None},
        ip=ctx.ip,
    )
    publish_event(
        db,
        ctx.company_id,
        "recording.deleted",
        recording_event(recording),
    )


def purge_deleted(db: Session, storage: Storage, now: datetime | None = None, *, limit: int = 200) -> int:
    """Permanently remove recordings whose grace period is over: audio first, then the rows."""
    now = now or timeutil.utcnow()
    due = db.scalars(
        select(Recording)
        .where(Recording.deleted_at.is_not(None), Recording.purge_after <= now)
        .order_by(Recording.purge_after)
        .limit(limit)
    ).all()
    purged = 0
    for recording in due:
        try:
            storage.delete(recording.storage_key)
        except OSError:
            continue  # keep the row so the next run retries instead of orphaning the file
        company_id, recording_id = recording.company_id, recording.id
        db.delete(recording)  # transcripts, segments and the job go with it (ON DELETE CASCADE)
        record_audit(
            db,
            action="recording.purged",
            company_id=company_id,
            object_type="recording",
            object_id=recording_id,
        )
        purged += 1
    if purged:
        db.commit()
    return purged


def enforce_retention(db: Session, now: datetime | None = None) -> int:
    """Move recordings older than their company's retention period into the deletion queue.

    "Older" counts from when the recording was stored, not from the time the locket claimed it was made: that
    time is chosen by the device, and a wrong clock or a bad actor could otherwise make a new clip look
    years old
    and have it deleted at the next pass."""
    now = now or timeutil.utcnow()
    marked = 0
    for company in db.scalars(select(Company).where(Company.retention_days.is_not(None))):
        assert company.retention_days is not None  # noqa: S101
        cutoff = now - timedelta(days=company.retention_days)
        for recording in db.scalars(
            select(Recording).where(
                and_(
                    Recording.company_id == company.id,
                    Recording.deleted_at.is_(None),
                    Recording.uploaded_at < cutoff,
                )
            )
        ):
            soft_delete(db, recording, now=now)
            record_audit(
                db,
                action="recording.retention_deleted",
                company_id=company.id,
                object_type="recording",
                object_id=recording.id,
                detail={"retention_days": company.retention_days},
            )
            marked += 1
    if marked:
        db.commit()
    return marked


def reprocess(db: Session, ctx: Ctx, recording: Recording) -> None:
    if recording.state in ACTIVE_STATES:
        raise ApiError(409, "already_processing", "This recording is already being processed.")
    now = timeutil.utcnow()
    recording.state = "queued"
    recording.error = None
    recording.retry_count += 1
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.recording_id == recording.id))
    if job is None:
        job = ProcessingJob(recording_id=recording.id, company_id=recording.company_id)
        db.add(job)
    job.status = "queued"
    job.attempts = 0
    job.last_error = None
    job.locked_by = None
    job.locked_at = None
    job.completed_at = None
    job.run_after = now
    job.updated_at = now
    record_audit(
        db,
        action="recording.reprocess_requested",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="recording",
        object_id=recording.id,
        ip=ctx.ip,
    )
    publish_event(
        db,
        ctx.company_id,
        "recording.state",
        recording_event(recording, state="queued"),
    )
