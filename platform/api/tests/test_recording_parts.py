"""A recording longer than the server's clip limit reaches the server as several clips that share a session
reference. The website says "Part 2" for such a clip, and only for those: a lone clip is just a recording.

The button-controlled firmware (platform/firmware/locket_web_upload) is what produces them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import new_web, provision_device
from tests.helpers import make_wav


def web_as(app, email: str):  # type: ignore[no-untyped-def]
    web = new_web(app)
    web.login(email)
    return web


def upload(locket, client_id: str, minutes: int, ref: str | None, sequence: int | None):  # type: ignore[no-untyped-def]
    extra = {}
    if ref is not None:
        extra["X-Session-Ref"] = ref
    if sequence is not None:
        extra["X-Sequence"] = str(sequence)
    started = (datetime.now(UTC) - timedelta(hours=1) + timedelta(minutes=minutes)).isoformat()
    response = locket.upload(
        client_id, make_wav(1.0, frequency=300.0 + minutes), recorded_at=started, extra=extra
    )
    assert response.status_code == 201, response.text
    return response.json()["recordingId"]


def parts_by_id(web) -> dict[str, int | None]:  # type: ignore[no-untyped-def]
    items = web.get("/api/recordings?includeParts=true").json()["items"]
    return {item["id"]: item["part"] for item in items}


class TestPartLabels:
    def test_the_parts_of_one_recording_are_numbered(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = web_as(app, "manager@acme.example.com")
        _, locket = provision_device(app, web)
        ids = [upload(locket, f"part-{n}-aaaaaaaa", n, "s-meeting-1", n) for n in (1, 2, 3)]
        parts = parts_by_id(web)
        assert [parts[i] for i in ids] == [1, 2, 3]

    def test_a_lone_clip_is_not_called_a_part(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = web_as(app, "manager@acme.example.com")
        _, locket = provision_device(app, web)
        lone = upload(
            locket, "lone-0001-aaaaaaaa", 1, "s-short-1", 1
        )  # a short recording: one part, numbered 1
        plain = upload(locket, "plain-001-aaaaaaaa", 2, None, None)  # an ordinary clip, no session at all
        parts = parts_by_id(web)
        assert parts[lone] is None and parts[plain] is None

    def test_a_part_that_arrives_alone_is_labelled_once_it_is_not_the_first(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        # Part 1 is still on the locket, waiting for the network. Part 2 is clearly part of something longer.
        web = web_as(app, "manager@acme.example.com")
        _, locket = provision_device(app, web)
        second = upload(locket, "part-2-aaaaaaaaaa", 5, "s-late-1", 2)
        assert parts_by_id(web)[second] == 2

    def test_two_lockets_choosing_the_same_reference_do_not_make_each_others_clips_parts(
        self, app, acme
    ) -> None:  # type: ignore[no-untyped-def]
        web = web_as(app, "manager@acme.example.com")
        _, first = provision_device(app, web, "Locket One")
        _, second = provision_device(app, web, "Locket Two")
        a = upload(first, "one-0001-aaaaaaaaa", 1, "s-same-ref", 1)
        b = upload(second, "two-0001-aaaaaaaaa", 2, "s-same-ref", 1)
        parts = parts_by_id(web)
        assert parts[a] is None and parts[b] is None

    def test_another_company_sees_none_of_it(self, app, acme, globex) -> None:  # type: ignore[no-untyped-def]
        web = web_as(app, "manager@acme.example.com")
        _, locket = provision_device(app, web)
        for n in (1, 2):
            upload(locket, f"part-{n}-aaaaaaaa", n, "s-private-1", n)
        other = web_as(app, "admin@globex.example.com")
        assert other.get("/api/recordings").json()["items"] == []
        # Filtering by someone else's session reference finds nothing either.
        assert other.get("/api/recordings?sessionRef=s-private-1").json()["items"] == []

    def test_the_session_filter_returns_the_clips_of_one_recording(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = web_as(app, "manager@acme.example.com")
        _, locket = provision_device(app, web)
        wanted = [upload(locket, f"want-{n}-aaaaaaaa", n, "s-want", n) for n in (1, 2)]
        upload(locket, "other-1-aaaaaaaaa", 9, "s-other", 1)
        items = web.get("/api/recordings?sessionRef=s-want").json()["items"]
        assert sorted(item["id"] for item in items) == sorted(wanted)

    def test_the_detail_page_carries_the_label_too(self, app, acme) -> None:  # type: ignore[no-untyped-def]
        web = web_as(app, "manager@acme.example.com")
        _, locket = provision_device(app, web)
        ids = [upload(locket, f"part-{n}-aaaaaaaa", n, "s-detail-1", n) for n in (1, 2)]
        assert web.get(f"/api/recordings/{ids[1]}").json()["part"] == 2
