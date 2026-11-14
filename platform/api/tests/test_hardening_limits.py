"""Size limits on ordinary requests, and file permissions. Run against the code before the fix that goes
with them."""

from __future__ import annotations

import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2 as httpx
import pytest

from app.services.storage import Storage
from tests.conftest import new_web, provision_device
from tests.helpers import make_wav


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestOrdinaryRequestsHaveASizeLimit:
    """JSON endpoints read their whole body before validating it, so one anonymous request with a huge body
    could
    use memory without limit. Uploads have their own, larger, limits and are not affected."""

    def test_an_oversized_body_is_refused_before_it_is_read(self, app) -> None:  # type: ignore[no-untyped-def]
        web = new_web(app)
        big = {"email": "a@example.com", "password": "x" * (2 * 1024 * 1024)}
        response = web.post("/api/auth/login", json=big)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "body_too_large"

    def test_an_ordinary_request_is_unaffected(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        assert new_web(app).login("admin@acme.example.com").status_code == 200

    def test_the_limit_is_in_settings(self, app, settings) -> None:  # type: ignore[no-untyped-def]
        assert settings.max_json_bytes == 1024 * 1024

    def test_a_clip_larger_than_the_json_limit_still_uploads(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        _, locket = provision_device(app, manager)
        clip = make_wav(seconds=40)  # about 1.2 MiB
        assert len(clip) > 1024 * 1024
        assert locket.upload("clip-bigger-than-json", clip).status_code == 201

    @pytest.mark.anyio
    async def test_a_body_sent_in_pieces_with_no_declared_length_is_cut_off_too(self, app) -> None:  # type: ignore[no-untyped-def]
        async def body() -> AsyncIterator[bytes]:
            for _ in range(12):
                yield b"x" * (256 * 1024)  # 3 MiB in all, never announced up front

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/auth/login",
                content=body(),
                headers={"Content-Type": "application/json", "Origin": "http://localhost:3000"},
            )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "body_too_large"


class TestStoredFilesArePrivateToTheService:
    def test_new_files_are_not_readable_by_other_accounts_on_the_machine(self, tmp_path: Path) -> None:
        if os.name == "nt":
            pytest.skip("POSIX permission bits do not apply on Windows")
        storage = Storage(tmp_path)
        path = storage.new_temp_path()
        path.write_bytes(b"audio")
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
