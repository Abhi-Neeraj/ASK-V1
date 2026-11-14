"""The API the locket itself calls. Authenticated by a per-device credential, never by a cookie."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app import timeutil
from app.errors import ApiError, not_found
from app.models import Recording
from app.schemas.devices import DevicePairIn, DevicePairOut, HeartbeatIn, HeartbeatOut
from app.schemas.uploads import UploadAck
from app.security.deps import DbDep, DeviceAuthed, LimiterDep, SettingsDep, client_ip
from app.security.ratelimit import enforce
from app.services import devices as svc
from app.services.storage import Storage
from app.services.uploads import (
    CLIENT_ID_PATTERN,
    SESSION_REF_PATTERN,
    StoredRecording,
    finalize_upload,
    parse_recorded_at,
    spool_to_disk,
)

router = APIRouter(prefix="/api/device/v1", tags=["device"])

ALLOWED_CONTENT_TYPES = {"audio/wav", "audio/x-wav", "audio/wave", "application/octet-stream"}


def get_storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


StorageDep = Annotated[Storage, Depends(get_storage)]


def _ack(stored: StoredRecording) -> UploadAck:
    recording = stored.recording
    return UploadAck(
        recording_id=recording.id,
        client_recording_id=recording.client_recording_id,
        status="stored",
        sha256=recording.sha256,
        bytes=recording.size_bytes,
        duration_ms=recording.duration_ms,
        duplicate=stored.duplicate,
        started_at=recording.started_at,
    )


@router.post("/pair", response_model=DevicePairOut)
def pair(
    body: DevicePairIn, request: Request, db: DbDep, settings: SettingsDep, limiter: LimiterDep
) -> DevicePairOut:
    """Redeem a one-time pairing code for the locket's own credential. The credential is returned once."""
    ip = client_ip(request, settings)
    enforce(limiter, f"pair:{ip}", settings.pair_attempts_per_minute, 60)
    device, credential = svc.redeem_pairing(
        db, settings, body.code, body.firmware_version, ip, simulated=body.simulated
    )
    db.commit()
    return DevicePairOut(
        device_id=device.public_id, credential=credential, config=svc.device_config(settings)
    )


@router.post("/heartbeat", response_model=HeartbeatOut)
def heartbeat(body: HeartbeatIn, ctx: DeviceAuthed, db: DbDep, settings: SettingsDep) -> HeartbeatOut:
    svc.record_heartbeat(
        db,
        ctx,
        firmware_version=body.firmware_version,
        battery_percent=body.battery_percent,
        upload_queue_depth=body.upload_queue_depth,
    )
    db.commit()
    return HeartbeatOut(server_time=timeutil.utcnow(), config=svc.device_config(settings))


@router.put("/recordings/{client_id}", response_model=UploadAck)
async def upload_recording(
    client_id: str,
    request: Request,
    response: Response,
    ctx: DeviceAuthed,
    db: DbDep,
    settings: SettingsDep,
    limiter: LimiterDep,
    storage: StorageDep,
) -> UploadAck:
    """Upload one WAV clip as the raw request body.

    Required headers: `X-Content-SHA256` (hex digest of the body) and `X-Recorded-At` (ISO 8601 start
    time with timezone). Optional: `X-Session-Ref` and `X-Sequence` to order multi-file recordings.
    `201` means stored; `200` means this exact clip was already stored. In both cases the device may
    archive its copy. Any other status means keep the file and retry later.
    """
    if not CLIENT_ID_PATTERN.match(client_id):
        raise ApiError(
            422,
            "validation_error",
            "The recording ID must be 8 to 64 letters, digits, dashes or underscores.",
            {"recordingId": "Invalid recording ID."},
        )
    enforce(limiter, f"upload:{ctx.device.id}", 120, 60)

    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ApiError(415, "unsupported_media_type", "Send the clip as audio/wav.")
    declared_length = request.headers.get("content-length")
    if (
        declared_length is not None
        and declared_length.isdigit()
        and int(declared_length) > settings.max_upload_bytes
    ):
        raise ApiError(413, "upload_too_large", "The recording is larger than the allowed size.")

    declared_sha = (request.headers.get("x-content-sha256") or "").strip().lower()
    if not declared_sha:
        raise ApiError(
            422, "validation_error", "X-Content-SHA256 is required.", {"x-content-sha256": "Required."}
        )
    recorded_at = parse_recorded_at(request.headers.get("x-recorded-at"), timeutil.utcnow())

    session_ref = request.headers.get("x-session-ref")
    if session_ref is not None and not SESSION_REF_PATTERN.match(session_ref):
        raise ApiError(422, "validation_error", "X-Session-Ref is not valid.", {"x-session-ref": "Invalid."})
    raw_sequence = request.headers.get("x-sequence")
    # isdecimal() and isascii(): str.isdigit() is also true for characters such as superscript two, which
    # int()
    # then refuses, so a header made of them would be an unhandled error instead of a refusal.
    if raw_sequence is not None and not (
        raw_sequence.isascii() and raw_sequence.isdecimal() and int(raw_sequence) < 1_000_000
    ):
        raise ApiError(
            422, "validation_error", "X-Sequence must be a whole number.", {"x-sequence": "Invalid."}
        )
    sequence = int(raw_sequence) if raw_sequence is not None else None

    # Authenticating opened a database transaction, which holds a connection until it ends. A slow sender
    # would
    # keep that connection for the whole upload, and a handful of them would use up the pool for every
    # company.
    # Nothing was changed, so this only gives the connection back; the work after the body uses a fresh one.
    db.commit()
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
        ctx,
        client_id=client_id,
        spooled=spooled,
        declared_sha256=declared_sha,
        recorded_at=recorded_at,
        session_ref=session_ref,
        sequence=sequence,
    )
    response.status_code = 200 if stored.duplicate else 201
    return _ack(stored)


@router.get("/recordings/{client_id}", response_model=UploadAck)
def upload_status(client_id: str, ctx: DeviceAuthed, db: DbDep) -> UploadAck:
    """Lets a locket confirm a clip arrived (for example after losing the response) before archiving it."""
    recording = db.scalar(
        select(Recording).where(
            Recording.device_id == ctx.device.id, Recording.client_recording_id == client_id
        )
    )
    if recording is None:
        raise not_found("recording")
    return _ack(StoredRecording(recording=recording, duplicate=True))
