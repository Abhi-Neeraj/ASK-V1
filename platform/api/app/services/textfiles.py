"""transcription.txt and summary.txt, the two plain-text files the phone saves as its local backup."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import Person, Recording, Transcript, TranscriptSegment
from app.services import meeting

PLACEHOLDER_LINE = "PLACEHOLDER: not a real transcript"


def _clock(ms: int) -> str:
    seconds = max(ms, 0) // 1000
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def transcription_txt(segments: Sequence[TranscriptSegment], names: Mapping[uuid.UUID, str]) -> str:
    """One line per segment, `[HH:MM:SS] who: text`.

    `who` is the person's name when the segment was matched to someone still known, otherwise "Speaker N".
    Every voice is numbered by its first appearance, named or not, so one voice keeps one number."""
    numbers: dict[str, int] = {}
    lines = []
    for segment in sorted(segments, key=lambda s: s.idx):
        number = numbers.setdefault(segment.speaker_label, len(numbers) + 1)
        who = names.get(segment.person_id) if segment.person_id is not None else None
        if who is None:
            who = f"Speaker {number}"
        lines.append(f"[{_clock(segment.start_ms)}] {who}: {segment.text.strip()}\n")
    return "".join(lines)


def meeting_transcription_txt(lines: Sequence[meeting.MeetingLine], names: Mapping[uuid.UUID, str]) -> str:
    """The same layout as `transcription_txt`, for a meeting made of several parts: the times run from the
    start of the whole meeting."""
    numbers: dict[str, int] = {}
    out = []
    for line in lines:
        number = numbers.setdefault(line.speaker_label, len(numbers) + 1)
        who = names.get(line.person_id) if line.person_id is not None else None
        if who is None:
            who = f"Speaker {number}"
        out.append(f"[{_clock(line.start_ms)}] {who}: {line.text.strip()}\n")
    return "".join(out)


def _transcript(db: Session, recording: Recording) -> Transcript:
    transcript = db.scalar(select(Transcript).where(Transcript.recording_id == recording.id))
    if transcript is None:
        raise ApiError(404, "transcript_not_ready", "This recording has no transcript yet.")
    return transcript


def _meeting_transcription(db: Session, parts: Sequence[Recording]) -> str:
    lines = meeting.processed_lines(db, parts)
    wanted = {line.person_id for line in lines if line.person_id is not None}
    names = (
        {p.id: p.display_name for p in db.scalars(select(Person).where(Person.id.in_(wanted)))}
        if wanted
        else {}
    )
    text = meeting_transcription_txt(lines, names)
    placeholders = db.scalar(
        select(func.count()).where(
            Transcript.recording_id.in_([p.id for p in parts]), Transcript.is_placeholder
        )
    )
    return f"{PLACEHOLDER_LINE}\n{text}" if placeholders else text


def transcription_file(db: Session, recording: Recording) -> str:
    transcript = _transcript(db, recording)  # still a 404 until this part itself has been processed
    parts = meeting.session_parts(db, recording)
    if len(parts) > 1:
        return _meeting_transcription(db, parts)
    segments = list(
        db.scalars(select(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript.id))
    )
    wanted = {s.person_id for s in segments if s.person_id is not None}
    names = (
        {p.id: p.display_name for p in db.scalars(select(Person).where(Person.id.in_(wanted)))}
        if wanted
        else {}
    )
    text = transcription_txt(segments, names)
    return f"{PLACEHOLDER_LINE}\n{text}" if transcript.is_placeholder else text


def summary_file(db: Session, recording: Recording) -> str:
    transcript = _transcript(db, recording)
    parts = meeting.session_parts(db, recording)
    # One of several parts: the meeting's summary is the newest one written (it covers every part so far).
    summary = meeting.newest_summary(db, parts) if len(parts) > 1 else transcript.summary_text
    if not summary:
        raise ApiError(404, "summary_not_ready", "This recording has no summary yet.")
    text = summary.strip()
    return f"{text}\n"
