from __future__ import annotations

from app.schemas.common import CamelModel


class FirmwareOut(CamelModel):
    version: str
    size_bytes: int
    sha256: str


class FirmwareLatestOut(FirmwareOut):
    # A signed link, valid for ASK_FIRMWARE_LINK_MINUTES, that the phone hands the locket over Bluetooth.
    download_url: str
