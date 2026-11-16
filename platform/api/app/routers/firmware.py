"""Firmware for over-the-air updates: upload (administrators), latest (anyone signed in), download (a locket,
with a signed link)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.errors import ApiError, not_found
from app.models import FirmwareRelease
from app.routers.device_api import StorageDep
from app.schemas.firmware import FirmwareLatestOut, FirmwareOut
from app.security.deps import Admin, Authed, DbDep, SettingsDep
from app.services import firmware as svc
from app.services.audit import record_audit
from app.services.uploads import spool_to_disk

router = APIRouter(prefix="/api/firmware", tags=["firmware"])


@router.put("/{version}", response_model=FirmwareOut, status_code=201)
async def upload_firmware(
    version: str, request: Request, ctx: Admin, db: DbDep, settings: SettingsDep, storage: StorageDep
) -> FirmwareOut:
    """The raw `.bin` as the request body. A version can be uploaded once, by the operator's company."""
    svc.require_publisher(settings, ctx.company_id)
    svc.parse_version(version)
    if svc.version_exists(db, version):
        raise ApiError(409, "version_exists", f"Firmware {version} was already uploaded.")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > settings.firmware_max_bytes:
        raise ApiError(413, "upload_too_large", "The firmware file is larger than the allowed size.")
    db.commit()  # give the connection back while the body arrives
    spooled = await spool_to_disk(
        request.stream(),
        storage,
        max_bytes=settings.firmware_max_bytes,
        read_timeout=settings.upload_read_timeout_seconds,
        total_timeout=settings.upload_total_seconds,
    )
    key = svc.storage_key(version, spooled.sha256)
    if storage.exists(key):  # the same bytes under the same version are already stored
        spooled.path.unlink(missing_ok=True)
        raise ApiError(409, "version_exists", f"Firmware {version} was already uploaded.")
    try:
        storage.commit(spooled.path, key)
    except BaseException:
        spooled.path.unlink(missing_ok=True)
        raise
    release = FirmwareRelease(
        version=version,
        sha256=spooled.sha256,
        size_bytes=spooled.size,
        storage_key=key,
        uploaded_by=ctx.user.id,
    )
    db.add(release)
    record_audit(
        db,
        action="firmware.uploaded",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="firmware",
        object_id=version,
        detail={"sizeBytes": spooled.size},
        ip=ctx.ip,
    )
    try:
        db.commit()
    except IntegrityError:
        # Another upload of this version won the race: keep its file, drop ours.
        db.rollback()
        storage.delete(key)
        raise ApiError(409, "version_exists", f"Firmware {version} was already uploaded.") from None
    return FirmwareOut(version=version, size_bytes=spooled.size, sha256=spooled.sha256)


@router.get("/latest", response_model=FirmwareLatestOut)
def latest_firmware(ctx: Authed, db: DbDep, settings: SettingsDep) -> FirmwareLatestOut:
    release = svc.latest(db)
    return FirmwareLatestOut(
        version=release.version,
        size_bytes=release.size_bytes,
        sha256=release.sha256,
        download_url=svc.download_url(settings, release.version),
    )


@router.get("/{version}/download")
def download_firmware(
    version: str, token: str, db: DbDep, settings: SettingsDep, storage: StorageDep
) -> Response:
    """For the locket, over Wi-Fi, with the link the phone gave it. No account needed; the link expires."""
    svc.check_token(settings, version, token)
    release = db.scalar(select(FirmwareRelease).where(FirmwareRelease.version == version))
    if release is None:
        raise not_found("firmware")
    return FileResponse(
        storage.path_for(release.storage_key),
        media_type="application/octet-stream",
        filename=f"ask-locket-{version}.bin",
    )
