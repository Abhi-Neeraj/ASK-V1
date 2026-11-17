"""Bluetooth lockets by product ID: registering, pairing with an emailed code, people, the limit."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from app.errors import ApiError, not_found
from app.models import Device
from app.routers.device_api import StorageDep, upload_ack
from app.schemas.lockets import (
    CodeSentOut,
    LimitIn,
    LocketCreateIn,
    LocketOut,
    LocketRegisteredOut,
    NonceIn,
    PairedIn,
)
from app.schemas.uploads import UploadAck
from app.security.deps import Admin, Authed, DbDep, LimiterDep, SettingsDep
from app.security.locket_codes import parse_nonce
from app.security.ratelimit import enforce
from app.services import lockets as svc
from app.services.audit import record_audit, record_device_event
from app.services.uploads import finalize_upload, parse_upload_headers, spool_to_disk

router = APIRouter(prefix="/api/lockets", tags=["lockets"])
logger = logging.getLogger("ask.lockets")


@router.get("", response_model=list[LocketOut])
def list_lockets(ctx: Authed, db: DbDep) -> list[LocketOut]:
    rows = db.scalars(svc.visible_lockets(ctx).order_by(Device.created_at, Device.name)).all()
    return [svc.locket_out(db, device) for device in rows]


@router.post("", response_model=LocketRegisteredOut, status_code=201)
def register_locket(
    body: LocketCreateIn, ctx: Admin, db: DbDep, settings: SettingsDep
) -> LocketRegisteredOut:
    device, secret_hex = svc.register_locket(db, settings, ctx.company_id, body.name, ctx.user.id, ctx.ip)
    db.commit()
    return LocketRegisteredOut(product_id=device.public_id, name=device.name, secret_hex=secret_hex)


@router.post("/{product_id}/pairing-code", response_model=CodeSentOut)
def request_pairing_code(
    product_id: str,
    body: NonceIn,
    request: Request,
    ctx: Authed,
    db: DbDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> CodeSentOut:
    """Email the caller a six-digit code for pairing their phone with this locket over Bluetooth."""
    nonce = parse_nonce(body.nonce)
    # Both limits count before the locket is looked up, so guessing product IDs counts too.
    enforce(limiter, f"locket-code:{ctx.user.id}", settings.locket_code_requests_per_hour, 3600)
    enforce(
        limiter,
        f"locket-code-locket:{ctx.company_id}:{product_id}",
        settings.locket_code_requests_per_locket_per_hour,
        3600,
    )
    device = svc.find_locket(db, ctx, product_id)
    sent_to, pending = svc.request_code(db, settings, request.app.state.email, ctx, device, nonce)
    db.commit()
    return CodeSentOut(sent_to=sent_to, expires_at=pending.expires_at)


@router.post("/{product_id}/paired", status_code=204)
def confirm_paired(
    product_id: str, body: PairedIn, ctx: Authed, db: DbDep, settings: SettingsDep
) -> Response:
    """After Bluetooth pairing succeeded: `{nonce, proof}`, where `proof` is what the locket showed the phone
    on its encrypted characteristic. Without the real locket's proof nobody is added."""
    nonce = parse_nonce(body.nonce)
    device = svc.find_locket(db, ctx, product_id, lock=True)
    svc.confirm_paired(db, settings, ctx, device, nonce, body.proof)
    db.commit()
    return Response(status_code=204)


@router.delete("/{product_id}/members/{user_id}", status_code=204)
def remove_member(product_id: str, user_id: uuid.UUID, ctx: Authed, db: DbDep) -> Response:
    """Managers remove anyone; everyone else can only take themselves off ("Forget this locket")."""
    if user_id != ctx.user.id and not ctx.can_manage:
        raise ApiError(403, "forbidden", "Only a manager can remove someone else from a locket.")
    device = svc.find_locket(db, ctx, product_id, lock=True)
    if not ctx.can_manage and not svc.remove_member(db, device, user_id):
        raise not_found("locket")
    if ctx.can_manage and not svc.remove_member(db, device, user_id):
        raise not_found("person on this locket")
    record_device_event(db, device, "member_removed", {"user": str(user_id)})
    record_audit(
        db,
        action="locket.member_removed",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="device",
        object_id=device.id,
        detail={"user": str(user_id)},
        ip=ctx.ip,
    )
    db.commit()
    return Response(status_code=204)


@router.delete("/{product_id}", status_code=204)
def delete_locket(product_id: str, ctx: Admin, db: DbDep) -> Response:
    """Administrators delete a locket: it disappears from every list and can no longer pair or upload. Its
    recordings are kept."""
    device = svc.find_locket(db, ctx, product_id, lock=True)
    svc.delete_locket(db, ctx, device)
    db.commit()
    return Response(status_code=204)


@router.patch("/{product_id}/limit", response_model=LocketOut)
def change_limit(product_id: str, body: LimitIn, ctx: Admin, db: DbDep) -> LocketOut:
    device = svc.find_locket(db, ctx, product_id, lock=True)
    before = device.max_users
    svc.set_limit(db, device, body.max_users)
    record_audit(
        db,
        action="locket.limit_changed",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="device",
        object_id=device.id,
        detail={"from": before, "to": body.max_users},
        ip=ctx.ip,
    )
    db.commit()
    return svc.locket_out(db, device)


@router.put("/{product_id}/recordings/{client_recording_id}", response_model=UploadAck)
async def upload_from_phone(
    product_id: str,
    client_recording_id: str,
    request: Request,
    response: Response,
    ctx: Authed,
    db: DbDep,
    settings: SettingsDep,
    limiter: LimiterDep,
    storage: StorageDep,
) -> UploadAck:
    """The phone uploads a WAV the locket sent it over Bluetooth. Same headers and answers as the locket's own
    upload (`201` stored, `200` already stored); the caller must be on the locket."""
    try:
        return await _upload_from_phone(
            product_id, client_recording_id, request, response, ctx, db, settings, limiter, storage
        )
    except ApiError as error:
        # The phone sets a refused part aside; without this line the hosted log never says why. Only the
        # reason, the locket, the clip name and the start time it claimed: never audio, paths or secrets.
        logger.warning(
            "phone upload refused: status=%s code=%s locket=%s clip=%s recorded_at=%r reason=%s",
            error.status,
            error.code,
            product_id,
            client_recording_id,
            request.headers.get("x-recorded-at"),
            error.message,
        )
        raise


async def _upload_from_phone(
    product_id: str,
    client_recording_id: str,
    request: Request,
    response: Response,
    ctx: Authed,
    db: DbDep,
    settings: SettingsDep,
    limiter: LimiterDep,
    storage: StorageDep,
) -> UploadAck:
    device = svc.find_locket(db, ctx, product_id)
    if not svc.is_member(db, device, ctx.user.id):
        raise not_found("locket")
    enforce(limiter, f"upload:{device.id}", 120, 60)
    headers = parse_upload_headers(client_recording_id, request, settings)
    db.commit()  # give the connection back while the body arrives (see the locket upload)
    spooled = await spool_to_disk(
        request.stream(),
        storage,
        max_bytes=settings.max_upload_bytes,
        read_timeout=settings.upload_read_timeout_seconds,
        total_timeout=settings.upload_total_seconds,
    )
    stored = await run_in_threadpool(
        finalize_upload,
        db,
        settings,
        storage,
        device,
        uploader=ctx.user.id,
        client_id=client_recording_id,
        spooled=spooled,
        declared_sha256=headers.sha256,
        recorded_at=headers.recorded_at,
        session_ref=headers.session_ref,
        sequence=headers.sequence,
    )
    response.status_code = 200 if stored.duplicate else 201
    return upload_ack(stored)
