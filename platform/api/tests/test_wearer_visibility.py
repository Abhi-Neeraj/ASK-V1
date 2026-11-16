"""A recording belongs to whoever wore the locket when it arrived.

Until now an employee saw a recording because its locket was assigned to them *now*, so handing a locket to
the next
person handed over everything it had ever recorded."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Device, Recording
from app.services import events as feed
from tests.conftest import new_web, provision_device
from tests.helpers import add_member, make_wav


def signed_in(app, email: str):  # type: ignore[no-untyped-def]
    web = new_web(app)
    assert web.login(email).status_code == 200
    return web


@pytest.fixture
def staff(db: Session, acme):  # type: ignore[no-untyped-def]
    """Two employees, so a locket can move from one to the other."""
    return {
        "ana": add_member(db, acme["company"], "ana@acme.example.com", "employee", name="Ana"),
        "ben": add_member(db, acme["company"], "ben@acme.example.com", "employee", name="Ben"),
    }


@pytest.fixture
def story(app, staff, acme):  # type: ignore[no-untyped-def]
    """A locket worn by Ana records one clip, is handed to Ben, and records another."""
    manager = signed_in(app, "manager@acme.example.com")
    device, locket = provision_device(app, manager, "Shared locket")
    assign = lambda who: manager.patch(  # noqa: E731
        f"/api/devices/{device['id']}", json={"assignedUserId": str(who.id) if who else None}
    )
    assert assign(staff["ana"]).status_code == 200
    first = uuid.UUID(locket.upload("clip-ana-0001", make_wav(2.0)).json()["recordingId"])
    assert assign(staff["ben"]).status_code == 200
    second = uuid.UUID(locket.upload("clip-ben-0001", make_wav(2.0)).json()["recordingId"])
    return {
        "manager": manager,
        "device": device,
        "locket": locket,
        "assign": assign,
        "ana_clip": first,
        "ben_clip": second,
    }


class TestARecordingStaysWithWhoeverWoreTheLocket:
    def test_it_is_recorded_when_the_clip_arrives(self, db: Session, story, staff) -> None:  # type: ignore[no-untyped-def]
        rows = {r.client_recording_id: r.assigned_user_id for r in db.scalars(select(Recording))}
        assert rows == {"clip-ana-0001": staff["ana"].id, "clip-ben-0001": staff["ben"].id}

    def test_the_previous_wearer_keeps_their_history_and_the_new_one_never_gets_it(
        self, app, story, staff
    ) -> None:  # type: ignore[no-untyped-def]
        ana = signed_in(app, "ana@acme.example.com")
        ben = signed_in(app, "ben@acme.example.com")

        ana_sees = {item["id"] for item in ana.get("/api/recordings").json()["items"]}
        ben_sees = {item["id"] for item in ben.get("/api/recordings").json()["items"]}
        assert ana_sees == {str(story["ana_clip"])}
        assert ben_sees == {str(story["ben_clip"])}

    def test_everything_about_a_recording_is_closed_to_the_other_person(self, app, story, staff) -> None:  # type: ignore[no-untyped-def]
        ben = signed_in(app, "ben@acme.example.com")
        clip = story["ana_clip"]
        for path in (
            f"/api/recordings/{clip}",
            f"/api/recordings/{clip}/audio",
            f"/api/recordings/{clip}/transcript",
        ):
            response = ben.get(path)
            # The same answer as for a recording that does not exist, so it cannot even be told to be there.
            assert response.status_code == 404, path
            assert response.json() == ben.get(f"/api/recordings/{uuid.uuid4()}").json(), path
        assert ben.get("/api/recordings", params={"q": "anything"}).json()["total"] == 0

    def test_and_open_to_the_person_it_belongs_to_even_though_the_locket_is_no_longer_theirs(
        self, app, story
    ) -> None:  # type: ignore[no-untyped-def]
        ana = signed_in(app, "ana@acme.example.com")
        clip = story["ana_clip"]
        assert ana.get(f"/api/recordings/{clip}").status_code == 200
        assert ana.get(f"/api/recordings/{clip}/audio").status_code == 200
        assert ana.get("/api/devices").json() == []  # the locket itself is no longer hers

    def test_the_summary_counts_only_what_is_theirs(self, app, story) -> None:  # type: ignore[no-untyped-def]
        for email in ("ana@acme.example.com", "ben@acme.example.com"):
            summary = signed_in(app, email).get("/api/summary").json()
            assert summary["inProgress"] == 1, email  # one queued clip each, not two

    def test_managers_see_all_of_it(self, app, story) -> None:  # type: ignore[no-untyped-def]
        ids = {item["id"] for item in story["manager"].get("/api/recordings").json()["items"]}
        assert ids == {str(story["ana_clip"]), str(story["ben_clip"])}

    def test_handing_the_locket_back_does_not_bring_the_history_with_it(self, app, story, staff) -> None:  # type: ignore[no-untyped-def]
        assert story["assign"](staff["ana"]).status_code == 200  # Ana has it again...
        ana = signed_in(app, "ana@acme.example.com")
        assert {i["id"] for i in ana.get("/api/recordings").json()["items"]} == {
            str(story["ana_clip"])
        }  # ...Ben's clip is still Ben's


class TestAnUnassignedLocket:
    def test_what_it_records_is_for_managers_only_even_if_it_is_assigned_later(
        self, app, db: Session, staff, acme
    ) -> None:  # type: ignore[no-untyped-def]
        manager = signed_in(app, "manager@acme.example.com")
        device, locket = provision_device(app, manager, "Loose locket")
        clip = uuid.UUID(locket.upload("clip-loose-0001", make_wav(2.0)).json()["recordingId"])
        assert db.get(Recording, clip).assigned_user_id is None  # type: ignore[union-attr]

        manager.patch(f"/api/devices/{device['id']}", json={"assignedUserId": str(staff["ben"].id)})
        ben = signed_in(app, "ben@acme.example.com")
        assert ben.get("/api/recordings").json()["total"] == 0
        assert ben.get(f"/api/recordings/{clip}").status_code == 404
        assert manager.get(f"/api/recordings/{clip}").status_code == 200


class TestWhoWasAssignedWhenItArrived:
    def test_a_handover_while_a_clip_is_still_arriving_counts_for_the_new_wearer(
        self, app, db: Session, staff, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        """The locket is read when the clip is stored, not when the upload began. A clip that takes a while
        to arrive
        belongs to whoever has the locket at the end."""
        from app.routers import device_api

        manager = signed_in(app, "manager@acme.example.com")
        device, locket = provision_device(app, manager, "Mid-upload")
        manager.patch(f"/api/devices/{device['id']}", json={"assignedUserId": str(staff["ana"].id)})

        real_spool = device_api.spool_to_disk

        async def spool_then_hand_over(*args, **kwargs):  # type: ignore[no-untyped-def]
            spooled = await real_spool(*args, **kwargs)
            db.execute(
                update(Device)
                .where(Device.public_id == device["deviceId"])
                .values(assigned_user_id=staff["ben"].id)
            )
            db.commit()
            return spooled

        monkeypatch.setattr(device_api, "spool_to_disk", spool_then_hand_over)
        clip = uuid.UUID(locket.upload("clip-mid-0001", make_wav(2.0)).json()["recordingId"])

        db.expire_all()
        assert db.get(Recording, clip).assigned_user_id == staff["ben"].id  # type: ignore[union-attr]


class TestTheLiveFeedFollowsTheSameRule:
    @pytest.fixture
    def viewers(self, db: Session, acme, staff) -> dict[str, feed.Viewer]:  # type: ignore[no-untyped-def]
        def viewer(user, can_manage):  # type: ignore[no-untyped-def]
            return feed.Viewer(
                user_id=user.id, company_id=acme["company"].id, can_manage=can_manage, session_id=uuid.uuid4()
            )

        return {
            "ana": viewer(staff["ana"], False),
            "ben": viewer(staff["ben"], False),
            "manager": viewer(acme["manager"], True),
        }

    def heard(self, db: Session, viewer: feed.Viewer) -> list[tuple[str, str | None]]:
        events, _ = feed.read_new_events(db, viewer, 0)
        return [(e.type, e.payload.get("recordingId")) for e in events if e.type.startswith("recording.")]

    def test_each_person_hears_about_their_own_recordings_only(self, db: Session, story, viewers) -> None:  # type: ignore[no-untyped-def]
        ana, ben = self.heard(db, viewers["ana"]), self.heard(db, viewers["ben"])
        assert ana and ben
        assert {rid for _, rid in ana} == {str(story["ana_clip"])}
        assert {rid for _, rid in ben} == {str(story["ben_clip"])}

    def test_a_manager_hears_everything(self, db: Session, story, viewers) -> None:  # type: ignore[no-untyped-def]
        assert {rid for _, rid in self.heard(db, viewers["manager"])} == {
            str(story["ana_clip"]),
            str(story["ben_clip"]),
        }

    def test_later_news_about_an_old_recording_still_goes_to_whoever_owns_it(
        self, app, db: Session, story, viewers
    ) -> None:  # type: ignore[no-untyped-def]
        # Processing the old clip again, after the locket changed hands, is news for Ana, not for Ben.
        db.execute(update(Recording).values(state="completed"))  # a finished clip can be processed again
        db.commit()
        assert story["manager"].post(f"/api/recordings/{story['ana_clip']}/reprocess").status_code == 202
        after_ana, after_ben = self.heard(db, viewers["ana"]), self.heard(db, viewers["ben"])
        assert ("recording.state", str(story["ana_clip"])) in after_ana
        assert str(story["ana_clip"]) not in {rid for _, rid in after_ben}

    def test_news_published_before_this_change_falls_back_to_the_locket(
        self, db: Session, acme, staff, viewers
    ) -> None:  # type: ignore[no-untyped-def]
        # Events are kept for a day. One written by the previous version names no owner; it keeps its old
        # audience.
        legacy = feed.FeedEvent(
            id=1, type="recording.state", payload={"recordingId": "r", "deviceId": "d1"}, created_at=None
        )  # type: ignore[arg-type]
        assert feed.is_visible(legacy, viewers["ana"], {"d1"}) is True
        assert feed.is_visible(legacy, viewers["ben"], set()) is False
        assert feed.is_visible(legacy, viewers["manager"], set()) is True
