"""The locket joins its Wi-Fi network by itself at start-up (platform/firmware/locket_web_upload).

The firmware has a network built in (config.h: ESPTEST). These tests run the sketch's own start-up and its
Wi-Fi manager (wifi_manager.cpp) against SIMULATED hardware: a pretend access point that accepts a name and
password, takes a little time to answer, and reports why it refused, the way the chip's own Wi-Fi events do.
See platform/firmware/tests/hostsim and the header of test_firmware_web_upload.py.

They say nothing about a real radio, a real hotspot, real signal strength or how long a real join takes.
Nothing here ran on a board.

Needs a C++17 compiler; SKIPPED without one (set ASK_FIRMWARE_CXX).
"""

# ruff: noqa: F811  (fixtures imported from test_firmware_web_upload are used by name)

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.config import Settings
from tests.test_firmware_web_upload import (  # noqa: F401  (the fixtures are used by name)
    BASE,
    SKETCH,
    Sim,
    assert_valid_for_the_server,
    audio_of,
    expected_pcm,
    sim_binary,
    world,
)
from tests.test_firmware_web_upload import _build as build_simulator

IDLE = "READY - PRESS BUTTON"
UNPAIRED = ("--scale", "20")
CONNECTING = "[WiFi] Connecting to ESPTEST..."
CONNECTED = "[WiFi] Connected successfully"
FAILED = re.compile(r"\[WiFi\] Connection failed: (?P<why>.*)\. Trying again in (?P<seconds>\d+) s\.")


@pytest.fixture(scope="session")
def sim_newer_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same firmware flashed again later: a different build, so it has its own stamp."""
    return build_simulator(
        tmp_path_factory.mktemp("hostsim_newer"), "hostsim_newer", "-DASK_BUILD_STAMP=newerbuild"
    )


@pytest.fixture(scope="session")
def sim_no_network_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A build with no network built in (config.h edited to an empty name and password)."""
    return build_simulator(
        tmp_path_factory.mktemp("hostsim_none"),
        "hostsim_none",
        '-DASK_DEFAULT_WIFI_SSID=""',
        '-DASK_DEFAULT_WIFI_PASSWORD=""',
    )


# ---- joining at start-up -------------------------------------------------------------------------


class TestJoiningAtStartUp:
    def test_the_locket_joins_the_built_in_network_with_no_command_and_no_pairing(self, world: Sim) -> None:
        # Nothing is typed, and the locket is not paired: the only action is to let it run.
        run = world.run(*UNPAIRED, "wait=5")
        out = run.out
        assert CONNECTING in out and CONNECTED in out
        assert out.index(CONNECTING) < out.index(CONNECTED)
        assert "[WiFi] IP address: 192.168.43.57   signal strength (RSSI): -52 dBm" in out
        wifi = run.res["wifi"]
        assert wifi["connected"] is True and wifi["state"] == "connected"
        assert wifi["ssid"] == "ESPTEST" and wifi["builtIn"] is True
        assert wifi["ip"] == "192.168.43.57" and wifi["rssi"] == -52
        assert wifi["reason"] == "" and wifi["attempts"] == 0
        assert run.res["paired"] is False  # joined without being paired
        assert "Connection failed" not in out and "[WiFi] No network is set" not in out
        # The password is not in anything the locket printed (Sim.run checks every run for it too).
        assert "12345678" not in out

    def test_joining_is_attempted_once_when_it_works_at_the_first_try(self, world: Sim) -> None:
        run = world.run(*UNPAIRED, "wait=20")
        assert run.out.count(CONNECTING) == 1 and run.out.count(CONNECTED) == 1
        assert run.res["wifi"]["beginCalls"] == 1

    def test_the_locket_is_ready_to_record_before_the_network_answers(self, world: Sim) -> None:
        # The access point takes 8 simulated seconds to answer. Start-up does not wait for it.
        run = world.run(*UNPAIRED, "--ap-delay", 8000, "wait=20")
        out = run.out
        assert CONNECTING in out
        assert out.index("[READY] Press the button to start recording") < out.index(CONNECTED)

    def test_a_slow_connection_does_not_hold_up_recording(self, world: Sim, settings: Settings) -> None:
        # The network answers after 15 simulated seconds. A whole recording is made and queued meanwhile.
        run = world.run(*BASE, "--server", "ok", "--ap-delay", 15000, "record=3", "until-empty=120")
        out = run.out
        joined = out.index(CONNECTED)
        assert out.index("[STATE] RECORDING") < joined
        assert out.index("[EVENT] UPLOAD_QUEUED") < joined
        assert out.index("[STATE] IDLE", out.index("[EVENT] UPLOAD_QUEUED")) < joined
        # Once the network is up, what was recorded is uploaded: nothing was lost by recording offline.
        res = run.res
        assert res["dirs"]["pending"] == []
        assert len(res["server"]["stored"]) == res["counters"]["partsSaved"] >= 1
        assert res["uploaded"] == res["counters"]["partsSaved"]
        assert res["state"] == IDLE
        assert res["violations"] == 0, res["violationNotes"]

    def test_a_locket_with_no_built_in_network_says_so_and_still_records(
        self, sim_no_network_binary: Path, tmp_path: Path
    ) -> None:
        none = Sim(sim_no_network_binary, tmp_path / "locket")
        run = none.run(*BASE, "--server", "ok", "record=3", "type=status")
        out = run.out
        assert "[WiFi] No network is set. Type: wifi <name> <password>" in out
        assert "Connecting to" not in out
        assert run.res["wifi"]["state"] == "no-network" and run.res["wifi"]["connected"] is False
        assert "wifi          no network is set" in out
        assert run.res["counters"]["partsSaved"] >= 1 and len(none.files("pending")) >= 1
        # Typing one still works, and `wifi default` then says there is nothing built in.
        again = none.run(
            *BASE, "--server", "ok", "type=wifi homenet topsecretpass", "wait=3", "type=wifi default"
        )
        assert again.res["wifi"]["ssid"] == ""
        assert "Forgotten. There is no built-in network" in again.out
        typed = none.run(*BASE, "--server", "ok", "type=wifi homenet topsecretpass", "wait=3")
        assert typed.res["wifi"]["connected"] is True and typed.res["wifi"]["ssid"] == "homenet"
        assert typed.res["wifi"]["builtIn"] is False


# ---- which network is used: the built-in one, or one typed ---------------------------------------


class TestWhichNetwork:
    def test_a_network_typed_replaces_the_built_in_one_and_survives_a_restart(self, world: Sim) -> None:
        first = world.run(*UNPAIRED, "type=wifi homenet topsecretpass", "wait=3", "type=status")
        assert "Wi-Fi saved: joining homenet now." in first.out
        assert "[WiFi] Connecting to homenet..." in first.out
        assert "connected to homenet (network you typed)" in first.out
        assert first.res["wifi"]["ssid"] == "homenet" and first.res["wifi"]["builtIn"] is False
        assert first.res["wifi"]["connected"] is True

        # The same firmware, started again: the typed network is still the one in use.
        second = world.run(*UNPAIRED, "wait=5")
        assert "[WiFi] Connecting to homenet..." in second.out
        assert "Connecting to ESPTEST" not in second.out and "was cleared" not in second.out
        assert second.res["wifi"]["ssid"] == "homenet" and second.res["wifi"]["connected"] is True

    def test_wifi_default_goes_back_to_the_built_in_network_and_stays_there(self, world: Sim) -> None:
        world.run(*UNPAIRED, "type=wifi homenet topsecretpass", "wait=3")
        back = world.run(*UNPAIRED, "type=wifi default", "wait=3")
        assert "Back to the built-in network. Joining it now." in back.out
        assert CONNECTING in back.out and CONNECTED in back.out
        assert back.res["wifi"]["ssid"] == "ESPTEST" and back.res["wifi"]["builtIn"] is True
        after = world.run(*UNPAIRED, "wait=5")
        assert CONNECTING in after.out and after.res["wifi"]["ssid"] == "ESPTEST"

    def test_a_network_saved_before_this_build_cannot_override_the_built_in_one(self, world: Sim) -> None:
        # An older firmware saved a network. It exists and would work: it must not be used.
        run = world.run(
            *UNPAIRED,
            "--ap",
            "oldnet:oldpassword1",
            "--pref",
            "ssid=oldnet",
            "--pref",
            "wpass=oldpassword1",
            "wait=5",
        )
        out = run.out
        assert (
            "A network saved before this firmware was flashed was cleared; using the built-in network." in out
        )
        assert CONNECTING in out and "oldnet" not in out.replace("A network saved", "")
        assert run.res["wifi"]["ssid"] == "ESPTEST" and run.res["wifi"]["builtIn"] is True
        assert run.res["wifi"]["connected"] is True

    def test_flashing_a_new_build_clears_a_network_typed_on_the_old_one(
        self, world: Sim, sim_newer_binary: Path
    ) -> None:
        old = world.run(*UNPAIRED, "type=wifi homenet topsecretpass", "wait=3")
        assert old.res["wifi"]["ssid"] == "homenet"
        newer = Sim(sim_newer_binary, world.root).run(*UNPAIRED, "wait=5")
        assert "A network saved before this firmware was flashed was cleared" in newer.out
        assert newer.res["wifi"]["ssid"] == "ESPTEST" and newer.res["wifi"]["builtIn"] is True
        assert newer.res["wifi"]["connected"] is True
        # A network typed on the new build is kept on that build.
        typed = Sim(sim_newer_binary, world.root).run(*UNPAIRED, "type=wifi homenet topsecretpass", "wait=3")
        assert typed.res["wifi"]["ssid"] == "homenet"
        kept = Sim(sim_newer_binary, world.root).run(*UNPAIRED, "wait=5")
        assert kept.res["wifi"]["ssid"] == "homenet" and "was cleared" not in kept.out

    def test_a_name_or_password_that_cannot_be_used_is_refused_and_changes_nothing(self, world: Sim) -> None:
        run = world.run(*UNPAIRED, "type=wifi homenet short", "wait=3")
        assert "Not saved: the password must be at least 8 characters" in run.out
        assert run.res["wifi"]["ssid"] == "ESPTEST" and run.res["wifi"]["connected"] is True
        assert "short" not in run.out.replace("Not saved: the password must be at least 8 characters", "")

    def test_pairing_needs_no_wifi_command(self, world: Sim) -> None:
        run = world.run(*UNPAIRED, "--server", "ok", "type=server https://ask.test", "type=pair ABCDE-FGHIJ")
        assert "paired as LKT-SIM-0001" in run.out
        assert run.res["paired"] is True and run.res["wifi"]["ssid"] == "ESPTEST"
        assert "Wi-Fi saved" not in run.out


# ---- when the network is not available -----------------------------------------------------------


class TestWhenTheNetworkFails:
    def test_a_wrong_password_is_reported_with_its_reason_and_tried_again_later(self, world: Sim) -> None:
        # The hotspot's password is not the one built in: the chip says AUTH_FAIL (202), as the real one does.
        run = world.run(*UNPAIRED, "--ap", "ESPTEST:wrongpassword1", "wait=150")
        out = run.out
        failures = list(FAILED.finditer(out))
        assert len(failures) >= 4
        first = failures[0]
        assert "the password was not accepted" in first["why"]
        assert "AUTH_FAIL" in first["why"] and "code 202" in first["why"]
        assert CONNECTED not in out and "[WiFi] Connection failed" in out
        # Every failure names the real reason, not just the first: nothing is left over from an earlier try.
        assert all("AUTH_FAIL" in m["why"] and "code 202" in m["why"] for m in failures), [
            m["why"] for m in failures
        ]
        waits = [int(m["seconds"]) for m in failures]
        assert waits[0] <= 5, waits  # the first wait is short
        assert all(waits[i] <= waits[i + 1] for i in range(3)), waits  # then it grows ...
        assert waits[3] >= 20, waits  # the fourth wait is at least 20 s: the back-off really grows
        assert all(w <= 60 for w in waits), waits  # ... to a minute at most
        assert "(attempt 2)" in out and "(attempt 3)" in out
        wifi = run.res["wifi"]
        assert wifi["connected"] is False and wifi["state"] in ("waiting", "connecting")
        assert wifi["reasonCode"] == 202 and "password was not accepted" in wifi["reason"]
        assert wifi["beginCalls"] >= len(failures)
        for password in ("wrongpassword1", "12345678"):
            assert password not in out

    def test_recording_still_works_when_the_network_cannot_be_joined(
        self, world: Sim, settings: Settings
    ) -> None:
        # The hotspot is up but its password is not the built-in one, so the locket cannot join. It records.
        run = world.run(*BASE, "--server", "ok", "--ap", "ESPTEST:wrongpassword1", "record=3", "wait=30")
        res = run.res
        assert res["state"] == IDLE
        assert res["counters"]["partsSaved"] >= 1
        pending = world.files("pending")
        assert len(pending) == res["counters"]["partsSaved"]
        for path in pending:
            assert_valid_for_the_server(path, settings)
        assert audio_of(pending) == expected_pcm(len(audio_of(pending)))
        assert "[EVENT] UPLOAD_QUEUED" in run.out
        # Nothing was claimed to be uploaded, because nothing was.
        assert res["uploaded"] == 0 and res["server"]["stored"] == []
        assert "[EVENT] UPLOADED" not in run.out and "[EVENT] UPLOADING" not in run.out
        assert res["wifi"]["connected"] is False
        assert res["violations"] == 0, res["violationNotes"]

    def test_what_was_recorded_offline_is_uploaded_once_the_network_is_joined(self, world: Sim) -> None:
        # Same power-on: the hotspot's password is put right while the locket keeps trying. It joins, sets its
        # clock and sends what it recorded. (A recording made before the clock was ever set can only be timed
        # in the same power-on; restarted first, that file would be kept in the "untimed" folder.)
        run = world.run(
            *BASE,
            "--server",
            "ok",
            "--ap",
            "ESPTEST:wrongpassword1",
            "record=3",
            "wait=5",
            "ap-set=ESPTEST:12345678",
            "until-empty=300",
        )
        res = run.res
        assert CONNECTED in run.out
        assert run.out.index("Connection failed") < run.out.index(CONNECTED)
        assert (
            run.out.index("[EVENT] UPLOAD_QUEUED")
            < run.out.index(CONNECTED)
            < run.out.index("[EVENT] UPLOADED")
        )
        assert res["dirs"]["pending"] == [] and res["dirs"]["untimed"] == []
        stored = res["server"]["stored"]
        assert len(stored) >= 1 and res["uploaded"] == len(stored)
        assert res["violations"] == 0, res["violationNotes"]

    def test_a_network_that_is_not_there_is_reported_and_joined_when_it_appears(self, world: Sim) -> None:
        run = world.run(*UNPAIRED, "--wifi", "down", "wait=20", "wifi-up", "wait=100")
        out = run.out
        failures = list(FAILED.finditer(out))
        assert len(failures) >= 2
        for failed in failures:  # each try says so, not only the first
            assert "no network with that name was found" in failed["why"]
            assert "NO_AP_FOUND" in failed["why"] and "2.4 GHz" in failed["why"]
        assert out.index("Connection failed") < out.index(CONNECTED)
        assert run.res["wifi"]["connected"] is True and run.res["wifi"]["attempts"] == 0

    def test_a_connection_that_never_answers_gives_up_after_the_time_allowed(self, world: Sim) -> None:
        # The access point would take 40 simulated seconds. The locket allows 20, then waits and tries again.
        run = world.run(*UNPAIRED, "--ap-delay", 40000, "wait=80")
        failed = FAILED.search(run.out)
        assert failed and "no answer within the time allowed" in failed["why"]
        assert CONNECTED not in run.out
        assert run.res["wifi"]["connected"] is False
        assert run.res["wifi"]["beginCalls"] >= 2

    def test_a_dropped_connection_is_noticed_and_joined_again(self, world: Sim) -> None:
        run = world.run(*UNPAIRED, "wait=5", "wifi-down", "wait=15", "wifi-up", "wait=100")
        out = run.out
        assert out.count(CONNECTED) == 2
        lost = out.index("[WiFi] Connection lost")
        assert out.index(CONNECTED) < lost < out.rindex(CONNECTED)
        assert "lost contact with the access point" in out[lost : lost + 200]
        assert run.res["wifi"]["connected"] is True

    def test_the_status_command_says_why_and_never_shows_the_password(self, world: Sim) -> None:
        run = world.run(*UNPAIRED, "--ap", "ESPTEST:wrongpassword1", "wait=3", "type=status")
        line = next(line for line in run.out.splitlines() if line.startswith("wifi "))
        assert "NOT connected to ESPTEST (built-in network)" in line
        assert "the password was not accepted" in line
        assert "wrongpassword1" not in run.out and "12345678" not in run.out

    def test_the_local_status_page_reports_the_network_and_not_the_password(self, world: Sim) -> None:
        run = world.run(*UNPAIRED, "--pref", "apon=1", "wait=3", "web=/status")
        code, body = run.web()[0]
        assert code == 200
        wifi = json.loads(body)["wifi"]
        assert wifi["connected"] is True and wifi["ssid"] == "ESPTEST" and wifi["builtIn"] is True
        assert wifi["ip"] == "192.168.43.57" and wifi["rssi"] == -52
        assert "12345678" not in body


# ---- the password stays out of everything that is printed ----------------------------------------


class TestThePasswordInTheSource:
    def test_the_built_in_password_is_in_config_h_only(self) -> None:
        code = [p for p in SKETCH.iterdir() if p.suffix in (".h", ".cpp", ".ino")]
        # (as the quoted string: the hex digits "0123456789abcdef" in core.cpp contain the same characters)
        assert sorted(p.name for p in code if '"12345678"' in p.read_text("utf-8")) == ["config.h"]

    def test_nothing_that_prints_can_reach_the_password(self) -> None:
        sources = {
            p.name: p.read_text("utf-8") for p in SKETCH.iterdir() if p.suffix in (".h", ".cpp", ".ino")
        }
        # The password is read in one place, where the connection is made.
        readers = sorted(
            name for name, text in sources.items() if name.endswith(".cpp") and "wifiPassword()" in text
        )
        assert readers == ["settings.cpp", "wifi_manager.cpp"]
        for name, text in sources.items():
            for number, line in enumerate(text.splitlines(), 1):
                if re.search(r"Serial\.(print|println|printf|write)\b|\bsnprintf\b|\bprintf\b", line):
                    for forbidden in (
                        "ASK_DEFAULT_WIFI_PASSWORD",
                        "wifiPassword",
                        '"wpass"',
                        "password.c_str()",
                    ):
                        assert forbidden not in line, (
                            f"{name}:{number} could print the Wi-Fi password: {line.strip()}"
                        )
        # Certificate checking and pairing are not touched by the Wi-Fi manager.
        manager = sources["wifi_manager.cpp"]
        assert (
            "setInsecure" not in manager
            and "setCACert" not in manager
            and "settings::setPairing" not in manager
        )
