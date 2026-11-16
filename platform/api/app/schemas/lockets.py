from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.common import CamelModel


class LocketCreateIn(CamelModel):
    name: str = Field(min_length=1, max_length=120)


class LocketRegisteredOut(CamelModel):
    """Returned once, at registration. `secret_hex` goes into the locket over USB; it is never shown again."""

    product_id: str
    name: str
    secret_hex: str


class LocketMemberOut(CamelModel):
    id: uuid.UUID
    name: str
    email: str


class LocketOut(CamelModel):
    product_id: str
    name: str
    max_users: int
    members: list[LocketMemberOut]
    firmware_version: str | None
    battery_percent: int | None
    last_seen_at: datetime | None


class NonceIn(CamelModel):
    nonce: str = Field(max_length=64)


class PairedIn(NonceIn):
    # The locket's proof that this pairing happened (bond_proof in app.security.locket_codes).
    proof: str = Field(min_length=64, max_length=64)


class CodeSentOut(CamelModel):
    sent_to: str
    expires_at: datetime


class LimitIn(CamelModel):
    max_users: int = Field(ge=1, le=50)
