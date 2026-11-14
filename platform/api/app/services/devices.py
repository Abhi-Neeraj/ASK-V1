"""Devices: creation, pairing, presence, and who may see or use which locket."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session

from app import timeutil
from app.config import Settings
from app.db import rows_affected
from app.errors import ApiError, not_found
from app.models import Device, DeviceConnection, DeviceCredential, DevicePairing, User
from app.schemas.devices import AssignedTo, DeviceConfig, DeviceOut
from app.security.deps import Ctx, DeviceCtx
from app.security.tokens import hash_pairing_code, hash_token, new_pairing_code, new_token
from app.services.audit import publish_event, record_audit, record_device_event

_ID_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_public_id(db: Session) -> str:
    while True:
        candidate = "ASK-" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
        if db.scalar(select(Device.id).where(Device.public_id == candidate)) is None:
            return candidate


def presence(device: Device, settings: Settings, now: datetime) -> str:
    """The status shown to people. `online` means an authenticated heartbeat arrived recently."""
    if device.status == "revoked":
        return "revoked"
    if device.status == "pending":
        return "pending"
    if device.last_heartbeat_at is not None and now - device.last_heartbeat_at <= timedelta(
        seconds=settings.heartbeat_expiry_seconds
    ):
        return "online"
    return "offline"


def device_config(settings: Settings) -> DeviceConfig:
    return DeviceConfig(
        chunk_seconds=settings.chunk_seconds,
        heartbeat_interval_seconds=max(settings.heartbeat_expiry_seconds // 3, 5),
        sample_rates=list(settings.allowed_sample_rates),
        max_upload_bytes=settings.max_upload_bytes,
        max_clip_seconds=settings.max_clip_seconds,
    )


# --- who can see what ---------------------------------------------------------------------------


def visible_devices(ctx: Ctx) -> Select[Device]:
    """Managers see every device in their company. Employees see only locket assigned to them.
    Nothing outside the caller's company is ever selectable from here."""
    query = select(Device).where(Device.company_id == ctx.company_id)
    if not ctx.can_manage:
        query = query.where(Device.assigned_user_id == ctx.user.id, Device.status != "revoked")
    return query


def get_visible_device(db: Session, ctx: Ctx, device_id: uuid.UUID) -> Device:
    device = db.scalar(visible_devices(ctx).where(Device.id == device_id))
    if device is None:
        raise not_found("device")  # same answer for "missing" and "someone else's"
    return device


def device_out(db: Session, ctx: Ctx, device: Device, settings: Settings, now: datetime) -> DeviceOut:
    connected = (
        db.scalar(
            select(DeviceConnection.id).where(
                DeviceConnection.device_id == device.id, DeviceConnection.user_id == ctx.user.id
            )
        )
        is not None
    )
    assigned = db.get(User, device.assigned_user_id) if device.assigned_user_id else None
    return DeviceOut(
        id=device.id,
        device_id=device.public_id,
        name=device.name,
        status=presence(device, settings, now),
        simulated=device.is_simulated,
        connected=connected,
        last_heartbeat_at=device.last_heartbeat_at,
        last_upload_at=device.last_upload_at,
        firmware_version=device.firmware_version,
        battery_percent=device.battery_percent,
        upload_queue_depth=device.upload_queue_depth,
        assigned_to=AssignedTo(id=assigned.id, name=assigned.name) if assigned else None,
    )


# --- creating and pairing ----------------------------------------------------------------------


def create_pairing(db: Session, settings: Settings, ctx: Ctx, device: Device) -> tuple[DevicePairing, str]:
    """Make a fresh single-use code for a device that is not paired yet. Older codes stop working."""
    now = timeutil.utcnow()
    for old in db.scalars(
        select(DevicePairing).where(DevicePairing.device_id == device.id, DevicePairing.redeemed_at.is_(None))
    ):
        old.redeemed_at = now  # retire it
    code = new_pairing_code()
    pairing = DevicePairing(
        device_id=device.id,
        code_hash=hash_pairing_code(code, settings.secret_key.get_secret_value()),
        expires_at=now + timedelta(minutes=settings.pairing_ttl_minutes),
        created_by=ctx.user.id,
    )
    db.add(pairing)
    db.flush()
    return pairing, code


def create_device(db: Session, settings: Settings, ctx: Ctx, name: str) -> tuple[Device, DevicePairing, str]:
    device = Device(
        company_id=ctx.company_id, public_id=new_public_id(db), name=name.strip(), status="pending"
    )
    db.add(device)
    db.flush()
    pairing, code = create_pairing(db, settings, ctx, device)
    record_device_event(db, device, "created", {"by": str(ctx.user.id)})
    record_audit(
        db,
        action="device.created",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="device",
        object_id=device.id,
        ip=ctx.ip,
    )
    return device, pairing, code


def redeem_pairing(
    db: Session,
    settings: Settings,
    code: str,
    firmware_version: str | None,
    ip: str | None,
    *,
    simulated: bool = False,
) -> tuple[Device, str]:
    """Turn a valid one-time code into a device credential. Nothing else makes a device trusted."""
    if simulated and not settings.simulated_devices_allowed:
        # Checked before the code is touched, so a refused simulator does not burn a real pairing code.
        raise ApiError(
            422,
            "simulation_not_allowed",
            "This server only accepts real lockets. Simulated lockets are for development.",
        )
    now = timeutil.utcnow()
    pairing = db.scalar(
        select(DevicePairing).where(
            DevicePairing.code_hash == hash_pairing_code(code, settings.secret_key.get_secret_value())
        )
    )
    if pairing is None or pairing.redeemed_at is not None or pairing.expires_at <= now:
        raise ApiError(400, "invalid_pairing_code", "The pairing code is not valid or has expired.")
    device = db.get(Device, pairing.device_id)
    if device is None or device.status != "pending":
        raise ApiError(400, "invalid_pairing_code", "The pairing code is not valid or has expired.")

    # Claim the code with one statement that only succeeds while it is unused: two requests that arrive
    # together
    # must not each get a credential from the same code.
    claimed = rows_affected(
        db.execute(
            update(DevicePairing)
            .where(DevicePairing.id == pairing.id, DevicePairing.redeemed_at.is_(None))
            .values(redeemed_at=now)
        )
    )
    if claimed != 1:
        raise ApiError(400, "invalid_pairing_code", "The pairing code is not valid or has expired.")
    secret = new_token()
    db.add(DeviceCredential(device_id=device.id, secret_hash=hash_token(secret)))
    device.status = "active"
    device.paired_at = now
    device.is_simulated = simulated
    device.firmware_version = firmware_version or device.firmware_version
    record_device_event(db, device, "paired", {"firmware": firmware_version, "simulated": simulated})
    record_audit(
        db,
        action="device.paired",
        company_id=device.company_id,
        actor_device_id=device.id,
        object_type="device",
        object_id=device.id,
        ip=ip,
    )
    publish_event(db, device.company_id, "device.paired", {"deviceId": str(device.id), "name": device.name})
    return device, f"{device.public_id}.{secret}"


def revoke_device(db: Session, ctx: Ctx, device: Device) -> None:
    if device.status == "revoked":
        return
    now = timeutil.utcnow()
    device.status = "revoked"
    device.revoked_at = now
    device.presence = "offline"
    for credential in db.scalars(
        select(DeviceCredential).where(
            DeviceCredential.device_id == device.id, DeviceCredential.revoked_at.is_(None)
        )
    ):
        credential.revoked_at = now
    for connection in db.scalars(select(DeviceConnection).where(DeviceConnection.device_id == device.id)):
        db.delete(connection)
    for pairing in db.scalars(
        select(DevicePairing).where(DevicePairing.device_id == device.id, DevicePairing.redeemed_at.is_(None))
    ):
        pairing.redeemed_at = now
    record_device_event(db, device, "revoked", {"by": str(ctx.user.id)})
    record_audit(
        db,
        action="device.revoked",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="device",
        object_id=device.id,
        ip=ctx.ip,
    )
    publish_event(db, device.company_id, "device.revoked", {"deviceId": str(device.id)})


# --- heartbeats and presence --------------------------------------------------------------------


def record_heartbeat(
    db: Session,
    ctx: DeviceCtx,
    *,
    firmware_version: str | None,
    battery_percent: int | None,
    upload_queue_depth: int | None,
) -> None:
    device = ctx.device
    now = timeutil.utcnow()
    device.last_heartbeat_at = now
    # Values the device does not send stay unknown rather than being invented.
    device.firmware_version = firmware_version or device.firmware_version
    device.battery_percent = battery_percent
    device.upload_queue_depth = upload_queue_depth
    if device.presence != "online":
        device.presence = "online"
        record_device_event(db, device, "online")
        publish_event(
            db, device.company_id, "device.online", {"deviceId": str(device.id), "name": device.name}
        )


def expire_stale_presence(db: Session, settings: Settings, now: datetime | None = None) -> int:
    """Mark devices whose heartbeats stopped as offline, announcing each change exactly once."""
    now = now or timeutil.utcnow()
    cutoff = now - timedelta(seconds=settings.heartbeat_expiry_seconds)
    stale = db.scalars(
        select(Device).where(
            Device.status == "active",
            Device.presence == "online",
            (Device.last_heartbeat_at.is_(None)) | (Device.last_heartbeat_at < cutoff),
        )
    ).all()
    for device in stale:
        device.presence = "offline"
        record_device_event(db, device, "offline")
        publish_event(
            db, device.company_id, "device.offline", {"deviceId": str(device.id), "name": device.name}
        )
    if stale:
        db.commit()
    return len(stale)
