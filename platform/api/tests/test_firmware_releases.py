"""Firmware builds for over-the-air updates: uploaded by an administrator, downloaded by a locket over Wi-Fi
with a short-lived link the phone hands it over Bluetooth."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx2 as httpx
import pytest

from app import timeutil
from tests.helpers import WebClient
from tests.test_lockets import as_user

FIRMWARE = b"\xe9" + b"firmware-image" * 1000


def put_firmware(client: WebClient, version: str, body: bytes = FIRMWARE) -> httpx.Response:
    return client.request(
        "PUT", f"/api/firmware/{version}", content=body, headers={"Content-Type": "application/octet-stream"}
    )


@pytest.fixture
def admin(app, acme) -> WebClient:  # type: ignore[no-untyped-def]
    """Acme is the company allowed to publish firmware on this server."""
    app.state.settings.firmware_publisher_company_id = acme["company"].id
    return as_user(app, "admin@acme.example.com")


@pytest.fixture
def employee(app, acme) -> WebClient:  # type: ignore[no-untyped-def]
    return as_user(app, "employee@acme.example.com")


def test_admin_uploads_and_latest_points_to_it(admin: WebClient, employee: WebClient) -> None:
    stored = put_firmware(admin, "1.2.0")
    assert stored.status_code == 201, stored.text
    assert stored.json() == {
        "version": "1.2.0",
        "sizeBytes": len(FIRMWARE),
        "sha256": hashlib.sha256(FIRMWARE).hexdigest(),
    }
    latest = employee.get("/api/firmware/latest")
    assert latest.status_code == 200
    body = latest.json()
    assert body["version"] == "1.2.0" and body["sha256"] == hashlib.sha256(FIRMWARE).hexdigest()
    assert "/api/firmware/1.2.0/download?token=" in body["downloadUrl"]


def test_no_firmware_yet_is_404(employee: WebClient) -> None:
    missing = employee.get("/api/firmware/latest")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "no_firmware"


def test_numeric_version_order(admin: WebClient) -> None:
    for version in ("1.9.0", "1.10.0", "1.2.5"):
        assert put_firmware(admin, version).status_code == 201
    assert admin.get("/api/firmware/latest").json()["version"] == "1.10.0"


def test_duplicate_version_409(admin: WebClient) -> None:
    assert put_firmware(admin, "2.0.0").status_code == 201
    assert put_firmware(admin, "2.0.0").status_code == 409


@pytest.mark.parametrize("version", ["1.2", "v1.2.0", "1.2.0-beta", "01.2.x"])
def test_bad_versions_are_refused(admin: WebClient, version: str) -> None:
    assert put_firmware(admin, version).status_code == 422


def test_too_large_413(app, admin: WebClient) -> None:  # type: ignore[no-untyped-def]
    app.state.settings.firmware_max_bytes = 1024
    assert put_firmware(admin, "3.0.0", b"x" * 2048).status_code == 413


def test_non_admin_cannot_upload(employee: WebClient) -> None:
    assert put_firmware(employee, "1.0.0").status_code == 403


def path_and_query(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.path}?{parsed.query}"


def test_download_with_valid_link(app, admin: WebClient) -> None:  # type: ignore[no-untyped-def]
    put_firmware(admin, "1.0.0")
    link = path_and_query(admin.get("/api/firmware/latest").json()["downloadUrl"])
    from fastapi.testclient import TestClient

    with TestClient(app) as locket:  # no cookies: the locket has no account
        got = locket.get(link)
    assert got.status_code == 200 and got.content == FIRMWARE
    assert got.headers["content-type"] == "application/octet-stream"


def test_expired_and_tampered_links_403(app, admin: WebClient, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    put_firmware(admin, "1.0.0")
    link = path_and_query(admin.get("/api/firmware/latest").json()["downloadUrl"])
    token = parse_qs(urlparse(link).query)["token"][0]
    expiry, signature = token.split(".")
    tampered = f"/api/firmware/1.0.0/download?token={int(expiry) + 3600}.{signature}"
    other_version = f"/api/firmware/9.9.9/download?token={token}"
    with TestClient(app) as locket:
        assert locket.get(tampered).json()["error"]["code"] == "link_invalid"
        assert locket.get(other_version).status_code in (403, 404)
        assert locket.get("/api/firmware/1.0.0/download?token=garbage").status_code == 403
        later = timeutil.utcnow() + timedelta(minutes=31)
        monkeypatch.setattr(timeutil, "utcnow", lambda: later)
        expired = locket.get(link)
        assert expired.status_code == 403 and expired.json()["error"]["code"] == "link_expired"


def test_only_the_publisher_company_can_upload(app, admin: WebClient, globex) -> None:  # type: ignore[no-untyped-def]
    other_admin = as_user(app, "admin@globex.example.com")
    refused = put_firmware(other_admin, "1.0.0")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "firmware_publisher_only"
    assert put_firmware(admin, "1.0.0").status_code == 201


def test_no_publisher_configured_means_nobody_uploads(app, acme) -> None:  # type: ignore[no-untyped-def]
    app.state.settings.firmware_publisher_company_id = None
    refused = put_firmware(as_user(app, "admin@acme.example.com"), "1.0.0")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "firmware_publisher_only"


def test_a_lost_upload_race_never_overwrites_the_winner(  # type: ignore[no-untyped-def]
    app, admin: WebClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two uploads of one version: the check before the body passes for both. The loser must not replace the
    stored file, or every locket would fail the checksum of the release the server keeps."""
    from fastapi.testclient import TestClient

    from app.services import firmware as firmware_service

    assert put_firmware(admin, "1.0.0").status_code == 201
    monkeypatch.setattr(firmware_service, "version_exists", lambda db, version: False)
    loser = put_firmware(admin, "1.0.0", b"\xe9" + b"other-image" * 500)
    assert loser.status_code == 409
    link = path_and_query(admin.get("/api/firmware/latest").json()["downloadUrl"])
    with TestClient(app) as locket:
        assert locket.get(link).content == FIRMWARE
