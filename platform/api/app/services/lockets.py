"""Bluetooth lockets: who is on which locket, the per-locket limit, and pairing with an emailed code.

A locket is a row in `devices`; its `public_id` is the product ID printed on it. People on a locket are
rows in `device_members`. `devices.assigned_user_id` is kept equal to the earliest member, so code that
still reads one holder keeps working.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from sqlalchemy import Select, exists, func, select
from sqlalchemy.orm import Session

from app import timeutil
from app.config import Settings
from app.errors import ApiError, not_found
from app.models import Device, DeviceConnection, DeviceMember, LocketPairingRequest, Membership, User
from app.schemas.lockets import LocketMemberOut, LocketOut
from app.security.deps import Ctx
from app.security.locket_codes import locket_secret, pairing_code, proof_matches
from app.services.audit import record_audit, record_device_event
from app.services.devices import new_public_id, revoke_device
from app.services.email import EmailSender, locket_code_email


def _master_key(settings: Settings) -> str:
    if settings.locket_master_key is None:
        raise ApiError(
            503,
            "locket_keys_missing",
            "Lockets cannot be registered or paired until ASK_LOCKET_MASTER_KEY is set on the server.",
        )
    return settings.locket_master_key.get_secret_value()


def _hash_nonce(nonce: bytes) -> str:
    return hashlib.sha256(nonce).hexdigest()


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


# --- who can see which locket -------------------------------------------------------------------


def visible_lockets(ctx: Ctx) -> Select[Device]:
    """Managers see every locket in their company; everyone else only the lockets they are on. A deleted
    (revoked) locket is in nobody's list."""
    query = select(Device).where(
        Device.company_id == ctx.company_id, Device.transport == "bluetooth", Device.status != "revoked"
    )
    if not ctx.can_manage:
        query = query.where(
            exists().where(DeviceMember.device_id == Device.id, DeviceMember.user_id == ctx.user.id),
        )
    return query


def delete_locket(db: Session, ctx: Ctx, device: Device) -> None:
    """Take a locket out of use for good: it is revoked, so nothing can be uploaded to it or paired with it
    again, and it is gone from every list. Its recordings stay, and its product ID is never given to another
    locket."""
    revoke_device(db, ctx, device)


def find_locket(db: Session, ctx: Ctx, product_id: str, *, lock: bool = False) -> Device:
    """A locket of the caller's company by product ID, whether or not the caller is on it yet (pairing needs
    that). Another company's locket, or a revoked one, answers exactly like a missing one."""
    query = select(Device).where(
        Device.company_id == ctx.company_id,
        Device.public_id == product_id,
        Device.transport == "bluetooth",
        Device.status != "revoked",
    )
    device = db.scalar(query.with_for_update() if lock else query)
    if device is None:
        raise not_found("locket")
    return device


# --- membership ---------------------------------------------------------------------------------


def members(db: Session, device: Device) -> list[User]:
    return list(
        db.scalars(
            select(User)
            .join(DeviceMember, DeviceMember.user_id == User.id)
            .where(DeviceMember.device_id == device.id)
            .order_by(DeviceMember.added_at, User.email)
        )
    )


def _count(db: Session, device: Device) -> int:
    return db.scalar(select(func.count()).where(DeviceMember.device_id == device.id)) or 0


def is_member(db: Session, device: Device, user_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(DeviceMember.id).where(
                DeviceMember.device_id == device.id, DeviceMember.user_id == user_id
            )
        )
        is not None
    )


def _sync_holder(db: Session, device: Device) -> None:
    db.flush()
    device.assigned_user_id = db.scalar(
        select(DeviceMember.user_id)
        .where(DeviceMember.device_id == device.id)
        .order_by(DeviceMember.added_at, DeviceMember.id)
        .limit(1)
    )


def _full(device: Device) -> ApiError:
    people = "person" if device.max_users == 1 else "people"
    return ApiError(
        409,
        "locket_full",
        f"This locket allows {device.max_users} {people}. An administrator can raise the limit.",
    )


def add_member(db: Session, device: Device, user: User, added_by: uuid.UUID | None) -> None:
    """Put someone on a locket. The caller must hold the device row locked (`find_locket(lock=True)`)."""
    if is_member(db, device, user.id):
        return
    if _count(db, device) >= device.max_users:
        raise _full(device)
    db.add(
        DeviceMember(company_id=device.company_id, device_id=device.id, user_id=user.id, added_by=added_by)
    )
    _sync_holder(db, device)


def remove_member(db: Session, device: Device, user_id: uuid.UUID) -> bool:
    row = db.scalar(
        select(DeviceMember).where(DeviceMember.device_id == device.id, DeviceMember.user_id == user_id)
    )
    if row is None:
        return False
    db.delete(row)
    # Leaving a locket also ends the person's connection to it.
    for connection in db.scalars(
        select(DeviceConnection).where(
            DeviceConnection.device_id == device.id, DeviceConnection.user_id == user_id
        )
    ):
        db.delete(connection)
    _sync_holder(db, device)
    return True


def replace_members(db: Session, device: Device, user_id: uuid.UUID | None, added_by: uuid.UUID) -> None:
    """The older "assign this locket to one person" action: that person becomes its only member."""
    for row in db.scalars(select(DeviceMember).where(DeviceMember.device_id == device.id)):
        if row.user_id != user_id:
            db.delete(row)
    db.flush()
    if user_id is not None and not is_member(db, device, user_id):
        db.add(
            DeviceMember(
                company_id=device.company_id, device_id=device.id, user_id=user_id, added_by=added_by
            )
        )
    _sync_holder(db, device)


def set_limit(db: Session, device: Device, max_users: int) -> None:
    if not 1 <= max_users <= 50:
        raise ApiError(
            422, "validation_error", "The limit must be between 1 and 50.", {"maxUsers": "1 to 50."}
        )
    current = _count(db, device)
    if max_users < current:
        raise ApiError(
            409,
            "limit_below_members",
            f"{current} people are on this locket. Remove someone before lowering the limit to {max_users}.",
        )
    device.max_users = max_users


# --- registering --------------------------------------------------------------------------------


def register_locket(
    db: Session, settings: Settings, company_id: uuid.UUID, name: str, actor: uuid.UUID | None, ip: str | None
) -> tuple[Device, str]:
    """A new locket for the company, with its permanent product ID. Returns the locket and its secret (hex),
    which is shown once so it can be written into the locket and is never stored."""
    master = _master_key(settings)
    device = Device(
        company_id=company_id,
        public_id=new_public_id(db),
        name=name.strip(),
        status="active",
        transport="bluetooth",
    )
    db.add(device)
    db.flush()
    record_device_event(db, device, "registered", {"by": str(actor) if actor else None})
    record_audit(
        db,
        action="locket.registered",
        company_id=company_id,
        actor_user_id=actor,
        object_type="device",
        object_id=device.id,
        detail={"productId": device.public_id},
        ip=ip,
    )
    return device, locket_secret(master, device.public_id).hex()


# --- pairing with an emailed code ---------------------------------------------------------------


def request_code(
    db: Session, settings: Settings, sender: EmailSender, ctx: Ctx, device: Device, nonce: bytes
) -> tuple[str, LocketPairingRequest]:
    """Email the caller the code for this locket and this nonce.

    Refused, with no email, when the locket is full and the caller is not already on it. Returns the masked
    address and the pending request."""
    master = _master_key(settings)
    if not is_member(db, device, ctx.user.id) and _count(db, device) >= device.max_users:
        raise _full(device)
    now = timeutil.utcnow()
    pending = LocketPairingRequest(
        company_id=device.company_id,
        device_id=device.id,
        user_id=ctx.user.id,
        nonce_hash=_hash_nonce(nonce),
        expires_at=now + timedelta(minutes=settings.locket_code_ttl_minutes),
    )
    db.add(pending)
    record_audit(
        db,
        action="locket.code_requested",
        company_id=device.company_id,
        actor_user_id=ctx.user.id,
        object_type="device",
        object_id=device.id,
        ip=ctx.ip,
    )
    db.flush()
    code = pairing_code(locket_secret(master, device.public_id), nonce)
    sender.send(locket_code_email(ctx.user.email, device.public_id, code, settings.locket_code_ttl_minutes))
    return mask_email(ctx.user.email), pending


def confirm_paired(
    db: Session, settings: Settings, ctx: Ctx, device: Device, nonce: bytes, proof: str
) -> None:
    """The phone reports the Bluetooth pairing succeeded: put the caller on the locket.

    Needs a pending, unexpired request from this caller for this nonce; each request works once. The limit
    is checked again here, so a code sent while there was room cannot overfill the locket."""
    now = timeutil.utcnow()
    pending = db.scalar(
        select(LocketPairingRequest).where(
            LocketPairingRequest.device_id == device.id,
            LocketPairingRequest.user_id == ctx.user.id,
            LocketPairingRequest.nonce_hash == _hash_nonce(nonce),
            LocketPairingRequest.completed_at.is_(None),
            LocketPairingRequest.expires_at > now,
        )
    )
    if pending is None:
        raise ApiError(
            404,
            "pairing_request_not_found",
            "That pairing code has expired or was already used. Ask for a new code.",
        )
    if not proof_matches(locket_secret(_master_key(settings), device.public_id), nonce, proof):
        record_audit(
            db,
            action="locket.pairing_proof_rejected",
            company_id=device.company_id,
            actor_user_id=ctx.user.id,
            object_type="device",
            object_id=device.id,
            ip=ctx.ip,
        )
        db.commit()
        raise ApiError(
            403,
            "pairing_proof_invalid",
            "The locket did not confirm this pairing. Pair again with the locket next to your phone.",
        )
    add_member(db, device, ctx.user, ctx.user.id)
    pending.completed_at = now
    record_device_event(db, device, "paired", {"user": str(ctx.user.id)})
    record_audit(
        db,
        action="locket.paired",
        company_id=device.company_id,
        actor_user_id=ctx.user.id,
        object_type="device",
        object_id=device.id,
        ip=ctx.ip,
    )


# --- output -------------------------------------------------------------------------------------


def locket_out(db: Session, device: Device) -> LocketOut:
    return LocketOut(
        product_id=device.public_id,
        name=device.name,
        max_users=device.max_users,
        members=[LocketMemberOut(id=u.id, name=u.name, email=u.email) for u in members(db, device)],
        firmware_version=device.firmware_version,
        battery_percent=device.battery_percent,
        last_seen_at=device.last_heartbeat_at,
    )


def company_user(db: Session, company_id: uuid.UUID, user_id: uuid.UUID) -> User | None:
    member = db.scalar(
        select(Membership).where(Membership.user_id == user_id, Membership.company_id == company_id)
    )
    return db.get(User, user_id) if member is not None else None
