from __future__ import annotations

from app.schemas.common import CamelModel


class ProcessingOut(CamelModel):
    """Whether recordings are actually being processed, and by what."""

    # A worker has reported in recently. If false, queued recordings are waiting for one.
    available: bool
    workers: int
    # What the running worker writes is placeholder text, not a real transcript.
    placeholder: bool
    transcription: bool
    diarization: bool
    speaker_matching: bool
    queued: int
    running: int
    # What is missing or degraded, in plain words.
    notes: list[str]
    # This server can take voice samples (it has a speaker model). Separate from the worker.
    enrollment_available: bool
