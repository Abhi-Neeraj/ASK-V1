"""Audit trail and the live event feed. Both are written in the caller's transaction, so an event
exists if and only if the change it describes was committed."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent, Device, DeviceEvent, LiveEvent, Recording


def record_audit(
    db: Session,
    *,
    action: str,
    company_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None = None,
    actor_device_id: uuid.UUID | None = None,
    object_type: str | None = None,
    object_id: object | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AuditEvent:
    """`detail` must never contain secrets, tokens, raw audio paths or voice embeddings."""
    event = AuditEvent(
        company_id=company_id,
        actor_user_id=actor_user_id,
        actor_device_id=actor_device_id,
        action=action,
        object_type=object_type,
        object_id=str(object_id) if object_id is not None else None,
        detail=detail,
        ip=ip,
    )
    db.add(event)
    return event


def recording_event(recording: Recording, **extra: Any) -> dict[str, Any]:
    """What a live event about a recording says. `wornBy` is who the recording belongs to, so the feed uses
    the same
    rule as the library: an employee hears about their own recordings, not about whichever locket they hold
    now."""
    return {
        "recordingId": str(recording.id),
        "deviceId": str(recording.device_id),
        "wornBy": str(recording.assigned_user_id) if recording.assigned_user_id else None,
        **extra,
    }


def publish_event(db: Session, company_id: uuid.UUID, type_: str, payload: dict[str, Any]) -> LiveEvent:
    event = LiveEvent(company_id=company_id, type=type_, payload=payload)
    db.add(event)
    return event


def record_device_event(
    db: Session, device: Device, type_: str, detail: dict[str, Any] | None = None
) -> None:
    db.add(DeviceEvent(company_id=device.company_id, device_id=device.id, type=type_, detail=detail))
