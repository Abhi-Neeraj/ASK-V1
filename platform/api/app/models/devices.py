from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    false,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.base import NEVER_SET, NO_VALUE

from app.db import Base
from app.models.base import JSONType, UTCDateTime, created_at_column, uuid_pk

DEVICE_STATUSES = ("pending", "active", "revoked")


class Device(Base):
    """A registered locket. `public_id` is printed on the device and used by firmware."""

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    public_id: Mapped[str] = mapped_column(String(24), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    # pending: created, pairing code not redeemed. active: paired. revoked: credential invalidated.
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # The earliest of the people on this locket (`device_members`), kept in step by app.services.lockets so
    # older code that reads a single holder keeps working. Membership itself lives in `device_members`.
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # How the locket talks: `wifi` (the older lockets that upload to the server themselves) or `bluetooth`
    # (registered with a product ID, paired to phones with an emailed code; see app.services.lockets).
    transport: Mapped[str] = mapped_column(String(12), default="wifi", server_default="wifi")
    # How many people may be on this locket at once. 1 unless an administrator raises it.
    max_users: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    firmware_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # True for a development stand-in (tools/simulate_device.py), never for real hardware. Chosen
    # when the device pairs, and refused outright by servers that do not allow simulated lockets.
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Null unless the device really reports them.
    battery_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upload_queue_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_upload_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Last presence we announced, so online/offline transitions are emitted exactly once.
    presence: Mapped[str] = mapped_column(String(8), default="offline")
    paired_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint("status IN ('pending','active','revoked')", name="status_valid"),
        CheckConstraint("presence IN ('online','offline')", name="presence_valid"),
        CheckConstraint(
            "battery_percent IS NULL OR (battery_percent >= 0 AND battery_percent <= 100)",
            name="battery_range",
        ),
        CheckConstraint("max_users >= 1 AND max_users <= 50", name="max_users_range"),
        CheckConstraint("transport IN ('wifi','bluetooth')", name="transport_valid"),
        Index("ix_devices_company", "company_id"),
        Index("ix_devices_assigned_user", "assigned_user_id"),
    )


class DeviceMember(Base):
    """A person allowed on a locket: added when they pair with an emailed code (or by a manager)."""

    __tablename__ = "device_members"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    added_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        UniqueConstraint("device_id", "user_id", name="uq_device_members_device_user"),
        Index("ix_device_members_user", "user_id"),
    )


class LocketPairingRequest(Base):
    """An emailed pairing code waiting to be used. Only a hash of the locket's nonce is kept, not the code."""

    __tablename__ = "locket_pairing_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    nonce_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (Index("ix_locket_pairing_requests_device_user", "device_id", "user_id"),)


class FirmwareRelease(Base):
    """A firmware build lockets can install over Wi-Fi. Not company-owned: every locket runs the same code."""

    __tablename__ = "firmware_releases"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[str] = mapped_column(String(40), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    # Path relative to the storage root. Never returned by the API.
    storage_key: Mapped[str] = mapped_column(String(300))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()


class PermanentProductIdError(ValueError):
    """Raised when code tries to change a saved locket's product ID. It is printed on the locket and written
    into its firmware at manufacture, so it never changes (migration 0006 also refuses it in the database)."""


@event.listens_for(Device.public_id, "set", active_history=True)
def _product_id_is_permanent(target: Device, value: str, oldvalue: object, _initiator: object) -> None:
    if oldvalue in (NO_VALUE, NEVER_SET, None) or oldvalue == value:
        return
    if inspect(target).persistent or inspect(target).detached:
        raise PermanentProductIdError(f"product ID {oldvalue!r} is permanent")


class DeviceCredential(Base):
    """The per-device secret. Only its SHA-256 is stored (the secret is high entropy, not a password)."""

    __tablename__ = "device_credentials"

    id: Mapped[uuid.UUID] = uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    secret_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    __table_args__ = (Index("ix_device_credentials_device", "device_id"),)


class DevicePairing(Base):
    """A short-lived, single-use code an administrator creates so a new locket can become trusted."""

    __tablename__ = "device_pairings"

    id: Mapped[uuid.UUID] = uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    redeemed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = created_at_column()


class DeviceConnection(Base):
    """An application-level connection a user made to a device (the Connect button)."""

    __tablename__ = "device_connections"

    id: Mapped[uuid.UUID] = uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    connected_at: Mapped[datetime] = created_at_column()

    __table_args__ = (UniqueConstraint("device_id", "user_id", name="uq_device_connections_device_user"),)


class DeviceEvent(Base):
    """Per-device history: paired, online, offline, revoked, assigned, upload rejected."""

    __tablename__ = "device_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(40))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (Index("ix_device_events_device_created", "device_id", "created_at"),)
