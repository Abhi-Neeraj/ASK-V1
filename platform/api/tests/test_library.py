from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import timeutil
from app.models import (
    AuditEvent,
    Company,
    ProcessingJob,
    Recording,
    Transcript,
    TranscriptSegment,
)
from app.services import recordings as recording_service
from app.services.storage import Storage
from tests.conftest import new_web, provision_device
from tests.helpers import add_person, add_transcript, make_wav

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)


def login(app, email: str = "manager@acme.example.com"):  # type: ignore[no-untyped-def]
    web = new_web(app)
    web.login(email)
    return web


@pytest.fixture
def library(app, acme, db: Session):  # type: ignore[no-untyped-def]
    """Three devices' worth of clips with transcripts, spread over several days."""
    web = login(app)
    first, locket = provision_device(app, web, "Front desk")
    second, other_locket = provision_device(app, web, "Warehouse")
    priya = add_person(db, acme["company"], "Priya Nair")
    marcus = add_person(db, acme["company"], "Marcus Webb")

    def upload(client, client_id: str, seconds: float, days_ago: int, freq: int, **extra):  # type: ignore[no-untyped-def]
        when = (NOW - timedelta(days=days_ago)).isoformat()
        response = client.upload(
            client_id, make_wav(seconds, frequency=freq), recorded_at=when, extra=extra or None
        )
        assert response.status_code == 201, response.text
        return uuid.UUID(response.json()["recordingId"])

    a = upload(locket, "clip-delivery-0001", 3.0, 1, 300)
    b = upload(locket, "clip-purchase-0002", 8.0, 2, 350)
    c = upload(other_locket, "clip-safety-0003", 5.0, 5, 400)
    d = upload(other_locket, "clip-pending-0004", 2.0, 0, 450)  # never transcribed

    add_transcript(
        db,
        a,
        [
            {
                "text": "Let's confirm the delivery window for the east warehouse.",
                "person": priya,
                "confidence": 0.93,
            },
            {"text": "Friday works if the truck leaves before noon.", "person": marcus, "confidence": 0.88},
        ],
    )
    add_transcript(
        db,
        b,
        [
            {"text": "Can you check the purchase order status today?", "person": priya, "confidence": 0.9},
            {"text": "I'll send the files.", "person": None, "needs_review": True},
        ],
    )
    add_transcript(
        db,
        c,
        [
            {
                "text": "We should move the safety briefing to 100% of shifts.",
                "person": marcus,
                "confidence": 0.8,
            }
        ],
    )
    return {
        "web": web,
        "first": first,
        "second": second,
        "ids": {"a": a, "b": b, "c": c, "d": d},
        "priya": priya,
        "marcus": marcus,
        "locket": locket,
        "other_locket": other_locket,
    }


def titles(response) -> list[str]:  # type: ignore[no-untyped-def]
    return [item["id"] for item in response.json()["items"]]


class TestListing:
    def test_pages_newest_first_with_totals(self, library) -> None:  # type: ignore[no-untyped-def]
        web = library["web"]
        first = web.get("/api/recordings?pageSize=3").json()
        assert first["total"] == 4 and first["page"] == 1 and first["pageSize"] == 3
        assert len(first["items"]) == 3
        assert first["items"][0]["id"] == str(library["ids"]["d"])  # newest
        second = web.get("/api/recordings?pageSize=3&page=2").json()
        assert len(second["items"]) == 1
        everything = [i["id"] for i in first["items"] + second["items"]]
        assert len(set(everything)) == 4  # no overlaps or gaps across pages

    def test_sorting(self, library) -> None:  # type: ignore[no-untyped-def]
        web = library["web"]
        longest_first = web.get("/api/recordings?sort=durationMs&order=desc").json()["items"]
        assert [i["durationMs"] for i in longest_first] == [8000, 5000, 3000, 2000]
        oldest_first = web.get("/api/recordings?order=asc").json()["items"]
        assert oldest_first[0]["id"] == str(library["ids"]["c"])

    def test_limits_are_enforced(self, library) -> None:  # type: ignore[no-untyped-def]
        web = library["web"]
        assert web.get("/api/recordings?pageSize=101").status_code == 422
        assert web.get("/api/recordings?page=0").status_code == 422
        assert web.get("/api/recordings?sort=password").status_code == 422

    def test_rows_summarize_the_transcript_and_speakers(self, library) -> None:  # type: ignore[no-untyped-def]
        web = library["web"]
        items = {i["id"]: i for i in web.get("/api/recordings").json()["items"]}
        a = items[str(library["ids"]["a"])]
        assert a["snippet"].startswith("Let's confirm the delivery window")
        assert a["speakers"] == ["Priya Nair", "Marcus Webb"]
        assert a["deviceName"] == "Front desk"
        b = items[str(library["ids"]["b"])]
        assert b["state"] == "needs_review"
        assert b["speakers"] == ["Priya Nair", "Unknown speaker"]
        d = items[str(library["ids"]["d"])]
        assert d["snippet"] is None and d["speakers"] == [] and d["state"] == "queued"

    def test_filters(self, library) -> None:  # type: ignore[no-untyped-def]
        web, ids = library["web"], library["ids"]
        by_device = titles(web.get(f"/api/recordings?deviceId={library['second']['id']}"))
        assert set(by_device) == {str(ids["c"]), str(ids["d"])}
        assert titles(web.get("/api/recordings?state=needs_review")) == [str(ids["b"])]
        assert titles(web.get("/api/recordings?state=queued")) == [str(ids["d"])]
        assert set(titles(web.get(f"/api/recordings?personId={library['marcus'].id}"))) == {
            str(ids["a"]),
            str(ids["c"]),
        }
        window = web.get(
            "/api/recordings",
            params={
                "dateFrom": (NOW - timedelta(days=3)).isoformat(),
                "dateTo": (NOW - timedelta(hours=12)).isoformat(),
            },
        )
        assert set(titles(window)) == {str(ids["a"]), str(ids["b"])}
        assert set(titles(web.get("/api/recordings?minDurationMs=4000"))) == {str(ids["b"]), str(ids["c"])}
        assert titles(web.get("/api/recordings?maxDurationMs=2500")) == [str(ids["d"])]

    def test_a_date_without_a_timezone_is_read_as_utc_not_a_crash(self, library) -> None:  # type: ignore[no-untyped-def]
        # Regression: "?dateFrom=2026-10-01" reached the database as a naive time and failed the request.
        web, ids = library["web"], library["ids"]
        every = set(titles(web.get("/api/recordings?pageSize=100")))
        for since in (
            "2000-01-01",
            "2000-01-01T00:00:00",
            "2000-01-01T00:00:00Z",
            "2000-01-01T05:30:00+05:30",
        ):
            response = web.get("/api/recordings", params={"dateFrom": since, "pageSize": 100})
            assert response.status_code == 200, (since, response.text)
            assert set(titles(response)) == every
        assert titles(web.get("/api/recordings", params={"dateTo": "2000-01-01"})) == []
        assert str(ids["a"]) in every

    def test_filters_combine(self, library) -> None:  # type: ignore[no-untyped-def]
        web, ids = library["web"], library["ids"]
        response = web.get(f"/api/recordings?deviceId={library['first']['id']}&minDurationMs=5000")
        assert titles(response) == [str(ids["b"])]


class TestSearch:
    def test_finds_text_case_insensitively(self, library) -> None:  # type: ignore[no-untyped-def]
        web = library["web"]
        assert titles(web.get("/api/recordings?q=PURCHASE order")) == [str(library["ids"]["b"])]

    def test_finds_by_speaker_name(self, library) -> None:  # type: ignore[no-untyped-def]
        web = library["web"]
        assert set(titles(web.get("/api/recordings?q=marcus"))) == {
            str(library["ids"]["a"]),
            str(library["ids"]["c"]),
        }

    def test_wildcard_characters_are_literal(self, library) -> None:  # type: ignore[no-untyped-def]
        web = library["web"]
        assert titles(web.get("/api/recordings", params={"q": "100%"})) == [str(library["ids"]["c"])]
        assert (
            web.get("/api/recordings", params={"q": "%"}).json()["total"] == 1
        )  # only the line that really contains it
        assert web.get("/api/recordings", params={"q": "_"}).json()["total"] == 0

    def test_no_match_is_an_empty_page_not_an_error(self, library) -> None:  # type: ignore[no-untyped-def]
        body = library["web"].get("/api/recordings?q=zzzz-not-present").json()
        assert body["total"] == 0 and body["items"] == []

    def test_search_only_looks_inside_the_callers_company(self, app, library, globex) -> None:  # type: ignore[no-untyped-def]
        outsider = login(app, "admin@globex.example.com")
        assert outsider.get("/api/recordings?q=delivery").json()["total"] == 0


class TestDetailAndAudio:
    def test_detail_includes_the_transcript(self, library) -> None:  # type: ignore[no-untyped-def]
        rid = library["ids"]["b"]
        body = library["web"].get(f"/api/recordings/{rid}").json()
        assert body["audioUrl"] == f"/api/recordings/{rid}/audio"
        segments = body["transcript"]["segments"]
        assert [s["text"] for s in segments] == [
            "Can you check the purchase order status today?",
            "I'll send the files.",
        ]
        assert segments[0]["person"]["name"] == "Priya Nair" and segments[0]["confidence"] == 0.9
        assert (
            segments[1]["person"] is None
            and segments[1]["confidence"] is None
            and segments[1]["needsReview"] is True
        )
        assert body["transcript"]["reviewStatus"] == "needs_review"
        assert "storage" not in str(body).lower()

    def test_a_recording_without_a_transcript_says_so(self, library) -> None:  # type: ignore[no-untyped-def]
        web, rid = library["web"], library["ids"]["d"]
        assert web.get(f"/api/recordings/{rid}").json()["transcript"] is None
        assert web.get(f"/api/recordings/{rid}/transcript").status_code == 404

    def test_audio_plays_and_supports_ranges(self, library, db: Session, settings) -> None:  # type: ignore[no-untyped-def]
        web, rid = library["web"], library["ids"]["a"]
        recording = db.get(Recording, rid)
        assert recording is not None
        original = Storage(settings.storage_dir).path_for(recording.storage_key).read_bytes()

        full = web.get(f"/api/recordings/{rid}/audio")
        assert full.status_code == 200
        assert full.headers["content-type"] == "audio/wav"
        assert full.content == original
        assert full.headers["content-disposition"].startswith("inline")
        assert "private" in full.headers["cache-control"]
        assert str(settings.storage_dir) not in str(full.headers)

        part = web.get(f"/api/recordings/{rid}/audio", headers={"Range": "bytes=0-99"})
        assert part.status_code == 206
        assert part.content == original[:100]

    def test_downloading_needs_a_manager_and_is_audited(self, app, library, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web, rid = library["web"], library["ids"]["a"]
        saved = web.get(f"/api/recordings/{rid}/audio?download=true")
        assert saved.status_code == 200
        assert saved.headers["content-disposition"].startswith("attachment")
        assert "recording.downloaded" in set(db.scalars(select(AuditEvent.action)))

        # An employee who wears the locket may listen to what it records for them, but not take a copy.
        web.patch(f"/api/devices/{library['first']['id']}", json={"assignedUserId": str(acme["employee"].id)})
        mine = library["locket"].upload("clip-employees-own-0005", make_wav(2.0)).json()["recordingId"]
        employee = login(app, "employee@acme.example.com")
        assert employee.get(f"/api/recordings/{mine}/audio").status_code == 200
        forbidden = employee.get(f"/api/recordings/{mine}/audio?download=true")
        assert forbidden.status_code == 403

    def test_audio_requires_a_session(self, app, library) -> None:  # type: ignore[no-untyped-def]
        rid = library["ids"]["a"]
        assert new_web(app).get(f"/api/recordings/{rid}/audio").status_code == 401


class TestEmployeeVisibility:
    def test_employees_see_only_what_was_recorded_while_the_locket_was_theirs(
        self, app, library, acme
    ) -> None:  # type: ignore[no-untyped-def]
        employee = login(app, "employee@acme.example.com")
        assert employee.get("/api/recordings").json()["total"] == 0
        assert employee.get(f"/api/recordings/{library['ids']['a']}").status_code == 404

        library["web"].patch(
            f"/api/devices/{library['first']['id']}", json={"assignedUserId": str(acme["employee"].id)}
        )
        # Being given the locket does not give them what it recorded before. (The full story, handovers and
        # live updates included, is in test_wearer_visibility.py.)
        assert employee.get("/api/recordings").json()["total"] == 0
        assert employee.get(f"/api/recordings/{library['ids']['a']}").status_code == 404
        assert employee.get("/api/summary").json()["devicesTotal"] == 1

        # What it records from now on is theirs, and the other locket's recordings never are.
        fresh = library["locket"].upload("clip-after-handover-0005", make_wav(2.0)).json()["recordingId"]
        seen = employee.get("/api/recordings").json()
        assert {i["id"] for i in seen["items"]} == {fresh}
        assert employee.get(f"/api/recordings/{library['ids']['c']}").status_code == 404


class TestCorrections:
    def segment_ids(self, library, key: str = "b") -> list[str]:  # type: ignore[no-untyped-def]
        body = library["web"].get(f"/api/recordings/{library['ids'][key]}").json()
        return [s["id"] for s in body["transcript"]["segments"]]

    def test_a_correction_updates_the_text_and_clears_the_review_flag(self, library, db: Session) -> None:  # type: ignore[no-untyped-def]
        web, rid = library["web"], library["ids"]["b"]
        _first, second = self.segment_ids(library)
        response = web.patch(
            f"/api/recordings/{rid}/transcript",
            json={"edits": [{"segmentId": second, "text": "  I will send the files by Friday.  "}]},
        )
        assert response.status_code == 200
        body = response.json()
        fixed = body["transcript"]["segments"][1]
        assert fixed["text"] == "I will send the files by Friday."
        assert fixed["corrected"] is True and fixed["needsReview"] is False
        assert body["transcript"]["reviewStatus"] == "reviewed"
        assert body["state"] == "completed"
        row = db.scalar(select(TranscriptSegment).where(TranscriptSegment.id == uuid.UUID(second)))
        assert row is not None and row.original_text == "I'll send the files."  # the model's output is kept
        full = db.scalar(select(Transcript.full_text).where(Transcript.recording_id == rid))
        assert full is not None and "by Friday" in full

    def test_the_audit_trail_records_that_it_happened_but_not_the_words(self, library, db: Session) -> None:  # type: ignore[no-untyped-def]
        web, rid = library["web"], library["ids"]["b"]
        _, second = self.segment_ids(library)
        web.patch(
            f"/api/recordings/{rid}/transcript",
            json={"edits": [{"segmentId": second, "text": "Highly confidential sentence."}]},
        )
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "transcript.corrected"))
        assert event is not None and event.detail == {"segments": 1, "changed": 1}
        assert "confidential" not in str(event.detail)

    def test_bad_edits_are_rejected(self, library) -> None:  # type: ignore[no-untyped-def]
        web, rid = library["web"], library["ids"]["b"]
        _, second = self.segment_ids(library)
        assert (
            web.patch(
                f"/api/recordings/{rid}/transcript", json={"edits": [{"segmentId": second, "text": "   "}]}
            ).status_code
            == 422
        )
        assert web.patch(f"/api/recordings/{rid}/transcript", json={"edits": []}).status_code == 422
        foreign = self.segment_ids(library, "a")[0]  # a segment of a different recording
        assert (
            web.patch(
                f"/api/recordings/{rid}/transcript",
                json={"edits": [{"segmentId": foreign, "text": "Hijack"}]},
            ).status_code
            == 422
        )
        assert (
            web.patch(
                f"/api/recordings/{library['ids']['d']}/transcript",
                json={"edits": [{"segmentId": second, "text": "x y"}]},
            ).status_code
            == 409
        )


class TestDeletion:
    def test_delete_hides_the_recording_now_and_purges_it_later(
        self, library, db: Session, settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        web, rid = library["web"], library["ids"]["d"]
        recording = db.get(Recording, rid)
        assert recording is not None
        audio = Storage(settings.storage_dir).path_for(recording.storage_key)
        assert audio.exists()

        assert web.delete(f"/api/recordings/{rid}").status_code == 204
        assert web.get(f"/api/recordings/{rid}").status_code == 404
        assert web.get(f"/api/recordings/{rid}/audio").status_code == 404
        assert str(rid) not in titles(web.get("/api/recordings"))
        assert audio.exists()  # still on disk during the grace period (7 days by default)

        db.expire_all()
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.recording_id == rid))
        assert job is not None and job.status == "failed"  # queued work is cancelled
        assert "recording.deleted" in set(db.scalars(select(AuditEvent.action)))

        storage = Storage(settings.storage_dir)
        assert recording_service.purge_deleted(db, storage) == 0  # too early
        later = timeutil.utcnow() + timedelta(days=8)
        assert recording_service.purge_deleted(db, storage, later) == 1
        assert not audio.exists()
        assert db.scalar(select(Recording).where(Recording.id == rid)) is None
        assert "recording.purged" in set(db.scalars(select(AuditEvent.action)))

    def test_purging_takes_the_transcript_with_it(self, library, db: Session, settings) -> None:  # type: ignore[no-untyped-def]
        web, rid = library["web"], library["ids"]["a"]
        web.delete(f"/api/recordings/{rid}")
        recording_service.purge_deleted(
            db, Storage(settings.storage_dir), timeutil.utcnow() + timedelta(days=30)
        )
        db.expire_all()
        assert db.scalar(select(Transcript).where(Transcript.recording_id == rid)) is None
        assert len(db.scalars(select(Transcript)).all()) == 2  # the other recordings keep theirs

    def test_the_grace_period_is_the_companys_setting(self, library, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web, rid = library["web"], library["ids"]["d"]
        admin = login_admin(library)
        admin.patch("/api/settings", json={"purgeGraceDays": 0})
        web.delete(f"/api/recordings/{rid}")
        db.expire_all()
        recording = db.get(Recording, rid)
        assert recording is not None and recording.purge_after is not None
        assert recording.purge_after - recording.deleted_at < timedelta(seconds=2)  # type: ignore[operator]

    def test_employees_cannot_delete(self, app, library, acme) -> None:  # type: ignore[no-untyped-def]
        library["web"].patch(
            f"/api/devices/{library['first']['id']}", json={"assignedUserId": str(acme["employee"].id)}
        )
        employee = login(app, "employee@acme.example.com")
        assert employee.delete(f"/api/recordings/{library['ids']['a']}").status_code == 403


def login_admin(library):  # type: ignore[no-untyped-def]
    web = new_web(library["web"].client.app)
    web.login("admin@acme.example.com")
    return web


class TestRetention:
    def test_old_recordings_are_moved_to_deletion_by_the_companys_policy(self, library, db: Session) -> None:  # type: ignore[no-untyped-def]
        # No policy yet: nothing is ever expired, however old.
        assert recording_service.enforce_retention(db, now=NOW + timedelta(days=3650)) == 0

        login_admin(library).patch("/api/settings", json={"retentionDays": 3})
        # Retention counts from when a clip was stored. These were uploaded just now, so make them as old as
        # the
        # recordings claim to be, as if each had been stored the moment it was made.
        db.execute(update(Recording).values(uploaded_at=Recording.started_at))
        db.commit()
        # Clips are 0, 1, 2 and 5 days old. Only the 5-day-old one is past a 3-day policy.
        assert recording_service.enforce_retention(db, now=NOW) == 1
        assert recording_service.enforce_retention(db, now=NOW) == 0  # never marked twice
        db.expire_all()
        expired = db.scalars(select(Recording).where(Recording.deleted_at.is_not(None))).all()
        assert [r.client_recording_id for r in expired] == ["clip-safety-0003"]
        assert "recording.retention_deleted" in set(db.scalars(select(AuditEvent.action)))
        assert library["web"].get(f"/api/recordings/{library['ids']['c']}").status_code == 404

    def test_one_companys_policy_never_touches_another_companys_recordings(
        self, library, globex, db: Session
    ) -> None:  # type: ignore[no-untyped-def]
        other = db.get(Company, globex["company"].id)
        assert other is not None
        other.retention_days = 1  # an aggressive policy, but for Globex only
        db.commit()
        assert recording_service.enforce_retention(db, now=NOW + timedelta(days=365)) == 0
        db.expire_all()
        assert all(r.deleted_at is None for r in db.scalars(select(Recording)))


class TestReprocessAndJobs:
    def test_a_failed_recording_can_be_queued_again(self, library, db: Session) -> None:  # type: ignore[no-untyped-def]
        web, rid = library["web"], library["ids"]["c"]
        recording = db.get(Recording, rid)
        assert recording is not None
        recording.state = "failed"
        recording.error = "Could not decode"
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.recording_id == rid))
        assert job is not None
        job.status, job.attempts = "failed", 3
        db.commit()

        response = web.post(f"/api/recordings/{rid}/reprocess")
        assert response.status_code == 202
        assert response.json()["state"] == "queued" and response.json()["retryCount"] == 1
        db.expire_all()
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.recording_id == rid))
        assert job is not None and job.status == "queued" and job.attempts == 0

        assert web.post(f"/api/recordings/{rid}/reprocess").status_code == 409  # already queued
        status = web.get(f"/api/jobs/{job.id}")
        assert status.status_code == 200 and status.json()["status"] == "queued"

    def test_employees_cannot_reprocess(self, app, library, acme) -> None:  # type: ignore[no-untyped-def]
        library["web"].patch(
            f"/api/devices/{library['first']['id']}", json={"assignedUserId": str(acme["employee"].id)}
        )
        assert (
            login(app, "employee@acme.example.com")
            .post(f"/api/recordings/{library['ids']['a']}/reprocess")
            .status_code
            == 403
        )


class TestSummary:
    def test_counts_come_from_the_data(self, library, db: Session) -> None:  # type: ignore[no-untyped-def]
        web = library["web"]
        library["locket"].heartbeat()
        body = web.get("/api/summary").json()
        assert body["devicesTotal"] == 2 and body["devicesOnline"] == 1
        assert body["needsReview"] == 1 and body["inProgress"] == 1 and body["failed"] == 0
        recording = db.get(Recording, library["ids"]["c"])
        assert recording is not None
        recording.state = "failed"
        db.commit()
        assert web.get("/api/summary").json()["failed"] == 1


class TestTenantIsolation:
    def test_another_company_cannot_reach_anything_about_a_recording(self, app, library, globex) -> None:  # type: ignore[no-untyped-def]
        outsider = login(app, "admin@globex.example.com")
        rid = library["ids"]["a"]
        segment = library["web"].get(f"/api/recordings/{rid}").json()["transcript"]["segments"][0]["id"]
        assert outsider.get("/api/recordings").json() == {"items": [], "total": 0, "page": 1, "pageSize": 20}
        for response in (
            outsider.get(f"/api/recordings/{rid}"),
            outsider.get(f"/api/recordings/{rid}/audio"),
            outsider.get(f"/api/recordings/{rid}/audio?download=true"),
            outsider.get(f"/api/recordings/{rid}/transcript"),
            outsider.patch(
                f"/api/recordings/{rid}/transcript",
                json={"edits": [{"segmentId": segment, "text": "Tampered"}]},
            ),
            outsider.delete(f"/api/recordings/{rid}"),
            outsider.post(f"/api/recordings/{rid}/reprocess"),
        ):
            assert response.status_code == 404, response.request.url
        assert (
            library["web"]
            .get(f"/api/recordings/{rid}")
            .json()["transcript"]["segments"][0]["text"]
            .startswith("Let's confirm")
        )

    def test_a_recording_can_not_be_found_through_a_job_id_either(
        self, app, library, globex, db: Session
    ) -> None:  # type: ignore[no-untyped-def]
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.recording_id == library["ids"]["a"]))
        assert job is not None
        assert login(app, "admin@globex.example.com").get(f"/api/jobs/{job.id}").status_code == 404

    def test_storage_files_are_namespaced_by_company(self, library, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        for recording in db.scalars(select(Recording)):
            assert recording.storage_key.startswith(str(acme["company"].id) + "/")


def test_files_are_where_the_database_says(library, db: Session, settings) -> None:  # type: ignore[no-untyped-def]
    storage = Storage(settings.storage_dir)
    for recording in db.scalars(select(Recording)):
        assert storage.exists(recording.storage_key)
    assert len(list(Path(settings.storage_dir).rglob("*.wav"))) == 4
