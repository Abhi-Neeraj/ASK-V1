"""A meeting that reached the server in several parts.

The phone uploads about a minute of audio at a time while the locket is still sending the meeting, so the
server can transcribe while people are still talking. Each upload is its own recording; the locket's session
reference ties the parts of one meeting together (same locket, same reference, ordered by their sequence).

What a person saves, `transcription.txt` and `summary.txt`, is for the WHOLE meeting so far: the transcript is
every processed part in order, and the summary is the newest one written, which was made from every part
processed at that moment. A recording that is the only part of its meeting behaves as it always did.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Recording, Transcript, TranscriptSegment

_LAST = 10**9


def session_parts(db: Session, recording: Recording) -> list[Recording]:
    """The parts of the meeting this recording belongs to, in order. Just the recording itself when it has no
    session reference. Deleted parts are left out."""
    if not recording.session_ref:
        return [recording]
    parts = list(
        db.scalars(
            select(Recording).where(
                Recording.device_id == recording.device_id,
                Recording.session_ref == recording.session_ref,
                Recording.deleted_at.is_(None),
            )
        )
    )
    if recording.id not in {p.id for p in parts}:
        parts.append(recording)
    parts.sort(key=lambda p: (p.sequence if p.sequence is not None else _LAST, p.started_at))
    return parts


def part_offsets(parts: Sequence[Recording]) -> dict[uuid.UUID, int]:
    """Milliseconds from the start of the first part to the start of each part."""
    base = min(p.started_at for p in parts)
    return {p.id: max(int((p.started_at - base).total_seconds() * 1000), 0) for p in parts}


@dataclass(frozen=True)
class MeetingLine:
    start_ms: int  # from the start of the meeting
    speaker_label: str
    person_id: uuid.UUID | None
    text: str


def processed_lines(
    db: Session, parts: Sequence[Recording], *, skip: uuid.UUID | None = None
) -> list[MeetingLine]:
    """Every transcript line of the parts that have a transcript, in meeting order. `skip` leaves one
    recording out (the one being processed again, whose old transcript is about to be replaced)."""
    ids = [p.id for p in parts if p.id != skip]
    if not ids:
        return []
    offsets = part_offsets(parts)
    order = {p.id: i for i, p in enumerate(parts)}
    rows = db.execute(
        select(Transcript.recording_id, TranscriptSegment)
        .join(TranscriptSegment, TranscriptSegment.transcript_id == Transcript.id)
        .where(Transcript.recording_id.in_(ids))
    ).all()
    lines = [
        (order[recording_id], segment.idx, offsets[recording_id], segment) for recording_id, segment in rows
    ]
    lines.sort(key=lambda item: (item[0], item[1]))
    return [
        MeetingLine(offset + segment.start_ms, segment.speaker_label, segment.person_id, segment.text)
        for _, _, offset, segment in lines
    ]


def newest_summary(db: Session, parts: Sequence[Recording]) -> str | None:
    """The newest summary among the parts (each was made from all the parts processed at the time)."""
    row = db.execute(
        select(Transcript.summary_text)
        .where(
            Transcript.recording_id.in_([p.id for p in parts]),
            Transcript.summary_text.is_not(None),
            Transcript.summary_text != "",
        )
        .order_by(Transcript.created_at.desc(), Transcript.updated_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def clock(ms: int) -> str:
    seconds = max(ms, 0) // 1000
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def summary_input(db: Session, recording: Recording, aligned: Sequence[tuple[int, str]]) -> str | None:
    """What the summarizer should read for this recording: the whole meeting so far, as `[HH:MM:SS] text`
    lines (no speaker numbers: the speaker labels of different parts do not belong together). `aligned` are
    this recording's own new lines as (start_ms within the part, text). None when the recording is not one of
    several parts, which is then summarised alone as before."""
    parts = session_parts(db, recording)
    if len(parts) < 2:
        return None
    offsets = part_offsets(parts)
    entries = [(line.start_ms, line.text) for line in processed_lines(db, parts, skip=recording.id)]
    entries.extend((offsets[recording.id] + start_ms, text) for start_ms, text in aligned)
    entries.sort(key=lambda entry: entry[0])
    return "".join(f"[{clock(start)}] {text.strip()}\n" for start, text in entries)
