from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base, make_engine, make_session_factory
from app.errors import ApiError
from app.main import create_app
from app.models import Device, LiveEvent, ProcessingJob, Recording
from app.services.email import MemoryEmailSender
from app.services.storage import Storage
from app.services.uploads import spool_to_disk
from tests.conftest import new_web, provision_device
from tests.helpers import DeviceClient, make_wav


def manager(app):  # type: ignore[no-untyped-def]
    web = new_web(app)
    web.login("manager@acme.example.com")
    return web


@pytest.fixture
def locket(app, acme):  # type: ignore[no-untyped-def]
    device, client = provision_device(app, manager(app))
    return device, client


def error_code(response) -> str:  # type: ignore[no-untyped-def]
    return response.json()["error"]["code"]


def temp_files(settings: Settings) -> list[Path]:
    return list((Path(settings.storage_dir) / ".tmp").glob("*"))


class TestHappyPath:
    def test_a_clip_is_stored_durably_and_acknowledged(self, app, settings, db: Session, locket) -> None:  # type: ignore[no-untyped-def]
        _device, client = locket
        wav = make_wav(2.0)
        response = client.upload("clip-0001-aaaa", wav)
        assert response.status_code == 201
        ack = response.json()
        assert ack["status"] == "stored"
        assert ack["duplicate"] is False
        assert ack["sha256"] == hashlib.sha256(wav).hexdigest()
        assert ack["bytes"] == len(wav)
        assert ack["durationMs"] == 2000

        recording = db.scalar(select(Recording))
        assert recording is not None
        assert str(recording.id) == ack["recordingId"]
        assert recording.state == "queued"
        assert recording.sample_rate == 16000 and recording.channels == 1 and recording.bits_per_sample == 16
        assert recording.ended_at - recording.started_at == timedelta(seconds=2)
        stored = Storage(settings.storage_dir).path_for(recording.storage_key)
        assert stored.read_bytes() == wav  # the exact bytes the locket sent
        assert str(recording.company_id) in recording.storage_key
        assert temp_files(settings) == []

    def test_a_job_event_and_device_timestamp_are_recorded_with_it(self, app, db: Session, locket) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        client.upload("clip-0001-aaaa", make_wav())
        assert db.scalar(select(func.count()).select_from(ProcessingJob)) == 1
        job = db.scalar(select(ProcessingJob))
        assert job is not None and job.status == "queued"
        assert db.scalar(select(Device.last_upload_at)) is not None
        assert "recording.uploaded" in set(db.scalars(select(LiveEvent.type)))

    def test_the_api_never_exposes_the_storage_path(self, app, locket) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        body = client.upload("clip-0001-aaaa", make_wav()).text
        assert "storage" not in body.lower() and ".wav" not in body

    def test_status_lets_a_locket_confirm_a_clip_before_archiving_it(self, app, locket) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        assert client.request("GET", "/api/device/v1/recordings/clip-0001-aaaa").status_code == 404
        client.upload("clip-0001-aaaa", make_wav())
        confirmed = client.request("GET", "/api/device/v1/recordings/clip-0001-aaaa")
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "stored"

    def test_multi_file_recordings_keep_their_order(self, app, db: Session, locket) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        base = datetime(2026, 10, 25, 9, 0, 0, tzinfo=UTC)
        # Arrive out of order; the sequence and timestamps preserve the real order.
        for sequence in (2, 0, 1):
            when = (base + timedelta(seconds=10 * sequence)).isoformat()
            response = client.upload(
                f"meeting-part-{sequence:04d}",
                make_wav(1.0, frequency=300 + sequence * 50),
                recorded_at=when,
                extra={"X-Session-Ref": "meeting-2026-10-25", "X-Sequence": str(sequence)},
            )
            assert response.status_code == 201
        rows = db.scalars(
            select(Recording)
            .where(Recording.session_ref == "meeting-2026-10-25")
            .order_by(Recording.sequence)
        ).all()
        assert [r.sequence for r in rows] == [0, 1, 2]
        assert [r.started_at for r in rows] == sorted(r.started_at for r in rows)


class TestIdempotency:
    def test_repeating_an_upload_stores_nothing_twice(self, app, settings, db: Session, locket) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        wav = make_wav()
        first = client.upload("clip-0001-aaaa", wav)
        second = client.upload("clip-0001-aaaa", wav)
        third = client.upload("clip-0001-aaaa", wav)
        assert (first.status_code, second.status_code, third.status_code) == (201, 200, 200)
        assert second.json()["duplicate"] is True
        assert first.json()["recordingId"] == second.json()["recordingId"] == third.json()["recordingId"]
        assert db.scalar(select(func.count()).select_from(Recording)) == 1
        assert db.scalar(select(func.count()).select_from(ProcessingJob)) == 1
        stored = list(Path(settings.storage_dir).rglob("*.wav"))
        assert len(stored) == 1
        assert temp_files(settings) == []

    def test_a_different_clip_under_a_used_id_is_a_conflict_and_changes_nothing(
        self, app, settings, db: Session, locket
    ) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        original = make_wav(1.0, frequency=300)
        client.upload("clip-0001-aaaa", original)
        clash = client.upload("clip-0001-aaaa", make_wav(1.0, frequency=900))
        assert clash.status_code == 409
        assert error_code(clash) == "recording_conflict"
        recording = db.scalar(select(Recording))
        assert recording is not None
        assert Storage(settings.storage_dir).path_for(recording.storage_key).read_bytes() == original
        assert len(list(Path(settings.storage_dir).rglob("*.wav"))) == 1

    def test_two_lockets_may_use_the_same_recording_id(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        web = manager(app)
        _, one = provision_device(app, web, "One")
        _, two = provision_device(app, web, "Two")
        assert one.upload("clip-0001-aaaa", make_wav(1.0, frequency=300)).status_code == 201
        assert two.upload("clip-0001-aaaa", make_wav(1.0, frequency=500)).status_code == 201
        assert db.scalar(select(func.count()).select_from(Recording)) == 2

    def test_a_raced_duplicate_resolves_to_one_recording(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two uploads of one clip pass the "already stored?" check together; the database decides."""
        engine = make_engine(f"sqlite:///{(tmp_path / 'race.sqlite3').as_posix()}")
        Base.metadata.create_all(engine)
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            env="test",
            secret_key=SecretStr("race-secret-key-0123456789-abcdefghijklmnop"),
            database_url="sqlite://",
            storage_dir=tmp_path / "recordings",
            email_backend="memory",
            run_background_tasks=False,
        )
        app = create_app(settings, engine=engine, email=MemoryEmailSender())
        factory = make_session_factory(engine)
        from tests.helpers import add_company, add_member

        with factory() as seed:
            company = add_company(seed, "Acme")
            add_member(seed, company, "manager@acme.example.com", "manager")
        web = new_web(app)
        web.login("manager@acme.example.com")
        _, client = provision_device(app, web)

        import app.services.uploads as uploads_module

        real_inspect = uploads_module.inspect_wav
        raced = {"done": False}

        def inspect_then_lose_the_race(path, cfg):  # type: ignore[no-untyped-def]
            info = real_inspect(path, cfg)
            if not raced["done"]:
                raced["done"] = True
                # Meanwhile, another request stores the same clip.
                other = TestClient(app)
                DeviceClient(other, client.credential).upload("clip-0001-aaaa", wav)
            return info

        wav = make_wav()
        monkeypatch.setattr(uploads_module, "inspect_wav", inspect_then_lose_the_race)
        response = client.upload("clip-0001-aaaa", wav)
        assert response.status_code == 200  # ours was the redundant one
        assert response.json()["duplicate"] is True
        with factory() as check:
            assert check.scalar(select(func.count()).select_from(Recording)) == 1
        assert len(list((tmp_path / "recordings").rglob("*.wav"))) == 1
        assert list((tmp_path / "recordings" / ".tmp").glob("*")) == []


class TestIntegrity:
    def test_a_body_that_does_not_match_its_checksum_is_refused(
        self, app, settings, db: Session, locket
    ) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        wav = make_wav()
        corrupted = wav[:-10] + b"\x00" * 10
        response = client.upload("clip-0001-aaaa", corrupted, sha256=hashlib.sha256(wav).hexdigest())
        assert response.status_code == 422
        assert error_code(response) == "checksum_mismatch"
        assert db.scalar(select(func.count()).select_from(Recording)) == 0
        assert temp_files(settings) == [] and list(Path(settings.storage_dir).rglob("*.wav")) == []

    @pytest.mark.parametrize(
        ("build", "status", "code"),
        [
            pytest.param(lambda: b"this is plainly not audio" * 50, 422, "invalid_audio", id="not-a-wav"),
            pytest.param(
                lambda: make_wav(1.0, rate=44100), 422, "unsupported_sample_rate", id="wrong-sample-rate"
            ),
            pytest.param(lambda: make_wav(1.0, bits=8), 422, "unsupported_audio_format", id="8-bit"),
            pytest.param(lambda: make_wav(0.1), 422, "audio_too_short", id="too-short"),
            pytest.param(lambda: make_wav(1.0, silent=True), 422, "silent_audio", id="silence-only"),
        ],
    )
    def test_unusable_audio_is_refused_with_a_reason(
        self, app, settings, db: Session, locket, build, status, code
    ) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        response = client.upload("clip-0001-aaaa", build())
        assert response.status_code == status
        assert error_code(response) == code
        assert db.scalar(select(func.count()).select_from(Recording)) == 0
        assert temp_files(settings) == []

    def test_a_clip_cut_short_by_a_power_loss_is_not_accepted(
        self, app, settings, db: Session, locket
    ) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        wav = make_wav(2.0)
        response = client.upload("clip-0001-aaaa", wav[: len(wav) // 2])
        assert response.status_code == 422
        assert error_code(response) == "truncated_audio"
        assert db.scalar(select(func.count()).select_from(Recording)) == 0

    def test_stereo_is_accepted_and_described(self, app, db: Session, locket) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        assert client.upload("clip-0001-aaaa", make_wav(1.0, channels=2)).status_code == 201
        recording = db.scalar(select(Recording))
        assert recording is not None and recording.channels == 2 and "stereo" in recording.format

    def test_headers_are_validated(self, app, locket) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        wav = make_wav()
        assert client.upload("clip-0001-aaaa", wav, sha256="").status_code == 422
        assert client.upload("clip-0001-aaaa", wav, recorded_at="yesterday").status_code == 422
        assert (
            client.upload("clip-0001-aaaa", wav, recorded_at="2026-10-25T09:00:00").status_code == 422
        )  # no timezone
        future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        assert client.upload("clip-0001-aaaa", wav, recorded_at=future).status_code == 422
        assert client.upload("clip-0001-aaaa", wav, content_type="text/plain").status_code == 415
        assert client.upload("clip-0001-aaaa", wav, extra={"X-Sequence": "-1"}).status_code == 422
        assert client.upload("clip-0001-aaaa", wav, extra={"X-Session-Ref": "bad ref!"}).status_code == 422

    @pytest.mark.parametrize(
        "bad_id", ["short", "has space in it", "semi;colon-id", "a" * 65, "caf%C3%A9-clip-1"]
    )
    def test_recording_ids_are_restricted_so_they_cannot_shape_paths(self, app, locket, bad_id: str) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        assert client.upload(bad_id, make_wav()).status_code == 422

    def test_empty_bodies_are_refused(self, app, settings, locket) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        assert client.upload("clip-0001-aaaa", b"").status_code == 422
        assert temp_files(settings) == []


class TestLimitsAndFailures:
    def small_app(self, tmp_path: Path, **overrides):  # type: ignore[no-untyped-def]
        engine = make_engine("sqlite://")
        Base.metadata.create_all(engine)
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            env="test",
            secret_key=SecretStr("limits-secret-key-0123456789-abcdefghijkl"),
            database_url="sqlite://",
            storage_dir=tmp_path / "recordings",
            email_backend="memory",
            run_background_tasks=False,
            **overrides,
        )
        app = create_app(settings, engine=engine, email=MemoryEmailSender())
        with make_session_factory(engine)() as seed:
            from tests.helpers import add_company, add_member

            add_member(seed, add_company(seed, "Acme"), "manager@acme.example.com", "manager")
        web = new_web(app)
        web.login("manager@acme.example.com")
        _, client = provision_device(app, web)
        return settings, client

    def test_oversized_uploads_are_cut_off_without_leaving_files(self, tmp_path: Path) -> None:
        settings, client = self.small_app(tmp_path, max_upload_bytes=4096)
        big = make_wav(2.0)
        response = client.upload("clip-0001-aaaa", big)
        assert response.status_code == 413
        assert error_code(response) == "upload_too_large"
        assert list((Path(settings.storage_dir) / ".tmp").glob("*")) == []

    def test_clips_longer_than_the_limit_are_refused(self, tmp_path: Path) -> None:
        _, client = self.small_app(tmp_path, max_clip_seconds=2)
        response = client.upload("clip-0001-aaaa", make_wav(3.0))
        assert response.status_code == 413
        assert error_code(response) == "audio_too_long"

    def test_a_database_failure_after_the_file_is_written_leaves_no_trace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings, client = self.small_app(tmp_path)
        import app.services.uploads as uploads_module

        def explode(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("database went away")

        monkeypatch.setattr(uploads_module, "publish_event", explode)
        # TestClient re-raises server errors by default; ask for the real 500 response instead.
        client.client = TestClient(client.client.app, raise_server_exceptions=False)
        response = client.upload("clip-0001-aaaa", make_wav())
        assert response.status_code == 500
        assert error_code(response) == "internal_error"
        assert "database went away" not in response.text  # internals are never exposed
        assert (
            list(Path(settings.storage_dir).rglob("*.wav")) == []
        )  # nothing was acknowledged, nothing is kept
        assert list((Path(settings.storage_dir) / ".tmp").glob("*")) == []

        monkeypatch.undo()
        retry = client.upload("clip-0001-aaaa", make_wav())
        assert retry.status_code == 201  # the retry works normally

    def test_an_upload_that_is_interrupted_leaves_nothing_behind(self, tmp_path: Path) -> None:
        storage = Storage(tmp_path / "recordings")

        async def broken() -> AsyncIterator[bytes]:
            yield b"RIFF" + b"\x00" * 5000
            raise ConnectionResetError("client went away")

        with pytest.raises(ConnectionResetError):
            asyncio.run(spool_to_disk(broken(), storage, max_bytes=10_000_000, read_timeout=5))
        assert list(storage.tmp.glob("*")) == []

    def test_an_upload_that_stalls_is_cancelled(self, tmp_path: Path) -> None:
        storage = Storage(tmp_path / "recordings")

        async def stalled() -> AsyncIterator[bytes]:
            yield b"RIFF" + b"\x00" * 100
            await asyncio.sleep(5)
            yield b"never"

        with pytest.raises(ApiError) as caught:
            asyncio.run(spool_to_disk(stalled(), storage, max_bytes=10_000_000, read_timeout=0.1))
        assert caught.value.status == 408 and caught.value.code == "upload_timeout"
        assert list(storage.tmp.glob("*")) == []

    def test_stale_partial_uploads_are_cleaned_up(self, tmp_path: Path) -> None:
        import os
        import time

        storage = Storage(tmp_path / "recordings")
        stale, fresh = storage.new_temp_path(), storage.new_temp_path()
        stale.write_bytes(b"x")
        fresh.write_bytes(b"x")
        old = time.time() - 7200
        os.utime(stale, (old, old))
        assert storage.remove_stale_temp_files(3600) == 1
        assert not stale.exists() and fresh.exists()

    def test_storage_refuses_keys_that_escape_the_root(self, tmp_path: Path) -> None:
        from app.services.storage import StorageError

        storage = Storage(tmp_path / "recordings")
        with pytest.raises(StorageError):
            storage.path_for("../outside.wav")


class TestAuthorization:
    def test_only_a_paired_locket_can_upload(self, app, acme, locket) -> None:  # type: ignore[no-untyped-def]
        _, client = locket
        wav = make_wav()
        assert DeviceClient(TestClient(app)).upload("clip-0001-aaaa", wav).status_code == 401
        assert client.upload("clip-0001-aaaa", wav, credential="ASK-XXXXXX.nope").status_code == 401
        # A signed-in browser session is not a device.
        web = manager(app)
        assert (
            web.request(
                "PUT",
                "/api/device/v1/recordings/clip-0001-aaaa",
                content=wav,
                csrf=False,
                headers={
                    "Content-Type": "audio/wav",
                    "X-Content-SHA256": hashlib.sha256(wav).hexdigest(),
                    "X-Recorded-At": datetime.now(UTC).isoformat(),
                },
            ).status_code
            == 401
        )

    def test_a_revoked_locket_cannot_upload(self, app, acme, locket) -> None:  # type: ignore[no-untyped-def]
        device, client = locket
        manager(app).post(f"/api/devices/{device['id']}/revoke")
        assert client.upload("clip-0001-aaaa", make_wav()).status_code == 401
