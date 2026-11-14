from __future__ import annotations

import uuid
from datetime import datetime

from app.schemas.common import CamelModel


class UploadAck(CamelModel):
    """Sent only after the audio and its record are both durable. The locket may now archive its copy."""

    recording_id: uuid.UUID
    client_recording_id: str
    status: str  # always "stored"
    sha256: str
    bytes: int
    duration_ms: int
    duplicate: bool
    started_at: datetime
