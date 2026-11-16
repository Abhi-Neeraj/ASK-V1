"""The worker asks the provider's summarizer for a written summary after transcribing. A failed or missing
summary never fails the recording."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.types import Provider
from app.models import Recording, Transcript
from tests.ai_doubles import FakeDiarizer, FakeTranscriber, two_speaker_turns
from tests.test_processing import reload, upload, worker_for


class FakeSummarizer:
    model = "gemini-test"

    def __init__(self, answer: str | Exception = "Summary\nThey agreed to begin.") -> None:
        self.answer = answer
        self.calls: list[str] = []

    def summarize(self, transcript: str) -> str:
        self.calls.append(transcript)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def provider(summarizer: FakeSummarizer, transcriber: FakeTranscriber | None = None) -> Provider:
    return Provider(
        name="test",
        placeholder=False,
        transcriber=transcriber or FakeTranscriber(),
        diarizer=FakeDiarizer(two_speaker_turns()),
        summarizer=summarizer,
    )


def transcript_of(db: Session, recording_id: uuid.UUID) -> Transcript:
    db.expire_all()
    transcript = db.scalar(select(Transcript).where(Transcript.recording_id == recording_id))
    assert transcript is not None
    return transcript


def test_summary_is_saved_with_the_transcript(app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
    recording_id, _ = upload(app)
    summarizer = FakeSummarizer()
    assert worker_for(app, provider(summarizer)).run(once=True) == 1
    transcript = transcript_of(db, recording_id)
    assert transcript.summary_text == "Summary\nThey agreed to begin."
    assert transcript.summary_model == "gemini-test"
    [sent] = summarizer.calls
    assert (
        sent
        == "[00:00:00] Speaker 1: Good morning everyone.\n[00:00:04] Speaker 2: Morning, shall we begin?\n"
    )


def test_a_failed_summary_keeps_the_transcript(app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
    recording_id, _ = upload(app)
    assert worker_for(app, provider(FakeSummarizer(RuntimeError("quota")))).run(once=True) == 1
    recording, job = reload(db, recording_id)
    assert (recording.state, job.status) == ("completed", "done")
    assert transcript_of(db, recording_id).summary_text is None


def test_silence_gives_an_empty_transcript_and_no_summary(app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
    recording_id, _ = upload(app)
    summarizer = FakeSummarizer()
    assert worker_for(app, provider(summarizer, FakeTranscriber([]))).run(once=True) == 1
    db.expire_all()
    assert db.get(Recording, recording_id).state == "completed"  # type: ignore[union-attr]
    assert summarizer.calls == []
    assert transcript_of(db, recording_id).summary_text is None
