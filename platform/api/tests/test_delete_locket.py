"""Deleting a locket, and deleting a whole meeting of many parts in one go."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.test_lockets import as_user, register
from tests.test_meeting_parts import SESSION, upload_part
from tests.test_phone_upload import make_setup


def names(client) -> list[str]:  # type: ignore[no-untyped-def]
    response = client.get("/api/lockets")
    assert response.status_code == 200, response.text
    return [item["productId"] for item in response.json()]


def test_an_administrator_deletes_a_locket_and_it_is_gone_for_everyone(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup = make_setup(app)
    product_id = setup["product_id"]
    assert product_id in names(setup["admin"]) and product_id in names(setup["employee"])

    deleted = setup["admin"].delete(f"/api/lockets/{product_id}")
    assert deleted.status_code == 204, deleted.text

    for who in ("admin", "manager", "employee"):
        assert product_id not in names(setup[who])  # managers used to keep seeing a revoked locket


def test_a_deleted_locket_can_no_longer_pair_or_take_recordings(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup = make_setup(app)
    product_id = setup["product_id"]
    assert setup["admin"].delete(f"/api/lockets/{product_id}").status_code == 204

    asked = setup["manager"].post(
        f"/api/lockets/{product_id}/pairing-code", json={"nonce": "00112233445566778899aabbccddeeff"}
    )
    assert asked.status_code == 404  # the pairing flow finds no such locket
    refused = setup["employee"].request(
        "PUT", f"/api/lockets/{product_id}/recordings/after-delete-0001", content=b"x", headers={}
    )
    assert refused.status_code in (404, 422)  # never stored


def test_only_an_administrator_may_delete_a_locket(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup = make_setup(app)
    product_id = setup["product_id"]
    assert setup["manager"].delete(f"/api/lockets/{product_id}").status_code == 403
    assert setup["employee"].delete(f"/api/lockets/{product_id}").status_code == 403
    assert product_id in names(setup["admin"])  # nothing happened


def test_deleting_twice_or_an_unknown_locket_is_a_plain_not_found(app, acme) -> None:  # type: ignore[no-untyped-def]
    admin = as_user(app, "admin@acme.example.com")
    product_id = register(admin)["productId"]
    assert admin.delete(f"/api/lockets/{product_id}").status_code == 204
    assert admin.delete(f"/api/lockets/{product_id}").status_code == 404
    assert admin.delete("/api/lockets/ASK-NOSUCH").status_code == 404


def two_part_meeting(app):  # type: ignore[no-untyped-def]
    setup = make_setup(app)
    begin = datetime.now(UTC) - timedelta(minutes=10)
    first = upload_part(setup, 0, begin)
    second = upload_part(setup, 1, begin + timedelta(seconds=60))
    return setup, first, second


def listed(client) -> list[str]:  # type: ignore[no-untyped-def]
    page = client.get("/api/recordings?pageSize=20").json()
    return [item["id"] for item in page["items"]]


def test_deleting_one_part_leaves_the_rest_of_the_meeting(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup, first, second = two_part_meeting(app)
    assert setup["manager"].delete(f"/api/recordings/{first}").status_code == 204
    assert listed(setup["manager"]) == [str(second)]


def test_deleting_a_meeting_removes_every_part(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup, _, second = two_part_meeting(app)
    response = setup["manager"].delete(f"/api/recordings/{second}?meeting=true")
    assert response.status_code == 204, response.text
    assert listed(setup["manager"]) == []
    assert SESSION  # (the two parts shared this meeting reference)


def test_deleting_a_meeting_never_reaches_another_lockets_recordings(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup, first, _ = two_part_meeting(app)
    other = make_setup(app)
    lone = upload_part(other, 0, datetime.now(UTC) - timedelta(minutes=3))
    assert setup["manager"].delete(f"/api/recordings/{first}?meeting=true").status_code == 204
    remaining = listed(other["manager"])
    assert str(lone) in remaining  # a different locket that used the same reference is untouched
