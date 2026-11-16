from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import CamelModel, CleanLine, CleanText

DEFAULT_CONSENT_STATEMENT = (
    "I agree that my voice may be recorded by company lockets, and that a voice profile may be created "
    "from samples I provide so my speech can be recognized in transcripts. I understand I can withdraw "
    "this consent at any time."
)


class PitchOut(CamelModel):
    """How high or low a voice sits, in hertz. Only a summary of the pitch: never the voice signature."""

    base_hz: float
    low_hz: float
    high_hz: float


class SignatureOut(CamelModel):
    """Where a person is with their voice signature (pitch plus voice colour)."""

    # Enough samples, made by the version of the measurement now in use: this person can be matched by it.
    ready: bool
    sample_count: int
    samples_required: int
    # Made by an older version of the measurement, so it can no longer be compared: they need to start again.
    outdated: bool
    updated_at: datetime | None
    # Base, low and high pitch. Shown to managers and administrators only; null for everyone else.
    pitch: PitchOut | None


class PersonOut(CamelModel):
    id: uuid.UUID
    display_name: str
    consent_status: str
    consent_recorded_at: datetime | None
    # Enough samples, made by the speaker model now in use: this person can be recognized.
    enrolled: bool
    enrolled_at: datetime | None
    sample_count: int
    samples_required: int
    # The speaker model changed since they enrolled, so their voice print can no longer be used.
    voice_outdated: bool
    signature: SignatureOut


class PersonCreateIn(CamelModel):
    display_name: CleanLine = Field(min_length=2, max_length=120)


class ConsentIn(CamelModel):
    granted: bool
    # What the person actually agreed to or withdrew. Defaults to the standard statement.
    statement: CleanText | None = Field(default=None, min_length=10, max_length=2000)


class SettingsOut(CamelModel):
    retention_days: int | None
    purge_grace_days: int


class SettingsPatchIn(CamelModel):
    # Present with null means "keep until deleted"; absent means "leave as is".
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    purge_grace_days: int | None = Field(default=None, ge=0, le=365)


class SummaryOut(CamelModel):
    devices_online: int
    devices_total: int
    recordings_today: int
    in_progress: int
    needs_review: int
    failed: int


class ContactIn(CamelModel):
    name: CleanLine = Field(min_length=2, max_length=120)
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    company: CleanLine | None = Field(default=None, max_length=160)
    message: CleanText = Field(min_length=20, max_length=4000)
    # Spam traps: a hidden field real visitors never fill in, and how long the form was open.
    website: str | None = Field(default=None, max_length=300)
    elapsed_ms: int | None = Field(default=None, ge=0, le=86_400_000)


class SignatureCandidateOut(CamelModel):
    person_id: uuid.UUID
    name: str
    # How alike the two voices are, 0 to 1. Not a probability and not proof of identity.
    score: float
    pitch_gap_octaves: float
    # False when the pitch alone ruled this person out (a much higher or lower voice).
    passed_pitch_gate: bool


class SignatureMatchOut(CamelModel):
    """Whom an uploaded voice sample is most like among people who have consented and enrolled a signature."""

    outcome: Literal["matched", "no_confident_match", "nobody_enrolled"]
    pitch: PitchOut
    voiced_seconds: float
    candidates_considered: int
    # Set only when the best match clears the threshold and beats the next person by the margin.
    match: SignatureCandidateOut | None
    # The closest few, best first. Listing a name here is NOT a match: only `match` is.
    closest: list[SignatureCandidateOut]
    notice: str
