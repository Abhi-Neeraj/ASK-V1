"""The phone uploads what the locket sent it over Bluetooth. Who may see it: the people on the locket when it
arrived (and managers), exactly like the wearer rule for clips the locket uploads itself."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import httpx2 as httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProcessingJob, Recording
from tests.helpers import WebClient, make_wav
from tests.test_lockets import OTHER_NONCE, as_user, pair, register


def upload(
    client: WebClient, product_id: str, clip: str, body: bytes, *, csrf: bool = True
) -> httpx.Response:
    return client.request(
        "PUT",
        f"/api/lockets/{product_id}/recordings/{clip}",
        content=body,
        csrf=csrf,
        headers={
            "Content-Type": "audio/wav",
            "X-Content-SHA256": hashlib.sha256(body).hexdigest(),
            "X-Recorded-At": datetime.now(UTC).isoformat(),
        },
    )


@pytest.fixture
def setup(app, acme):  # type: ignore[no-untyped-def]
    return make_setup(app)


def make_setup(app):  # type: ignore[no-untyped-def]
    """Acme's admin registers a locket and the employee pairs with it."""
    admin = as_user(app, "admin@acme.example.com")
    employee = as_user(app, "employee@acme.example.com")
    manager = as_user(app, "manager@acme.example.com")
    product_id = register(admin)["productId"]
    assert pair(employee, product_id) == 204
    return {"admin": admin, "employee": employee, "manager": manager, "product_id": product_id}


def test_member_uploads_and_a_job_is_queued(setup, db: Session) -> None:  # type: ignore[no-untyped-def]
    stored = upload(setup["employee"], setup["product_id"], "phone-clip-0001", make_wav(2.0))
    assert stored.status_code == 201, stored.text
    recording_id = uuid.UUID(stored.json()["recordingId"])
    assert db.scalar(select(ProcessingJob).where(ProcessingJob.recording_id == recording_id)) is not None
    recording = db.get(Recording, recording_id)
    assert recording is not None and recording.state == "queued"


def test_upload_is_idempotent(setup) -> None:  # type: ignore[no-untyped-def]
    body = make_wav(2.0)
    first = upload(setup["employee"], setup["product_id"], "phone-clip-0002", body)
    again = upload(setup["employee"], setup["product_id"], "phone-clip-0002", body)
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["duplicate"] is True and again.json()["recordingId"] == first.json()["recordingId"]


def test_non_member_gets_404(app, setup) -> None:  # type: ignore[no-untyped-def]
    stranger = as_user(app, "admin@acme.example.com")  # an admin who is not on this locket
    assert upload(stranger, setup["product_id"], "phone-clip-0003", make_wav(1.0)).status_code == 404


def test_csrf_is_required(setup) -> None:  # type: ignore[no-untyped-def]
    refused = upload(setup["employee"], setup["product_id"], "phone-clip-0004", make_wav(1.0), csrf=False)
    assert refused.status_code == 403


def test_not_a_wav_is_refused(setup) -> None:  # type: ignore[no-untyped-def]
    assert upload(setup["employee"], setup["product_id"], "phone-clip-0005", b"x" * 100).status_code == 422


def test_a_refused_upload_is_logged_with_its_reason(setup, caplog) -> None:  # type: ignore[no-untyped-def]
    """A refusal is written to the server log (code, locket, clip), so a hosted failure can be read there."""
    with caplog.at_level("WARNING", logger="ask.lockets"):
        refused = upload(setup["employee"], setup["product_id"], "phone-clip-0006", b"x" * 100)
    assert refused.status_code == 422
    lines = [r.getMessage() for r in caplog.records if r.name == "ask.lockets"]
    wanted = ("invalid_audio", setup["product_id"], "phone-clip-0006")
    assert any(all(part in line for part in wanted) for line in lines), lines


def test_a_refused_upload_log_never_holds_the_audio_or_its_path(setup, caplog) -> None:  # type: ignore[no-untyped-def]
    with caplog.at_level("WARNING", logger="ask.lockets"):
        upload(setup["employee"], setup["product_id"], "phone-clip-0007", b"x" * 100)
    text = " ".join(r.getMessage() for r in caplog.records if r.name == "ask.lockets")
    assert "xxxx" not in text and "/" not in text.replace(setup["product_id"], "")


def test_every_member_at_arrival_can_see_it(setup) -> None:  # type: ignore[no-untyped-def]
    setup["admin"].patch(f"/api/lockets/{setup['product_id']}/limit", json={"maxUsers": 2})
    assert pair(setup["manager"], setup["product_id"], OTHER_NONCE) == 204
    stored = upload(setup["employee"], setup["product_id"], "phone-clip-0006", make_wav(1.0))
    recording_id = stored.json()["recordingId"]
    assert setup["employee"].get(f"/api/recordings/{recording_id}").status_code == 200
    assert setup["manager"].get(f"/api/recordings/{recording_id}").status_code == 200


def test_a_new_member_does_not_see_older_recordings(app, setup, db: Session, staff_ben) -> None:  # type: ignore[no-untyped-def]
    stored = upload(setup["employee"], setup["product_id"], "phone-clip-0007", make_wav(1.0))
    recording_id = stored.json()["recordingId"]
    setup["admin"].patch(f"/api/lockets/{setup['product_id']}/limit", json={"maxUsers": 2})
    ben = as_user(app, "ben@acme.example.com")
    assert pair(ben, setup["product_id"], OTHER_NONCE) == 204
    assert ben.get(f"/api/recordings/{recording_id}").status_code == 404
    assert ben.get("/api/recordings").json()["total"] == 0


def test_removed_member_keeps_what_arrived_while_they_were_on(setup, acme) -> None:  # type: ignore[no-untyped-def]
    stored = upload(setup["employee"], setup["product_id"], "phone-clip-0008", make_wav(1.0))
    recording_id = stored.json()["recordingId"]
    me = acme["employee"].id
    assert setup["employee"].delete(f"/api/lockets/{setup['product_id']}/members/{me}").status_code == 204
    assert setup["employee"].get(f"/api/recordings/{recording_id}").status_code == 200


@pytest.fixture
def staff_ben(db: Session, acme):  # type: ignore[no-untyped-def]
    from tests.helpers import add_member

    return add_member(db, acme["company"], "ben@acme.example.com", "employee", name="Ben")


def test_the_uploader_is_the_wearer_and_every_member_hears_about_it(setup, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
    from app.services import events as feed

    setup["admin"].patch(f"/api/lockets/{setup['product_id']}/limit", json={"maxUsers": 2})
    assert pair(setup["manager"], setup["product_id"], OTHER_NONCE) == 204
    stored = upload(setup["manager"], setup["product_id"], "phone-clip-0009", make_wav(1.0))
    recording = db.get(Recording, uuid.UUID(stored.json()["recordingId"]))
    assert recording is not None and recording.assigned_user_id == acme["manager"].id

    viewer = feed.Viewer(
        user_id=acme["employee"].id, company_id=acme["company"].id, can_manage=False, session_id=uuid.uuid4()
    )
    events, _ = feed.read_new_events(db, viewer, 0)
    assert any(
        e.type == "recording.uploaded" and e.payload["recordingId"] == str(recording.id) for e in events
    )
