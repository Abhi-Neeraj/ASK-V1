from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import JSONType, UTCDateTime, created_at_column, uuid_pk

PROCESSING_STATES = (
    "uploaded",
    "queued",
    "validating",
    "transcribing",
    "diarizing",
    "identifying_speakers",
    "aligning",
    "completed",
    "failed",
    "retrying",
    "needs_review",
)
_STATE_LIST = ",".join(f"'{s}'" for s in PROCESSING_STATES)


class Recording(Base):
    """One uploaded audio clip. The audio itself lives in file storage, never in the database."""

    __tablename__ = "recordings"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    # The ID the device gave the clip. With the device it forms the idempotency key for uploads.
    client_recording_id: Mapped[str] = mapped_column(String(64))
    # Several files can belong to one longer recording: shared session reference plus a sequence.
    session_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)

    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime] = mapped_column(UTCDateTime)
    duration_ms: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    sample_rate: Mapped[int] = mapped_column(Integer)
    channels: Mapped[int] = mapped_column(Integer)
    bits_per_sample: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(40))
    # Path relative to the storage root. Never returned by the API.
    storage_key: Mapped[str] = mapped_column(String(300))
    uploaded_at: Mapped[datetime] = created_at_column()

    state: Mapped[str] = mapped_column(String(24), default="queued")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Who was assigned the locket when this clip arrived. An employee sees a recording because it is theirs,
    # not because its locket is theirs now: reassigning a locket must not hand its history to the next wearer.
    # Null when the locket was unassigned on arrival (then only managers see it). Set once, never rewritten.
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Soft delete. The audio and transcript are purged for good after `purge_after`.
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    purge_after: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("device_id", "client_recording_id", name="uq_recordings_device_client_id"),
        CheckConstraint(f"state IN ({_STATE_LIST})", name="state_valid"),
        CheckConstraint("duration_ms > 0", name="duration_positive"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        Index("ix_recordings_company_started", "company_id", "started_at"),
        Index("ix_recordings_company_state", "company_id", "state"),
        Index("ix_recordings_device", "device_id"),
        Index("ix_recordings_assigned_user", "assigned_user_id"),
        Index("ix_recordings_purge", "purge_after"),
    )


class RecordingAccess(Base):
    """Who may see a recording besides managers: the people on its locket when it arrived.

    Written once, when the recording is stored; joining a locket later does not open its history, and
    leaving it does not close it."""

    __tablename__ = "recording_access"

    recording_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)

    __table_args__ = (Index("ix_recording_access_user", "user_id"),)


class ProcessingJob(Base):
    """The durable work queue. A worker claims one row at a time; retries are bounded."""

    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = uuid_pk()
    recording_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), unique=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(12), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = created_at_column()
    locked_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = created_at_column()
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('queued','running','done','failed')", name="status_valid"),
        Index("ix_processing_jobs_claim", "status", "run_after"),
    )


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = uuid_pk()
    recording_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), unique=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    full_text: Mapped[str] = mapped_column(Text, default="")
    transcription_model: Mapped[str] = mapped_column(String(80))
    transcription_model_version: Mapped[str] = mapped_column(String(80))
    diarization_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    diarization_model_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Written by the development stub, not by a speech model. The text is not what was said, and
    # every screen and export says so.
    is_placeholder: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # A written summary of the transcript, by the provider's summarizer (Gemini for `google`). None until
    # made, and for placeholder transcripts.
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    review_status: Mapped[str] = mapped_column(String(16), default="none")
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint("review_status IN ('none','needs_review','reviewed')", name="review_valid"),
        Index("ix_transcripts_company", "company_id"),
    )


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[uuid.UUID] = uuid_pk()
    transcript_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transcripts.id", ondelete="CASCADE"))
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    # Diarization label such as SPEAKER_00. It says "same voice", not "who".
    speaker_label: Mapped[str] = mapped_column(String(40))
    # Set only when a voice match cleared the calibrated threshold. Never from transcript text.
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("people.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    needs_review: Mapped[bool] = mapped_column(default=False)
    corrected: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (
        UniqueConstraint("transcript_id", "idx", name="uq_transcript_segments_transcript_idx"),
        CheckConstraint("end_ms >= start_ms", name="span_valid"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"
        ),
        Index("ix_transcript_segments_company", "company_id"),
        Index("ix_transcript_segments_person", "person_id"),
    )


class RecordingAudio(Base):
    """A compressed copy of a recording's audio kept in the database, so playback survives a server with no
    permanent disk (a free host). IMA ADPCM, four bits a sample: a quarter of the WAV's size. See
    app/services/audio_archive.py."""

    __tablename__ = "recording_audio"

    recording_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recordings.id", ondelete="CASCADE"), primary_key=True
    )
    sample_rate: Mapped[int] = mapped_column(Integer)
    pcm_bytes: Mapped[int] = mapped_column(Integer)  # the WAV's sample data, decoded
    stored_bytes: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = created_at_column()


class RecordingAnalysis(Base):
    """What was said, worked out from a transcript by fixed rules (no generative AI): the voices (pitch and
    talk
    time per speaker) and, per sentence, what it was, its context, the activity, the task and who owns it,
    plus
    the summary built from those. It belongs to one transcript and goes with it when the recording is
    processed
    again. It names speakers only by label: who a label is comes from the transcript's own segments when it is
    read, so withdrawing consent also takes the names out of the analysis."""

    __tablename__ = "recording_analyses"

    id: Mapped[uuid.UUID] = uuid_pk()
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transcripts.id", ondelete="CASCADE"), unique=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    # How sentences were matched to activities: "semantic" (a sentence-embedding model) or "lexical" (words).
    engine: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint("engine IN ('semantic','lexical')", name="engine_valid"),
        Index("ix_recording_analyses_company", "company_id"),
    )
