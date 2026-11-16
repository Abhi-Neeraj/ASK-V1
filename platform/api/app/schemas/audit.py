from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.common import CamelModel


class AuditActorOut(CamelModel):
    """Who did it. `user` is a person in this company; `device` is a locket."""

    kind: Literal["user", "device"]
    id: uuid.UUID
    name: str
    email: str | None = None


class AuditEventOut(CamelModel):
    id: uuid.UUID
    at: datetime
    action: str
    # The part of `action` before the first dot ("auth", "device", ...), for grouping and filtering.
    category: str
    # Null when the action had no signed-in actor (an operator command or housekeeping) or when the
    # actor's account has since been removed.
    actor: AuditActorOut | None = None
    object_type: str | None = None
    object_id: str | None = None
    detail: dict[str, Any] | None = Field(default=None, description="Counts and flags only, never content.")
    ip: str | None = None
