from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import AuditEvent, ConsentRecord, ContactMessage, Person, TranscriptSegment, VoiceProfile
from app.services.email import MemoryEmailSender
from tests.conftest import new_web, provision_device
from tests.helpers import add_person, add_transcript, make_wav


def login(app, email: str):  # type: ignore[no-untyped-def]
    web = new_web(app)
    web.login(email)
    return web


class TestPeople:
    def test_managers_create_people_and_everyone_in_the_company_can_list_them(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = login(app, "manager@acme.example.com")
        created = manager.post("/api/people", json={"displayName": "  Priya Nair "})
        assert created.status_code == 201
        assert created.json()["displayName"] == "Priya Nair"
        assert created.json()["consentStatus"] == "pending"
        assert created.json()["enrolled"] is False and created.json()["sampleCount"] == 0

        employee = login(app, "employee@acme.example.com")
        assert [p["displayName"] for p in employee.get("/api/people").json()] == ["Priya Nair"]
        assert employee.post("/api/people", json={"displayName": "Not Allowed"}).status_code == 403

    def test_names_are_validated(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = login(app, "manager@acme.example.com")
        assert manager.post("/api/people", json={"displayName": "A"}).status_code == 422
        assert manager.post("/api/people", json={"displayName": "x" * 121}).status_code == 422

    def test_consent_is_recorded_as_an_append_only_history(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        manager = login(app, "manager@acme.example.com")
        pid = manager.post("/api/people", json={"displayName": "Priya Nair"}).json()["id"]
        granted = manager.post(f"/api/people/{pid}/consent", json={"granted": True})
        assert granted.status_code == 200
        assert (
            granted.json()["consentStatus"] == "granted" and granted.json()["consentRecordedAt"] is not None
        )
        manager.post(
            f"/api/people/{pid}/consent",
            json={"granted": False, "statement": "Withdrawn by email on request."},
        )
        records = db.scalars(select(ConsentRecord).order_by(ConsentRecord.recorded_at)).all()
        assert [r.status for r in records] == ["granted", "revoked"]
        assert records[1].statement == "Withdrawn by email on request."
        assert records[0].recorded_by == acme["manager"].id
        assert {"person.consent_granted", "person.consent_revoked"} <= set(
            db.scalars(select(AuditEvent.action))
        )

    def test_withdrawing_consent_deletes_the_voice_and_forgets_past_matches(
        self, app, acme, db: Session
    ) -> None:  # type: ignore[no-untyped-def]
        manager = login(app, "manager@acme.example.com")
        _, locket = provision_device(app, manager)
        person = add_person(db, acme["company"], "Priya Nair")
        db.add(
            VoiceProfile(
                person_id=person.id,
                company_id=acme["company"].id,
                embedding=b"\x01" * 16,
                embedding_dim=4,
                model_name="m",
                model_version="1",
                sample_count=3,
            )
        )
        db.commit()
        rid = locket.upload("clip-0001-aaaa", make_wav(), recorded_at=datetime.now(UTC).isoformat()).json()[
            "recordingId"
        ]
        import uuid as _uuid

        add_transcript(
            db, _uuid.UUID(rid), [{"text": "Hello there everyone.", "person": person, "confidence": 0.9}]
        )

        listed = manager.get("/api/people").json()[0]
        assert listed["enrolled"] is True and listed["sampleCount"] == 3
        assert "embedding" not in str(listed).lower()  # the voice data itself is never returned

        manager.post(f"/api/people/{person.id}/consent", json={"granted": False})
        db.expire_all()
        assert db.scalar(select(func.count()).select_from(VoiceProfile)) == 0
        segment = db.scalar(select(TranscriptSegment))
        assert segment is not None and segment.person_id is None and segment.confidence is None
        assert segment.text == "Hello there everyone."  # the words stay, the attribution goes
        assert manager.get("/api/people").json()[0]["enrolled"] is False

    def test_deleting_a_voice_profile_keeps_the_person(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        manager = login(app, "manager@acme.example.com")
        person = add_person(db, acme["company"], "Marcus Webb")
        db.add(
            VoiceProfile(
                person_id=person.id,
                company_id=acme["company"].id,
                embedding=b"\x02" * 16,
                embedding_dim=4,
                model_name="m",
                model_version="1",
                sample_count=3,
            )
        )
        db.commit()
        assert manager.delete(f"/api/people/{person.id}/voice-profile").status_code == 204
        assert manager.delete(f"/api/people/{person.id}/voice-profile").status_code == 204  # idempotent
        listing = manager.get("/api/people").json()
        assert listing[0]["displayName"] == "Marcus Webb" and listing[0]["enrolled"] is False
        assert "person.voice_profile_deleted" in set(db.scalars(select(AuditEvent.action)))

    def test_deleting_a_person_removes_them_and_their_voice(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        manager = login(app, "manager@acme.example.com")
        person = add_person(db, acme["company"], "Sam Okafor")
        db.add(
            VoiceProfile(
                person_id=person.id,
                company_id=acme["company"].id,
                embedding=b"\x03" * 16,
                embedding_dim=4,
                model_name="m",
                model_version="1",
                sample_count=3,
            )
        )
        db.commit()
        assert manager.delete(f"/api/people/{person.id}").status_code == 204
        assert manager.get("/api/people").json() == []
        assert db.scalar(select(func.count()).select_from(VoiceProfile)) == 0

    def test_enrollment_needs_consent_and_the_speaker_model(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = login(app, "manager@acme.example.com")
        pid = manager.post("/api/people", json={"displayName": "Priya Nair"}).json()["id"]
        blocked = manager.post(f"/api/people/{pid}/enroll")
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "consent_required"
        manager.post(f"/api/people/{pid}/consent", json={"granted": True})
        pending = manager.post(f"/api/people/{pid}/enroll")
        assert pending.status_code == 501 and pending.json()["error"]["code"] == "not_available"

    def test_another_company_cannot_see_or_change_people(self, app, acme, globex, db: Session) -> None:  # type: ignore[no-untyped-def]
        person = add_person(db, acme["company"], "Priya Nair")
        outsider = login(app, "admin@globex.example.com")
        assert outsider.get("/api/people").json() == []
        pid = person.id
        for response in (
            outsider.post(f"/api/people/{pid}/consent", json={"granted": True}),
            outsider.post(f"/api/people/{pid}/enroll"),
            outsider.delete(f"/api/people/{pid}/voice-profile"),
            outsider.delete(f"/api/people/{pid}"),
        ):
            assert response.status_code == 404
        db.expire_all()
        assert db.get(Person, pid) is not None


class TestSettings:
    def test_everyone_can_read_and_only_administrators_can_change(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        employee = login(app, "employee@acme.example.com")
        assert employee.get("/api/settings").json() == {"retentionDays": None, "purgeGraceDays": 7}
        assert employee.patch("/api/settings", json={"retentionDays": 30}).status_code == 403
        manager = login(app, "manager@acme.example.com")
        assert manager.patch("/api/settings", json={"retentionDays": 30}).status_code == 403

        admin = login(app, "admin@acme.example.com")
        updated = admin.patch("/api/settings", json={"retentionDays": 90, "purgeGraceDays": 14})
        assert updated.status_code == 200 and updated.json() == {"retentionDays": 90, "purgeGraceDays": 14}
        assert employee.get("/api/settings").json()["retentionDays"] == 90

    def test_null_clears_the_retention_period_and_absent_leaves_it_alone(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        admin = login(app, "admin@acme.example.com")
        admin.patch("/api/settings", json={"retentionDays": 30})
        assert admin.patch("/api/settings", json={"purgeGraceDays": 3}).json() == {
            "retentionDays": 30,
            "purgeGraceDays": 3,
        }
        assert admin.patch("/api/settings", json={"retentionDays": None}).json() == {
            "retentionDays": None,
            "purgeGraceDays": 3,
        }

    @pytest.mark.parametrize(
        "body", [{"retentionDays": 0}, {"retentionDays": -5}, {"purgeGraceDays": 366}, {"purgeGraceDays": -1}]
    )
    def test_values_are_range_checked(self, app, acme, body: dict[str, int]) -> None:  # type: ignore[no-untyped-def]
        assert login(app, "admin@acme.example.com").patch("/api/settings", json=body).status_code == 422

    def test_each_company_has_its_own_settings(self, app, acme, globex) -> None:  # type: ignore[no-untyped-def]
        login(app, "admin@acme.example.com").patch("/api/settings", json={"retentionDays": 30})
        assert login(app, "admin@globex.example.com").get("/api/settings").json()["retentionDays"] is None

    def test_changes_are_audited_without_secrets(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        login(app, "admin@acme.example.com").patch("/api/settings", json={"retentionDays": 30})
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "settings.updated"))
        assert event is not None and event.detail == {"retention_days": 30}


class TestContactForm:
    valid = {
        "name": "Sam Okafor",
        "email": "Sam@Example.com",
        "company": "Northwind",
        "message": "We would like a pilot for ten lockets next quarter.",
        "website": "",
        "elapsedMs": 9000,
    }

    def test_a_real_message_is_stored(self, web, db: Session) -> None:  # type: ignore[no-untyped-def]
        response = web.post("/api/contact", json=self.valid)
        assert response.status_code == 204
        stored = db.scalar(select(ContactMessage))
        assert stored is not None
        assert stored.email == "sam@example.com" and stored.company == "Northwind"
        assert stored.ip_hash is not None and "testclient" not in stored.ip_hash  # a hash, never the address

    def test_bots_get_the_same_answer_but_nothing_is_kept(self, web, db: Session) -> None:  # type: ignore[no-untyped-def]
        assert (
            web.post("/api/contact", json={**self.valid, "website": "http://spam.example"}).status_code == 204
        )
        assert web.post("/api/contact", json={**self.valid, "elapsedMs": 100}).status_code == 204
        assert db.scalar(select(func.count()).select_from(ContactMessage)) == 0

    def test_invalid_input_comes_back_as_field_errors(self, web) -> None:  # type: ignore[no-untyped-def]
        response = web.post("/api/contact", json={"name": "S", "email": "nope", "message": "short"})
        assert response.status_code == 422
        details = response.json()["error"]["details"]
        assert {"name", "email", "message"} <= set(details)

    def test_it_is_rate_limited(self, web) -> None:  # type: ignore[no-untyped-def]
        codes = [web.post("/api/contact", json=self.valid).status_code for _ in range(7)]
        assert codes[:5] == [204] * 5
        assert codes[-1] == 429

    def test_a_foreign_origin_is_refused(self, web) -> None:  # type: ignore[no-untyped-def]
        response = web.post("/api/contact", json=self.valid, headers={"Origin": "https://evil.example"})
        assert response.status_code == 403

    def test_messages_are_forwarded_when_a_recipient_is_configured(
        self, settings, engine, outbox: MemoryEmailSender
    ) -> None:  # type: ignore[no-untyped-def]
        from fastapi.testclient import TestClient

        from app.main import create_app
        from tests.helpers import WebClient

        configured = settings.model_copy(update={"contact_recipient": "team@example.com"})
        client = WebClient(TestClient(create_app(configured, engine=engine, email=outbox)))
        assert client.post("/api/contact", json=self.valid).status_code == 204
        assert outbox.outbox[-1].to == "team@example.com"
        assert "pilot for ten lockets" in outbox.outbox[-1].body


class TestSystem:
    def test_health_and_readiness_need_no_sign_in(self, web) -> None:  # type: ignore[no-untyped-def]
        assert web.get("/api/health").json() == {"status": "ok"}
        assert web.get("/api/ready").json() == {"status": "ready"}

    def test_readiness_reports_an_unwritable_storage_directory(self, web, app, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(app.state.storage, "is_writable", lambda: False)
        response = web.get("/api/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_unavailable"

    def test_readiness_reports_a_database_that_is_down(self, web, app, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        def refuse() -> None:
            raise OperationalError("SELECT 1", {}, Exception("database is down"))

        monkeypatch.setattr(app.state.engine, "connect", refuse)
        response = web.get("/api/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_unavailable"
        assert "database is down" not in response.text  # internals are logged, not returned

    def test_errors_use_one_envelope(self, web) -> None:  # type: ignore[no-untyped-def]
        missing = web.get("/api/no-such-route")
        assert missing.status_code == 404
        assert set(missing.json()) == {"error"} and missing.json()["error"]["code"] == "not_found"
        wrong_method = web.delete("/api/health")
        assert (
            wrong_method.status_code == 405 and wrong_method.json()["error"]["code"] == "method_not_allowed"
        )

    def test_internal_errors_never_leak_details(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        from fastapi.testclient import TestClient

        @app.get("/api/boom")
        def boom() -> None:
            raise RuntimeError("secret internal detail")

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/boom")
        assert response.status_code == 500
        assert response.json() == {
            "error": {"code": "internal_error", "message": "Something went wrong on our side."}
        }

    def test_interactive_docs_exist_outside_production(self, web) -> None:  # type: ignore[no-untyped-def]
        assert web.get("/api/openapi.json").status_code == 200
