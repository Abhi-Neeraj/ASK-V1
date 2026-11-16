"""The physical button's two-press cycle, one test per case on the owner's list.

First press starts a recording, the second press stops it, then the locket waits for the next press. The
sketch's own button handling and state machine run here against a SIMULATED button (GPIO2 / D1, internal
pull-up, released HIGH and pressed LOW), card, microphone and network: see platform/firmware/tests/hostsim and
the header of test_firmware_web_upload.py. Nothing here ran on a real board.

Needs a C++17 compiler; SKIPPED without one (set ASK_FIRMWARE_CXX).
"""

# ruff: noqa: F811  (fixtures imported from test_firmware_web_upload are used by name)

from __future__ import annotations

import re

from app.config import Settings
from tests.test_firmware_web_upload import (  # noqa: F401  (the fixtures are used by name)
    BASE,
    BLOCK,
    BYTES_PER_SECOND,
    Sim,
    assert_valid_for_the_server,
    expected_pcm,
    parse,
    queued_outage,
    read_wav,
    sim_binary,
    world,
)

IDLE = "READY - PRESS BUTTON"


class TestTheButtonWorkflow:
    def test_power_on_leaves_the_locket_idle_and_recording_nothing(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "wait=5")
        res = run.res
        assert res["state"] == IDLE
        assert res["counters"]["sessions"] == 0
        assert res["counters"]["transitions"] == 1  # Booting -> Idle, and nothing else
        assert res["micBytes"] == 0
        assert all(not res["dirs"][name] for name in ("recording", "pending", "rejected", "untimed"))
        assert "[STATE] IDLE" in run.out and "[STATE] RECORDING" not in run.out

    def test_the_button_is_read_on_gpio2_with_the_internal_pull_up(self, world: Sim) -> None:
        # The simulated board has one button, between D1 (GPIO2) and GND, and no resistor. A pin without the
        # internal pull-up reads as permanently down, so passing means the sketch reads GPIO2, pulled up.
        run = world.run(
            *BASE,
            "--server",
            "outage",
            "type=button",
            "button-down",
            "type=button",
            "button-up",
            "type=button",
        )
        reads = re.findall(r"buttons \(D1 = GPIO2, BOOT = GPIO0\): (.*)", run.out)
        assert reads == ["both up (read HIGH)", "one is DOWN (reads LOW)", "both up (read HIGH)"]
        assert run.res["counters"]["sessions"] == 1  # and pressing it did start a recording

    def test_a_button_held_down_at_power_up_is_not_a_press(self, world: Sim) -> None:
        run = world.run(
            *BASE,
            "--server",
            "outage",
            "--hold-button",
            "wait=6",
            "dump",
            "button-up",
            "wait=2",
            "dump",
            "start",
            "wait=3",
            "stop",
        )
        held, released, final = run.results[0], run.results[1], run.results[-1]
        assert held["counters"]["sessions"] == 0 and held["state"] == IDLE  # still held: nothing started
        assert released["counters"]["sessions"] == 0  # letting go is not a press either
        assert "held down at power-up" in run.out
        assert final["counters"]["sessions"] == 1  # a fresh press after the release works
        assert final["state"] == IDLE

    def test_the_first_press_starts_exactly_one_recording_and_letting_go_does_not_stop_it(
        self, world: Sim
    ) -> None:
        run = world.run(
            *BASE, "--server", "outage", "button-down", "wait=4", "dump", "button-up", "wait=4", "dump"
        )
        held, released = run.results[0], run.results[1]
        assert held["state"] == "RECORDING" and released["state"] == "RECORDING"
        assert released["counters"]["sessions"] == 1
        assert (
            released["counters"]["transitions"] == 2
        )  # Booting -> Idle -> Recording: nothing stopped or restarted
        assert run.out.count("[STATE] RECORDING") == 1
        assert "RECORDING_STOPPED" not in run.out
        assert released["micBytes"] > 6 * BYTES_PER_SECOND  # audio kept flowing after the button was let go

    def test_the_second_press_stops_it_and_the_part_is_queued_then_the_locket_is_ready_again(
        self, world: Sim, settings: Settings
    ) -> None:
        run = world.run(*BASE, "--server", "outage", "start", "wait=4", "stop", "wait=3")
        res, out = run.res, run.out
        markers = [
            "[STATE] RECORDING",
            "[BUTTON] press: stop",
            "[STATE] FINALIZING",
            "RECORDING_STOPPED",
            "UPLOAD_QUEUED",
        ]
        positions = [out.index(marker) for marker in markers]
        assert positions == sorted(positions)  # in this order
        assert out.index("[STATE] IDLE", out.index("UPLOAD_QUEUED")) > positions[-1]  # then ready again
        assert res["state"] == IDLE
        assert res["counters"]["transitions"] == 4  # Booting->Idle, Idle->Recording, ->Finalizing, ->Idle

        # The finalized WAV: a correct header, the true duration, and exactly the audio that was captured.
        (path,) = world.files("pending")
        assert_valid_for_the_server(path, settings)
        fields, audio = read_wav(path)
        assert fields["data"] == res["micBytes"] and len(audio) == res["micBytes"]
        assert fields["file"] == 44 + res["micBytes"]
        assert audio == expected_pcm(len(audio))
        assert res["dirs"]["recording"] == []  # nothing left half-written

    def test_contact_bounce_makes_exactly_one_transition_per_press(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "bounce", "wait=3", "bounce", "wait=3")
        res = run.res
        assert res["counters"]["sessions"] == 1
        assert res["counters"]["transitions"] == 4  # one start and one stop, not a flurry
        assert res["counters"]["pressesIgnored"] == 0
        assert run.out.count("[STATE] RECORDING") == 1 and run.out.count("RECORDING_STOPPED") == 1
        assert res["state"] == IDLE

    def test_holding_the_button_down_does_not_toggle_repeatedly(self, world: Sim) -> None:
        # A ten-second hold to start, then a ten-second hold to stop. Each is one press.
        run = world.run(*BASE, "--server", "outage", "hold=10", "wait=3", "dump", "hold=10", "wait=3")
        started, final = run.results[0], run.results[-1]
        assert started["state"] == "RECORDING" and started["counters"]["transitions"] == 2
        assert final["state"] == IDLE
        assert final["counters"]["sessions"] == 1  # holding after the stop did not start another recording
        assert final["counters"]["transitions"] == 4
        assert run.out.count("[STATE] RECORDING") == 1

    def test_the_next_press_starts_a_new_recording_with_its_own_id_and_the_old_one_stays(
        self, world: Sim
    ) -> None:
        run = world.run(
            *BASE, "--server", "outage", "start", "wait=3", "stop", "start", "wait=3", "stop", "wait=2"
        )
        res = run.res
        assert res["counters"]["sessions"] == 2
        assert res["counters"]["transitions"] == 7  # boot, then two full cycles of three
        assert run.out.count("[STATE] RECORDING") == 2
        names = [parse(p) for p in world.files("pending")]
        assert len({n["session"] for n in names}) == 2  # two different recordings
        assert len({n["id"] for n in names}) == len(names)  # every part has its own id
        assert all(read_wav(p)[1] for p in world.files("pending"))  # and both are still there, complete
        assert res["state"] == IDLE

    def test_presses_while_the_last_part_is_being_saved_neither_start_nor_stop_anything(
        self, world: Sim
    ) -> None:
        # A very slow card makes saving take a long time, and three more presses arrive while it does.
        run = world.run(
            *BASE,
            "--server",
            "outage",
            "--sd-slow",
            8000,
            "start",
            "wait=3",
            "stop-nowait",
            "press",
            "press",
            "press",
            "wait=200",
            timeout=420,
        )
        res = run.res
        assert res["counters"]["sessions"] == 1
        assert res["counters"]["transitions"] == 4  # none of the extra presses moved the machine
        assert res["counters"]["pressesIgnored"] >= 2
        assert "[BUTTON] press ignored while FINALIZING" in run.out
        assert run.out.count("[STATE] RECORDING") == 1
        assert res["state"] == IDLE and len(world.files("pending")) == 1

    def test_a_refused_start_is_recoverable_by_pairing_and_pressing_again(self, world: Sim) -> None:
        run = world.run(
            "--scale",
            20,
            "--server",
            "ok",
            "--server-max-clip",
            6,
            "type=server https://ask.test",
            "type=wifi simulated-network simulated-password",
            "start",
            "dump",
            "type=pair ABCDE-FGHIJ",
            "wait=3",
            "start",
            "wait=3",
            "stop",
            "until-empty=300",
            timeout=420,
        )
        refused, final = run.results[0], run.results[-1]
        assert refused["state"] == "ERROR" and "not paired" in refused["error"]
        assert (
            refused["counters"]["sessions"] == 0 and refused["micBytes"] == 0
        )  # nothing pretended to record
        assert final["counters"]["sessions"] == 1 and final["state"] == IDLE
        assert len(final["server"]["stored"]) >= 1

    def test_the_next_recording_can_start_while_an_earlier_upload_is_still_in_flight(
        self, world: Sim
    ) -> None:
        # The server takes 40 simulated seconds to answer each upload. The owner must not have to wait.
        run = world.run(
            *BASE,
            "--server",
            "slow",
            "start",
            "wait=4",
            "stop",
            "start",
            "dump",
            "wait=4",
            "stop",
            "until-empty=900",
            timeout=420,
        )
        midway, final = run.results[0], run.res
        assert midway["state"] == "RECORDING"  # the second recording is under way...
        assert (
            midway["uploading"] is True and len(midway["dirs"]["pending"]) == 1
        )  # ...while the first is being sent
        assert final["counters"]["sessions"] == 2
        assert final["dirs"]["pending"] == []
        stored = final["server"]["stored"]
        assert len(stored) == 2 and len({s["session"] for s in stored}) == 2
        for s in stored:
            audio = read_wav(world.root / "server" / f"{s['id']}.wav")[1]
            assert audio == expected_pcm(len(audio))  # neither recording was disturbed by the other's upload

    def test_a_locket_with_too_many_recordings_waiting_refuses_a_new_one_clearly(self, world: Sim) -> None:
        waiting = queued_outage(world, seconds=8)
        run = world.run(*BASE, "--server", "outage", "--pref", f"maxpend={len(waiting)}", "start", "wait=2")
        res = run.res
        assert res["state"] == "ERROR" and "too many recordings are waiting" in res["error"]
        assert res["counters"]["sessions"] == 0
        assert [p.name for p in world.files("pending")] == waiting  # and nothing was touched

    def test_a_missing_memory_card_gives_a_clear_error_and_nothing_pretends_to_record(
        self, world: Sim
    ) -> None:
        run = world.run(*BASE, "--server", "outage", "--sd-begin-failures", 99, "wait=2", "start", "wait=2")
        res = run.res
        assert res["state"] == "ERROR" and "memory card" in res["error"]
        assert res["counters"]["sessions"] == 0 and res["micBytes"] == 0
        assert "[STATE] RECORDING" not in run.out and "Part 1 opened" not in run.out

    def test_without_psram_the_locket_says_so_and_does_not_record(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "--psram", 0, "start", "wait=2")
        res = run.res
        assert res["state"] == "ERROR" and "not enough memory" in res["error"]
        assert "PSRAM is not enabled" in run.out  # said at start-up, before anyone pressed anything
        assert res["counters"]["sessions"] == 0 and "[STATE] RECORDING" not in run.out
