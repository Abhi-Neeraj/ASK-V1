"""Reading the audit trail back. Writing it is `services.audit`; this module only ever reads, and only
for the company of the person asking."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Device, Membership, User
from app.schemas.audit import AuditActorOut, AuditEventOut
from app.security.deps import Ctx

# The categories that exist today: the part of an action name before its first dot.
CATEGORIES = (
    "auth",
    "company",
    "device",
    "invitation",
    "member",
    "person",
    "recording",
    "settings",
    "transcript",
)

# Writers are meant to keep these out of `detail`. If one ever slips, the viewer still does not show it.
_HIDDEN_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "credential",
    "embedding",
    "authorization",
    "cookie",
    "path",
    "storage",
    "hash",
)
_MAX_TEXT = 200
_MAX_ITEMS = 20
_MAX_DEPTH = 3


def scrub(value: Any, depth: int = 0) -> Any:
    """A copy of `value` that is safe to show: no sensitive-looking keys, bounded in size and depth."""
    if isinstance(value, dict):
        if depth >= _MAX_DEPTH:
            return "…"
        return {
            str(key): scrub(item, depth + 1)
            for key, item in list(value.items())[:_MAX_ITEMS]
            if not any(part in str(key).lower() for part in _HIDDEN_KEY_PARTS)
        }
    if isinstance(value, list | tuple):
        if depth >= _MAX_DEPTH:
            return "…"
        return [scrub(item, depth + 1) for item in list(value)[:_MAX_ITEMS]]
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT else value[:_MAX_TEXT] + "…"
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:_MAX_TEXT]


@dataclass(frozen=True)
class AuditFilters:
    category: str | None = None
    action: str | None = None
    actor_user_id: uuid.UUID | None = None
    object_type: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    page: int = 1
    page_size: int = 25


def search(db: Session, ctx: Ctx, filters: AuditFilters) -> tuple[list[AuditEvent], int]:
    # The company comes from the signed-in session, never from the request.
    conditions = [AuditEvent.company_id == ctx.company_id]
    if filters.category:
        conditions.append(AuditEvent.action.like(f"{filters.category}.%"))
    if filters.action:
        conditions.append(AuditEvent.action == filters.action)
    if filters.actor_user_id:
        conditions.append(AuditEvent.actor_user_id == filters.actor_user_id)
    if filters.object_type:
        conditions.append(AuditEvent.object_type == filters.object_type)
    if filters.date_from:
        conditions.append(AuditEvent.created_at >= filters.date_from)
    if filters.date_to:
        conditions.append(AuditEvent.created_at <= filters.date_to)

    total = db.scalar(select(func.count()).select_from(AuditEvent).where(*conditions)) or 0
    rows = db.scalars(
        select(AuditEvent)
        .where(*conditions)
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .offset((filters.page - 1) * filters.page_size)
        .limit(filters.page_size)
    ).all()
    return list(rows), total


def audit_outs(db: Session, ctx: Ctx, rows: list[AuditEvent]) -> list[AuditEventOut]:
    """Names for the actors. Looked up through this company's own members and lockets only, so an
    account that belongs to another company is never named here."""
    user_ids = {r.actor_user_id for r in rows if r.actor_user_id}
    device_ids = {r.actor_device_id for r in rows if r.actor_device_id}

    users: dict[uuid.UUID, AuditActorOut] = {}
    if user_ids:
        for user in db.scalars(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.company_id == ctx.company_id, User.id.in_(user_ids))
        ):
            users[user.id] = AuditActorOut(kind="user", id=user.id, name=user.name, email=user.email)

    devices: dict[uuid.UUID, AuditActorOut] = {}
    if device_ids:
        for device in db.scalars(
            select(Device).where(Device.company_id == ctx.company_id, Device.id.in_(device_ids))
        ):
            devices[device.id] = AuditActorOut(kind="device", id=device.id, name=device.name)

    outs = []
    for row in rows:
        actor = users.get(row.actor_user_id) if row.actor_user_id else None
        if actor is None and row.actor_device_id:
            actor = devices.get(row.actor_device_id)
        outs.append(
            AuditEventOut(
                id=row.id,
                at=row.created_at,
                action=row.action,
                category=row.action.split(".", 1)[0],
                actor=actor,
                object_type=row.object_type,
                object_id=row.object_id,
                detail=scrub(row.detail) if row.detail else None,
                ip=row.ip,
            )
        )
    return outs
