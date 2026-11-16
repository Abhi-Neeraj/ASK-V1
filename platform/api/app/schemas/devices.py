from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.schemas.common import CamelModel, CleanLine

FIRMWARE_PATTERN = r"^[A-Za-z0-9._+\-]{1,40}$"


class AssignedTo(CamelModel):
    id: uuid.UUID
    name: str


class DeviceOut(CamelModel):
    id: uuid.UUID
    device_id: str  # the public id printed on the locket
    name: str
    status: str  # pending | online | offline | revoked
    # A development stand-in, not real hardware. Never true on a server that does not allow it.
    simulated: bool
    connected: bool
    last_heartbeat_at: datetime | None
    last_upload_at: datetime | None
    firmware_version: str | None
    battery_percent: int | None
    upload_queue_depth: int | None
    assigned_to: AssignedTo | None


class DeviceCreateIn(CamelModel):
    name: CleanLine = Field(min_length=2, max_length=120)


class DevicePatchIn(CamelModel):
    name: CleanLine | None = Field(default=None, min_length=2, max_length=120)
    # Present with null means "unassign"; absent means "leave as is".
    assigned_user_id: uuid.UUID | None = None


class PairingCodeOut(CamelModel):
    id: uuid.UUID
    device_id: str
    device_name: str
    code: str
    expires_at: datetime


class DeviceEventOut(CamelModel):
    id: uuid.UUID
    type: str
    detail: dict[str, Any] | None
    created_at: datetime


class DeviceConfig(CamelModel):
    """Settings the locket should follow. Returned when it pairs and on every heartbeat."""

    chunk_seconds: int
    heartbeat_interval_seconds: int
    sample_rates: list[int]
    max_upload_bytes: int
    # The longest single clip the server accepts. A locket that records for longer saves the recording as
    # several clips (X-Session-Ref and X-Sequence tie them together), each no longer than this.
    max_clip_seconds: int


class DevicePairIn(CamelModel):
    code: str = Field(min_length=6, max_length=24)
    firmware_version: str | None = Field(default=None, pattern=FIRMWARE_PATTERN)
    # Only tools/simulate_device.py sends this. Real firmware leaves it out.
    simulated: bool = False


class DevicePairOut(CamelModel):
    device_id: str
    # Shown once. The locket stores it and sends it as `Authorization: Bearer <credential>`.
    credential: str
    config: DeviceConfig


class HeartbeatIn(CamelModel):
    firmware_version: str | None = Field(default=None, pattern=FIRMWARE_PATTERN)
    battery_percent: int | None = Field(default=None, ge=0, le=100)
    upload_queue_depth: int | None = Field(default=None, ge=0, le=100000)


class HeartbeatOut(CamelModel):
    server_time: datetime
    config: DeviceConfig
