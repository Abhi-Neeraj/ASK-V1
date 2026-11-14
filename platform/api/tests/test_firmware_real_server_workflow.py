"""After the button workflow: what happens on the server once a recording has been uploaded.

Two cases from the owner's list that need the real API behind the firmware:

* a failed transcription must not damage the audio or stop later recordings;
* recordings from two companies' lockets must only ever appear in their own company's account.

The firmware's upload code runs against SIMULATED hardware and a real uvicorn server on a local socket (plain
http, the development setup); nothing here ran on a board. See test_firmware_real_server.py.
"""

# ruff: noqa: F811  (fixtures imported from the other firmware test modules are used by name)

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app import timeutil
from app.config import Settings
from app.db import make_session_factory
from app.models import Device, ProcessingJob, Recording
from app.services.jobs import PUBLIC_FAILURE
from app.worker import Worker
from tests.ai_doubles import make_provider
from tests.conftest import new_web
from tests.test_firmware_real_server import (  # noqa: F401  (the fixtures are used by name)
    Sim,
    live,
    new_locket_code,
    pair_and_queue,
    real_credential,
    settings,
    tenants,
    world,
)
from tests.test_firmware_web_upload import expected_pcm, read_wav, sim_plain_binary  # noqa: F401
from tests.test_processing import Explodes


def make_all_due(app) -> None:  # type: ignore[no-untyped-def]
    with app.state.session_factory() as db:
        for job in db.scalars(select(ProcessingJob)):
            job.run_after = timeutil.utcnow() - timedelta(seconds=1)
        db.commit()


def run_worker(app, provider, rounds: int) -> None:  # type: ignore[no-untyped-def]
    """Work the queue, making every retry due at once, until no job is left waiting (or `rounds` run out)."""
    for _ in range(rounds):
        # A fresh worker each round, as `python -m app.worker --once` would be.
        Worker(app.state.session_factory, app.state.settings, provider, app.state.storage, "worker-test").run(
            once=True
        )
        make_all_due(app)
        with app.state.session_factory() as db:
            if not db.scalar(select(ProcessingJob).where(ProcessingJob.status.in_(("queued", "running")))):
                return
    with app.state.session_factory() as db:
        left = [(j.status, j.attempts) for j in db.scalars(select(ProcessingJob))]
    raise AssertionError(f"jobs still waiting after {rounds} rounds: {left}")


def stored_audio(settings: Settings, row: Recording) -> bytes:
    path = Path(settings.storage_dir) / row.storage_key
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest() == row.sha256
    )  # the file is exactly what was received
    return read_wav(path)[1]


class TestAfterTheUpload:
    def test_a_failed_transcription_leaves_the_audio_intact_and_does_not_stop_later_recordings(
        self,
        world: Sim,
        live: str,
        tenants: dict[str, str],
        file_app,  # type: ignore[no-untyped-def]
        settings: Settings,
    ) -> None:
        web = new_web(file_app)
        web.login(tenants["manager"])
        code, _ = new_locket_code(web)
        pair_and_queue(world, live, code, seconds=14)
        first = world.run("--forward", "--scale", 20, "wifi-up", "until-empty=600", timeout=420)
        assert first.res["dirs"]["pending"] == []
        count = first.res["uploaded"]
        assert count >= 3

        # Every transcription attempt blows up, until the server gives up on each recording.
        run_worker(file_app, make_provider(transcriber=Explodes(failures=999)), rounds=3 * count + 2)  # type: ignore[arg-type]

        with make_session_factory(file_app.state.engine)() as db:
            rows = db.scalars(select(Recording).order_by(Recording.sequence)).all()
            assert len(rows) == count
            assert {row.state for row in rows} == {"failed"}  # the real status, not a pretend success
            assert {row.error for row in rows} == {PUBLIC_FAILURE}
            audio = b"".join(stored_audio(settings, row) for row in rows)
            assert audio == expected_pcm(len(audio))  # the failure did not touch a byte of the audio
        listed = web.get("/api/recordings").json()["items"]
        assert {item["state"] for item in listed} == {"failed"}
        assert all(
            item["snippet"] is None and item["speakers"] == [] for item in listed
        )  # and no text was made up

        # The locket is not held up: its next recording uploads normally.
        second = world.run("--forward", "--scale", 20, "record=6", "until-empty=600", timeout=420)
        assert second.res["dirs"]["pending"] == [] and second.res["uploaded"] >= 1
        with make_session_factory(file_app.state.engine)() as db:
            newer = db.scalars(select(Recording).where(Recording.state == "queued")).all()
            assert newer, "the new recording was not stored"

        # Once processing works again the new recording is transcribed; failed ones are not quietly flipped.
        run_worker(file_app, make_provider(), rounds=len(newer) + 1)
        with make_session_factory(file_app.state.engine)() as db:
            states = {
                row.state
                for row in db.scalars(select(Recording).where(Recording.id.in_([n.id for n in newer])))
            }
            assert states == {"completed"}
            assert (
                db.scalar(select(Recording).where(Recording.state == "failed")) is not None
            )  # the old ones stay failed

    def test_each_companys_locket_only_ever_fills_its_own_account(
        self,
        tmp_path: Path,
        sim_plain_binary: Path,
        live: str,
        tenants: dict[str, str],
        file_app,  # type: ignore[no-untyped-def]
    ) -> None:
        acme_world = Sim(sim_plain_binary, tmp_path / "acme-locket")
        globex_world = Sim(sim_plain_binary, tmp_path / "globex-locket")
        acme = new_web(file_app)
        acme.login(tenants["manager"])
        globex = new_web(file_app)
        globex.login(tenants["other"])

        for sim, web in ((acme_world, acme), (globex_world, globex)):
            code, _ = new_locket_code(web)
            run = sim.run(
                "--forward",
                "--scale",
                20,
                f"type=server {live}",
                "type=wifi simnet simulated-password",
                f"type=pair {code}",
                "wait=3",
                "record=8",
                "until-empty=600",
                timeout=420,
            )
            assert run.res["dirs"]["pending"] == [] and run.res["uploaded"] >= 1
            assert real_credential(sim) not in run.out

        acme_items = acme.get("/api/recordings").json()["items"]
        globex_items = globex.get("/api/recordings").json()["items"]
        assert acme_items and globex_items
        assert not ({i["id"] for i in acme_items} & {i["id"] for i in globex_items})  # nothing is shared

        acme_ids = {uuid.UUID(item["id"]) for item in acme_items}
        globex_ids = {uuid.UUID(item["id"]) for item in globex_items}
        with make_session_factory(file_app.state.engine)() as db:
            rows = db.scalars(select(Recording)).all()
            assert len({row.company_id for row in rows}) == 2
            assert len(rows) == len(acme_ids) + len(
                globex_ids
            )  # every recording is listed for exactly one company
            for row in rows:
                device = db.get(Device, row.device_id)
                assert (
                    device is not None and device.company_id == row.company_id
                )  # the company of the locket that sent it
                assert (row.id in acme_ids) != (row.id in globex_ids)
