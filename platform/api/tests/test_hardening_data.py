"""Regression tests for the Phase F security review, part three: consent against concurrent work, one-time
tokens, audio access, upload validation cost, retention, and what ends up in logs. Each was run against the
code before the fix that goes with it."""

from __future__ import annotations

import io
import logging
import struct
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ai.registry import ProviderHolder
from app.config import Settings
from app.db import make_engine, make_session_factory
from app.errors import ApiError
from app.models import (
    AuditEvent,
    Device,
    DeviceCredential,
    EmailToken,
    Recording,
    TranscriptSegment,
    VoiceProfile,
)
from app.services import accounts, audio, devices
from app.services import recordings as recording_service
from app.services.jobs import sanitize_error
from app.services.matching import encode_vector, normalize
from app.worker import Worker
from tests.ai_doubles import (
    TEST_VOICE_MODEL,
    FakeDiarizer,
    FakeEmbedder,
    SequenceEmbedder,
    make_provider,
    two_speaker_turns,
)
from tests.conftest import new_web, provision_device
from tests.helpers import add_company, add_member, add_person, make_wav


def manager_web(app, email: str = "manager@acme.example.com"):  # type: ignore[no-untyped-def]
    web = new_web(app)
    assert web.login(email).status_code == 200
    return web


class TestConsentCannotBeOutrunByWorkAlreadyUnderWay:
    """Consent is checked when work starts. If it is withdrawn while a voice sample is being turned into a
    print,
    or while a recording is being matched, that work used to land anyway: a voice print for someone who had
    just
    said no, or their name on a new transcript after their old lines had been unnamed."""

    def test_a_voice_sample_is_not_kept_if_consent_is_withdrawn_while_it_is_processed(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)

        class WithdrawsConsentMidway(SequenceEmbedder):
            def embed(self, audio, spans=None):  # type: ignore[no-untyped-def]
                reply = web.post(f"/api/people/{ana.id}/consent", json={"granted": False})
                assert reply.status_code == 200
                return super().embed(audio, spans)

        app.state.ai = ProviderHolder(app.state.settings, make_provider(embedder=WithdrawsConsentMidway()))
        response = web.post(
            f"/api/people/{ana.id}/enroll", content=make_wav(4.0), headers={"Content-Type": "audio/wav"}
        )

        assert response.status_code == 409 and response.json()["error"]["code"] == "consent_required"
        db.expire_all()
        assert db.scalar(select(VoiceProfile).where(VoiceProfile.person_id == ana.id)) is None

    def test_a_transcript_does_not_name_someone_whose_consent_was_withdrawn_during_matching(
        self,
        app,
        db: Session,
        acme,  # type: ignore[no-untyped-def]
    ) -> None:
        manager = manager_web(app)
        _, locket = provision_device(app, manager, "Race locket")
        stored = locket.upload(f"clip-{uuid.uuid4().hex[:12]}", make_wav(9.0))
        recording_id = uuid.UUID(stored.json()["recordingId"])
        ana = add_person(db, acme["company"], "Ana")
        unit = normalize((1, 0, 0))
        db.add(
            VoiceProfile(
                person_id=ana.id,
                company_id=ana.company_id,
                embedding=encode_vector(unit),
                embedding_dim=3,
                model_name=TEST_VOICE_MODEL,
                model_version="1.0",
                sample_count=3,
            )
        )
        db.commit()

        class WithdrawsConsentMidway(FakeEmbedder):
            def embed(self, audio, spans=None):  # type: ignore[no-untyped-def]
                manager.post(f"/api/people/{ana.id}/consent", json={"granted": False})
                return super().embed(audio, spans)

        embedder = WithdrawsConsentMidway({0: (1, 0, 0), 3500: (0, 1, 0)})
        provider = make_provider(diarizer=FakeDiarizer(two_speaker_turns()), embedder=embedder)
        assert (
            Worker(app.state.session_factory, app.state.settings, provider, app.state.storage, "w").run(
                once=True
            )
            == 1
        )

        db.expire_all()
        saved = list(db.scalars(select(TranscriptSegment).join(Recording, Recording.id == recording_id)))
        assert saved, "the recording should still have been transcribed"
        assert all(s.person_id is None and s.confidence is None for s in saved)
        # ...and no lingering voice print either, since withdrawing consent deletes it.
        assert db.scalar(select(VoiceProfile).where(VoiceProfile.person_id == ana.id)) is None


class TestOneTimeTokensAreUsedOnce:
    """Check-then-set let two requests that arrive together both pass the check. The second must lose."""

    def test_a_password_reset_link_works_for_only_one_of_two_simultaneous_requests(self, file_app) -> None:  # type: ignore[no-untyped-def]
        factory = make_session_factory(file_app.state.engine)
        with factory() as setup:
            user = add_member(setup, add_company(setup, "Race Co"), "racer@race.example.com", "employee")
            raw = accounts.issue_email_token(setup, user, "password_reset", timedelta(hours=1))
            setup.commit()

        with factory() as first, factory() as second:
            real_scalar = first.scalar
            interleaved = {"done": False}

            def racing(statement, *args, **kwargs):  # type: ignore[no-untyped-def]
                row = real_scalar(statement, *args, **kwargs)
                if isinstance(row, EmailToken) and not interleaved["done"]:
                    interleaved["done"] = True  # the other request uses the link after this one looked at it
                    accounts.consume_email_token(second, raw, "password_reset")
                    second.commit()
                return row

            first.scalar = racing  # type: ignore[method-assign]
            with pytest.raises(ApiError) as lost:
                accounts.consume_email_token(first, raw, "password_reset")
            assert lost.value.code == "invalid_token"

    def test_a_pairing_code_makes_one_credential_not_two(self, file_app) -> None:  # type: ignore[no-untyped-def]
        factory = make_session_factory(file_app.state.engine)
        with factory() as setup:
            add_member(setup, add_company(setup, "Pair Co"), "boss@pair.example.com", "manager")
        manager = new_web(file_app)
        assert manager.login("boss@pair.example.com").status_code == 200
        code = manager.post("/api/devices", json={"name": "Raced locket"}).json()["code"]
        settings = file_app.state.settings

        with factory() as first, factory() as second:
            real_get = first.get
            interleaved = {"done": False}

            def racing(entity, ident, *args, **kwargs):  # type: ignore[no-untyped-def]
                row = real_get(entity, ident, *args, **kwargs)
                if entity is Device and not interleaved["done"]:
                    # The first request has seen a code that is unused and a locket that is pending. The
                    # second
                    # request now uses the code, completely, before the first goes on to use it as well.
                    interleaved["done"] = True
                    devices.redeem_pairing(second, settings, code, None, None)
                    second.commit()
                return row

            first.get = racing  # type: ignore[method-assign]
            with pytest.raises(ApiError) as lost:
                devices.redeem_pairing(first, settings, code, None, None)
            assert lost.value.code == "invalid_pairing_code"
        with factory() as check:
            assert len(check.scalars(select(DeviceCredential)).all()) == 1  # one credential, not two


class TestAudioAccessIsRecorded:
    """Only `?download=true` was audited and restricted, but the same bytes stream from the plain URL to
    anyone
    who can see the recording, so a script could take every recording without leaving a trace."""

    def count(self, db: Session, action: str) -> int:
        db.expire_all()
        return len(db.scalars(select(AuditEvent).where(AuditEvent.action == action)).all())

    def test_starting_to_listen_is_audited_with_who_and_which_recording(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        manager = manager_web(app)
        _, locket = provision_device(app, manager, "Audit locket")
        recording_id = locket.upload("clip-audit-0001", make_wav(2.0)).json()["recordingId"]

        assert manager.get(f"/api/recordings/{recording_id}/audio").status_code == 200
        db.expire_all()
        event = db.scalars(select(AuditEvent).where(AuditEvent.action == "recording.played")).one()
        assert event.actor_user_id == acme["manager"].id and event.object_id == recording_id

    def test_scrubbing_through_a_clip_does_not_flood_the_log(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        manager = manager_web(app)
        _, locket = provision_device(app, manager, "Scrub locket")
        recording_id = locket.upload("clip-audit-0002", make_wav(2.0)).json()["recordingId"]
        url = f"/api/recordings/{recording_id}/audio"

        manager.get(url, headers={"Range": "bytes=0-"})  # a player starting
        for start in (100, 5000, 20000):  # ...then reading on as it plays or is scrubbed
            assert manager.get(url, headers={"Range": f"bytes={start}-"}).status_code == 206
        assert self.count(db, "recording.played") == 1

    def test_a_download_is_still_recorded_as_a_download(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        manager = manager_web(app)
        _, locket = provision_device(app, manager, "Download locket")
        recording_id = locket.upload("clip-audit-0003", make_wav(2.0)).json()["recordingId"]
        manager.get(f"/api/recordings/{recording_id}/audio?download=true")
        assert self.count(db, "recording.downloaded") == 1 and self.count(db, "recording.played") == 0


def riff_with_empty_chunks(count: int) -> bytes:
    """A valid 16 kHz mono clip with `count` zero-length chunks in front of the audio."""
    audio_bytes = make_wav(1.0)[44:]
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16)
    junk = b"JUNK" + struct.pack("<I", 0)
    data = b"data" + struct.pack("<I", len(audio_bytes)) + audio_bytes
    body = b"WAVE" + fmt + junk * count + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


class TestAudioValidationIsBounded:
    def test_a_file_made_of_thousands_of_empty_sections_is_refused_quickly(
        self, tmp_path: Path, settings: Settings
    ) -> None:
        path = tmp_path / "many.wav"
        path.write_bytes(riff_with_empty_chunks(200_000))  # about 1.6 MB: 25 MiB of these took seconds of CPU
        started = time.perf_counter()
        with pytest.raises(ApiError) as caught:
            audio.inspect_wav(path, settings)
        assert time.perf_counter() - started < 0.5
        assert caught.value.code == "invalid_audio"

    def test_ordinary_extra_sections_are_still_fine(self, tmp_path: Path, settings: Settings) -> None:
        path = tmp_path / "few.wav"
        path.write_bytes(riff_with_empty_chunks(3))  # a LIST or other metadata chunk is normal
        assert audio.inspect_wav(path, settings).duration_ms == 1000


class TestRetentionCountsFromWhenItWasStored:
    def test_a_clip_with_an_old_recording_time_is_not_deleted_the_moment_it_arrives(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        # The recording time is whatever the locket says. A wrong clock, or a bad actor, must not be able to
        # make a
        # clip count as years old and have it swept away at the next housekeeping pass.
        manager = manager_web(app)
        admin = manager_web(app, "admin@acme.example.com")
        admin.patch("/api/settings", json={"retentionDays": 30})
        _, locket = provision_device(app, manager, "Old clock")
        recorded_at = (datetime.now(UTC) - timedelta(days=400)).isoformat()
        stored = locket.upload("clip-old-clock-1", make_wav(2.0), recorded_at=recorded_at)
        assert stored.status_code == 201, stored.text

        assert recording_service.enforce_retention(db) == 0
        db.expire_all()
        recording = db.scalar(select(Recording))
        assert recording is not None and recording.deleted_at is None

    def test_a_clip_stored_long_ago_is_deleted(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        manager = manager_web(app)
        admin = manager_web(app, "admin@acme.example.com")
        admin.patch("/api/settings", json={"retentionDays": 30})
        _, locket = provision_device(app, manager, "Old store")
        locket.upload("clip-old-store-1", make_wav(2.0))
        db.execute(update(Recording).values(uploaded_at=datetime.now(UTC) - timedelta(days=45)))
        db.commit()
        assert recording_service.enforce_retention(db) == 1


class TestNothingPrivateIsWrittenToLogs:
    def test_an_unexpected_error_logs_where_it_happened_not_what_it_said(
        self, app, caplog: pytest.LogCaptureFixture
    ) -> None:  # type: ignore[no-untyped-def]
        # The message of an unexpected exception can hold anything: a request value, a file path, a database
        # row.
        # Built here, not written out: a traceback prints source lines, and the test must show the *message*
        # is
        # what is kept out of the log.
        secret, where, who = (
            "secret-" + "value-9917",
            "C:/recordings/acme/x" + ".wav",
            "ceo@victim" + ".example.com",
        )

        def boom() -> None:
            raise RuntimeError(f"{secret} at {where} for {who}")

        app.add_api_route("/api/_boom", boom)
        from fastapi.testclient import TestClient

        with caplog.at_level(logging.ERROR), TestClient(app, raise_server_exceptions=False) as client:
            assert client.get("/api/_boom").status_code == 500
        text = caplog.text
        assert "RuntimeError" in text and "boom" in text  # enough to find the bug
        for private in ("secret-value-9917", "x.wav", "victim.example.com"):
            assert private not in text

    def test_database_errors_do_not_carry_the_values_that_were_being_stored(self) -> None:
        engine = make_engine("sqlite://")
        assert engine.hide_parameters is True

    def test_a_stored_failure_message_has_no_query_or_values_in_it(self) -> None:
        raw = (
            "(sqlite3.IntegrityError) UNIQUE constraint failed: voice_profiles.person_id\n"
            "[SQL: INSERT INTO voice_profiles (embedding, person_id) VALUES (?, ?)]\n"
            "[parameters: (<memory at 0x7f>, 'b0d1c2e3-aaaa-bbbb-cccc-0123456789ab')]"
        )
        cleaned = sanitize_error(raw)
        assert "b0d1c2e3" not in cleaned and "INSERT INTO" not in cleaned and "memory at" not in cleaned
        assert "UNIQUE constraint failed" in cleaned  # the useful part is kept


class TestLeftoverUploadsAreCleanedUp:
    def test_housekeeping_removes_old_partial_uploads_and_leaves_recent_ones(
        self, app, settings: Settings
    ) -> None:  # type: ignore[no-untyped-def]
        from app.services.housekeeping import run_housekeeping

        storage = app.state.storage
        old, recent = storage.new_temp_path(), storage.new_temp_path()
        for path in (old, recent):
            path.write_bytes(b"x" * 10)
        long_ago = time.time() - 7200
        import os

        os.utime(old, (long_ago, long_ago))

        results = run_housekeeping(app.state.session_factory, settings, storage)

        assert results["partial_uploads_removed"] == 1
        assert not old.exists() and recent.exists()  # an upload in progress is never touched
        recent.unlink()
        assert io.StringIO is not None  # (keeps the import used by the helpers above honest)
