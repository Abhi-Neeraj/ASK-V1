"""Firmware builds for over-the-air updates.

An administrator uploads a build. The phone asks for the latest and, if the locket runs something older, hands
the locket (over Bluetooth) the download link, its size and its SHA-256. The locket fetches it over Wi-Fi; the
link carries a signed token valid for `ASK_FIRMWARE_LINK_MINUTES`, so the locket needs no account.
"""

from __future__ import annotations

import hashlib
import hmac
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timeutil
from app.config import Settings
from app.errors import ApiError
from app.models import FirmwareRelease

VERSION_PATTERN = re.compile(r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})$")


def parse_version(version: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.match(version)
    if match is None:
        raise ApiError(
            422,
            "validation_error",
            "The version must look like 1.2.3.",
            {"version": "Three whole numbers separated by dots."},
        )
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def storage_key(version: str, sha256: str) -> str:
    """Includes the checksum, so a second upload of the same version can never replace the stored file."""
    return f"firmware/{version}-{sha256[:16]}.bin"


def version_exists(db: Session, version: str) -> bool:
    return db.scalar(select(FirmwareRelease.id).where(FirmwareRelease.version == version)) is not None


def require_publisher(settings: Settings, company_id: object) -> None:
    """Only the operator's company publishes firmware: every locket on the server is offered it."""
    if settings.firmware_publisher_company_id is None or company_id != settings.firmware_publisher_company_id:
        raise ApiError(
            403,
            "firmware_publisher_only",
            "Only the server operator can publish firmware (ASK_FIRMWARE_PUBLISHER_COMPANY_ID).",
        )


def latest(db: Session) -> FirmwareRelease:
    releases = list(db.scalars(select(FirmwareRelease)))
    if not releases:
        raise ApiError(404, "no_firmware", "No firmware has been uploaded yet.")
    return max(releases, key=lambda r: parse_version(r.version))


def _signature(settings: Settings, version: str, expiry: int) -> str:
    message = f"fw|{version}|{expiry}".encode()
    return hmac.new(settings.secret_key.get_secret_value().encode(), message, hashlib.sha256).hexdigest()


def download_token(settings: Settings, version: str) -> str:
    expiry = int(timeutil.utcnow().timestamp()) + settings.firmware_link_minutes * 60
    return f"{expiry}.{_signature(settings, version, expiry)}"


def check_token(settings: Settings, version: str, token: str) -> None:
    expiry_text, _, signature = token.partition(".")
    if not expiry_text.isascii() or not expiry_text.isdecimal() or not signature:
        raise ApiError(403, "link_invalid", "This download link is not valid.")
    expiry = int(expiry_text)
    if not hmac.compare_digest(signature, _signature(settings, version, expiry)):
        raise ApiError(403, "link_invalid", "This download link is not valid.")
    if timeutil.utcnow().timestamp() > expiry:
        raise ApiError(403, "link_expired", "This download link has expired. Ask the app for a new one.")


def download_url(settings: Settings, version: str) -> str:
    base = settings.web_base_url.rstrip("/")
    return f"{base}/api/firmware/{version}/download?token={download_token(settings, version)}"
