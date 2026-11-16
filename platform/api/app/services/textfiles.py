"""transcription.txt and summary.txt, the two plain-text files the phone saves as its local backup."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import Person, Recording, Transcript, TranscriptSegment

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


def _transcript(db: Session, recording: Recording) -> Transcript:
    transcript = db.scalar(select(Transcript).where(Transcript.recording_id == recording.id))
    if transcript is None:
        raise ApiError(404, "transcript_not_ready", "This recording has no transcript yet.")
    return transcript


def transcription_file(db: Session, recording: Recording) -> str:
    transcript = _transcript(db, recording)
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
    if not transcript.summary_text:
        raise ApiError(404, "summary_not_ready", "This recording has no summary yet.")
    text = transcript.summary_text.strip()
    return f"{text}\n"
