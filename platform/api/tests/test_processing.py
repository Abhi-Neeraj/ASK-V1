"""Processing: the durable queue, the pipeline's states and results, retries, and the worker."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timeutil
from app.ai.stub import build_stub
from app.ai.types import AsrSegment, PermanentError, Word
from app.db import make_session_factory
from app.models import (
    AuditEvent,
    LiveEvent,
    Person,
    ProcessingJob,
    Recording,
    Transcript,
    TranscriptSegment,
    VoiceProfile,
)
from app.services import jobs
from app.services.jobs import PUBLIC_FAILURE, retry_delay, sanitize_error
from app.services.matching import encode_vector, normalize
from app.worker import Worker
from tests.ai_doubles import (
    TEST_VOICE_MODEL,
    FakeDiarizer,
    FakeEmbedder,
    FakeTranscriber,
    default_segments,
    make_provider,
    two_speaker_turns,
)
from tests.conftest import new_web, provision_device
from tests.helpers import add_company, add_member, add_person, make_wav


def sign_in_manager(app):  # type: ignore[no-untyped-def]
    web = new_web(app)
    web.login("manager@acme.example.com")
    return web


def upload(app, seconds: float = 9.0):  # type: ignore[no-untyped-def]
    """Store a clip the way a locket does, so there is a real file, recording and queued job."""
    manager = sign_in_manager(app)
    device, locket = provision_device(app, manager, f"Locket {uuid.uuid4().hex[:4]}")
    stored = locket.upload(f"clip-{uuid.uuid4().hex[:12]}", make_wav(seconds))
    assert stored.status_code == 201, stored.text
    return uuid.UUID(stored.json()["recordingId"]), device


def worker_for(app, provider, name: str = "worker-a") -> Worker:  # type: ignore[no-untyped-def]
    return Worker(app.state.session_factory, app.state.settings, provider, app.state.storage, name)


def states_of(db: Session, recording_id: uuid.UUID) -> list[str]:
    db.expire_all()
    rows = db.scalars(select(LiveEvent).where(LiveEvent.type == "recording.state").order_by(LiveEvent.id))
    return [e.payload["state"] for e in rows if e.payload["recordingId"] == str(recording_id)]


def reload(db: Session, recording_id: uuid.UUID) -> tuple[Recording, ProcessingJob]:
    db.expire_all()
    recording = db.get(Recording, recording_id)
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.recording_id == recording_id))
    assert recording is not None and job is not None
    return recording, job


def segments_of(db: Session, recording_id: uuid.UUID) -> list[TranscriptSegment]:
    db.expire_all()
    return list(
        db.scalars(
            select(TranscriptSegment)
            .join(Transcript, Transcript.id == TranscriptSegment.transcript_id)
            .where(Transcript.recording_id == recording_id)
            .order_by(TranscriptSegment.idx)
        )
    )


def enroll(db: Session, person: Person, vector, *, model: str = TEST_VOICE_MODEL, samples: int = 3) -> None:  # type: ignore[no-untyped-def]
    unit = normalize(vector)
    db.add(
        VoiceProfile(
            person_id=person.id,
            company_id=person.company_id,
            embedding=encode_vector(unit),
            embedding_dim=len(unit),
            model_name=model,
            model_version="1.0",
            sample_count=samples,
        )
    )
    db.commit()


# --- the queue ---------------------------------------------------------------------------------------


class TestQueue:
    def test_a_new_upload_is_queued_and_a_worker_claims_it_exactly_once(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app, 1.0)
        _, job = reload(db, recording_id)
        assert (job.status, job.attempts) == ("queued", 0)

        first = jobs.claim_next(db, "worker-a")
        assert first is not None and first.recording_id == recording_id and first.attempts == 1
        assert jobs.claim_next(db, "worker-b") is None  # nothing else is due

        _, job = reload(db, recording_id)
        assert (job.status, job.locked_by) == ("running", "worker-a")

    def test_the_oldest_due_job_goes_first(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        newer, _ = upload(app, 1.0)
        older, _ = upload(app, 1.0)
        _, job = reload(db, older)
        job.run_after = timeutil.utcnow() - timedelta(minutes=5)
        db.commit()
        claimed = jobs.claim_next(db, "worker-a")
        assert claimed is not None and claimed.recording_id == older
        assert newer != older

    def test_a_job_that_is_not_due_yet_is_left_alone(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app, 1.0)
        _, job = reload(db, recording_id)
        job.run_after = timeutil.utcnow() + timedelta(minutes=10)
        db.commit()
        assert jobs.claim_next(db, "worker-a") is None
        assert jobs.claim_next(db, "worker-a", now=timeutil.utcnow() + timedelta(minutes=11)) is not None

    def test_deleted_recordings_are_never_claimed(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app, 1.0)
        assert sign_in_manager(app).delete(f"/api/recordings/{recording_id}").status_code == 204
        assert jobs.claim_next(db, "worker-a") is None

    def test_two_workers_never_take_the_same_job(self, file_app) -> None:  # type: ignore[no-untyped-def]
        factory = make_session_factory(file_app.state.engine)
        with factory() as db:
            company = add_company(db, "Acme")
            add_member(db, company, "manager@acme.example.com", "manager")
        manager = sign_in_manager(file_app)
        _device, locket = provision_device(file_app, manager)
        for number in range(8):
            assert locket.upload(f"clip-race-{number:04d}", make_wav(1.0)).status_code == 201

        taken: dict[str, list[uuid.UUID]] = {"a": [], "b": []}
        barrier = threading.Barrier(2)

        def compete(name: str) -> None:
            barrier.wait()
            while True:
                with factory() as session:
                    job = jobs.claim_next(session, f"worker-{name}")
                if job is None:
                    return
                taken[name].append(job.id)

        threads = [threading.Thread(target=compete, args=(name,)) for name in "ab"]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        everything = taken["a"] + taken["b"]
        assert len(everything) == 8
        assert len(set(everything)) == 8  # no job twice
        with factory() as db:
            assert {j.attempts for j in db.scalars(select(ProcessingJob))} == {1}

    def test_attempts_are_counted_when_claimed_so_a_job_that_kills_its_worker_still_runs_out(
        self,
        app,
        db: Session,
        acme,  # type: ignore[no-untyped-def]
    ) -> None:
        recording_id, _ = upload(app, 1.0)
        settings = app.state.settings
        for expected in (1, 2, 3):
            claimed = jobs.claim_next(db, f"worker-{expected}")
            assert claimed is not None and claimed.attempts == expected
            # The worker dies without a word. Time passes; the lease runs out.
            jobs.recover_stale(db, settings, now=timeutil.utcnow() + timedelta(hours=expected))
            _, job = reload(db, recording_id)
            job.run_after = timeutil.utcnow() - timedelta(seconds=1)
            db.commit()
        recording, job = reload(db, recording_id)
        assert job.status == "failed" and recording.state == "failed"
        assert recording.error == PUBLIC_FAILURE
        assert jobs.claim_next(db, "worker-4") is None

    def test_a_stalled_job_is_taken_back_at_once_and_marked_retrying(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app, 1.0)
        claimed = jobs.claim_next(db, "worker-a")
        assert claimed is not None
        settings = app.state.settings

        assert jobs.recover_stale(db, settings, now=timeutil.utcnow()) == 0  # lease still fresh
        later = timeutil.utcnow() + timedelta(seconds=settings.job_lock_timeout_seconds + 5)
        assert jobs.recover_stale(db, settings, now=later) == 1

        recording, job = reload(db, recording_id)
        assert (job.status, job.locked_by) == ("queued", None)
        assert job.run_after <= later  # a crash is retried straight away, not after a failure delay
        assert (recording.state, recording.retry_count) == ("retrying", 1)
        assert jobs.claim_next(db, "worker-b", now=later) is not None

    def test_a_lease_can_be_renewed_by_its_owner_only(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        upload(app, 1.0)
        claimed = jobs.claim_next(db, "worker-a")
        assert claimed is not None
        assert jobs.touch(db, claimed, "worker-a")
        assert not jobs.touch(db, claimed, "worker-b")

    def test_handing_a_job_back_does_not_cost_it_an_attempt(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app, 1.0)
        claimed = jobs.claim_next(db, "worker-a")
        assert claimed is not None
        jobs.release(db, claimed, "worker-a")
        recording, job = reload(db, recording_id)
        assert (job.status, job.attempts, job.locked_by) == ("queued", 0, None)
        assert recording.state == "queued"

    def test_retry_delays_grow_by_doubling(self, settings) -> None:  # type: ignore[no-untyped-def]
        base = settings.job_retry_base_seconds
        assert [retry_delay(settings, n).total_seconds() for n in (1, 2, 3, 4)] == [
            base,
            base * 2,
            base * 4,
            base * 8,
        ]

    def test_error_text_loses_paths_and_tokens_and_is_bounded(self, tmp_path: Path) -> None:
        raw = (
            f"cannot open {tmp_path / 'a' / 'clip.wav'} and C:\\Users\\bob\\audio.wav, "
            "also /var/lib/ask/recordings/x.wav with token hf_abcdefghijklmnopqrstuvwxyz at http://host/path"
        )
        clean = sanitize_error(raw, tmp_path)
        assert str(tmp_path) not in clean
        assert "C:\\Users" not in clean and "/var/lib" not in clean
        assert "hf_abcdef" not in clean
        assert "http://host" in clean  # a URL is not a file path
        assert len(sanitize_error("x" * 5000)) <= 500


# --- a successful run --------------------------------------------------------------------------------


class TestThePipeline:
    def full_provider(self, **kwargs):  # type: ignore[no-untyped-def]
        embedder = FakeEmbedder({0: (1, 0, 0), 3500: (0, 1, 0)})
        return make_provider(
            diarizer=FakeDiarizer(two_speaker_turns()), embedder=embedder, **kwargs
        ), embedder

    def test_every_stage_that_runs_is_announced_in_order_and_ends_completed(
        self,
        app,
        db: Session,
        acme,  # type: ignore[no-untyped-def]
    ) -> None:
        recording_id, _ = upload(app)
        enroll(db, add_person(db, acme["company"], "Ana"), (1, 0, 0))
        provider, _ = self.full_provider()

        assert worker_for(app, provider).run(once=True) == 1

        assert states_of(db, recording_id) == [
            "validating",
            "transcribing",
            "diarizing",
            "identifying_speakers",
            "aligning",
            "completed",
        ]
        recording, job = reload(db, recording_id)
        assert (recording.state, job.status, recording.error) == ("completed", "done", None)
        assert job.locked_by is None and job.completed_at is not None

    def test_speakers_separated_by_an_experimental_method_are_flagged_to_the_dashboard(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        class ExperimentalDiarizer(FakeDiarizer):
            def diarize(self, audio: Path):  # type: ignore[no-untyped-def]
                result = super().diarize(audio)
                return type(result)(result.turns, model="some method (experimental)", model_version="1")

        manager = sign_in_manager(app)
        ordinary, _ = upload(app)
        worker_for(app, make_provider(diarizer=FakeDiarizer(two_speaker_turns()))).run(once=True)
        flagged, _ = upload(app)
        worker_for(app, make_provider(diarizer=ExperimentalDiarizer(two_speaker_turns()))).run(once=True)

        plain = manager.get(f"/api/recordings/{ordinary}").json()["transcript"]
        experimental = manager.get(f"/api/recordings/{flagged}").json()["transcript"]
        assert plain["separationExperimental"] is False
        assert experimental["separationExperimental"] is True
        # The method's name is not part of what the dashboard is told is experimental; only the fact is.
        assert "some method" not in str({k: v for k, v in experimental.items() if k != "diarizationModel"})

    def test_a_stage_that_does_not_run_is_not_announced(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        worker_for(app, make_provider()).run(once=True)  # recognizer only
        assert states_of(db, recording_id) == ["validating", "transcribing", "aligning", "completed"]

    def test_speaker_matching_is_skipped_when_nobody_is_enrolled(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        provider, embedder = self.full_provider()
        worker_for(app, provider).run(once=True)
        assert "identifying_speakers" not in states_of(db, recording_id)
        assert embedder.calls == []  # no voice was even compared

    def test_the_transcript_is_saved_with_speakers_times_and_the_models_used(
        self,
        app,
        db: Session,
        acme,  # type: ignore[no-untyped-def]
    ) -> None:
        recording_id, _ = upload(app)
        provider, _ = self.full_provider()
        worker_for(app, provider).run(once=True)

        db.expire_all()
        transcript = db.scalar(select(Transcript).where(Transcript.recording_id == recording_id))
        assert transcript is not None
        assert (transcript.transcription_model, transcript.diarization_model) == ("test-asr", "test-diarizer")
        assert (transcript.language, transcript.is_placeholder, transcript.review_status) == (
            "en",
            False,
            "none",
        )
        assert transcript.full_text == "Good morning everyone.\nMorning, shall we begin?"
        lines = segments_of(db, recording_id)
        assert [(s.speaker_label, s.text, s.start_ms, s.end_ms) for s in lines] == [
            ("SPEAKER_00", "Good morning everyone.", 500, 2200),
            ("SPEAKER_01", "Morning, shall we begin?", 4200, 6000),
        ]
        assert all(s.person_id is None and s.confidence is None for s in lines)  # nobody enrolled

    def test_an_enrolled_voice_is_named_and_a_stranger_stays_unknown(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        ana = add_person(db, acme["company"], "Ana")
        enroll(db, ana, (1, 0, 0))
        enroll(db, add_person(db, acme["company"], "Bo"), (0, 0, 1))  # sounds like neither speaker
        provider, embedder = self.full_provider()

        worker_for(app, provider).run(once=True)

        first, second = segments_of(db, recording_id)
        assert first.person_id == ana.id
        assert first.confidence == pytest.approx(1.0)
        assert second.person_id is None and second.confidence is None
        assert len(embedder.calls) == 2  # both speakers had enough speech to judge

    def test_only_consenting_fully_enrolled_same_model_same_company_voices_are_ever_tried(
        self,
        app,
        db: Session,
        acme,
        globex,  # type: ignore[no-untyped-def]
    ) -> None:
        recording_id, _ = upload(app)
        voice = (1, 0, 0)  # every profile below sounds exactly like the first speaker
        enroll(db, add_person(db, acme["company"], "Revoked", consent="revoked"), voice)
        enroll(db, add_person(db, acme["company"], "Half done"), voice, samples=2)
        enroll(db, add_person(db, acme["company"], "Old model"), voice, model="older-voice-model")
        enroll(db, add_person(db, globex["company"], "Other company"), voice)
        provider, embedder = self.full_provider()

        worker_for(app, provider).run(once=True)

        assert all(s.person_id is None for s in segments_of(db, recording_id))
        assert embedder.calls == []  # not one of them was a candidate, so no comparison ran at all

    def test_two_similar_voices_are_not_guessed_between(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        enroll(db, add_person(db, acme["company"], "Twin one"), (1, 0.02, 0))
        enroll(db, add_person(db, acme["company"], "Twin two"), (1, -0.02, 0))
        provider, _ = self.full_provider()
        worker_for(app, provider).run(once=True)
        assert segments_of(db, recording_id)[0].person_id is None

    def test_a_speaker_with_too_little_speech_is_not_judged_by_voice(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        enroll(db, add_person(db, acme["company"], "Ana"), (1, 0, 0))
        brief = FakeDiarizer((Turn_(0, 1500, "SPEAKER_00"), Turn_(1500, 9000, "SPEAKER_01")))
        embedder = FakeEmbedder({1500: (0, 1, 0)})
        worker_for(app, make_provider(diarizer=brief, embedder=embedder)).run(once=True)
        assert embedder.calls == [((1500, 9000),)]  # only the speaker who talked long enough was compared
        assert segments_of(db, recording_id)[0].person_id is None

    def test_unsure_lines_send_the_recording_to_review(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        shaky = (
            AsrSegment(
                500,
                2200,
                "mumbled",
                (Word(500, 1200, " mum", 0.2), Word(1200, 2200, " bled", 0.3)),
                0.25,
            ),
            *default_segments()[1:],
        )
        provider = make_provider(
            transcriber=FakeTranscriber(shaky), diarizer=FakeDiarizer(two_speaker_turns())
        )
        worker_for(app, provider).run(once=True)

        recording, _ = reload(db, recording_id)
        assert recording.state == "needs_review"
        lines = segments_of(db, recording_id)
        assert [s.needs_review for s in lines] == [True, False]
        transcript = db.scalar(select(Transcript).where(Transcript.recording_id == recording_id))
        assert transcript is not None and transcript.review_status == "needs_review"

    def test_without_speaker_separation_the_transcript_says_so(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        worker_for(app, make_provider()).run(once=True)
        db.expire_all()
        transcript = db.scalar(select(Transcript).where(Transcript.recording_id == recording_id))
        assert transcript is not None and transcript.diarization_model is None
        assert {s.speaker_label for s in segments_of(db, recording_id)} == {"SPEAKER_00"}

    def test_a_silent_recording_completes_with_an_empty_transcript(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        worker_for(app, make_provider(transcriber=FakeTranscriber([]))).run(once=True)
        recording, _ = reload(db, recording_id)
        assert recording.state == "completed"
        assert segments_of(db, recording_id) == []

    def test_reprocessing_replaces_the_transcript_instead_of_adding_to_it(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        provider = make_provider(diarizer=FakeDiarizer(two_speaker_turns()))
        worker_for(app, provider).run(once=True)
        edited = segments_of(db, recording_id)[0]
        edited.text, edited.corrected = "a person's correction", True
        db.commit()

        manager = sign_in_manager(app)
        assert manager.post(f"/api/recordings/{recording_id}/reprocess").status_code == 202
        worker_for(app, provider).run(once=True)

        lines = segments_of(db, recording_id)
        assert len(lines) == 2  # not four
        assert lines[0].text == "Good morning everyone." and not lines[0].corrected
        assert len(list(db.scalars(select(Transcript).where(Transcript.recording_id == recording_id)))) == 1

    def test_the_audit_trail_records_that_it_was_processed_without_recording_what_was_said(
        self,
        app,
        db: Session,
        acme,  # type: ignore[no-untyped-def]
    ) -> None:
        upload(app)
        worker_for(app, make_provider(diarizer=FakeDiarizer(two_speaker_turns()))).run(once=True)
        db.expire_all()
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "recording.processed"))
        assert event is not None
        assert event.detail == {
            "provider": "test",
            "placeholder": False,
            "segments": 2,
            "diarized": True,
            "speakersNamed": 0,
        }
        assert "morning" not in str(event.detail).lower()

    def test_live_events_carry_the_locket_so_employees_hear_only_their_own(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        recording_id, device = upload(app)
        worker_for(app, make_provider()).run(once=True)
        db.expire_all()
        events = [
            e.payload
            for e in db.scalars(select(LiveEvent).where(LiveEvent.type == "recording.state"))
            if e.payload["recordingId"] == str(recording_id)
        ]
        assert events and all(p["deviceId"] == device["id"] for p in events)


def Turn_(start: int, end: int, speaker: str):  # type: ignore[no-untyped-def]  # noqa: N802
    from app.ai.types import Turn

    return Turn(start, end, speaker)


# --- the development stub ------------------------------------------------------------------------------


class TestThePlaceholderStub:
    def test_it_writes_labelled_placeholders_and_never_a_name(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app, 9.0)
        enroll(db, add_person(db, acme["company"], "Ana"), (1, 0, 0))  # even with someone enrolled

        worker_for(app, build_stub()).run(once=True)

        db.expire_all()
        transcript = db.scalar(select(Transcript).where(Transcript.recording_id == recording_id))
        assert transcript is not None
        assert transcript.is_placeholder
        assert "placeholder" in transcript.transcription_model.lower()
        assert transcript.diarization_model is None
        lines = segments_of(db, recording_id)
        assert len(lines) == 3  # 9 s in 4 s pieces
        assert all(s.text.startswith("[Placeholder") and "nothing was transcribed" in s.text for s in lines)
        assert all(s.person_id is None for s in lines)
        assert not any(s.needs_review for s in lines)  # not "uncertain": simply not real

    def test_the_stub_cannot_identify_anyone_by_voice(self) -> None:
        stub = build_stub()
        assert stub.embedder is None and stub.diarizer is None
        assert stub.capabilities.placeholder and not stub.capabilities.speaker_matching
        assert stub.notes  # says why, in words


# --- when things go wrong --------------------------------------------------------------------------------


class Explodes:
    """A recognizer that fails a set number of times, then works."""

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        self.failures, self.error, self.calls = failures, error, 0
        self.inner = FakeTranscriber()

    def transcribe(self, audio: Path):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error or RuntimeError("the model fell over")
        return self.inner.transcribe(audio)


def make_due(db: Session, recording_id: uuid.UUID) -> None:
    _, job = reload(db, recording_id)
    job.run_after = timeutil.utcnow() - timedelta(seconds=1)
    db.commit()


class TestFailures:
    def test_a_temporary_failure_is_retried_later_and_then_succeeds(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        flaky = Explodes(failures=1)
        provider = make_provider(transcriber=flaky)  # type: ignore[arg-type]

        assert worker_for(app, provider).run(once=True) == 1
        recording, job = reload(db, recording_id)
        assert (job.status, job.attempts, recording.state, recording.retry_count) == (
            "queued",
            1,
            "retrying",
            1,
        )
        assert job.run_after > timeutil.utcnow() + timedelta(seconds=10)  # not hammered straight away
        assert recording.error is None  # nothing has gone wrong yet as far as anyone is concerned
        assert "the model fell over" in (job.last_error or "")
        assert worker_for(app, provider).run(once=True) == 0  # not due yet, so left alone

        make_due(db, recording_id)
        assert worker_for(app, provider).run(once=True) == 1
        recording, job = reload(db, recording_id)
        assert (recording.state, job.status, job.attempts) == ("completed", "done", 2)
        assert states_of(db, recording_id).count("retrying") == 1

    def test_it_gives_up_after_the_attempts_run_out_with_a_safe_message(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        provider = make_provider(transcriber=Explodes(failures=99))  # type: ignore[arg-type]
        for _ in range(3):
            worker_for(app, provider).run(once=True)
            make_due(db, recording_id)

        recording, job = reload(db, recording_id)
        assert (recording.state, job.status, job.attempts) == ("failed", "failed", 3)
        assert recording.error == PUBLIC_FAILURE
        assert recording.retry_count == 2
        assert "fell over" not in recording.error  # raw errors never reach the screen
        assert states_of(db, recording_id)[-1] == "failed"
        assert worker_for(app, provider).run(once=True) == 0  # and it stays failed

    def test_a_manager_is_told_why_it_failed_and_an_employee_is_only_told_that_it_did(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        manager = sign_in_manager(app)
        device, locket = provision_device(app, manager, "Locket detail")
        employee_id = str(acme["employee"].id)
        assigned = manager.patch(f"/api/devices/{device['id']}", json={"assignedUserId": employee_id})
        assert assigned.status_code == 200, assigned.text
        stored = locket.upload("clip-detail-0001-aaaa", make_wav(9.0))
        recording_id = uuid.UUID(stored.json()["recordingId"])
        provider = make_provider(transcriber=Explodes(failures=99))  # type: ignore[arg-type]

        worker_for(app, provider).run(once=True)
        retrying = manager.get(f"/api/recordings/{recording_id}").json()
        assert retrying["state"] == "retrying" and "the model fell over" in retrying["failureDetail"]

        for _ in range(2):
            make_due(db, recording_id)
            worker_for(app, provider).run(once=True)
        seen = manager.get(f"/api/recordings/{recording_id}").json()
        assert seen["state"] == "failed" and seen["error"] == PUBLIC_FAILURE
        assert "the model fell over" in seen["failureDetail"]

        staff = new_web(app)
        staff.login("employee@acme.example.com")
        theirs = staff.get(f"/api/recordings/{recording_id}").json()
        assert theirs["state"] == "failed" and theirs["failureDetail"] is None
        assert "fell over" not in str(theirs)
        # The same goes for the job record itself.
        _, job = reload(db, recording_id)
        assert "the model fell over" in manager.get(f"/api/jobs/{job.id}").json()["lastError"]
        assert staff.get(f"/api/jobs/{job.id}").json()["lastError"] is None

    def test_a_recording_that_did_not_fail_has_no_failure_detail(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        worker_for(app, make_provider()).run(once=True)
        assert sign_in_manager(app).get(f"/api/recordings/{recording_id}").json()["failureDetail"] is None

    def test_unusable_audio_fails_at_once_without_retrying(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        broken = Explodes(failures=99, error=PermanentError("The audio is too noisy to use."))
        worker_for(app, make_provider(transcriber=broken)).run(once=True)  # type: ignore[arg-type]

        recording, job = reload(db, recording_id)
        assert (recording.state, job.status, job.attempts) == ("failed", "failed", 1)
        assert recording.error == "The audio is too noisy to use."
        assert broken.calls == 1

    def test_a_missing_audio_file_fails_with_no_path_in_the_message(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        db.expire_all()
        recording = db.get(Recording, recording_id)
        assert recording is not None
        app.state.storage.path_for(recording.storage_key).unlink()

        worker_for(app, make_provider()).run(once=True)

        recording, job = reload(db, recording_id)
        assert recording.state == "failed" and job.attempts == 1
        assert recording.error == "The audio file is missing from storage."
        assert str(app.state.storage.root) not in (recording.error + (job.last_error or ""))

    def test_damaged_audio_is_caught_by_its_checksum(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        db.expire_all()
        recording = db.get(Recording, recording_id)
        assert recording is not None
        path = app.state.storage.path_for(recording.storage_key)
        data = bytearray(path.read_bytes())
        data[-10] ^= 0xFF  # one flipped byte, same size
        path.write_bytes(bytes(data))

        transcriber = FakeTranscriber()
        worker_for(app, make_provider(transcriber=transcriber)).run(once=True)

        recording, _ = reload(db, recording_id)
        assert recording.state == "failed" and "checksum" in (recording.error or "")
        assert transcriber.calls == []  # never fed damaged audio to a model

    def test_error_text_from_a_model_never_leaks_a_path_or_token(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        leaky = Explodes(
            failures=99,
            error=RuntimeError(f"cannot load {app.state.storage.root}/x.wav with hf_abcdefghijklmnop"),
        )
        worker_for(app, make_provider(transcriber=leaky)).run(once=True)  # type: ignore[arg-type]
        _, job = reload(db, recording_id)
        assert job.last_error is not None
        assert str(app.state.storage.root) not in job.last_error and "hf_abc" not in job.last_error

        sign_in = sign_in_manager(app)
        shown = sign_in.get(f"/api/jobs/{job.id}").json()["lastError"]
        assert str(app.state.storage.root) not in shown and "hf_abc" not in shown

    def test_a_recording_deleted_while_it_is_processed_is_dropped_quietly(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        manager = sign_in_manager(app)

        def delete_midway(_: Path) -> None:
            assert manager.delete(f"/api/recordings/{recording_id}").status_code == 204

        transcriber = FakeTranscriber(before=delete_midway)
        worker_for(app, make_provider(transcriber=transcriber)).run(once=True)

        db.expire_all()
        assert db.scalar(select(Transcript).where(Transcript.recording_id == recording_id)) is None
        recording, job = reload(db, recording_id)
        assert recording.state != "failed"  # nothing went wrong; it was simply deleted
        assert job.status == "failed" and "deleted" in (job.last_error or "")

    def test_a_worker_that_lost_its_job_writes_nothing(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)

        def another_worker_takes_over(_: Path) -> None:
            _, job = reload(db, recording_id)
            job.locked_by = "worker-b"
            db.commit()

        transcriber = FakeTranscriber(before=another_worker_takes_over)
        outcome = worker_for(app, make_provider(transcriber=transcriber)).pipeline.run(
            jobs.claim_next(db, "worker-a")  # type: ignore[arg-type]
        )

        assert outcome == "lost"
        db.expire_all()
        assert db.scalar(select(Transcript).where(Transcript.recording_id == recording_id)) is None
        _, job = reload(db, recording_id)
        assert (job.status, job.locked_by) == ("running", "worker-b")  # untouched: it is worker-b's now

    def test_asking_a_worker_to_stop_hands_the_job_back_between_stages(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app)
        holder: list[Worker] = []
        # The stop request (Ctrl+C, a service stopping) arrives while the recognizer is running.
        transcriber = FakeTranscriber(before=lambda _: holder[0].stop())
        diarizer = FakeDiarizer(two_speaker_turns())
        worker = worker_for(app, make_provider(transcriber=transcriber, diarizer=diarizer))
        holder.append(worker)

        assert worker.run(once=True) == 1  # one job handled, and then it really stopped

        recording, job = reload(db, recording_id)
        assert (job.status, job.attempts, recording.state) == ("queued", 0, "queued")
        assert diarizer.calls == 0  # it did not carry on into the next stage
        assert db.scalar(select(Transcript).where(Transcript.recording_id == recording_id)) is None
        assert states_of(db, recording_id) == ["validating", "transcribing", "queued"]


# --- a long model must not look like a crash ------------------------------------------------------------


class TestTheLease:
    def test_a_slow_model_keeps_its_job_while_it_runs(self, file_app) -> None:  # type: ignore[no-untyped-def]
        slow_settings = file_app.state.settings.model_copy(update={"job_lock_timeout_seconds": 0.6})
        factory = make_session_factory(file_app.state.engine)
        with factory() as db:
            company = add_company(db, "Acme")
            add_member(db, company, "manager@acme.example.com", "manager")
        manager = sign_in_manager(file_app)
        _, locket = provision_device(file_app, manager)
        assert locket.upload("clip-slow-0001", make_wav(2.0)).status_code == 201

        stolen: list[int] = []

        def slow(_: Path) -> None:
            # The model runs for 1.2 s against a 0.6 s lock timeout. Without a renewed lease the job
            # would look abandoned from 0.6 s on; at 0.9 s a second worker looks for abandoned jobs.
            time.sleep(0.9)
            with factory() as other:
                stolen.append(jobs.recover_stale(other, slow_settings))
            time.sleep(0.3)

        provider = make_provider(transcriber=FakeTranscriber(before=slow))
        worker = Worker(factory, slow_settings, provider, file_app.state.storage, "worker-a")

        assert worker.run(once=True) == 1

        assert stolen == [0]  # the lease had been renewed, so the job was not mistaken for a crash
        with factory() as db:
            assert db.scalar(select(ProcessingJob.status)) == "done"


# --- the worker process ---------------------------------------------------------------------------------


class TestTheWorker:
    def test_it_processes_everything_that_is_due_then_stops_when_asked_to_run_once(
        self,
        app,
        db: Session,
        acme,  # type: ignore[no-untyped-def]
    ) -> None:
        ids = [upload(app, 1.0)[0] for _ in range(3)]
        assert worker_for(app, make_provider()).run(once=True) == 3
        assert {reload(db, i)[0].state for i in ids} == {"completed"}

    def test_it_takes_back_stalled_work_before_looking_for_new_work(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, _ = upload(app, 1.0)
        assert jobs.claim_next(db, "dead-worker") is not None
        _, job = reload(db, recording_id)
        job.locked_at = timeutil.utcnow() - timedelta(hours=2)
        db.commit()

        assert worker_for(app, make_provider()).run(once=True) == 1
        assert reload(db, recording_id)[0].state == "completed"

    def test_it_reports_in_while_running_and_says_goodbye_when_it_stops(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        from app.models import Worker as WorkerRow

        seen: list[bool] = []
        observed = FakeTranscriber(
            before=lambda _: seen.append(
                db.scalar(select(WorkerRow.id).where(WorkerRow.worker_id == "worker-a")) is not None
            )
        )
        upload(app, 1.0)
        worker_for(app, make_provider(transcriber=observed)).run(once=True)
        assert seen == [True]  # present while it worked
        db.expire_all()
        assert db.scalar(select(WorkerRow.id).where(WorkerRow.worker_id == "worker-a")) is None  # gone after

    def test_it_refuses_to_start_without_a_provider_and_says_what_to_do(
        self,
        settings,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,  # type: ignore[no-untyped-def]
    ) -> None:
        from app import worker as worker_module

        monkeypatch.setattr(
            worker_module, "get_settings", lambda: settings.model_copy(update={"ai_provider": "none"})
        )
        assert worker_module.main(["--once"]) == 2
        assert "ASK_AI_PROVIDER" in caplog.text

    def test_the_stub_worker_starts_and_warns_loudly_that_it_is_a_placeholder(
        self,
        settings,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,  # type: ignore[no-untyped-def]
    ) -> None:
        from app import worker as worker_module
        from app.db import Base, make_engine

        url = f"sqlite:///{(tmp_path / 'w.sqlite3').as_posix()}"
        Base.metadata.create_all(make_engine(url))
        stub_settings = settings.model_copy(update={"ai_provider": "stub", "database_url": url})
        monkeypatch.setattr(worker_module, "get_settings", lambda: stub_settings)

        assert worker_module.main(["--once"]) == 0
        assert "PLACEHOLDER MODE" in caplog.text
