from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Dialect, TypeDecorator, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import mapped_column

from app import timeutil


class UTCDateTime(TypeDecorator[datetime]):
    """Always timezone-aware UTC in Python, whatever the database returns.

    SQLite drops timezone info, PostgreSQL keeps it. Rejecting naive values on the way in and
    re-attaching UTC on the way out makes both behave the same.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: use timezone-aware UTC values")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# JSON everywhere, binary JSON (JSONB) on PostgreSQL for indexing and speed.
JSONType = JSON().with_variant(JSONB(), "postgresql")


def uuid_pk() -> Any:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _now() -> datetime:
    # Looked up on every call (not captured once) so tests can control time.
    return timeutil.utcnow()


def created_at_column() -> Any:
    return mapped_column(UTCDateTime, nullable=False, default=_now)
