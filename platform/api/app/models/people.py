from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import JSONType, UTCDateTime, created_at_column, uuid_pk


class Person(Base):
    """Someone whose voice may be recognized. Created before any voice data exists."""

    __tablename__ = "people"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    display_name: Mapped[str] = mapped_column(String(120))
    consent_status: Mapped[str] = mapped_column(String(10), default="pending")
    consent_recorded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint("consent_status IN ('pending','granted','revoked')", name="consent_valid"),
        Index("ix_people_company", "company_id"),
    )


class VoiceProfile(Base):
    """A person's voice embedding. Sensitive: never logged, never returned by the API."""

    __tablename__ = "voice_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), unique=True)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    embedding_dim: Mapped[int] = mapped_column(Integer)
    model_name: Mapped[str] = mapped_column(String(80))
    model_version: Mapped[str] = mapped_column(String(80))
    sample_count: Mapped[int] = mapped_column(Integer)
    enrolled_at: Mapped[datetime] = created_at_column()

    __table_args__ = (Index("ix_voice_profiles_company", "company_id"),)


class PersonSignature(Base):
    """A person's voice signature: how high their voice sits (pitch) and its colour (timbre). Sensitive, like
    a voice print: the timbre is never logged and never returned. Only the pitch summary is shown, and only
    to managers. Deleted with the person's consent, like the voice profile."""

    __tablename__ = "person_signatures"

    id: Mapped[uuid.UUID] = uuid_pk()
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), unique=True)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    timbre: Mapped[bytes] = mapped_column(LargeBinary)
    # Which version of the measurement made it. A signature is only compared with ones of the same version.
    version: Mapped[str] = mapped_column(String(24))
    pitch_base_hz: Mapped[float] = mapped_column(Float)
    pitch_low_hz: Mapped[float] = mapped_column(Float)
    pitch_high_hz: Mapped[float] = mapped_column(Float)
    voiced_seconds: Mapped[float] = mapped_column(Float)
    sample_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint("sample_count >= 1", name="samples_positive"),
        Index("ix_person_signatures_company", "company_id"),
    )


class ConsentRecord(Base):
    """An append-only record of consent being given or withdrawn, and who recorded it."""

    __tablename__ = "consent_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(10))
    statement: Mapped[str] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    recorded_at: Mapped[datetime] = created_at_column()
    extra: Mapped[dict[str, object] | None] = mapped_column(JSONType, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('granted','revoked')", name="status_valid"),
        Index("ix_consent_records_person", "person_id"),
    )
