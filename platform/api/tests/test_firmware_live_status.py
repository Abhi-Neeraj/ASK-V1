"""The status line the button recorder prints every 10 seconds, so the serial monitor always says whether the
locket is recording, whether it is online, and whether anything is being sent.

Run against SIMULATED hardware (platform/firmware/tests/hostsim), like the other firmware tests. Nothing
here ran on a board.
"""

# ruff: noqa: F811  (fixtures imported from test_firmware_web_upload are used by name)

from __future__ import annotations

import re

from tests.test_firmware_web_upload import (  # noqa: F401  (the fixtures are used by name)
    BASE,
    Sim,
    sim_binary,
    world,
)

UNPAIRED = ("--scale", "20")
STATUS = re.compile(r"^\[STATUS\] (?P<line>.*)$", re.MULTILINE)


def status_lines(out: str) -> list[str]:
    return [m["line"] for m in STATUS.finditer(out)]


class TestTheStatusLine:
    def test_an_idle_locket_says_so_every_ten_seconds(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "ok", "wait=45")
        lines = status_lines(run.out)
        assert len(lines) >= 3, run.out[-1500:]
        for line in lines:
            assert line.startswith("READY - PRESS BUTTON | Wi-Fi: ESPTEST -52 dBm | server: "), line
            assert " | button: up | to send 0 | untimed 0 | uploaded 0 | upload: idle" in line, line
        assert "server: online" in lines[-1]

    def test_it_shows_that_a_recording_is_in_progress_and_for_how_long(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "record=28")
        recording = [line for line in status_lines(run.out) if line.startswith("RECORDING ")]
        assert recording, status_lines(run.out)
        assert re.match(r"RECORDING \d+ s \(part 1\) \| Wi-Fi: ESPTEST", recording[0]), recording[0]

    def test_it_counts_what_has_been_uploaded(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "ok", "record=3", "until-empty=60", "wait=25")
        lines = status_lines(run.out)
        assert any("uploaded 1 |" in line and "to send 0" in line for line in lines), lines

    def test_it_says_when_the_server_cannot_be_reached(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "wait=35")
        assert any("server: not reachable" in line for line in status_lines(run.out)), status_lines(run.out)

    def test_it_says_when_the_locket_is_not_paired(self, world: Sim) -> None:
        run = world.run(*UNPAIRED, "wait=35")
        assert any("server: not paired" in line for line in status_lines(run.out)), status_lines(run.out)

    def test_it_says_when_wifi_is_down(self, world: Sim) -> None:
        run = world.run(*BASE, "--wifi", "down", "--server", "ok", "wait=35")
        assert any("Wi-Fi: NOT connected (" in line for line in status_lines(run.out)), status_lines(run.out)

    def test_it_can_be_switched_off_and_on(self, world: Sim) -> None:
        off = world.run(*BASE, "--server", "ok", "type=live off", "wait=35")
        assert "Status line every 10 seconds: off." in off.out
        assert status_lines(off.out) == []
        again = world.run(*BASE, "--server", "ok", "type=live off", "type=live on", "wait=35")
        assert "Status line every 10 seconds: on." in again.out
        assert len(status_lines(again.out)) >= 2
        asked = world.run(*BASE, "--server", "ok", "type=live")
        assert "The status line is on." in asked.out

    def test_it_never_carries_a_password_or_the_credential(self, world: Sim) -> None:
        # Sim.run fails the test if the built-in password or the locket's credential is in any output at all.
        run = world.run(*BASE, "--server", "ok", "record=3", "wait=30")
        assert status_lines(run.out)


class TestEverythingIsSaidOutLoud:
    """The complaint these answer: the serial monitor did not say whether the button was noticed, whether it
    was recording, or whether anything was being sent."""

    def test_every_press_the_locket_notices_is_reported(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "record=3")
        assert run.out.count("[BUTTON] press noticed (READY - PRESS BUTTON): starting a recording") == 1
        assert "[BUTTON] press: stop" in run.out  # and the second press, as before
        assert run.out.index("[BUTTON] press noticed") < run.out.index("[STATE] RECORDING")

    def test_a_press_that_cannot_start_a_recording_is_still_reported_with_the_reason(
        self, world: Sim
    ) -> None:
        run = world.run(*UNPAIRED, "record=3")
        assert "[BUTTON] press noticed (READY - PRESS BUTTON): starting a recording" in run.out
        assert "not paired" in run.res["error"]  # the press was seen; the start was refused, and says why

    def test_the_status_line_shows_whether_the_button_is_down(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "ok", "button-down", "wait=12")
        assert any("button: DOWN" in line for line in status_lines(run.out)), status_lines(run.out)

    def test_a_failed_upload_says_why_and_when_it_will_try_again(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "record=3", "wait=40")
        failed = re.findall(r"\[UPLOAD\] \S+ was not sent: (.*?)\. Trying again in (\d+) s", run.out)
        assert failed, run.out[-1500:]
        assert "could not reach the server" in failed[0][0]

    def test_files_lists_what_waits_on_the_card_and_which_can_be_sent(self, world: Sim) -> None:
        world.run(*BASE, "--server", "outage", "record=3")
        again = world.run(*BASE, "--server", "outage", "type=files")
        match = re.search(r"/pending: (\d+) file\(s\)", again.out)
        assert match and int(match.group(1)) >= 1, again.out[-1500:]
        assert ".wav" in again.out and "NOT a name" not in again.out

    def test_a_file_this_firmware_did_not_make_is_pointed_out_and_explained(self, world: Sim) -> None:
        pending = world.root / "sd" / "pending"
        pending.mkdir(parents=True)
        (pending / "clip-0001.wav").write_bytes(b"RIFF" + bytes(40))
        run = world.run(*BASE, "--server", "ok", "type=files", "wait=12")
        assert "/pending: 0 file(s) waiting to be sent, and 1 it cannot send" in run.out
        assert "clip-0001.wav" in run.out and "NOT a name this firmware makes" in run.out
        said = "1 file(s) in /pending have names this firmware does not recognise, so they cannot be sent"
        assert said in run.out
        lines = status_lines(run.out)
        assert any("UNREADABLE 1 (type files)" in line for line in lines), lines
        assert "to send 0" in lines[-1]  # it is not a queue: nothing is waiting to be sent
        assert (
            run.res["dirs"]["pending"] and run.res["uploaded"] == 0
        )  # kept, never uploaded under a guessed id

    def test_files_is_refused_while_recording(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "start", "type=files", "stop")
        assert "Not while recording: try again once it is saved." in run.out

    def test_every_change_of_a_button_is_echoed_as_it_happens(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "button-down", "button-up")
        assert "[BUTTON] D1 button (GPIO2): DOWN" in run.out
        assert run.out.index("[BUTTON] D1 button (GPIO2): DOWN") < run.out.index(
            "[BUTTON] D1 button (GPIO2): released"
        )

    def test_the_boards_own_boot_button_starts_and_stops_a_recording_too(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "boot-press", "wait=6", "boot-press", "wait=8")
        assert "[BUTTON] BOOT button (GPIO0): DOWN" in run.out
        assert "[BUTTON] press noticed (READY - PRESS BUTTON): starting a recording" in run.out
        assert "[BUTTON] press: stop" in run.out
        assert run.res["counters"]["sessions"] == 1 and run.res["counters"]["partsSaved"] >= 1
        assert run.res["state"] == "READY - PRESS BUTTON"
