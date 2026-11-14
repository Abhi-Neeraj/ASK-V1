"""The live event feed behind GET /api/events: what a signed-in person may be told, and how.

Events are rows in `live_events`, written in the same transaction as the change they describe, so an
event exists only if the change was committed. A stream follows that table in order, which also lets
a browser that dropped its connection resume from the last event it saw (`Last-Event-ID`) and lets
the API and a separate worker process share one feed without any extra infrastructure.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import timeutil
from app.config import Settings
from app.db import rows_affected
from app.models import AuthSession, Device, LiveEvent, Membership, User

BATCH_SIZE = 100
# Event IDs are 64-bit database counters. Anything outside this range is not an ID we issued.
_MAX_ID = 2**63 - 1


@dataclass(frozen=True)
class Viewer:
    """Who is watching. Captured once when the stream opens, re-checked while it runs."""

    user_id: uuid.UUID
    company_id: uuid.UUID
    can_manage: bool
    session_id: uuid.UUID


@dataclass(frozen=True)
class FeedEvent:
    id: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


def parse_event_id(raw: str | None) -> int | None:
    """Read a `Last-Event-ID` the client sent back. Garbage is ignored, never an error."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw.isascii() or not raw.isdigit() or len(raw) > 19:
        return None
    value = int(raw)
    return value if value <= _MAX_ID else None


def latest_event_id(db: Session, company_id: uuid.UUID) -> int:
    return db.scalar(select(func.max(LiveEvent.id)).where(LiveEvent.company_id == company_id)) or 0


def session_is_open(db: Session, settings: Settings, viewer: Viewer) -> bool:
    """False once the sign-in ended: logged out, revoked, expired, idle too long, or the person was
    deactivated or removed from the company. The stream closes instead of leaking events."""
    now = timeutil.utcnow()
    session = db.get(AuthSession, viewer.session_id)
    if (
        session is None
        or session.revoked_at is not None
        or session.expires_at <= now
        or now - session.last_seen_at > timedelta(hours=settings.session_idle_hours)
    ):
        return False
    user = db.get(User, viewer.user_id)
    if user is None or not user.is_active:
        return False
    return (
        db.scalar(
            select(Membership.id).where(
                Membership.user_id == viewer.user_id, Membership.company_id == viewer.company_id
            )
        )
        is not None
    )


def is_visible(event: FeedEvent, viewer: Viewer, assigned_devices: set[str]) -> bool:
    """Managers hear everything in their company. Everyone else hears only about their own lockets
    (and about being given or losing one), which is the same boundary the REST API enforces."""
    if viewer.can_manage:
        return True
    if event.type == "device.assigned":
        involved = (event.payload.get("from"), event.payload.get("to"))
        return str(viewer.user_id) in involved
    if event.type.startswith("recording.") and "wornBy" in event.payload:
        # News about a recording goes to whoever it belongs to, the same rule the library uses.
        return bool(event.payload["wornBy"] == str(viewer.user_id))
    # Everything else, and events written before recordings carried an owner (they are kept for a day),
    # follows
    # the locket's current assignee.
    device_id = event.payload.get("deviceId")
    return isinstance(device_id, str) and device_id in assigned_devices


def read_new_events(db: Session, viewer: Viewer, after_id: int) -> tuple[list[FeedEvent], int]:
    """The next batch of events this person may see, and the cursor to continue from.

    The cursor advances past events that were filtered out, so they are never read twice. The query is
    always scoped to the viewer's company: nothing here takes a company from the client.
    """
    rows = db.scalars(
        select(LiveEvent)
        .where(LiveEvent.company_id == viewer.company_id, LiveEvent.id > after_id)
        .order_by(LiveEvent.id)
        .limit(BATCH_SIZE)
    ).all()
    if not rows:
        return [], after_id

    assigned: set[str] = set()
    if not viewer.can_manage:
        assigned = {
            str(device_id)
            for device_id in db.scalars(
                select(Device.id).where(
                    Device.company_id == viewer.company_id, Device.assigned_user_id == viewer.user_id
                )
            )
        }
    events = [
        FeedEvent(id=row.id, type=row.type, payload=row.payload, created_at=row.created_at) for row in rows
    ]
    visible = [event for event in events if is_visible(event, viewer, assigned)]
    return visible, rows[-1].id


def format_event(event: FeedEvent) -> str:
    """One server-sent event. JSON is written on a single line, so a name containing a line break
    cannot end the event early or smuggle in a second one."""
    body = json.dumps(
        {"type": event.type, "at": event.created_at.isoformat(), "data": event.payload},
        separators=(",", ":"),
    )
    return f"id: {event.id}\nevent: {event.type}\ndata: {body}\n\n"


def prune_events(db: Session, settings: Settings, now: datetime | None = None) -> int:
    """Old events are of no use: a client that was away longer simply refreshes everything."""
    cutoff = (now or timeutil.utcnow()) - timedelta(hours=settings.live_event_retention_hours)
    removed = rows_affected(db.execute(delete(LiveEvent).where(LiveEvent.created_at < cutoff)))
    db.commit()
    return removed


class StreamRegistry:
    """Counts open streams so a few tabs cannot starve the server. Per process, like the rate limiter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._per_user: dict[uuid.UUID, int] = {}
        self._total = 0

    def acquire(self, user_id: uuid.UUID, *, per_user: int, total: int) -> bool:
        with self._lock:
            if self._total >= total or self._per_user.get(user_id, 0) >= per_user:
                return False
            self._per_user[user_id] = self._per_user.get(user_id, 0) + 1
            self._total += 1
            return True

    def release(self, user_id: uuid.UUID) -> None:
        with self._lock:
            remaining = self._per_user.get(user_id, 0) - 1
            if remaining > 0:
                self._per_user[user_id] = remaining
            else:
                self._per_user.pop(user_id, None)
            self._total = max(0, self._total - 1)

    @property
    def open_streams(self) -> int:
        with self._lock:
            return self._total
