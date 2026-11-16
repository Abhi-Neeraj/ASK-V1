from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import JSONType, created_at_column, uuid_pk


class AuditEvent(Base):
    """Who did what to which object. Never holds secrets, raw audio, or voice embeddings."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(60))
    object_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        Index("ix_audit_events_company_created", "company_id", "created_at"),
        Index("ix_audit_events_action", "action"),
    )


class LiveEvent(Base):
    """Append-only feed the browser follows (device online/offline, uploads, processing)."""

    __tablename__ = "live_events"

    # Monotonic so a reconnecting client can resume with Last-Event-ID. SQLite needs Integer to autoincrement.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (Index("ix_live_events_company_id", "company_id", "id"),)


class Worker(Base):
    """A processing worker that has reported in. How the dashboard knows work is actually being done,
    instead of promising it. Rows are only a heartbeat: nothing here is needed to process a job."""

    __tablename__ = "workers"

    id: Mapped[uuid.UUID] = uuid_pk()
    # Unique per process start (host, process id, random suffix), so a restart is a new row.
    worker_id: Mapped[str] = mapped_column(String(80), unique=True)
    provider: Mapped[str] = mapped_column(String(16))
    # The worker produces placeholder output rather than real transcripts.
    placeholder: Mapped[bool] = mapped_column(Boolean, default=False)
    # What this worker can and cannot do (transcription, speaker separation, speaker matching).
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONType)
    started_at: Mapped[datetime] = created_at_column()
    last_seen_at: Mapped[datetime] = created_at_column()

    __table_args__ = (Index("ix_workers_last_seen", "last_seen_at"),)


class ContactMessage(Base):
    """Messages from the public contact form."""

    __tablename__ = "contact_messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254))
    company: Mapped[str | None] = mapped_column(String(160), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    # Only a hash of the sender address, for abuse analysis without storing the address.
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = created_at_column()
