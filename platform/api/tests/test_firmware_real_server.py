"""The firmware's own upload code against the REAL ASK API, over a local socket.

The firmware sources run on this computer against simulated hardware (see test_firmware_web_upload.py), but
here the simulated HTTP client is connected to a real uvicorn server running the real application and a real
database.
So the headers the firmware builds, its streaming of the file, its SHA-256, and its reading of the server's
receipt are all checked against the real thing, not against a fake server written by the same person.

This is a plain-http build for localhost (ASK_ALLOW_PLAIN_HTTP), the documented development setup. TLS, a real
certificate and the radio are NOT exercised here: only the real board can do that.

Needs a C++17 compiler and the `uvicorn` package; SKIPPED without them.
"""

from __future__ import annotations

import shutil
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db import make_session_factory
from app.models import Device, ProcessingJob, Recording
from tests.conftest import new_web
from tests.helpers import add_company, add_member
from tests.test_firmware_web_upload import (  # noqa: F401  (fixtures are used by name)
    BASE,
    Sim,
    expected_pcm,
    parse,
    read_wav,
    sim_plain_binary,
)

BLOCK = 32768
PART_BYTES = 5 * BLOCK  # parts of 5 blocks (5.12 s): the server below allows 6-second clips


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The server only accepts clips up to 6 seconds, so a short test recording becomes several parts."""
    return settings.model_copy(update={"max_clip_seconds": 6})


@pytest.fixture
def live(file_app) -> Iterator[str]:  # type: ignore[no-untyped-def]
    """The real application served on a free local port."""
    uvicorn = pytest.importorskip("uvicorn")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(file_app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "the test server did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=20)


@pytest.fixture
def tenants(file_app) -> dict[str, str]:  # type: ignore[no-untyped-def]
    with make_session_factory(file_app.state.engine)() as session:
        acme = add_company(session, "Acme")
        add_member(session, acme, "manager@acme.example.com", "manager")
        globex = add_company(session, "Globex")
        add_member(session, globex, "admin@globex.example.com", "admin")
    return {"manager": "manager@acme.example.com", "other": "admin@globex.example.com"}


@pytest.fixture
def world(tmp_path: Path, sim_plain_binary: Path) -> Sim:  # noqa: F811
    return Sim(sim_plain_binary, tmp_path / "locket")


def new_locket_code(web) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    created = web.post("/api/devices", json={"name": "Locket One"})
    assert created.status_code == 201, created.text
    body = created.json()
    return body["code"], body["id"]


def real_credential(world: Sim) -> str:
    for line in (world.root / "nvs.txt").read_text().splitlines():
        key, _, value = line.partition("\t")
        if key == "cred":
            return value
    raise AssertionError("the locket was not paired")


def pair_and_queue(world: Sim, live: str, code: str, *, seconds: int = 14) -> None:
    """Pair with the real server, then record with the network down, so the parts wait on the card."""
    run = world.run(
        "--forward",
        "--scale",
        20,
        f"type=server {live}",
        "type=wifi simnet simulated-password",
        f"type=pair {code}",
        "wait=3",
        "wifi-down",
        f"record={seconds}",
        "wait=2",
    )
    assert "paired as" in run.out, run.out[-1500:]
    assert run.res["dirs"]["pending"], run.res
    assert real_credential(world) not in run.out  # the credential is never printed


class TestAgainstTheRealServer:
    def test_what_the_firmware_uploads_is_accepted_stored_and_shown_to_the_right_company(
        self,
        world: Sim,
        live: str,
        tenants: dict[str, str],
        file_app,
        settings: Settings,  # type: ignore[no-untyped-def]
    ) -> None:
        web = new_web(file_app)
        web.login(tenants["manager"])
        code, _ = new_locket_code(web)

        pair_and_queue(world, live, code)
        queued = world.files("pending")
        assert len(queued) >= 3
        run = world.run("--forward", "--scale", 20, "wifi-up", "until-empty=600", timeout=420)
        res = run.res
        assert real_credential(world) not in run.out
        assert (
            res["dirs"]["pending"] == [] and res["dirs"]["rejected"] == []
        )  # every part was confirmed, then deleted
        assert res["uploaded"] == len(queued)
        assert all(r["status"] in (200, 201) for r in res["server"]["requests"] if r["method"] == "PUT")

        # The real server's database holds exactly those parts, in one session, in order.
        with make_session_factory(file_app.state.engine)() as db:
            rows = db.scalars(select(Recording).order_by(Recording.sequence)).all()
            assert len(rows) == len(queued)
            assert [r.sequence for r in rows] == list(range(1, len(queued) + 1))
            assert len({r.session_ref for r in rows}) == 1
            assert all(
                r.state == "queued" for r in rows
            )  # stored and waiting to be processed: not faked as done
            assert all((r.sample_rate, r.channels, r.bits_per_sample) == (16000, 1, 16) for r in rows)
            assert db.scalar(select(func.count()).select_from(ProcessingJob)) == len(
                rows
            )  # processing was triggered
            device = db.scalar(select(Device))
            assert device is not None and device.last_upload_at is not None
            assert device.firmware_version == "0.2.0-web-upload"

            # What the server stored is exactly what was recorded: no gap or repeat across the parts.
            audio = b""
            for row in rows:
                fields, pcm = read_wav(Path(settings.storage_dir) / row.storage_key)
                assert fields["data"] == row.size_bytes - 44
                audio += pcm
            assert audio == expected_pcm(len(audio))
            names = sorted(parse(p)["when"] for p in queued)
            started = [int(r.started_at.timestamp()) for r in rows]
            assert started == names  # the start times the firmware sent are the ones the server recorded

        # The website shows them as parts of one recording, with real processing states.
        items = web.get("/api/recordings").json()["items"]
        assert len(items) == len(queued)
        assert sorted(item["part"] for item in items) == list(range(1, len(queued) + 1))
        assert {item["state"] for item in items} <= {"queued", "uploaded"}
        assert all(item["snippet"] is None for item in items)  # no transcript is invented

        # The locket shows as online from its authenticated heartbeat, with its queue depth.
        listed = web.get("/api/devices").json()
        assert listed[0]["connected"] or listed[0]["status"] == "online"
        assert listed[0]["lastUploadAt"] is not None

        # Another company sees none of it.
        other = new_web(file_app)
        other.login(tenants["other"])
        assert other.get("/api/recordings").json()["items"] == []
        assert other.get("/api/devices").json() == []

    def test_sending_the_same_parts_again_is_acknowledged_and_creates_no_second_copy(
        self,
        world: Sim,
        live: str,
        tenants: dict[str, str],
        file_app,
        tmp_path: Path,  # type: ignore[no-untyped-def]
    ) -> None:
        web = new_web(file_app)
        web.login(tenants["manager"])
        code, _ = new_locket_code(web)
        pair_and_queue(world, live, code, seconds=8)
        backup = tmp_path / "backup"
        shutil.copytree(world.root / "sd" / "pending", backup)
        count = len(list(backup.glob("*")))

        first = world.run("--forward", "--scale", 20, "wifi-up", "until-empty=600", timeout=420)
        assert first.res["dirs"]["pending"] == []
        with make_session_factory(file_app.state.engine)() as db:
            before = db.scalar(select(func.count()).select_from(Recording))
        assert before == count

        # Put the parts back, as if the locket had lost the receipts (or been restored from a backup).
        for path in backup.glob("*"):
            shutil.copy(path, world.root / "sd" / "pending" / path.name)
        again = world.run("--forward", "--scale", 20, "until-empty=600", timeout=420)
        assert again.res["dirs"]["pending"] == []  # the repeat was acknowledged, so the copies were deleted
        statuses = [r["status"] for r in again.res["server"]["requests"] if r["method"] == "PUT"]
        assert statuses and set(statuses) == {200}  # "already stored", not "created"
        with make_session_factory(file_app.state.engine)() as db:
            assert db.scalar(select(func.count()).select_from(Recording)) == before

    def test_a_revoked_locket_keeps_every_recording_and_stops_starting_new_ones(
        self,
        world: Sim,
        live: str,
        tenants: dict[str, str],
        file_app,  # type: ignore[no-untyped-def]
    ) -> None:
        web = new_web(file_app)
        web.login(tenants["manager"])
        code, device_id = new_locket_code(web)
        pair_and_queue(world, live, code, seconds=8)
        names = sorted(p.name for p in world.files("pending"))

        assert web.post(f"/api/devices/{device_id}/revoke").status_code == 200
        run = world.run("--forward", "--scale", 20, "wifi-up", "wait=15", "record=3", timeout=300)
        res = run.res
        assert sorted(p.name for p in world.files("pending")) == names  # nothing deleted, nothing renamed
        assert res["needsPairing"] is True
        assert res["state"] == "ERROR" and "pair" in res["error"]
        assert not [
            r for r in res["server"]["requests"] if r["method"] == "PUT" and r["status"] in (200, 201)
        ]
        with make_session_factory(file_app.state.engine)() as db:
            assert db.scalar(select(func.count()).select_from(Recording)) == 0  # the server stored nothing
        assert real_credential(world) not in run.out
