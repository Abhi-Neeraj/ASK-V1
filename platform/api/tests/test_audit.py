from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.services.audit_log import scrub
from tests.conftest import new_web, provision_device
from tests.helpers import add_company, add_member

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def seed(db: Session, company, action: str, minutes_ago: int = 0, **fields) -> AuditEvent:  # type: ignore[no-untyped-def]
    event = AuditEvent(
        company_id=company.id, action=action, created_at=NOW - timedelta(minutes=minutes_ago), **fields
    )
    db.add(event)
    db.commit()
    return event


def admin_of(app, email: str = "admin@acme.example.com"):  # type: ignore[no-untyped-def]
    web = new_web(app)
    assert web.login(email).status_code == 200
    return web


class TestWhoCanRead:
    def test_only_administrators(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        assert admin_of(app).get("/api/audit").status_code == 200
        assert admin_of(app, "manager@acme.example.com").get("/api/audit").status_code == 403
        assert admin_of(app, "employee@acme.example.com").get("/api/audit").status_code == 403

    def test_not_without_signing_in(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        assert new_web(app).get("/api/audit").status_code == 401

    def test_a_locket_credential_does_not_open_it(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        _, locket = provision_device(app, admin_of(app))
        assert locket.request("GET", "/api/audit").status_code == 401


class TestWhatIsShown:
    def test_newest_first_with_who_did_it(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        seed(
            db, acme["company"], "person.created", 30, actor_user_id=acme["manager"].id, object_type="person"
        )
        seed(
            db,
            acme["company"],
            "settings.updated",
            5,
            actor_user_id=acme["admin"].id,
            object_type="company",
            detail={"retention_days": 30},
            ip="203.0.113.7",
        )

        body = admin_of(app).get("/api/audit").json()
        # The administrator's own sign-in is recorded too, and is the newest of the three.
        actions = [item["action"] for item in body["items"]]
        assert actions[0] == "auth.login"
        assert actions[1:] == ["settings.updated", "person.created"]
        settings_event = body["items"][1]
        assert settings_event["category"] == "settings"
        assert settings_event["actor"] == {
            "kind": "user",
            "id": str(acme["admin"].id),
            "name": acme["admin"].name,
            "email": "admin@acme.example.com",
        }
        assert settings_event["detail"] == {"retention_days": 30}
        assert settings_event["ip"] == "203.0.113.7"
        assert body["total"] == 3

    def test_real_actions_leave_a_trail_that_can_be_read(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        admin = admin_of(app)
        assert admin.patch("/api/settings", json={"retentionDays": 90}).status_code == 200
        manager = admin_of(app, "manager@acme.example.com")
        created = manager.post("/api/people", json={"displayName": "Priya Nair"})
        assert created.status_code == 201

        items = admin.get("/api/audit", params={"category": "person"}).json()["items"]
        assert [i["action"] for i in items] == ["person.created"]
        assert items[0]["actor"]["email"] == "manager@acme.example.com"
        assert items[0]["objectId"] == created.json()["id"]

        changed = admin.get("/api/audit", params={"action": "settings.updated"}).json()["items"]
        assert changed[0]["detail"] == {"retention_days": 90}

    def test_a_locket_is_named_as_the_actor(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        device, _ = provision_device(app, admin_of(app), name="Front desk")
        from uuid import UUID

        seed(db, acme["company"], "device.paired", actor_device_id=UUID(device["id"]), object_type="device")

        item = admin_of(app).get("/api/audit", params={"action": "device.paired"}).json()["items"][0]
        assert item["actor"] == {"kind": "device", "id": device["id"], "name": "Front desk", "email": None}

    def test_an_event_with_no_actor_has_none(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        seed(db, acme["company"], "recording.purged", object_type="recording")
        item = admin_of(app).get("/api/audit", params={"action": "recording.purged"}).json()["items"][0]
        assert item["actor"] is None


class TestOtherCompanies:
    def test_each_company_sees_only_its_own_events(self, app, acme, globex, db: Session) -> None:  # type: ignore[no-untyped-def]
        seed(db, acme["company"], "person.created", actor_user_id=acme["admin"].id)
        seed(db, globex["company"], "person.deleted", actor_user_id=globex["admin"].id)

        acme_actions = {i["action"] for i in admin_of(app).get("/api/audit").json()["items"]}
        globex_actions = {
            i["action"] for i in admin_of(app, "admin@globex.example.com").get("/api/audit").json()["items"]
        }
        assert "person.created" in acme_actions and "person.deleted" not in acme_actions
        assert "person.deleted" in globex_actions and "person.created" not in globex_actions

    def test_the_company_cannot_be_chosen_by_the_request(self, app, acme, globex, db: Session) -> None:  # type: ignore[no-untyped-def]
        seed(db, globex["company"], "person.deleted", actor_user_id=globex["admin"].id)
        admin = admin_of(app)
        # Asking for Globex's events by name, actor or company id finds nothing of Globex's.
        for params in ({"actorId": str(globex["admin"].id)}, {"action": "person.deleted"}):
            assert admin.get("/api/audit", params=params).json()["items"] == [], params
        # A company id in the request is not a thing the endpoint knows about: it is ignored, and the
        # answer is the same as without it, Acme's events only.
        plain = admin.get("/api/audit").json()
        with_company = admin.get("/api/audit", params={"companyId": str(globex["company"].id)}).json()
        assert with_company["total"] == plain["total"]
        assert "person.deleted" not in {i["action"] for i in with_company["items"]}

    def test_a_person_from_another_company_is_never_named(self, app, acme, globex, db: Session) -> None:  # type: ignore[no-untyped-def]
        # An event in Acme whose actor id belongs to someone who is only a member of Globex.
        seed(db, acme["company"], "person.created", actor_user_id=globex["employee"].id)
        item = admin_of(app).get("/api/audit", params={"action": "person.created"}).json()["items"][0]
        assert item["actor"] is None
        assert "globex" not in str(item).lower()

    def test_an_account_in_both_companies_is_named_in_each(self, app, acme, globex, db: Session) -> None:  # type: ignore[no-untyped-def]
        add_company  # noqa: B018  (both fixtures already created their companies)
        shared = add_member(db, acme["company"], "shared@example.com", "manager", name="Sam Shared")
        from app.models import Membership

        db.add(Membership(user_id=shared.id, company_id=globex["company"].id, role="employee"))
        db.commit()
        seed(db, acme["company"], "person.created", actor_user_id=shared.id)
        seed(db, globex["company"], "person.created", actor_user_id=shared.id)

        for email in ("admin@acme.example.com", "admin@globex.example.com"):
            items = (
                admin_of(app, email).get("/api/audit", params={"action": "person.created"}).json()["items"]
            )
            assert [i["actor"]["name"] for i in items] == ["Sam Shared"]


class TestFilters:
    def test_by_category_and_by_exact_action(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        for action in ("person.created", "person.deleted", "device.created", "recording.deleted"):
            seed(db, acme["company"], action)
        admin = admin_of(app)
        people = admin.get("/api/audit", params={"category": "person"}).json()
        assert sorted(i["action"] for i in people["items"]) == ["person.created", "person.deleted"]
        assert people["total"] == 2
        one = admin.get("/api/audit", params={"action": "device.created"}).json()
        assert [i["action"] for i in one["items"]] == ["device.created"]

    def test_a_category_does_not_match_a_longer_name(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        seed(db, acme["company"], "personal.thing")
        assert admin_of(app).get("/api/audit", params={"category": "person"}).json()["items"] == []

    def test_by_person_who_did_it(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        seed(db, acme["company"], "person.created", actor_user_id=acme["manager"].id)
        seed(db, acme["company"], "person.deleted", actor_user_id=acme["admin"].id)
        items = admin_of(app).get("/api/audit", params={"actorId": str(acme["manager"].id)}).json()["items"]
        assert [i["action"] for i in items] == ["person.created"]

    def test_by_what_it_was_done_to(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        seed(db, acme["company"], "person.created", object_type="person")
        seed(db, acme["company"], "device.created", object_type="device")
        items = admin_of(app).get("/api/audit", params={"objectType": "device"}).json()["items"]
        assert [i["action"] for i in items] == ["device.created"]

    def test_by_date_with_or_without_a_timezone(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        seed(db, acme["company"], "person.created", minutes_ago=24 * 60 * 3)  # Oct 17
        seed(db, acme["company"], "person.deleted", minutes_ago=0)  # Oct 20
        admin = admin_of(app)
        for since in ("2026-10-19T00:00:00Z", "2026-10-19T00:00:00", "2026-10-19"):
            items = admin.get("/api/audit", params={"dateFrom": since, "action": "person.deleted"}).json()
            assert [i["action"] for i in items["items"]] == ["person.deleted"], since
        before = admin.get("/api/audit", params={"dateTo": "2026-10-18", "category": "person"}).json()
        assert [i["action"] for i in before["items"]] == ["person.created"]

    def test_filters_combine(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        seed(db, acme["company"], "person.created", actor_user_id=acme["manager"].id)
        seed(db, acme["company"], "person.created", actor_user_id=acme["admin"].id)
        items = (
            admin_of(app)
            .get("/api/audit", params={"category": "person", "actorId": str(acme["manager"].id)})
            .json()["items"]
        )
        assert len(items) == 1

    def test_values_that_could_not_be_real_are_refused_not_run(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        admin = admin_of(app)
        for params in (
            {"category": "everything"},
            {"action": "person.created' OR '1'='1"},
            {"action": "x" * 61},
            {"objectType": "person; drop table audit_events"},
            {"actorId": "not-a-uuid"},
            {"dateFrom": "yesterday"},
            {"page": 0},
            {"pageSize": 101},
        ):
            assert admin.get("/api/audit", params=params).status_code == 422, params


class TestPaging:
    def test_pages_cover_everything_once(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        for n in range(7):
            seed(db, acme["company"], "person.created", minutes_ago=n)
        admin = admin_of(app)
        seen: list[str] = []
        for page in (1, 2, 3):
            body = admin.get("/api/audit", params={"category": "person", "pageSize": 3, "page": page}).json()
            assert body["total"] == 7 and body["page"] == page and body["pageSize"] == 3
            seen += [i["id"] for i in body["items"]]
        assert len(seen) == 7 and len(set(seen)) == 7
        assert (
            admin.get("/api/audit", params={"category": "person", "pageSize": 3, "page": 4}).json()["items"]
            == []
        )

    def test_events_at_the_same_moment_keep_a_stable_order(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        for _ in range(6):
            seed(db, acme["company"], "person.created", minutes_ago=1)
        admin = admin_of(app)
        first = [
            i["id"]
            for i in admin.get("/api/audit", params={"category": "person", "pageSize": 3}).json()["items"]
        ]
        second = [
            i["id"]
            for i in admin.get("/api/audit", params={"category": "person", "pageSize": 3, "page": 2}).json()[
                "items"
            ]
        ]
        assert not set(first) & set(second)


class TestNothingSensitiveIsShown:
    def test_detail_is_scrubbed_on_the_way_out(self, app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
        seed(
            db,
            acme["company"],
            "person.created",
            detail={
                "count": 3,
                "password": "hunter2",
                "new_password": "hunter3",
                "credential": "abc.def",
                "session_token": "tok",
                "embedding": [0.1, 0.2],
                "storage_key": "acme/2026/10/x.wav",
                "audio_path": "C:/data/x.wav",
                "sha256_hash": "00",
                "nested": {"secret": "s", "fine": True},
            },
        )
        item = admin_of(app).get("/api/audit", params={"action": "person.created"}).json()["items"][0]
        assert item["detail"] == {"count": 3, "nested": {"fine": True}}
        text = str(item)
        for leaked in ("hunter", "abc.def", "tok", "x.wav", "0.1"):
            assert leaked not in text

    def test_logging_in_records_who_and_from_where_but_not_the_password(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = new_web(app)
        web.login("admin@acme.example.com", "wrong-password-entirely")
        admin = admin_of(app)
        body = admin.get("/api/audit", params={"category": "auth"}).text
        assert "auth.login_failed" in body and "auth.login" in body
        assert "wrong-password-entirely" not in body
        assert "correct-horse-battery-staple" not in body


class TestScrub:
    def test_keeps_plain_values(self) -> None:
        assert scrub({"a": 1, "b": "text", "c": None, "d": True, "e": 1.5}) == {
            "a": 1,
            "b": "text",
            "c": None,
            "d": True,
            "e": 1.5,
        }

    def test_hides_sensitive_keys_in_any_case(self) -> None:
        assert scrub({"Password": "x", "API_TOKEN": "x", "Embedding": "x", "ok": 1}) == {"ok": 1}

    def test_limits_text_depth_and_length(self) -> None:
        assert scrub({"t": "x" * 500})["t"].endswith("…") and len(scrub({"t": "x" * 500})["t"]) == 201
        deep = {"a": {"b": {"c": {"d": 1}}}}
        assert scrub(deep) == {"a": {"b": {"c": "…"}}}
        assert len(scrub({"l": list(range(100))})["l"]) == 20
        assert len(scrub({str(n): n for n in range(100)})) == 20

    def test_turns_unknown_objects_into_short_text(self) -> None:
        class Odd:
            def __str__(self) -> str:
                return "odd" * 200

        assert len(scrub({"o": Odd()})["o"]) == 200
