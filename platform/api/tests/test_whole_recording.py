"""A recording that reached the server in parts (the locket sends half a minute at a time while it records)
is ONE item on the website: one row in the list with the whole one's length and state, one transcript, one
audio file, one delete. The screens are the ones that always showed a recording; only what the server
answers changed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Recording
from tests.test_meeting_parts import SESSION, process, upload_part
from tests.test_phone_upload import make_setup


def meeting(app, *, processed: int = 2):  # type: ignore[no-untyped-def]
    """Two parts, a minute apart; the first `processed` of them have been through the worker."""
    setup = make_setup(app)
    begin = datetime.now(UTC) - timedelta(minutes=10)
    first = upload_part(setup, 0, begin)
    if processed >= 1:
        process(app, "We will ship the report on Friday.", "SUMMARY ONE")
    second = upload_part(setup, 1, begin + timedelta(seconds=60))
    if processed >= 2:
        process(app, "Priya sends the budget on Wednesday.", "SUMMARY TWO")
    return setup, first, second


def rows(client, query: str = "") -> list[dict]:  # type: ignore[no-untyped-def,type-arg]
    return client.get(f"/api/recordings?pageSize=20{query}").json()["items"]


def test_the_list_has_one_row_whose_state_is_that_of_the_whole(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup, first, _ = meeting(app, processed=1)  # the second part has arrived and is waiting
    [row] = rows(setup["manager"])
    assert row["id"] == str(first) and row["durationMs"] == 4000 and row["partCount"] == 2
    assert row["state"] == "queued"  # a part is still waiting: the whole is not done
    process(app, "Priya sends the budget on Wednesday.", "SUMMARY TWO")
    [row] = rows(setup["manager"])
    assert row["state"] == "completed" and row["snippet"].startswith("We will ship")
    assert len(rows(setup["manager"], "&includeParts=true")) == 2


def test_the_detail_has_the_transcript_of_every_part_in_order(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup, first, _ = meeting(app)
    detail = setup["manager"].get(f"/api/recordings/{first}").json()
    texts = [s["text"] for s in detail["transcript"]["segments"]]
    assert texts == ["We will ship the report on Friday.", "Priya sends the budget on Wednesday."]
    starts = [s["startMs"] for s in detail["transcript"]["segments"]]
    assert starts == [1000, 61000]  # the second part began a minute after the first
    assert detail["durationMs"] == 4000 and detail["sessionRef"] == SESSION


def test_play_audio_plays_the_parts_one_after_the_other(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup, first, _ = meeting(app, processed=0)
    played = setup["manager"].get(f"/api/recordings/{first}/audio")
    assert played.status_code == 200 and played.headers["content-type"] == "audio/wav"
    assert played.content[:4] == b"RIFF" and len(played.content) == 44 + 4 * 16000 * 2  # four seconds in all
    assert int(played.headers["content-length"]) == len(played.content)
    middle = setup["manager"].get(f"/api/recordings/{first}/audio", headers={"Range": "bytes=64000-64099"})
    assert middle.status_code == 206 and middle.content == played.content[64000:64100]
    assert middle.headers["content-range"] == f"bytes 64000-64099/{len(played.content)}"
    tail = setup["manager"].get(f"/api/recordings/{first}/audio", headers={"Range": "bytes=-100"})
    assert tail.status_code == 206 and tail.content == played.content[-100:]
    past = setup["manager"].get(f"/api/recordings/{first}/audio", headers={"Range": "bytes=999999999-"})
    assert past.status_code == 416


def test_audio_still_plays_after_the_files_are_gone_from_the_disk(app, acme) -> None:  # type: ignore[no-untyped-def]
    app.state.settings.audio_archive_max_bytes = 50_000_000
    setup, first, _ = meeting(app)
    played = setup["manager"].get(f"/api/recordings/{first}/audio")
    assert played.status_code == 200 and len(played.content) == 44 + 4 * 16000 * 2


def test_deleting_the_recording_deletes_all_its_parts_and_a_later_part_only_itself(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup, first, second = meeting(app, processed=0)
    assert setup["manager"].delete(f"/api/recordings/{second}").status_code == 204
    assert [r["id"] for r in rows(setup["manager"], "&includeParts=true")] == [str(first)]
    assert setup["manager"].delete(f"/api/recordings/{first}").status_code == 204
    assert rows(setup["manager"], "&includeParts=true") == []
    other, other_first, other_second = meeting(app, processed=0)
    assert other["manager"].delete(f"/api/recordings/{other_first}").status_code == 204
    assert rows(other["manager"], "&includeParts=true") == []  # the first part took the second with it
    assert other_second is not None


def test_when_the_first_part_is_gone_the_next_one_stands_for_the_recording(
    app,
    acme,
    db: Session,  # type: ignore[no-untyped-def]
) -> None:
    setup, first, second = meeting(app, processed=0)
    row = db.get(Recording, first)
    assert row is not None
    row.deleted_at = datetime.now(UTC)  # removed behind the website's back (retention, for example)
    db.commit()
    assert [r["id"] for r in rows(setup["manager"])] == [str(second)]


def test_an_employee_sees_the_same_single_row(app, acme) -> None:  # type: ignore[no-untyped-def]
    setup, first, _ = meeting(app, processed=0)
    assert [r["id"] for r in rows(setup["employee"])] == [str(first)]
    assert setup["employee"].get(f"/api/recordings/{first}/audio").status_code == 200
