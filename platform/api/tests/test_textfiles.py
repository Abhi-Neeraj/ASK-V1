"""transcription.txt and summary.txt: the two files the phone keeps as its local backup."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Transcript, TranscriptSegment
from app.services.textfiles import PLACEHOLDER_LINE, transcription_txt
from tests.helpers import add_person, add_transcript, make_wav
from tests.test_phone_upload import make_setup, upload


def segment(
    idx: int, start_ms: int, label: str, text: str, person: uuid.UUID | None = None
) -> TranscriptSegment:
    return TranscriptSegment(
        transcript_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        idx=idx,
        start_ms=start_ms,
        end_ms=start_ms + 500,
        speaker_label=label,
        person_id=person,
        text=text,
    )


def test_lines_and_speaker_numbering() -> None:
    ana = uuid.uuid4()
    lines = [
        segment(0, 5_000, "SPEAKER_01", "Hello."),
        segment(1, 65_000, "SPEAKER_00", "Hi there."),
        segment(2, 3_725_000, "SPEAKER_01", "Bye."),
        segment(3, 3_726_000, "SPEAKER_02", "Named.", person=ana),
    ]
    assert transcription_txt(lines, {ana: "Ana"}) == (
        "[00:00:05] Speaker 1: Hello.\n"
        "[00:01:05] Speaker 2: Hi there.\n"
        "[01:02:05] Speaker 1: Bye.\n"
        "[01:02:06] Ana: Named.\n"
    )


def test_an_unknown_person_falls_back_to_the_speaker_number() -> None:
    lines = [segment(0, 0, "SPEAKER_00", "Hi.", person=uuid.uuid4())]
    assert transcription_txt(lines, {}) == "[00:00:00] Speaker 1: Hi.\n"


def test_no_segments_gives_an_empty_file() -> None:
    assert transcription_txt([], {}) == ""


@pytest.fixture
def recording_id(setup) -> str:  # type: ignore[no-untyped-def]
    stored = upload(setup["employee"], setup["product_id"], "text-file-clip-01", make_wav(1.0))
    assert stored.status_code == 201
    return stored.json()["recordingId"]  # type: ignore[no-any-return]


def test_not_ready_answers_404(setup, recording_id: str) -> None:  # type: ignore[no-untyped-def]
    missing = setup["employee"].get(f"/api/recordings/{recording_id}/transcription.txt")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "transcript_not_ready"
    missing = setup["employee"].get(f"/api/recordings/{recording_id}/summary.txt")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "transcript_not_ready"


def test_transcription_txt_downloads_with_names(  # type: ignore[no-untyped-def]
    setup, recording_id: str, db: Session, acme
) -> None:
    ana = add_person(db, acme["company"], "Ana")
    add_transcript(db, uuid.UUID(recording_id), [{"text": "Hello.", "person": ana}, {"text": "Hi."}])
    got = setup["employee"].get(f"/api/recordings/{recording_id}/transcription.txt")
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("text/plain")
    assert 'filename="transcription.txt"' in got.headers["content-disposition"]
    assert got.text == "[00:00:00] Ana: Hello.\n[00:00:01] Speaker 2: Hi.\n"


def test_placeholder_is_marked(setup, recording_id: str, db: Session) -> None:  # type: ignore[no-untyped-def]
    transcript = add_transcript(db, uuid.UUID(recording_id), [{"text": "Lorem."}])
    transcript.is_placeholder = True
    db.commit()
    got = setup["employee"].get(f"/api/recordings/{recording_id}/transcription.txt")
    assert got.text.startswith(PLACEHOLDER_LINE + "\n")


def test_summary_txt_returns_the_saved_summary(setup, recording_id: str, db: Session) -> None:  # type: ignore[no-untyped-def]
    add_transcript(db, uuid.UUID(recording_id), [{"text": "We ship Friday."}])
    missing = setup["employee"].get(f"/api/recordings/{recording_id}/summary.txt")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "summary_not_ready"
    transcript = db.scalar(select(Transcript).where(Transcript.recording_id == uuid.UUID(recording_id)))
    assert transcript is not None
    transcript.summary_text = "Summary\nShip on Friday."
    transcript.summary_model = "gemini-test"
    db.commit()
    got = setup["employee"].get(f"/api/recordings/{recording_id}/summary.txt")
    assert got.status_code == 200 and got.text == "Summary\nShip on Friday.\n"
    assert 'filename="summary.txt"' in got.headers["content-disposition"]


def test_other_users_cannot_download(app, setup, recording_id: str, db: Session) -> None:  # type: ignore[no-untyped-def]
    from tests.test_lockets import as_user

    add_transcript(db, uuid.UUID(recording_id), [{"text": "Private."}])
    outsider = as_user(app, "employee@globex.example.com")
    assert outsider.get(f"/api/recordings/{recording_id}/transcription.txt").status_code in (401, 404)


@pytest.fixture
def setup(app, acme, globex):  # type: ignore[no-untyped-def]
    return make_setup(app)
