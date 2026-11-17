"""A meeting that reaches the server as several parts (the phone uploads about a minute at a time while the
locket is still sending): the saved files and the summary are for the whole meeting, not one minute of it."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.ai.types import AsrSegment, Provider
from app.models import Recording
from app.services import meeting
from tests.ai_doubles import FakeTranscriber
from tests.helpers import make_wav
from tests.test_phone_upload import make_setup
from tests.test_pipeline_summary import FakeSummarizer
from tests.test_processing import worker_for

SESSION = "SESSIONREF000001"


def upload_part(setup, sequence: int, started: datetime) -> uuid.UUID:  # type: ignore[no-untyped-def]
    body = make_wav(2.0)
    response = setup["employee"].request(
        "PUT",
        f"/api/lockets/{setup['product_id']}/recordings/{SESSION}-{sequence}",
        content=body,
        headers={
            "Content-Type": "audio/wav",
            "X-Content-SHA256": hashlib.sha256(body).hexdigest(),
            "X-Recorded-At": started.isoformat(),
            "X-Session-Ref": SESSION,
            "X-Sequence": str(sequence),
        },
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["recordingId"])


def process(app, text: str, summary: str) -> FakeSummarizer:  # type: ignore[no-untyped-def]
    summarizer = FakeSummarizer(summary)
    provider = Provider(
        name="test",
        placeholder=False,
        transcriber=FakeTranscriber([AsrSegment(1000, 3000, text, (), 0.95)]),
        summarizer=summarizer,
    )
    assert worker_for(app, provider).run(once=True) == 1
    return summarizer


def meeting_of_two(app, acme):  # type: ignore[no-untyped-def]
    setup = make_setup(app)
    begin = datetime.now(UTC) - timedelta(minutes=10)
    first = upload_part(setup, 0, begin)
    first_run = process(app, "We will ship the report on Friday.", "FIRST SUMMARY")
    second = upload_part(setup, 1, begin + timedelta(seconds=60))
    second_run = process(app, "Priya sends the budget on Wednesday.", "WHOLE MEETING SUMMARY")
    return setup, first, second, first_run, second_run


def test_a_later_part_is_summarised_together_with_the_parts_before_it(app, acme) -> None:  # type: ignore[no-untyped-def]
    _, _, _, first_run, second_run = meeting_of_two(app, acme)
    # The first part, alone so far, is summarised as it always was.
    assert "We will ship the report on Friday." in first_run.calls[0]
    # The second sees the whole meeting so far, in order, its own time one minute in, with no speaker numbers.
    assert second_run.calls == [
        "[00:00:01] We will ship the report on Friday.\n[00:01:01] Priya sends the budget on Wednesday.\n"
    ]


def test_the_saved_files_cover_the_whole_meeting(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup, first, second, _, _ = meeting_of_two(app, acme)
    employee = setup["employee"]
    for recording_id in (first, second):  # the same files whichever part is asked
        transcript = employee.get(f"/api/recordings/{recording_id}/transcription.txt")
        assert transcript.status_code == 200, transcript.text
        assert transcript.text == (
            "[00:00:01] Speaker 1: We will ship the report on Friday.\n"
            "[00:01:01] Speaker 1: Priya sends the budget on Wednesday.\n"
        )
        summary = employee.get(f"/api/recordings/{recording_id}/summary.txt")
        assert summary.status_code == 200, summary.text
        assert summary.text == "WHOLE MEETING SUMMARY\n"  # the newest, which was made from every part


def test_a_recording_on_its_own_is_unchanged(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup = make_setup(app)
    only = upload_part(setup, 0, datetime.now(UTC) - timedelta(minutes=5))
    run = process(app, "Just one short note.", "ONLY SUMMARY")
    assert (
        "Speaker 1: Just one short note." in run.calls[0]
    )  # speaker numbers stay for a recording on its own
    assert setup["employee"].get(f"/api/recordings/{only}/summary.txt").text == "ONLY SUMMARY\n"
    assert setup["employee"].get(f"/api/recordings/{only}/transcription.txt").text == (
        "[00:00:01] Speaker 1: Just one short note.\n"
    )


def test_a_part_processed_again_does_not_count_its_old_words_twice(app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
    _, _, second, _, _ = meeting_of_two(app, acme)
    recording = db.get(Recording, second)
    assert recording is not None
    text = meeting.summary_input(db, recording, [(500, "Brand new words.")])
    assert text == "[00:00:01] We will ship the report on Friday.\n[01:00:00] Brand new words.\n".replace(
        "[01:00:00]", "[00:01:00]"
    )
    assert "Priya sends the budget" not in text  # its old transcript is about to be replaced


def test_parts_are_ordered_by_sequence_and_a_lone_recording_has_no_meeting(app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
    _, first, second, _, _ = meeting_of_two(app, acme)
    recording = db.get(Recording, second)
    assert recording is not None
    assert [p.id for p in meeting.session_parts(db, recording)] == [first, second]
    assert meeting.summary_input(db, db.get(Recording, first), []) is not None  # type: ignore[arg-type]
    other = make_setup(app)
    lone = upload_part(other, 0, datetime.now(UTC) - timedelta(minutes=4))
    lone_recording = db.get(Recording, lone)
    assert lone_recording is not None
    assert meeting.summary_input(db, lone_recording, [(0, "x")]) is None
