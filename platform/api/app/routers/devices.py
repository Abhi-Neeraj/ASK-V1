from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import delete, select

from app import timeutil
from app.db import rows_affected
from app.errors import ApiError
from app.models import Device, DeviceConnection, DeviceEvent, Membership, User
from app.schemas.devices import (
    DeviceCreateIn,
    DeviceEventOut,
    DeviceOut,
    DevicePatchIn,
    PairingCodeOut,
)
from app.security.deps import Authed, DbDep, Manager, SettingsDep
from app.services import devices as svc
from app.services.audit import publish_event, record_audit, record_device_event

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("", response_model=list[DeviceOut])
def list_devices(ctx: Authed, db: DbDep, settings: SettingsDep) -> list[DeviceOut]:
    now = timeutil.utcnow()
    rows = db.scalars(svc.visible_devices(ctx).order_by(Device.created_at, Device.name)).all()
    return [svc.device_out(db, ctx, device, settings, now) for device in rows]


@router.post("", response_model=PairingCodeOut, status_code=201)
def create_device(body: DeviceCreateIn, ctx: Manager, db: DbDep, settings: SettingsDep) -> PairingCodeOut:
    device, pairing, code = svc.create_device(db, settings, ctx, body.name)
    db.commit()
    return PairingCodeOut(
        id=device.id,
        device_id=device.public_id,
        device_name=device.name,
        code=code,
        expires_at=pairing.expires_at,
    )


@router.get("/{device_id}", response_model=DeviceOut)
def get_device(device_id: uuid.UUID, ctx: Authed, db: DbDep, settings: SettingsDep) -> DeviceOut:
    device = svc.get_visible_device(db, ctx, device_id)
    return svc.device_out(db, ctx, device, settings, timeutil.utcnow())


@router.get("/{device_id}/status", response_model=DeviceOut)
def get_device_status(device_id: uuid.UUID, ctx: Authed, db: DbDep, settings: SettingsDep) -> DeviceOut:
    return get_device(device_id, ctx, db, settings)


@router.patch("/{device_id}", response_model=DeviceOut)
def update_device(
    device_id: uuid.UUID, body: DevicePatchIn, ctx: Manager, db: DbDep, settings: SettingsDep
) -> DeviceOut:
    device = svc.get_visible_device(db, ctx, device_id)
    fields = body.model_fields_set
    if "name" in fields and body.name is not None:
        device.name = body.name.strip()
    if "assigned_user_id" in fields:
        new_owner = body.assigned_user_id
        if new_owner is not None:
            # The person must belong to the same company: assignment can never cross tenants.
            member = db.scalar(
                select(Membership).where(
                    Membership.user_id == new_owner, Membership.company_id == ctx.company_id
                )
            )
            owner = db.get(User, new_owner) if member is not None else None
            if member is None or owner is None or not owner.is_active:
                # Someone who has been switched off cannot be handed a locket either.
                raise ApiError(
                    422,
                    "validation_error",
                    "That person is not in your company.",
                    {"assignedUserId": "That person is not in your company."},
                )
        previous = device.assigned_user_id
        device.assigned_user_id = new_owner
        if previous != new_owner:
            # Anyone who loses access also loses their connection to the device.
            if previous is not None:
                db.execute(
                    delete(DeviceConnection).where(
                        DeviceConnection.device_id == device.id, DeviceConnection.user_id == previous
                    )
                )
            record_device_event(
                db,
                device,
                "assigned",
                {"from": str(previous) if previous else None, "to": str(new_owner) if new_owner else None},
            )
            record_audit(
                db,
                action="device.assigned",
                company_id=ctx.company_id,
                actor_user_id=ctx.user.id,
                object_type="device",
                object_id=device.id,
                detail={"to": str(new_owner) if new_owner else None},
                ip=ctx.ip,
            )
            # Both people are told: one gains the locket, the other loses it.
            publish_event(
                db,
                ctx.company_id,
                "device.assigned",
                {
                    "deviceId": str(device.id),
                    "name": device.name,
                    "from": str(previous) if previous else None,
                    "to": str(new_owner) if new_owner else None,
                },
            )
    db.commit()
    return svc.device_out(db, ctx, device, settings, timeutil.utcnow())


@router.post("/{device_id}/pairing", response_model=PairingCodeOut)
def new_pairing_code(device_id: uuid.UUID, ctx: Manager, db: DbDep, settings: SettingsDep) -> PairingCodeOut:
    """A fresh code for a locket that has not been paired yet (the earlier code expired or was lost)."""
    device = svc.get_visible_device(db, ctx, device_id)
    if device.status == "revoked":
        raise ApiError(
            409, "device_revoked", "This locket was revoked. Add the hardware as a new locket instead."
        )
    if device.status != "pending":
        raise ApiError(409, "already_paired", "This locket is already paired. Revoke it to pair it again.")
    pairing, code = svc.create_pairing(db, settings, ctx, device)
    db.commit()
    return PairingCodeOut(
        id=device.id,
        device_id=device.public_id,
        device_name=device.name,
        code=code,
        expires_at=pairing.expires_at,
    )


@router.post("/{device_id}/revoke", response_model=DeviceOut)
def revoke_device(device_id: uuid.UUID, ctx: Manager, db: DbDep, settings: SettingsDep) -> DeviceOut:
    device = svc.get_visible_device(db, ctx, device_id)
    svc.revoke_device(db, ctx, device)
    db.commit()
    return svc.device_out(db, ctx, device, settings, timeutil.utcnow())


@router.post("/{device_id}/connect", response_model=DeviceOut)
def connect_device(device_id: uuid.UUID, ctx: Authed, db: DbDep, settings: SettingsDep) -> DeviceOut:
    """Application-level connection: the user asks to work with a locket that is really online."""
    device = svc.get_visible_device(db, ctx, device_id)
    now = timeutil.utcnow()
    status = svc.presence(device, settings, now)
    if status != "online":
        raise ApiError(409, "device_offline", "This locket is not online right now.")
    exists = db.scalar(
        select(DeviceConnection.id).where(
            DeviceConnection.device_id == device.id, DeviceConnection.user_id == ctx.user.id
        )
    )
    if exists is None:
        db.add(DeviceConnection(device_id=device.id, user_id=ctx.user.id))
        record_device_event(db, device, "connected", {"user": str(ctx.user.id)})
        record_audit(
            db,
            action="device.connected",
            company_id=ctx.company_id,
            actor_user_id=ctx.user.id,
            object_type="device",
            object_id=device.id,
            ip=ctx.ip,
        )
        publish_event(
            db, ctx.company_id, "device.connected", {"deviceId": str(device.id), "userId": str(ctx.user.id)}
        )
    db.commit()
    return svc.device_out(db, ctx, device, settings, now)


@router.post("/{device_id}/disconnect", response_model=DeviceOut)
def disconnect_device(device_id: uuid.UUID, ctx: Authed, db: DbDep, settings: SettingsDep) -> DeviceOut:
    """Ends the connection only. Recordings and the device itself are untouched."""
    device = svc.get_visible_device(db, ctx, device_id)
    removed = rows_affected(
        db.execute(
            delete(DeviceConnection).where(
                DeviceConnection.device_id == device.id, DeviceConnection.user_id == ctx.user.id
            )
        )
    )
    if removed:
        record_device_event(db, device, "disconnected", {"user": str(ctx.user.id)})
        publish_event(
            db,
            ctx.company_id,
            "device.disconnected",
            {"deviceId": str(device.id), "userId": str(ctx.user.id)},
        )
    db.commit()
    return svc.device_out(db, ctx, device, settings, timeutil.utcnow())


@router.get("/{device_id}/events", response_model=list[DeviceEventOut])
def device_history(device_id: uuid.UUID, ctx: Manager, db: DbDep) -> list[DeviceEventOut]:
    device = svc.get_visible_device(db, ctx, device_id)
    rows = db.scalars(
        select(DeviceEvent)
        .where(DeviceEvent.device_id == device.id)
        .order_by(DeviceEvent.created_at.desc())
        .limit(100)
    )
    return [DeviceEventOut(id=r.id, type=r.type, detail=r.detail, created_at=r.created_at) for r in rows]
