"""What the API says about processing, and the operator tools around it."""

from __future__ import annotations

import uuid
import wave
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cli, timeutil
from app.ai.registry import capabilities
from app.ai.smoke import smoke_test
from app.ai.stub import build_stub
from app.ai.types import Capabilities
from app.models import Worker
from app.services import workers
from app.services.calibration import calibrate_directory, embed_directory
from app.worker import Worker as WorkerProcess
from tests.ai_doubles import (
    FakeDiarizer,
    FakeEmbedder,
    FakeTranscriber,
    FileEmbedder,
    SequenceEmbedder,
    make_provider,
    two_speaker_turns,
)
from tests.conftest import new_web, provision_device
from tests.helpers import add_company, add_member, make_wav


def manager_web(app):  # type: ignore[no-untyped-def]
    web = new_web(app)
    web.login("manager@acme.example.com")
    return web


def upload(app, seconds: float = 9.0):  # type: ignore[no-untyped-def]
    web = manager_web(app)
    _, locket = provision_device(app, web)
    stored = locket.upload(f"clip-{uuid.uuid4().hex[:12]}", make_wav(seconds))
    assert stored.status_code == 201
    return web, stored.json()["recordingId"]


def run_worker(app, provider) -> None:  # type: ignore[no-untyped-def]
    WorkerProcess(
        app.state.session_factory, app.state.settings, provider, app.state.storage, "worker-test"
    ).run(once=True)


class TestProcessingStatus:
    def test_with_no_worker_it_says_recordings_are_waiting(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web, _ = upload(app, 1.0)
        status = web.get("/api/processing").json()
        assert status["available"] is False and status["workers"] == 0
        assert (status["queued"], status["running"]) == (1, 0)
        assert status["transcription"] is False
        assert any("ASK_AI_PROVIDER" in note for note in status["notes"])  # what to do about it, in words
        assert status["enrollmentAvailable"] is False

    def test_a_worker_that_reports_in_makes_processing_available_and_says_what_it_can_do(
        self,
        app,
        db: Session,
        acme,  # type: ignore[no-untyped-def]
    ) -> None:
        web, _ = upload(app, 1.0)
        workers.beat(db, "worker-a", build_stub().capabilities)

        status = web.get("/api/processing").json()
        assert status["available"] is True and status["workers"] == 1
        assert status["placeholder"] is True  # it is the development stub, and the screens must say so
        assert (status["transcription"], status["diarization"], status["speakerMatching"]) == (
            True,
            False,
            False,
        )
        assert any("not real" in note.lower() for note in status["notes"])

    def test_a_worker_that_went_quiet_is_not_counted(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        workers.beat(db, "worker-a", build_stub().capabilities)
        row = db.scalar(select(Worker))
        assert row is not None
        row.last_seen_at = timeutil.utcnow() - timedelta(seconds=app.state.settings.worker_online_seconds + 5)
        db.commit()
        assert web.get("/api/processing").json()["available"] is False

    def test_counts_are_for_your_company_only(self, app, db: Session, acme, globex) -> None:  # type: ignore[no-untyped-def]
        upload(app, 1.0)
        upload(app, 1.0)
        other = new_web(app)
        other.login("admin@globex.example.com")
        assert other.get("/api/processing").json()["queued"] == 0
        assert manager_web(app).get("/api/processing").json()["queued"] == 2

    def test_everyone_signed_in_may_see_it_but_nobody_else(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        staff = new_web(app)
        staff.login("employee@acme.example.com")
        assert staff.get("/api/processing").status_code == 200
        assert new_web(app).get("/api/processing").status_code == 401

    def test_workers_that_stopped_are_forgotten_after_a_day(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        workers.beat(db, "old", build_stub().capabilities, now=timeutil.utcnow() - timedelta(days=2))
        workers.beat(db, "new", build_stub().capabilities)
        assert workers.prune(db) == 1
        assert [w.worker_id for w in db.scalars(select(Worker))] == ["new"]

    def test_a_degraded_real_worker_reports_each_missing_piece(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        degraded = Capabilities(
            "local", False, True, False, False, notes=("Speaker separation is unavailable: no token.",)
        )
        workers.beat(db, "worker-a", degraded)
        status = web.get("/api/processing").json()
        assert (status["transcription"], status["diarization"], status["speakerMatching"]) == (
            True,
            False,
            False,
        )
        assert status["placeholder"] is False
        assert status["notes"] == ["Speaker separation is unavailable: no token."]


class TestTranscriptsSayWhenTheyAreNotReal:
    def test_placeholder_output_is_flagged_on_the_list_the_detail_and_the_transcript(
        self,
        app,
        db: Session,
        acme,  # type: ignore[no-untyped-def]
    ) -> None:
        web, recording_id = upload(app)
        run_worker(app, build_stub())

        listed = web.get("/api/recordings").json()["items"][0]
        assert listed["placeholder"] is True
        assert listed["snippet"].startswith("[Placeholder")
        detail = web.get(f"/api/recordings/{recording_id}").json()
        assert detail["placeholder"] is True
        assert detail["transcript"]["placeholder"] is True
        assert "placeholder" in detail["transcript"]["transcriptionModel"].lower()
        assert detail["transcript"]["diarizationModel"] is None  # and it says it was not separated
        assert web.get(f"/api/recordings/{recording_id}/transcript").json()["placeholder"] is True

    def test_real_output_is_not_flagged(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web, recording_id = upload(app)
        run_worker(app, make_provider(diarizer=FakeDiarizer(two_speaker_turns())))
        detail = web.get(f"/api/recordings/{recording_id}").json()
        assert detail["placeholder"] is False
        assert detail["transcript"]["placeholder"] is False
        assert detail["transcript"]["language"] == "en"
        assert detail["transcript"]["diarizationModel"] == "test-diarizer"
        assert detail["state"] == "completed"
        assert [s["speakerLabel"] for s in detail["transcript"]["segments"]] == ["SPEAKER_00", "SPEAKER_01"]

    def test_a_recording_with_no_transcript_yet_is_not_a_placeholder(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web, recording_id = upload(app, 1.0)
        assert web.get(f"/api/recordings/{recording_id}").json()["placeholder"] is False

    def test_an_employee_sees_the_flag_on_their_own_locket_only(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web, recording_id = upload(app)
        run_worker(app, build_stub())
        staff = new_web(app)
        staff.login("employee@acme.example.com")
        assert staff.get(f"/api/recordings/{recording_id}").status_code == 404  # not theirs: same as ever
        assert web.get(f"/api/recordings/{recording_id}").json()["placeholder"] is True


class TestWhatTheConfigurationAllows:
    def test_the_placeholder_provider_is_refused_in_production(self) -> None:
        from pydantic import SecretStr, ValidationError

        from app.config import Settings

        with pytest.raises(ValidationError, match="placeholder"):
            Settings(
                _env_file=None,  # type: ignore[call-arg]
                env="production",
                secret_key=SecretStr("a-long-random-secret-for-tests-0123456789"),
                database_url="postgresql+psycopg://u:p@db/ask",
                email_backend="smtp",
                smtp_host="smtp.example.test",
                ai_provider="stub",
            )

    def test_inconsistent_enrollment_and_sample_limits_are_refused(self) -> None:
        from pydantic import SecretStr, ValidationError

        from app.config import Settings

        base = {"_env_file": None, "secret_key": SecretStr("a-long-random-secret-for-tests-0123456789")}
        with pytest.raises(ValidationError, match="MIN_SAMPLES"):
            Settings(**base, voice_enroll_min_samples=5, voice_enroll_max_samples=3)  # type: ignore[arg-type]
        with pytest.raises(ValidationError, match="MIN_SECONDS"):
            Settings(**base, voice_sample_min_seconds=10, voice_sample_max_seconds=5)  # type: ignore[arg-type]

    def test_what_each_provider_can_do_is_known_without_loading_anything(  # type: ignore[no-untyped-def]
        self, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Whatever is installed on this machine, behave as though none of the processing libraries are:
        # the test is about explaining each gap, not about what the developer happens to have installed.
        monkeypatch.setattr("app.ai.local._importable", lambda name: False)
        none = capabilities(settings)
        assert (none.provider, none.transcription, none.placeholder) == ("none", False, False)
        stub = capabilities(settings.model_copy(update={"ai_provider": "stub"}))
        assert (stub.transcription, stub.diarization, stub.speaker_matching, stub.placeholder) == (
            True,
            False,
            False,
            True,
        )
        local = capabilities(settings.model_copy(update={"ai_provider": "local"}))
        assert local.provider == "local" and local.placeholder is False
        # The AI libraries are not installed where the tests run, and each gap is explained.
        assert not local.transcription and any("faster-whisper" in n for n in local.notes)
        assert any("Hugging Face" in n or "pyannote" in n for n in local.notes)


class TestOperatorTools:
    def test_ai_check_without_a_provider_fails_and_explains(
        self,
        settings,
        capsys: pytest.CaptureFixture[str],  # type: ignore[no-untyped-def]
    ) -> None:
        assert cli.ai_check(settings, load=False) == 1
        out = capsys.readouterr().out
        assert "provider" in out and "NO" in out and "ASK_AI_PROVIDER" in out

    def test_ai_check_for_the_stub_says_loudly_that_it_is_not_real(
        self,
        settings,
        capsys: pytest.CaptureFixture[str],  # type: ignore[no-untyped-def]
    ) -> None:
        assert cli.ai_check(settings.model_copy(update={"ai_provider": "stub"}), load=False) == 0
        assert "PLACEHOLDER" in capsys.readouterr().out

    def test_ai_check_can_actually_run_each_model_once(
        self,
        settings,
        capsys: pytest.CaptureFixture[str],  # type: ignore[no-untyped-def]
    ) -> None:
        assert cli.ai_check(settings.model_copy(update={"ai_provider": "stub"}), load=True) == 0
        out = capsys.readouterr().out
        assert "ok" in out and "transcription" in out

    def test_the_smoke_test_reports_each_part_separately_and_keeps_going_after_a_failure(self) -> None:
        provider = make_provider(
            transcriber=FakeTranscriber(
                before=lambda _: (_ for _ in ()).throw(RuntimeError("no weights at /models/x"))
            ),
            diarizer=FakeDiarizer(two_speaker_turns()),
            embedder=SequenceEmbedder([(1, 0, 0)]),
        )
        checks = {c.name: c for c in smoke_test(provider)}
        assert set(checks) == {"transcription", "speaker separation", "speaker matching"}
        assert not checks["transcription"].ok and "/models" not in checks["transcription"].detail
        assert checks["speaker separation"].ok and checks["speaker matching"].ok

    def test_the_smoke_test_catches_a_speaker_model_that_is_not_repeatable(self) -> None:
        wobbly = SequenceEmbedder([(1, 0, 0), (0, 1, 0)])  # the same audio, two unrelated voice prints
        (check,) = (c for c in smoke_test(make_provider(embedder=wobbly)) if c.name == "speaker matching")
        assert not check.ok and "different voice prints" in check.detail

    def make_samples(self, root: Path, layout: dict[str, int]) -> dict[str, tuple[float, ...]]:
        """Folders of tiny .wav files; the fake embedder decides what each one 'sounds like'."""
        prints: dict[str, tuple[float, ...]] = {}
        axes = {name: index for index, name in enumerate(layout)}
        for person, count in layout.items():
            (root / person).mkdir()
            for n in range(count):
                stem = f"{person}-{n}"
                with wave.open(str(root / person / f"{stem}.wav"), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(16000)
                    handle.writeframes(b"\x00\x00" * 100)
                vector = [0.05 * n, 0.0, 0.0, 0.0]
                vector[axes[person]] += 1.0
                prints[stem] = tuple(vector)
        return prints

    def test_calibration_reads_a_folder_per_person_and_suggests_a_threshold(self, tmp_path: Path) -> None:
        prints = self.make_samples(tmp_path, {"ana": 6, "bo": 6, "cy": 6})
        report = calibrate_directory(FileEmbedder(prints), tmp_path, 0.0)
        assert (report.people, report.samples) == (3, 18)
        assert report.result.false_accept_rate == 0.0
        assert 0 < report.result.threshold < 1
        assert report.result.same_pairs == 3 * 15 and report.result.different_pairs == 3 * 36

    def test_calibration_warns_when_there_is_too_little_to_trust(self, tmp_path: Path) -> None:
        prints = self.make_samples(tmp_path, {"ana": 2, "bo": 2})
        report = calibrate_directory(FileEmbedder(prints), tmp_path, 0.01)
        text = " ".join(report.warnings)
        assert "Fewer than" in text and "comparisons" in text and "Only a few people" in text

    def test_calibration_explains_bad_input(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not a folder"):
            embed_directory(FileEmbedder({}), tmp_path / "missing")
        with pytest.raises(ValueError, match="No folders"):
            embed_directory(FileEmbedder({}), tmp_path)
        (tmp_path / "ana").mkdir()
        with pytest.raises(ValueError, match="No folders"):
            embed_directory(FileEmbedder({}), tmp_path)  # a person folder with no recordings

    def test_the_calibrate_command_refuses_a_provider_with_no_speaker_model(
        self,
        settings,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],  # type: ignore[no-untyped-def]
    ) -> None:
        stub_settings = settings.model_copy(update={"ai_provider": "stub"})
        assert cli.calibrate_speakers(stub_settings, tmp_path, 0.01) == 1
        assert "no speaker model" in capsys.readouterr().err

    def test_the_calibrate_command_prints_what_to_set(
        self,
        settings,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],  # type: ignore[no-untyped-def]
    ) -> None:
        prints = self.make_samples(tmp_path, {"ana": 6, "bo": 6, "cy": 6})
        provider = make_provider(embedder=FileEmbedder(prints))  # type: ignore[arg-type]
        monkeypatch.setattr("app.ai.registry.build_provider", lambda _settings: provider)
        assert cli.calibrate_speakers(settings, tmp_path, 0.0) == 0
        out = capsys.readouterr().out
        assert "ASK_SPEAKER_MATCH_THRESHOLD=" in out and "Suggested threshold" in out


def test_a_worker_row_never_holds_anything_but_what_it_can_do(app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
    workers.beat(db, "worker-a", make_provider(embedder=FakeEmbedder({0: (1, 0, 0)})).capabilities)
    row = db.scalar(select(Worker))
    assert row is not None
    assert set(row.capabilities) == {"transcription", "diarization", "speakerMatching", "notes"}
    assert add_company and add_member  # (helpers kept imported for readability of the fixtures above)
