"""The real transcription pipeline on real audio: no stub, no test doubles standing in for a model.

The audio is synthetic (Windows text-to-speech voices), so these tests prove the pipeline works on real
speech signals and that its safeguards hold. They do NOT measure accuracy on real people, rooms or
microphones. See tests/real/conftest.py to run them.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import timeutil
from app.ai.types import PermanentError, Provider, Transcription
from app.models import AuditEvent, ProcessingJob, Recording, Transcript, TranscriptSegment
from app.services import jobs
from app.worker import Worker
from tests.conftest import new_web, provision_device

CONVERSATION_WORDS = (
    "Could you confirm whether the delivery arrives on Monday or on Tuesday morning? "
    "Please remember to submit your expense reports before the end of the month. "
    "Let us meet at the front entrance at nine o'clock and walk to the conference room together. "
    "That sounds great, and I will bring the printed handouts for the whole team."
)
SINGLE_HAZEL = "Could you confirm whether the delivery arrives on Monday or on Tuesday morning?"


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("o'clock", "oclock"))


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate: edits needed to turn the hypothesis into the reference, over the reference length."""
    ref, hyp = tokens(reference), tokens(hypothesis)
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        previous, row[0] = row[0], i
        for j, h in enumerate(hyp, start=1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (r != h))
    return row[len(hyp)] / max(1, len(ref))


def text_of(transcription: Transcription) -> str:
    return " ".join(segment.text for segment in transcription.segments)


class TestRealTranscription:
    def test_a_sentence_comes_back_as_real_text_with_word_times(
        self, real_provider: Provider, samples: Path
    ) -> None:
        result = real_provider.transcriber.transcribe(samples / "single-hazel.wav")
        error = wer(SINGLE_HAZEL, text_of(result))
        print(f"\n  single sentence: {text_of(result)!r}  word error rate {error:.0%}")
        assert error <= 0.2
        assert result.model.startswith("faster-whisper") and result.model_version
        assert result.language == "en"
        words = [w for segment in result.segments for w in segment.words]
        assert words, "word timestamps were asked for"
        assert all(0 <= w.start_ms <= w.end_ms for w in words)
        assert [w.start_ms for w in words] == sorted(w.start_ms for w in words)
        assert all(w.probability is None or 0 <= w.probability <= 1 for w in words)
        assert all(s.confidence is None or 0 <= s.confidence <= 1 for s in result.segments)

    def test_a_conversation_is_transcribed_in_order(self, real_provider: Provider, samples: Path) -> None:
        result = real_provider.transcriber.transcribe(samples / "conversation.wav")
        error = wer(CONVERSATION_WORDS, text_of(result))
        print(f"\n  conversation word error rate {error:.0%}")
        assert error <= 0.3
        starts = [s.start_ms for s in result.segments]
        assert starts == sorted(starts)
        # Each spoken turn is found where it was said (known from how the file was built).
        turns = json.loads((samples / "manifest.json").read_text())["conversation"]["turns"]
        for turn in turns:
            middle = (turn["startMs"] + turn["endMs"]) // 2
            assert any(s.start_ms - 1500 <= middle <= s.end_ms + 1500 for s in result.segments), turn

    def test_noise_added_to_speech_does_not_stop_it_being_transcribed(
        self, real_provider: Provider, samples: Path
    ) -> None:
        result = real_provider.transcriber.transcribe(samples / "conversation-noisy.wav")
        error = wer(CONVERSATION_WORDS, text_of(result))
        print(f"\n  noisy conversation (about 10 dB signal to noise) word error rate {error:.0%}")
        assert error <= 0.5

    def test_a_very_short_clip_is_still_transcribed(self, real_provider: Provider, samples: Path) -> None:
        result = real_provider.transcriber.transcribe(samples / "short.wav")
        assert "works" in text_of(result).lower()

    @pytest.mark.parametrize("name", ["silence.wav", "quiet-noise.wav"])
    def test_nothing_is_invented_for_silence_or_faint_noise(
        self, real_provider: Provider, samples: Path, name: str
    ) -> None:
        result = real_provider.transcriber.transcribe(samples / name)
        assert result.segments == (), f"words were invented for {name}: {text_of(result)!r}"

    def test_overlapping_voices_still_give_some_real_text(
        self, real_provider: Provider, samples: Path
    ) -> None:
        """Overlapping speech is hard for any recognizer. It must not crash, and it must hear real words."""
        result = real_provider.transcriber.transcribe(samples / "overlap.wav")
        heard = set(tokens(text_of(result)))
        known = set(tokens(CONVERSATION_WORDS))
        print(f"\n  overlap heard: {text_of(result)!r}")
        assert heard and len(heard & known) >= 3


# --- through the database, API and worker ---------------------------------------------------------------


def upload_sample(app, samples: Path, name: str):  # type: ignore[no-untyped-def]
    manager = new_web(app)
    manager.login("manager@acme.example.com")
    device, locket = provision_device(app, manager, f"Locket {uuid.uuid4().hex[:4]}")
    stored = locket.upload(f"clip-{uuid.uuid4().hex[:12]}", (samples / name).read_bytes())
    return manager, device, locket, stored


def real_worker(app, provider: Provider, name: str = "real-worker") -> Worker:  # type: ignore[no-untyped-def]
    return Worker(app.state.session_factory, app.state.settings, provider, app.state.storage, name)


def reload(db: Session, recording_id: uuid.UUID) -> tuple[Recording, ProcessingJob]:
    db.expire_all()
    recording = db.get(Recording, recording_id)
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.recording_id == recording_id))
    assert recording is not None and job is not None
    return recording, job


class FailsThenWorks:
    """The real transcriber, except that it raises the given errors first (a crash in inference)."""

    def __init__(self, real: Provider, failures: list[Exception]) -> None:
        self.real = real.transcriber
        self.failures = failures
        self.calls = 0

    def transcribe(self, audio: Path) -> Transcription:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.real.transcribe(audio)


def with_transcriber(provider: Provider, transcriber: object) -> Provider:
    return Provider(
        name=provider.name,
        placeholder=provider.placeholder,
        transcriber=transcriber,  # type: ignore[arg-type]
        diarizer=provider.diarizer,
        embedder=provider.embedder,
    )


class TestRealPipelineThroughTheDatabase:
    def test_an_uploaded_recording_becomes_a_real_transcript_the_website_can_show(
        self, app, db: Session, acme, real_provider: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        manager, _, _, stored = upload_sample(app, samples, "single-hazel.wav")
        assert stored.status_code == 201, stored.text
        recording_id = uuid.UUID(stored.json()["recordingId"])
        assert manager.get(f"/api/recordings/{recording_id}").json()["state"] == "queued"
        assert (
            manager.get(f"/api/recordings/{recording_id}").json()["transcript"] is None
        )  # nothing invented yet

        assert real_worker(app, real_provider).work_once() is True

        detail = manager.get(f"/api/recordings/{recording_id}").json()
        assert detail["state"] in ("completed", "needs_review")
        assert detail["placeholder"] is False
        transcript = detail["transcript"]
        assert transcript["placeholder"] is False
        assert transcript["transcriptionModel"].startswith("faster-whisper")
        text = " ".join(s["text"] for s in transcript["segments"])
        assert wer(SINGLE_HAZEL, text) <= 0.2, text
        assert all(0 <= s["startMs"] <= s["endMs"] for s in transcript["segments"])
        # Without speaker separation every line is one speaker, and the transcript says it was not separated.
        if real_provider.diarizer is None:
            assert transcript["diarizationModel"] is None
        # Listed, searchable by what was said, and exportable.
        listed = manager.get("/api/recordings", params={"q": "delivery"}).json()
        assert [r["id"] for r in listed["items"]] == [str(recording_id)]
        exported = manager.get(f"/api/recordings/{recording_id}/transcript").json()
        assert exported["placeholder"] is False
        _, job = reload(db, recording_id)
        assert (job.status, job.attempts) == ("done", 1)
        processed = db.scalars(select(AuditEvent).where(AuditEvent.action == "recording.processed")).one()
        assert processed.detail["provider"] == "local" and processed.detail["placeholder"] is False

    def test_processing_the_same_recording_twice_leaves_one_transcript(
        self, app, db: Session, acme, real_provider: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        manager, _, _, stored = upload_sample(app, samples, "single-zira.wav")
        recording_id = uuid.UUID(stored.json()["recordingId"])
        worker = real_worker(app, real_provider)
        worker.work_once()
        first = [
            s["text"] for s in manager.get(f"/api/recordings/{recording_id}").json()["transcript"]["segments"]
        ]

        assert manager.post(f"/api/recordings/{recording_id}/reprocess").status_code in (200, 202)
        assert worker.work_once() is True

        db.expire_all()
        assert (
            db.scalar(
                select(func.count()).select_from(Transcript).where(Transcript.recording_id == recording_id)
            )
            == 1
        )
        segments = db.scalar(select(func.count()).select_from(TranscriptSegment))
        assert segments == len(first)  # replaced, not added to
        second = [
            s["text"] for s in manager.get(f"/api/recordings/{recording_id}").json()["transcript"]["segments"]
        ]
        assert second == first  # the same model on the same audio says the same thing

    def test_a_job_that_runs_again_over_an_existing_transcript_replaces_it_instead_of_failing_or_doubling(
        self, app, db: Session, acme, real_provider: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        """The path 'Process again' does not cover: the job is queued again with the old transcript still in
        place (a crash right after a save, or an operator re-queuing a job). Saving must replace it."""
        manager, _, _, stored = upload_sample(app, samples, "single-hazel.wav")
        recording_id = uuid.UUID(stored.json()["recordingId"])
        worker = real_worker(app, real_provider)
        worker.work_once()
        before = manager.get(f"/api/recordings/{recording_id}").json()["transcript"]["segments"]

        recording, job = reload(db, recording_id)
        job.status, job.attempts, job.run_after, job.locked_by = "queued", 0, timeutil.utcnow(), None
        recording.state = "queued"
        db.commit()
        assert worker.work_once() is True

        recording, job = reload(db, recording_id)
        assert (recording.state in ("completed", "needs_review"), job.status) == (True, "done")
        assert db.scalar(select(func.count()).select_from(Transcript)) == 1
        after = manager.get(f"/api/recordings/{recording_id}").json()["transcript"]["segments"]
        assert [s["text"] for s in after] == [s["text"] for s in before]
        assert db.scalar(select(func.count()).select_from(TranscriptSegment)) == len(before)

    @pytest.mark.parametrize(
        "name", ["bad-empty.wav", "bad-not-audio.wav", "bad-truncated.wav", "bad-no-frames.wav"]
    )
    def test_empty_corrupt_or_truncated_audio_is_refused_and_nothing_is_stored(
        self, app, db: Session, acme, real_provider: Provider, samples: Path, name: str
    ) -> None:  # type: ignore[no-untyped-def]
        _, _, _, stored = upload_sample(app, samples, name)
        assert 400 <= stored.status_code < 500, (name, stored.status_code, stored.text)
        assert db.scalar(select(func.count()).select_from(Recording)) == 0
        assert db.scalar(select(func.count()).select_from(ProcessingJob)) == 0

    def test_pure_silence_is_refused_at_upload(self, app, db: Session, acme, samples: Path) -> None:  # type: ignore[no-untyped-def]
        _, _, _, stored = upload_sample(app, samples, "silence.wav")
        assert stored.status_code == 422 and stored.json()["error"]["code"] == "silent_audio"

    def test_faint_noise_completes_with_no_invented_text(
        self, app, db: Session, acme, real_provider: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        manager, _, _, stored = upload_sample(app, samples, "quiet-noise.wav")
        if stored.status_code != 201:  # the upload check may judge it silent; either way nothing is invented
            assert stored.json()["error"]["code"] == "silent_audio"
            return
        recording_id = uuid.UUID(stored.json()["recordingId"])
        real_worker(app, real_provider).work_once()
        detail = manager.get(f"/api/recordings/{recording_id}").json()
        assert detail["state"] == "completed" and detail["transcript"]["segments"] == []

    def test_a_recording_damaged_after_upload_fails_for_good_and_says_why(
        self, app, db: Session, acme, real_provider: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        manager, _, _, stored = upload_sample(app, samples, "single-hazel.wav")
        recording_id = uuid.UUID(stored.json()["recordingId"])
        recording, _ = reload(db, recording_id)
        path = app.state.storage.path_for(recording.storage_key)
        data = bytearray(path.read_bytes())
        data[-100:] = b"\x7f" * 100  # damage the audio but keep the file size
        path.write_bytes(bytes(data))

        real_worker(app, real_provider).work_once()

        recording, job = reload(db, recording_id)
        assert (recording.state, job.status, job.attempts) == ("failed", "failed", 1)  # permanent: no retries
        assert "checksum" in (job.last_error or "") or "damaged" in (job.last_error or "")
        assert manager.get(f"/api/recordings/{recording_id}").json()["transcript"] is None

    def test_a_crash_in_inference_is_recorded_and_the_retry_succeeds(
        self, app, db: Session, acme, real_provider: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        manager, _, _, stored = upload_sample(app, samples, "single-hazel.wav")
        recording_id = uuid.UUID(stored.json()["recordingId"])
        flaky = FailsThenWorks(real_provider, [RuntimeError("simulated crash inside the model")])
        worker = real_worker(app, with_transcriber(real_provider, flaky))

        worker.work_once()
        recording, job = reload(db, recording_id)
        assert (recording.state, job.status, job.attempts) == ("retrying", "queued", 1)
        assert "simulated crash" in (job.last_error or "")
        assert manager.get(f"/api/recordings/{recording_id}").json()["transcript"] is None  # no half result

        worker.work_once()
        recording, job = reload(db, recording_id)
        assert (recording.state in ("completed", "needs_review"), job.status, job.attempts) == (
            True,
            "done",
            2,
        )
        assert flaky.calls == 2
        assert (
            wer(
                SINGLE_HAZEL,
                " ".join(
                    s["text"]
                    for s in manager.get(f"/api/recordings/{recording_id}").json()["transcript"]["segments"]
                ),
            )
            <= 0.2
        )

    def test_a_failure_that_will_never_pass_is_not_retried(
        self, app, db: Session, acme, real_provider: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        _, _, _, stored = upload_sample(app, samples, "single-hazel.wav")
        recording_id = uuid.UUID(stored.json()["recordingId"])
        broken = FailsThenWorks(real_provider, [PermanentError("The audio could not be decoded.")])
        real_worker(app, with_transcriber(real_provider, broken)).work_once()
        recording, job = reload(db, recording_id)
        assert (recording.state, job.status, job.attempts) == ("failed", "failed", 1)

    def test_retries_run_out_and_the_failure_is_final(
        self, app, db: Session, acme, real_provider: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        _, _, _, stored = upload_sample(app, samples, "single-hazel.wav")
        recording_id = uuid.UUID(stored.json()["recordingId"])
        always = FailsThenWorks(real_provider, [RuntimeError("boom")] * 5)
        worker = real_worker(app, with_transcriber(real_provider, always))
        for _ in range(4):
            worker.work_once()
        recording, job = reload(db, recording_id)
        assert (recording.state, job.status, job.attempts) == ("failed", "failed", job.max_attempts)
        assert always.calls == job.max_attempts

    def test_a_restarted_worker_takes_over_a_job_its_predecessor_died_on(
        self, app, db: Session, acme, real_provider: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        manager, _, _, stored = upload_sample(app, samples, "single-zira.wav")
        recording_id = uuid.UUID(stored.json()["recordingId"])

        dead = jobs.claim_next(db, "worker-that-was-killed")  # it claimed the job, then the machine went down
        assert dead is not None
        _, job = reload(db, recording_id)
        job.locked_at = timeutil.utcnow() - 2 * timedelta_for(app.state.settings.job_lock_timeout_seconds)
        db.commit()
        assert reload(db, recording_id)[0].state == "queued"

        restarted = real_worker(app, real_provider, "worker-after-restart")
        assert restarted.work_once() is True

        recording, job = reload(db, recording_id)
        assert recording.state in ("completed", "needs_review") and job.status == "done"
        transcript = manager.get(f"/api/recordings/{recording_id}").json()["transcript"]
        assert transcript is not None and transcript["placeholder"] is False
        assert db.scalar(select(func.count()).select_from(Transcript)) == 1


def timedelta_for(seconds: float):  # type: ignore[no-untyped-def]
    from datetime import timedelta

    return timedelta(seconds=seconds)


def test_the_real_provider_is_never_the_placeholder(real_provider: Provider) -> None:
    assert real_provider.placeholder is False and real_provider.name == "local"
    assert real_provider.capabilities.transcription is True
