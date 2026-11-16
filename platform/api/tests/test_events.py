"""The live event feed: who may hear what, resuming after a dropped connection, and staying bounded."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app import timeutil
from app.config import Settings
from app.db import make_session_factory
from app.main import create_app
from app.models import AuthSession, Device, LiveEvent, User
from app.routers import events as events_router
from app.services import events as feed
from app.services.audit import publish_event
from app.services.email import MemoryEmailSender
from app.services.housekeeping import run_housekeeping
from tests.conftest import new_web, provision_device
from tests.helpers import add_company, add_member


def event(type_: str, payload: dict[str, object], id_: int = 1) -> feed.FeedEvent:
    return feed.FeedEvent(id=id_, type=type_, payload=payload, created_at=timeutil.utcnow())


def viewer_for(user, company, *, can_manage: bool) -> feed.Viewer:  # type: ignore[no-untyped-def]
    return feed.Viewer(user_id=user.id, company_id=company.id, can_manage=can_manage, session_id=uuid.uuid4())


def parse_stream(body: str) -> list[dict[str, object]]:
    """Turn a text/event-stream body into [{id, event, data}] (comments and control lines skipped)."""
    parsed: list[dict[str, object]] = []
    for block in body.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and line[0] != ":")
        if "event" in fields:
            parsed.append(
                {
                    "id": fields.get("id"),
                    "event": fields["event"],
                    "data": json.loads(fields.get("data", "{}")),
                }
            )
    return parsed


@pytest.fixture
def fast_app(settings: Settings, engine: Engine, outbox: MemoryEmailSender):  # type: ignore[no-untyped-def]
    """The same application with a stream that polls quickly and ends itself, so tests can read it whole."""
    quick = settings.model_copy(
        update={
            "event_poll_interval_seconds": 0.05,
            "event_keepalive_seconds": 0.2,
            "event_stream_max_seconds": 0.6,
        }
    )
    return create_app(quick, engine=engine, email=outbox)


def read_stream(web, **kwargs) -> tuple[int, dict[str, str], str]:  # type: ignore[no-untyped-def]
    with web.client.stream("GET", "/api/events", **kwargs) as response:
        status = response.status_code
        headers = dict(response.headers)
        body = "".join(response.iter_text()) if status == 200 else response.read().decode()
    return status, headers, body


class TestParsingAndFormatting:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("0", 0),
            ("42", 42),
            (" 7 ", 7),
            (None, None),
            ("", None),
            ("abc", None),
            ("-1", None),
            ("1.5", None),
            ("1e3", None),
            ("١٢٣", None),  # Arabic-Indic digits are digits to str.isdigit() but not IDs we issued
            ("9" * 40, None),
            (str(2**63), None),
        ],
    )
    def test_last_event_id_is_read_strictly(self, raw: str | None, expected: int | None) -> None:
        assert feed.parse_event_id(raw) == expected

    def test_an_event_is_one_json_line_even_if_a_name_contains_line_breaks(self) -> None:
        text = feed.format_event(event("device.online", {"name": "Evil\nid: 999\nevent: x"}, id_=5))
        assert text.startswith("id: 5\nevent: device.online\ndata: ")
        assert text.endswith("\n\n")
        # Exactly three lines before the blank terminator: nothing the name says can add a field.
        assert text.count("\n") == 4
        payload = json.loads(text.splitlines()[2].removeprefix("data: "))
        assert payload["data"]["name"] == "Evil\nid: 999\nevent: x"


class TestWhoMayHearWhat:
    def test_managers_hear_everything_in_their_company(self, acme) -> None:  # type: ignore[no-untyped-def]
        manager = viewer_for(acme["manager"], acme["company"], can_manage=True)
        assert feed.is_visible(event("device.paired", {"deviceId": "d1"}), manager, set())
        assert feed.is_visible(event("something.without.a.device", {}), manager, set())

    def test_employees_hear_only_about_their_own_lockets(self, acme) -> None:  # type: ignore[no-untyped-def]
        employee = viewer_for(acme["employee"], acme["company"], can_manage=False)
        assert feed.is_visible(event("device.online", {"deviceId": "mine"}), employee, {"mine"})
        assert not feed.is_visible(event("device.online", {"deviceId": "someone-elses"}), employee, {"mine"})
        assert not feed.is_visible(event("anything", {}), employee, {"mine"})  # no device, no business

    def test_assignment_events_reach_both_the_old_and_the_new_owner_only(self, acme) -> None:  # type: ignore[no-untyped-def]
        employee = viewer_for(acme["employee"], acme["company"], can_manage=False)
        gained = event("device.assigned", {"deviceId": "d", "from": None, "to": str(employee.user_id)})
        lost = event("device.assigned", {"deviceId": "d", "from": str(employee.user_id), "to": None})
        other = event("device.assigned", {"deviceId": "d", "from": None, "to": str(uuid.uuid4())})
        assert feed.is_visible(gained, employee, set())
        assert feed.is_visible(lost, employee, set())
        assert not feed.is_visible(other, employee, set())

    def test_reading_never_crosses_companies_and_the_cursor_moves_past_filtered_events(
        self,
        db: Session,
        acme,
        globex,  # type: ignore[no-untyped-def]
    ) -> None:
        publish_event(db, globex["company"].id, "device.online", {"deviceId": "g"})
        mine = publish_event(db, acme["company"].id, "device.online", {"deviceId": "a"})
        db.commit()

        manager = viewer_for(acme["manager"], acme["company"], can_manage=True)
        events, cursor = feed.read_new_events(db, manager, 0)
        assert [e.payload["deviceId"] for e in events] == ["a"]
        assert cursor == mine.id

        employee = viewer_for(acme["employee"], acme["company"], can_manage=False)
        events, cursor = feed.read_new_events(db, employee, 0)
        assert events == []  # not their locket...
        assert cursor == mine.id  # ...but it is not read again

    def test_a_viewer_with_nothing_new_gets_nothing(self, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        manager = viewer_for(acme["manager"], acme["company"], can_manage=True)
        assert feed.read_new_events(db, manager, 0) == ([], 0)


class TestSessionChecks:
    def open_viewer(self, db: Session, acme) -> tuple[feed.Viewer, AuthSession]:  # type: ignore[no-untyped-def]
        now = timeutil.utcnow()
        session = AuthSession(
            user_id=acme["manager"].id,
            company_id=acme["company"].id,
            token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            expires_at=now + timedelta(hours=1),
            last_seen_at=now,
        )
        db.add(session)
        db.commit()
        viewer = feed.Viewer(
            user_id=acme["manager"].id, company_id=acme["company"].id, can_manage=True, session_id=session.id
        )
        return viewer, session

    def test_an_open_session_is_open(self, db: Session, settings: Settings, acme) -> None:  # type: ignore[no-untyped-def]
        viewer, _ = self.open_viewer(db, acme)
        assert feed.session_is_open(db, settings, viewer)

    def test_revoked_expired_idle_and_deactivated_sessions_are_closed(  # type: ignore[no-untyped-def]
        self, db: Session, settings: Settings, acme
    ) -> None:
        viewer, session = self.open_viewer(db, acme)

        session.revoked_at = timeutil.utcnow()
        db.commit()
        assert not feed.session_is_open(db, settings, viewer)

        session.revoked_at = None
        session.expires_at = timeutil.utcnow() - timedelta(seconds=1)
        db.commit()
        assert not feed.session_is_open(db, settings, viewer)

        session.expires_at = timeutil.utcnow() + timedelta(hours=1)
        session.last_seen_at = timeutil.utcnow() - timedelta(hours=settings.session_idle_hours + 1)
        db.commit()
        assert not feed.session_is_open(db, settings, viewer)

        session.last_seen_at = timeutil.utcnow()
        acme["manager"].is_active = False
        db.commit()
        assert not feed.session_is_open(db, settings, viewer)


class TestRetentionAndLimits:
    def test_old_events_are_pruned_and_recent_ones_kept(self, db: Session, settings: Settings, acme) -> None:  # type: ignore[no-untyped-def]
        old = publish_event(db, acme["company"].id, "device.online", {"deviceId": "old"})
        fresh = publish_event(db, acme["company"].id, "device.online", {"deviceId": "fresh"})
        db.commit()
        old.created_at = timeutil.utcnow() - timedelta(hours=settings.live_event_retention_hours + 1)
        db.commit()

        assert feed.prune_events(db, settings) == 1
        assert [e.id for e in db.scalars(select(LiveEvent))] == [fresh.id]

    def test_housekeeping_prunes_the_feed_and_says_so(
        self, app, db: Session, settings: Settings, acme
    ) -> None:  # type: ignore[no-untyped-def]
        stale = publish_event(db, acme["company"].id, "device.online", {"deviceId": "old"})
        db.commit()
        stale.created_at = timeutil.utcnow() - timedelta(days=3)
        db.commit()

        results = run_housekeeping(app.state.session_factory, settings)
        assert results["live_events_pruned"] == 1

    def test_the_registry_enforces_per_user_and_total_limits_and_gives_slots_back(self) -> None:
        registry = feed.StreamRegistry()
        alice, bob = uuid.uuid4(), uuid.uuid4()
        assert registry.acquire(alice, per_user=2, total=3)
        assert registry.acquire(alice, per_user=2, total=3)
        assert not registry.acquire(alice, per_user=2, total=3)  # her third tab
        assert registry.acquire(bob, per_user=2, total=3)
        assert not registry.acquire(uuid.uuid4(), per_user=2, total=3)  # the server is full
        registry.release(alice)
        assert registry.acquire(uuid.uuid4(), per_user=2, total=3)
        assert registry.open_streams == 3


class TestTheEndpoint:
    def test_it_needs_a_signed_in_session(self, fast_app, acme) -> None:  # type: ignore[no-untyped-def]
        status, _, body = read_stream(new_web(fast_app))
        assert status == 401
        assert json.loads(body)["error"]["code"] == "unauthenticated"

    def test_another_site_cannot_open_it_even_with_a_valid_session(self, fast_app, acme) -> None:  # type: ignore[no-untyped-def]
        web = new_web(fast_app)
        web.login("manager@acme.example.com")
        status, _, body = read_stream(web, headers={"Origin": "https://evil.example"})
        assert status == 403
        assert json.loads(body)["error"]["code"] == "bad_origin"

        status, _, _ = read_stream(web, headers={"Origin": "http://localhost:3000"})  # our own web app
        assert status == 200

    def test_it_is_a_streaming_response_that_proxies_must_not_buffer(self, fast_app, acme) -> None:  # type: ignore[no-untyped-def]
        web = new_web(fast_app)
        web.login("manager@acme.example.com")
        status, headers, body = read_stream(web)
        assert status == 200
        assert headers["content-type"].startswith("text/event-stream")
        assert "no-transform" in headers["cache-control"]
        assert headers["x-accel-buffering"] == "no"
        assert body.startswith("retry: ")
        assert "event: stream.renew" in body  # the stream ends itself so the browser reconnects

    def test_a_fresh_connection_starts_now_and_does_not_replay_history(
        self, fast_app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        publish_event(db, acme["company"].id, "device.online", {"deviceId": "history"})
        db.commit()
        web = new_web(fast_app)
        web.login("manager@acme.example.com")
        _, _, body = read_stream(web)
        assert "history" not in body

    def test_a_returning_connection_replays_exactly_what_it_missed(self, fast_app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        first = publish_event(db, acme["company"].id, "device.online", {"deviceId": "one"})
        second = publish_event(db, acme["company"].id, "device.offline", {"deviceId": "two"})
        db.commit()
        web = new_web(fast_app)
        web.login("manager@acme.example.com")

        _, _, body = read_stream(web, headers={"Last-Event-ID": str(first.id)})
        received = [e for e in parse_stream(body) if e["event"] != "stream.renew"]
        assert [e["event"] for e in received] == ["device.offline"]
        assert received[0]["id"] == str(second.id)
        assert received[0]["data"]["data"] == {"deviceId": "two"}

    def test_the_resume_position_may_also_come_in_the_url(self, fast_app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        first = publish_event(db, acme["company"].id, "device.online", {"deviceId": "one"})
        publish_event(db, acme["company"].id, "device.offline", {"deviceId": "two"})
        db.commit()
        web = new_web(fast_app)
        web.login("manager@acme.example.com")
        _, _, body = read_stream(web, params={"lastEventId": str(first.id)})
        assert "device.offline" in body and "device.online" not in body

    def test_a_garbage_or_future_position_never_replays_history_or_errors(
        self, fast_app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        publish_event(db, acme["company"].id, "device.online", {"deviceId": "history"})
        db.commit()
        web = new_web(fast_app)
        web.login("manager@acme.example.com")
        for bad in ("not-a-number", "-5", "999999999"):
            status, _, body = read_stream(web, headers={"Last-Event-ID": bad})
            assert status == 200
            assert "history" not in body

    def test_other_companies_events_are_never_sent(self, fast_app, db: Session, acme, globex) -> None:  # type: ignore[no-untyped-def]
        publish_event(db, globex["company"].id, "device.online", {"deviceId": "globex-secret"})
        publish_event(db, acme["company"].id, "device.online", {"deviceId": "acme-ok"})
        db.commit()
        web = new_web(fast_app)
        web.login("admin@acme.example.com")
        _, _, body = read_stream(web, headers={"Last-Event-ID": "0"})
        assert "acme-ok" in body
        assert "globex-secret" not in body

    def test_an_employee_hears_only_about_the_locket_assigned_to_them(self, app, fast_app, acme) -> None:  # type: ignore[no-untyped-def]
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        mine, mine_locket = provision_device(app, manager, "Mine")
        theirs, theirs_locket = provision_device(app, manager, "Theirs")
        assert (
            manager.patch(
                f"/api/devices/{mine['id']}", json={"assignedUserId": str(acme["employee"].id)}
            ).status_code
            == 200
        )
        mine_locket.heartbeat()
        theirs_locket.heartbeat()

        staff = new_web(fast_app)
        staff.login("employee@acme.example.com")
        _, _, body = read_stream(staff, headers={"Last-Event-ID": "0"})
        seen = [e for e in parse_stream(body) if e["event"] not in {"stream.renew", "session.ended"}]
        assert seen, "the employee should hear about their own locket"
        for item in seen:
            payload = item["data"]["data"]
            assert payload.get("deviceId") == mine["id"] or str(acme["employee"].id) in (
                payload.get("from"),
                payload.get("to"),
            )
        assert theirs["id"] not in body
        assert "Theirs" not in body

        boss = new_web(fast_app)
        boss.login("manager@acme.example.com")
        _, _, everything = read_stream(boss, headers={"Last-Event-ID": "0"})
        assert theirs["id"] in everything  # managers hear about every locket

    def test_the_slot_is_given_back_when_the_stream_ends(self, fast_app, acme) -> None:  # type: ignore[no-untyped-def]
        web = new_web(fast_app)
        web.login("manager@acme.example.com")
        read_stream(web)
        assert fast_app.state.streams.open_streams == 0

    def test_too_many_open_streams_are_refused_with_a_retry_hint(self, fast_app, acme) -> None:  # type: ignore[no-untyped-def]
        web = new_web(fast_app)
        web.login("manager@acme.example.com")
        limit = fast_app.state.settings.max_event_streams_per_user
        for _ in range(limit):
            assert fast_app.state.streams.acquire(acme["manager"].id, per_user=limit, total=limit * 10)

        status, headers, body = read_stream(web)
        assert status == 429
        assert json.loads(body)["error"]["code"] == "too_many_streams"
        assert headers["retry-after"] == "5"
        # A refused attempt must not take a slot of its own.
        assert fast_app.state.streams.open_streams == limit


class TestWhileTheStreamIsOpen:
    def test_an_event_written_after_connecting_arrives(self, file_app) -> None:  # type: ignore[no-untyped-def]
        factory = make_session_factory(file_app.state.engine)
        with factory() as db:
            company = add_company(db, "Acme")
            add_member(db, company, "manager@acme.example.com", "manager")

        def later() -> None:
            time.sleep(0.5)
            with factory() as db:
                publish_event(db, company.id, "device.online", {"deviceId": "d1", "name": "Front desk"})
                db.commit()

        web = new_web(file_app)
        web.login("manager@acme.example.com")
        writer = threading.Thread(target=later)
        writer.start()
        _, _, body = read_stream(web)
        writer.join()

        received = [e for e in parse_stream(body) if e["event"] == "device.online"]
        assert len(received) == 1
        assert received[0]["data"]["data"]["name"] == "Front desk"
        assert received[0]["id"] is not None  # browsers use this to resume

    def test_signing_out_elsewhere_ends_the_stream(self, file_app, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(events_router, "SESSION_CHECK_SECONDS", 0.1)
        factory = make_session_factory(file_app.state.engine)
        with factory() as db:
            company = add_company(db, "Acme")
            add_member(db, company, "manager@acme.example.com", "manager")

        def sign_out() -> None:
            time.sleep(0.4)
            with factory() as db:
                for session in db.scalars(select(AuthSession)):
                    session.revoked_at = timeutil.utcnow()
                db.commit()

        web = new_web(file_app)
        web.login("manager@acme.example.com")
        worker = threading.Thread(target=sign_out)
        worker.start()
        started = time.monotonic()
        _, _, body = read_stream(web)
        worker.join()

        assert "event: session.ended" in body
        assert "event: stream.renew" not in body
        assert time.monotonic() - started < 1.5  # ended on its own, well before the stream's lifetime

    def test_a_deactivated_person_stops_receiving_events(
        self, file_app, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(events_router, "SESSION_CHECK_SECONDS", 0.1)
        factory = make_session_factory(file_app.state.engine)
        with factory() as db:
            company = add_company(db, "Acme")
            user = add_member(db, company, "manager@acme.example.com", "manager")

        def deactivate() -> None:
            time.sleep(0.3)
            with factory() as db:
                person = db.get(User, user.id)
                assert person is not None
                person.is_active = False
                db.commit()

        web = new_web(file_app)
        web.login("manager@acme.example.com")
        worker = threading.Thread(target=deactivate)
        worker.start()
        _, _, body = read_stream(web)
        worker.join()
        assert "event: session.ended" in body


class TestPublishedEvents:
    def test_assigning_a_locket_announces_who_gained_and_who_lost_it(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        device, _ = provision_device(app, manager)
        first, second = acme["employee"], acme["admin"]

        manager.patch(f"/api/devices/{device['id']}", json={"assignedUserId": str(first.id)})
        manager.patch(f"/api/devices/{device['id']}", json={"assignedUserId": str(second.id)})
        manager.patch(f"/api/devices/{device['id']}", json={"assignedUserId": None})

        assigned = [
            e.payload for e in db.scalars(select(LiveEvent).where(LiveEvent.type == "device.assigned"))
        ]
        assert [(p["from"], p["to"]) for p in assigned] == [
            (None, str(first.id)),
            (str(first.id), str(second.id)),
            (str(second.id), None),
        ]
        assert all(p["deviceId"] == device["id"] for p in assigned)

    def test_assigning_to_the_same_person_again_announces_nothing(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        device, _ = provision_device(app, manager)
        for _ in range(2):
            manager.patch(f"/api/devices/{device['id']}", json={"assignedUserId": str(acme["employee"].id)})
        count = len(db.scalars(select(LiveEvent).where(LiveEvent.type == "device.assigned")).all())
        assert count == 1

    def test_recording_events_carry_the_locket_so_employees_can_be_filtered(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        from tests.helpers import make_wav

        manager = new_web(app)
        manager.login("manager@acme.example.com")
        device, locket = provision_device(app, manager)
        uploaded = locket.upload("clip-0001-aaaa", make_wav(1.0))
        assert uploaded.status_code == 201
        recording_id = uploaded.json()["recordingId"]
        assert manager.delete(f"/api/recordings/{recording_id}").status_code == 204

        by_type = {e.type: e.payload for e in db.scalars(select(LiveEvent))}
        for name in ("recording.uploaded", "recording.deleted"):
            assert by_type[name]["deviceId"] == device["id"]
        assert db.scalar(select(Device).where(Device.id == uuid.UUID(device["id"]))) is not None
