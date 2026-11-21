"""What a meeting of several hours needs from the server: audio that does not fill a small disk, a clear
refusal when there is no audio to work from, and a meeting reference so the app can show the parts as one."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.ai.types import AsrSegment, Provider
from app.models import Recording, Transcript
from app.worker import Worker
from tests.ai_doubles import FakeTranscriber
from tests.test_meeting_parts import SESSION, upload_part
from tests.test_phone_upload import make_setup
from tests.test_pipeline_summary import FakeSummarizer


def provider(text: str) -> Provider:
    return Provider(
        name="test",
        placeholder=False,
        transcriber=FakeTranscriber([AsrSegment(1000, 3000, text, (), 0.95)]),
        summarizer=FakeSummarizer("SUMMARY"),
    )


def run_worker(app, *, delete_audio: bool, text: str = "A line of speech.") -> None:  # type: ignore[no-untyped-def]
    settings = app.state.settings.model_copy(update={"delete_audio_after_processing": delete_audio})
    worker = Worker(app.state.session_factory, settings, provider(text), app.state.storage, "worker-long")
    assert worker.run(once=True) == 1


def test_audio_is_kept_by_default(app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
    setup = make_setup(app)
    recording_id = upload_part(setup, 0, datetime.now(UTC) - timedelta(minutes=5))
    run_worker(app, delete_audio=False)
    db.expire_all()
    recording = db.get(Recording, recording_id)
    assert recording is not None and app.state.storage.exists(recording.storage_key)


def test_the_audio_is_removed_once_the_transcript_is_saved_when_asked(app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
    setup = make_setup(app)
    recording_id = upload_part(setup, 0, datetime.now(UTC) - timedelta(minutes=5))
    run_worker(app, delete_audio=True)
    db.expire_all()
    recording = db.get(Recording, recording_id)
    assert recording is not None
    assert not app.state.storage.exists(recording.storage_key)  # the audio is gone...
    transcript = db.query(Transcript).filter(Transcript.recording_id == recording_id).one()
    assert transcript.summary_text == "SUMMARY"  # ...and the transcript and summary are kept
    assert recording.state == "completed"
    # The files a person saves still work, and playback says plainly that there is no audio.
    employee = setup["employee"]
    assert employee.get(f"/api/recordings/{recording_id}/transcription.txt").status_code == 200
    assert employee.get(f"/api/recordings/{recording_id}/summary.txt").status_code == 200
    assert employee.get(f"/api/recordings/{recording_id}/audio").status_code == 404


def test_processing_again_without_the_audio_is_refused_clearly(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup = make_setup(app)
    recording_id = upload_part(setup, 0, datetime.now(UTC) - timedelta(minutes=5))
    run_worker(app, delete_audio=True)
    response = setup["manager"].post(f"/api/recordings/{recording_id}/reprocess")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "audio_not_kept"


def test_the_parts_of_one_recording_are_one_row_with_the_whole_ones_length(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup = make_setup(app)
    begin = datetime.now(UTC) - timedelta(minutes=10)
    upload_part(setup, 0, begin)
    upload_part(setup, 1, begin + timedelta(seconds=60))
    # One row for the whole recording (the first part stands for it), with the whole one's length...
    items = setup["employee"].get("/api/recordings?pageSize=10").json()["items"]
    assert len(items) == 1
    [whole] = items
    assert whole["sessionRef"] == SESSION and whole["partCount"] == 2
    parts = setup["employee"].get("/api/recordings?pageSize=10&includeParts=true").json()["items"]
    assert len(parts) == 2 and all(item["part"] is not None for item in parts)
    assert whole["durationMs"] == whole["meetingDurationMs"] == 4000  # two parts of two seconds
    assert whole["startedAt"] == whole["meetingStartedAt"] == min(item["startedAt"] for item in parts)


def test_an_ordinary_recording_has_no_meeting_reference(app, acme) -> None:  # type: ignore[no-untyped-def]
    from tests.test_phone_upload import upload

    setup = make_setup(app)
    from tests.helpers import make_wav

    assert upload(setup["employee"], setup["product_id"], "plain-clip-0001", make_wav(2.0)).status_code == 201
    items = setup["employee"].get("/api/recordings?pageSize=10").json()["items"]
    assert [item["sessionRef"] for item in items] == [None]
