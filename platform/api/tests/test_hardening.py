"""Regression tests for the Phase F security review. Each one failed against the code as it stood before the
fix that goes with it (the commit that added a test also changed the code, and the test was run first)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import httpx2 as httpx
import pytest

from app.config import Settings
from app.db import make_session_factory
from app.errors import ApiError
from app.security.deps import client_ip
from app.security.ratelimit import RateLimiter
from app.services.storage import Storage
from app.services.uploads import spool_to_disk
from tests.conftest import new_web, provision_device
from tests.helpers import add_company, add_member, make_wav


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def request_with(headers: dict[str, str], host: str = "10.0.0.9") -> SimpleNamespace:
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))


@pytest.mark.anyio
class TestAnUploadCannotHoldTheDatabaseHostage:
    """A device that sends its body very slowly used to keep a database connection checked out the whole
    time (device authentication opened a transaction and nothing closed it), so a handful of such uploads
    used up the pool and every other company got errors."""

    async def test_the_overall_time_is_limited_not_just_the_gap_between_chunks(self, tmp_path: Path) -> None:
        async def drip() -> AsyncIterator[bytes]:
            for _ in range(100):
                await asyncio.sleep(0.05)  # each gap is well inside the per-chunk timeout...
                yield b"x"

        storage = Storage(tmp_path)
        with pytest.raises(ApiError) as caught:
            # ...but the whole upload has 0.3 s.
            await spool_to_disk(drip(), storage, max_bytes=1000, read_timeout=5.0, total_timeout=0.3)
        assert caught.value.status == 408 and caught.value.code == "upload_timeout"
        assert list(tmp_path.rglob("*.part")) == []  # nothing left behind

    async def test_a_normal_upload_is_unaffected(self, tmp_path: Path) -> None:
        async def body() -> AsyncIterator[bytes]:
            yield b"a" * 100
            yield b"b" * 100

        spooled = await spool_to_disk(
            body(), Storage(tmp_path), max_bytes=1000, read_timeout=5.0, total_timeout=5.0
        )
        assert spooled.size == 200
        spooled.path.unlink()

    async def test_the_connection_is_given_back_before_the_body_is_waited_for(self, file_app) -> None:  # type: ignore[no-untyped-def]
        # `file_app` has a database of its own, so its people are made there, not by the shared `acme`
        # fixture.
        with make_session_factory(file_app.state.engine)() as session:
            add_member(session, add_company(session, "Slowco"), "manager@slowco.example.com", "manager")
        manager = new_web(file_app)
        assert manager.login("manager@slowco.example.com").status_code == 200
        _, locket = provision_device(file_app, manager)
        # Authentication only commits when a credential has not been used for a minute. A device that has just
        # sent a heartbeat (as a real one does every 30 seconds) is therefore the case that held the
        # connection.
        assert locket.request("POST", "/api/device/v1/heartbeat", json={}).status_code == 200
        wav = make_wav(seconds=1)
        observed: list[int] = []

        async def body() -> AsyncIterator[bytes]:
            yield wav[:100]
            await asyncio.sleep(0.2)  # the server has authenticated by now and is waiting for the rest
            observed.append(file_app.state.engine.pool.checkedout())
            yield wav[100:]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=file_app), base_url="http://test"
        ) as client:
            await client.put(
                "/api/device/v1/recordings/slow-upload-0001",
                content=body(),
                headers={
                    "Authorization": f"Bearer {locket.credential}",
                    "Content-Type": "audio/wav",
                    "X-Content-SHA256": "0" * 64,
                    "X-Recorded-At": "2026-10-20T12:00:00Z",
                },
            )
        assert observed == [0], "a connection stayed checked out while the upload body was still arriving"

    async def test_a_number_that_only_looks_like_a_digit_is_refused_not_a_crash(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        _, locket = provision_device(app, manager)
        # Straight into the application, because the test client re-encodes header text and would hide this.
        # Superscript two: str.isdigit() says yes, int() says no.
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.put(
                "/api/device/v1/recordings/sequence-test-01",
                content=make_wav(seconds=1),
                headers={
                    "Authorization": f"Bearer {locket.credential}",
                    "Content-Type": "audio/wav",
                    "X-Content-SHA256": "0" * 64,
                    "X-Recorded-At": "2026-10-20T12:00:00Z",
                    "X-Sequence": bytes([0xB2]),
                },
            )
        assert response.status_code == 422, response.text


class TestTheClientAddressCannotBeChosenByTheClient:
    def settings(self, **changes: object) -> Settings:
        return Settings(  # type: ignore[call-arg]
            _env_file=None, secret_key="x" * 40, trust_forwarded_for=True, **changes
        )

    def test_the_address_the_trusted_proxy_added_is_the_one_used(self) -> None:
        # A client that sends its own X-Forwarded-For gets it passed along, with the real address added after
        # it.
        request = request_with({"x-forwarded-for": "6.6.6.6, 203.0.113.50"})
        assert client_ip(request, self.settings()) == "203.0.113.50"

    def test_two_proxies_in_front_means_one_more_step_back(self) -> None:
        request = request_with({"x-forwarded-for": "6.6.6.6, 203.0.113.50, 10.1.1.1"})
        assert client_ip(request, self.settings(trusted_proxy_hops=2)) == "203.0.113.50"

    def test_a_single_address_is_that_address(self) -> None:
        assert client_ip(request_with({"x-forwarded-for": "203.0.113.50"}), self.settings()) == "203.0.113.50"

    def test_fewer_addresses_than_proxies_uses_the_first_rather_than_failing(self) -> None:
        assert client_ip(
            request_with({"x-forwarded-for": "203.0.113.50"}), self.settings(trusted_proxy_hops=3)
        ) == ("203.0.113.50")

    def test_without_the_header_the_connection_itself_is_used(self) -> None:
        assert client_ip(request_with({}), self.settings()) == "10.0.0.9"
        assert client_ip(request_with({"x-forwarded-for": " , "}), self.settings()) == "10.0.0.9"

    def test_when_the_header_is_not_trusted_it_is_ignored_completely(self) -> None:
        untrusted = Settings(_env_file=None, secret_key="x" * 40, trust_forwarded_for=False)  # type: ignore[call-arg]
        assert client_ip(request_with({"x-forwarded-for": "6.6.6.6"}), untrusted) == "10.0.0.9"

    def test_a_long_value_is_cut_not_stored_whole(self) -> None:
        assert len(client_ip(request_with({"x-forwarded-for": "9" * 500}), self.settings()) or "") == 64


class TestTheRateLimiterDoesNotGrowWithoutEnd:
    def test_keys_whose_window_has_passed_are_forgotten(self) -> None:
        limiter = RateLimiter()
        for n in range(5000):  # five thousand different made-up emails, each hit once
            limiter.check(f"login:{n}", 5, 60, now=1000.0)
        assert len(limiter._hits) > 4000
        # A while later, any activity sweeps away everything that expired.
        limiter.check("login:fresh", 5, 60, now=1100.0)
        assert len(limiter._hits) <= 2

    def test_limits_still_hold_inside_the_window(self) -> None:
        limiter = RateLimiter()
        assert [limiter.check("k", 3, 60, now=float(t)) for t in range(3)] == [None, None, None]
        assert limiter.check("k", 3, 60, now=3.0) is not None
        # Another key with a longer window is not swept early by activity on a short-window key.
        limiter.check("long", 1, 3600, now=0.0)
        for n in range(300):
            limiter.check(f"short:{n}", 1, 1, now=100.0 + n)
        assert limiter.check("long", 1, 3600, now=500.0) is not None
