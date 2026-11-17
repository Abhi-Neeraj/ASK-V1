from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import CamelModel, CleanText
from app.schemas.people import PitchOut


class PersonRef(CamelModel):
    id: uuid.UUID
    name: str


class RecordingOut(CamelModel):
    id: uuid.UUID
    device_id: uuid.UUID
    device_name: str
    # Made by a development stand-in rather than real hardware, so the audio is not a real recording.
    simulated: bool
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    size_bytes: int
    format: str
    uploaded_at: datetime
    # 1, 2, 3 ... when this clip is one part of a longer recording the locket cut up (a button-controlled
    # locket does this for anything longer than the server's clip limit); null for an ordinary single clip.
    part: int | None = None
    # The locket's reference for the whole meeting this clip belongs to: the same for every part of it, null
    # for an ordinary clip. Chosen by the locket and shown only to people who can already see the recording.
    session_ref: str | None = None
    # For a recording that is one of several parts of a meeting: how many parts have arrived, their total
    # length and when the first began (so a meeting of hours can be shown as one row). Null otherwise.
    part_count: int | None = None
    meeting_duration_ms: int | None = None
    meeting_started_at: datetime | None = None
    state: str
    retry_count: int
    error: str | None
    snippet: str | None
    speakers: list[str]
    # The transcript was written by the development stub, so its text is not what was said.
    placeholder: bool


class SegmentOut(CamelModel):
    id: uuid.UUID
    start_ms: int
    end_ms: int
    speaker_label: str
    person: PersonRef | None
    confidence: float | None
    text: str
    needs_review: bool
    corrected: bool


class TranscriptOut(CamelModel):
    transcription_model: str
    transcription_model_version: str
    diarization_model: str | None
    diarization_model_version: str | None
    review_status: str
    # Placeholder text from the development stub. Every screen and export must say so.
    placeholder: bool
    # Who-spoke-when was worked out by an experimental method (no gated model needed), so the speaker labels
    # should be checked by listening. The model's name is deliberately not part of this answer.
    separation_experimental: bool = False
    language: str | None
    segments: list[SegmentOut]


class VoiceOut(CamelModel):
    """One speaker in a recording: how much they spoke and how high their voice sits."""

    label: str
    # A name only where the voice was matched to a person with consent on record; otherwise "Speaker 1", ...
    title: str
    name: str | None
    talk_ms: int
    share: float
    turns: int
    # Base, low and high pitch of this speaker in this recording. Null when too little voiced speech.
    pitch: PitchOut | None
    # How the name was found. Null when the speaker is not named.
    matched_by: Literal["voice_model", "signature"] | None
    # How alike the voice was to the person's (0 to 1). Managers only. Not a probability.
    match_score: float | None


class ItemContextOut(CamelModel):
    previous: str | None
    previous_speaker: str | None
    topic: list[str]
    # Which context (topic) this sentence is in, whether it opens a new one, and how similar it was to the
    # closest of the three sentences before it. Null when contexts were not worked out for this recording.
    group: int | None = None
    new_context: bool | None = None
    similarity: float | None = None


class AnalysisItemOut(CamelModel):
    """One sentence, worked out by fixed rules: what was said, its context, the key activity, the task that
    follows from it and who it falls to."""

    segment_index: int
    start_ms: int
    end_ms: int
    speaker: str
    what: str
    context: ItemContextOut
    activity: str
    activity_label: str
    secondary: list[str]
    confidence: float
    task: str | None
    owner: str | None
    due: str | None
    # An activity together with a date or time: a task with a deadline.
    dated: bool = False
    # The action verb and the main words of the sentence.
    verb: str | None = None
    keywords: list[str] = Field(default_factory=list)
    # Questions only: true when nobody answered it.
    open: bool | None


class SummaryLineOut(CamelModel):
    activity: str
    activity_label: str
    text: str
    said: str
    speaker: str
    owner: str | None
    due: str | None
    dated: bool = False
    open: bool | None
    at_ms: int
    segment_index: int
    confidence: float | None


class SummarySectionOut(CamelModel):
    key: str
    title: str
    total: int
    lines: list[SummaryLineOut]


class SummaryOut(CamelModel):
    """Built from the analysis by fixed templates, with no generative AI."""

    narrative: list[str]
    sections: list[SummarySectionOut]
    sentences: int
    truncated: bool


class ContextOut(CamelModel):
    """One stretch of the conversation about one subject."""

    group: int
    start_ms: int
    end_ms: int
    sentences: int
    keywords: list[str]


class AnalysisOut(CamelModel):
    # How sentences were matched to activities: by meaning and key phrases (semantic), or by key phrases and
    # shared words (lexical). The product that did the matching is deliberately not named.
    engine: Literal["semantic", "lexical"]
    voices: list[VoiceOut]
    summary: SummaryOut
    items: list[AnalysisItemOut]
    counts: dict[str, int]
    # The topics, in order. Empty when contexts were not worked out (the words engine, unless asked for).
    contexts: list[ContextOut] = Field(default_factory=list)
    context_threshold: float | None = None
    created_at: datetime


class RecordingDetailOut(RecordingOut):
    transcript: TranscriptOut | None
    # Same-origin URL for playback. Null if the caller may not play this recording.
    audio_url: str | None
    # Why processing failed or is being retried: for managers and administrators only, null for everyone else.
    failure_detail: str | None = None
    # The summary and the per-speaker voice measurements. Null while the recording is processed, for
    # placeholder
    # transcripts, and when the analysis could not be made.
    analysis: AnalysisOut | None = None


class SegmentEdit(CamelModel):
    segment_id: uuid.UUID
    text: CleanText = Field(min_length=1, max_length=5000)


class TranscriptPatchIn(CamelModel):
    edits: list[SegmentEdit] = Field(min_length=1, max_length=200)


class JobOut(CamelModel):
    id: uuid.UUID
    recording_id: uuid.UUID
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
