"""The button-recorder firmware (platform/firmware/locket_web_upload) run against SIMULATED hardware.

This compiles the firmware's real source files (the sketch's own setup(), storage, card writer, recorder,
uploader, dashboard and console) against stand-ins for the Arduino core, FreeRTOS, the memory card, the
microphone, Wi-Fi and the HTTP client (platform/firmware/tests/hostsim). It exercises the firmware's LOGIC
with faults injected: ordering, locking, recovery, retries, and what is deleted and when.

It is NOT a test on the ESP32. It says nothing about real timing, the real card, the real radio, TLS on the
chip or the real microphone. Those are in platform/docs/HARDWARE_ACCEPTANCE_TESTS.md and have not been run.

Needs a C++17 compiler; SKIPPED without one. Point ASK_FIRMWARE_CXX at one if c++/g++/clang++ is not on the
path, for example ASK_FIRMWARE_CXX="python -m ziglang c++".
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import Settings
from app.services.audio import inspect_wav
from tests.test_firmware_interop import find_compiler

FIRMWARE = Path(__file__).resolve().parents[2] / "firmware"
# ASK_FIRMWARE_SKETCH points the tests at a modified copy of the sketch (used to check the tests can fail).
SKETCH = Path(os.environ.get("ASK_FIRMWARE_SKETCH") or FIRMWARE / "locket_web_upload")
HOSTSIM = FIRMWARE / "tests" / "hostsim"
MODULES = (
    "core",
    "status",
    "settings",
    "storage",
    "sdwriter",
    "recorder",
    "uploader",
    "dashboard",
    "console",
    "wifi_manager",
)

CREDENTIAL = "sim-credential-0123456789abcdef"
# Every Wi-Fi password any test uses, the built-in one among them: none may appear in the device's output.
WIFI_PASSWORDS = ("12345678", "otherpass123", "topsecretpass", "wrongpassword1")
BYTES_PER_SECOND = 32000
BLOCK = 32768
EPOCH_AT_BOOT = 1767225600  # what the simulated clock reads at start-up once it is set

NAME = re.compile(
    r"^(?P<id>r[0-9a-f]{16}-[0-9a-f]{8})\.(?P<untimed>u?)(?P<when>\d+)\.(?P<part>\d+)\."
    r"(?P<session>s[0-9a-f]{16}-[0-9a-f]{8})\.wav$"
)


def _build(directory: Path, name: str, *defines: str) -> Path:
    compiler = find_compiler()
    if compiler is None:
        pytest.skip("no C++ compiler (set ASK_FIRMWARE_CXX to run the simulated-hardware tests)")
    binary = directory / (name + (".exe" if os.name == "nt" else ""))
    # Each build of the firmware has a stamp (its compile time) that decides whether settings saved earlier
    # belong to it. Some compilers refuse the time macros, so the tests name a stamp; one uses a second stamp.
    if not any(define.startswith("-DASK_BUILD_STAMP=") for define in defines):
        defines = (*defines, "-DASK_BUILD_STAMP=simbuild")
    command = [
        *compiler,
        "-std=c++17",
        "-O1",
        "-Wall",
        "-Wextra",
        *defines,
        f"-I{HOSTSIM / 'stubs'}",
        f"-I{SKETCH}",
        str(HOSTSIM / "hostsim_main.cpp"),
        str(HOSTSIM / "hostsim_http.cpp"),
        *[str(SKETCH / f"{module}.cpp") for module in MODULES],
        "-o",
        str(binary),
    ]
    if os.name == "nt":
        command.append("-lws2_32")
    done = subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)
    assert done.returncode == 0, done.stderr[-3000:]
    # The firmware's own files must compile without a warning here too (the real board build has none).
    ours = [line for line in done.stderr.splitlines() if "warning:" in line and "locket_web_upload" in line]
    assert not ours, "\n".join(ours[:10])
    return binary


@pytest.fixture(scope="session")
def sim_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The firmware as shipped: https only, certificates checked."""
    return _build(tmp_path_factory.mktemp("hostsim"), "hostsim")


@pytest.fixture(scope="session")
def sim_plain_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The development setup: plain http allowed, so it can talk to a real server on localhost."""
    return _build(tmp_path_factory.mktemp("hostsim_plain"), "hostsim_plain", "-DASK_ALLOW_PLAIN_HTTP=1")


@dataclass
class Run:
    code: int
    out: str
    results: list[dict] = field(default_factory=list)

    @property
    def res(self) -> dict:
        assert self.results, "the program printed no RESULT line:\n" + self.out[-3000:]
        return self.results[-1]

    def web(self) -> list[tuple[int, str]]:
        found = []
        for line in self.out.splitlines():
            match = re.match(r"^WEB (\d+) ?(.*)$", line)
            if match:
                found.append((int(match.group(1)), match.group(2)))
        return found


class Sim:
    def __init__(self, binary: Path, root: Path) -> None:
        self.binary = binary
        self.root = root

    def run(self, *args: object, timeout: int = 300) -> Run:
        done = subprocess.run(
            [str(self.binary), "--dir", str(self.root), "--timeout", str(timeout - 20), *map(str, args)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        results = [json.loads(line[7:]) for line in done.stdout.splitlines() if line.startswith("RESULT ")]
        run = Run(code=done.returncode, out=done.stdout, results=results)
        # Whatever happened, a secret never appears in the device's output.
        assert CREDENTIAL not in done.stdout, "the credential was printed"
        assert "simulated-password" not in done.stdout, "the Wi-Fi password was printed"
        for password in WIFI_PASSWORDS:
            assert password not in done.stdout, "a Wi-Fi password was printed"
        return run

    def files(self, folder: str) -> list[Path]:
        return sorted((self.root / "sd" / folder).glob("*"))

    def server_files(self) -> list[Path]:
        return sorted((self.root / "server").glob("*.wav"))


@pytest.fixture
def world(tmp_path: Path, sim_binary: Path) -> Sim:
    return Sim(sim_binary, tmp_path / "locket")


@pytest.fixture
def plain_world(tmp_path: Path, sim_plain_binary: Path) -> Sim:
    return Sim(sim_plain_binary, tmp_path / "locket")


BASE = ("--paired", "--scale", "20")


# ---- helpers on what is on the card --------------------------------------------------------------


def sample_at(index: int) -> int:
    h = (index * 0x9E3779B1) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    return (h & 0x7FFF) - 0x4000


def expected_pcm(byte_count: int) -> bytes:
    import array

    samples = array.array("h", (sample_at(i) for i in range(byte_count // 2)))
    return samples.tobytes()


def parse(path: Path) -> dict:
    match = NAME.match(path.name)
    assert match, f"not a name the firmware makes: {path.name}"
    return {
        "id": match["id"],
        "untimed": bool(match["untimed"]),
        "when": int(match["when"]),
        "part": int(match["part"]),
        "session": match["session"],
    }


def read_wav(path: Path) -> tuple[dict, bytes]:
    """The header fields and the audio the header describes."""
    raw = path.read_bytes()
    assert len(raw) >= 44, f"{path.name} is shorter than a header"
    riff, wave, fmt, data = raw[0:4], raw[8:12], raw[12:16], raw[36:40]
    assert (riff, wave, fmt, data) == (b"RIFF", b"WAVE", b"fmt ", b"data"), path.name
    size = int.from_bytes(raw[40:44], "little")
    assert int.from_bytes(raw[4:8], "little") == 36 + size, (
        f"{path.name}: RIFF size disagrees with the data size"
    )
    fields = {
        "rate": int.from_bytes(raw[24:28], "little"),
        "channels": int.from_bytes(raw[22:24], "little"),
        "bits": int.from_bytes(raw[34:36], "little"),
        "data": size,
        "file": len(raw),
    }
    assert (fields["rate"], fields["channels"], fields["bits"]) == (16000, 1, 16)
    assert 44 + size <= len(raw) < 44 + size + 2, f"{path.name}: file length disagrees with the header"
    return fields, raw[44 : 44 + size]


def assert_valid_for_the_server(path: Path, settings: Settings) -> None:
    info = inspect_wav(path, settings)  # the server's own validation
    assert (info.sample_rate, info.channels, info.bits_per_sample) == (16000, 1, 16)


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def audio_of(files: list[Path]) -> bytes:
    ordered = sorted(files, key=lambda p: (parse(p)["session"], parse(p)["part"]))
    return b"".join(read_wav(p)[1] for p in ordered)


# ---- the rules -----------------------------------------------------------------------------------


@pytest.mark.parametrize("tests", ["test_core.cpp", "test_web_upload_core.cpp"])
def test_the_button_recorders_rules_pass_their_unit_tests(tests: str, tmp_path: Path) -> None:
    """core.cpp (names, WAV check, receipt check, planning, debounce, and the half shared with the other
    firmware) built with strict C++11 warnings and run against its own tests."""
    compiler = find_compiler()
    if compiler is None:
        pytest.skip("no C++ compiler (set ASK_FIRMWARE_CXX to run the firmware tests)")
    binary = tmp_path / ("unit.exe" if os.name == "nt" else "unit")
    build = subprocess.run(
        [
            *compiler,
            "-std=c++11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            f"-I{SKETCH}",
            str(FIRMWARE / "tests" / tests),
            str(SKETCH / "core.cpp"),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert build.returncode == 0, build.stderr[-2000:]
    done = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stdout[-2000:]
    assert done.stdout.strip().endswith("0 failed")


# ---- recording -----------------------------------------------------------------------------------


class TestRecording:
    def test_a_recording_is_saved_as_numbered_parts_of_valid_audio(
        self, world: Sim, settings: Settings
    ) -> None:
        # Parts of 5 blocks (5.12 s). The server is unreachable, so everything stays on the card.
        run = world.run(*BASE, "--server", "outage", "--pref", "maxclip=6", "record=14")
        res = run.res
        mic = res["micBytes"]
        part_bytes = 5 * BLOCK
        full, tail = divmod(mic, part_bytes)
        dropped = 1 if 0 < tail < BYTES_PER_SECOND // 2 else 0
        expected_parts = full + (1 if tail >= BYTES_PER_SECOND // 2 else 0)

        files = world.files("pending")
        assert len(files) == expected_parts >= 3
        names = [parse(p) for p in files]
        assert [n["part"] for n in names] == list(range(1, expected_parts + 1))
        assert len({n["session"] for n in names}) == 1  # one recording
        assert len({n["id"] for n in names}) == expected_parts  # every part has its own id
        assert not any(n["untimed"] for n in names)

        sizes = [read_wav(p)[0]["data"] for p in files]
        assert sizes[:full] == [part_bytes] * full
        for path in files:
            assert_valid_for_the_server(path, settings)

        # The audio is exactly what the microphone produced: no gap, no repeat, across part boundaries.
        audio = audio_of(files)
        assert len(audio) == mic - dropped * tail
        assert audio == expected_pcm(len(audio))

        # Each part starts where the previous one ended (to the second), by the audio clock.
        first = names[0]["when"]
        for index, n in enumerate(names):
            assert n["when"] == first + (index * part_bytes) // BYTES_PER_SECOND

        assert res["dirs"]["recording"] == []
        assert res["counters"]["partsSaved"] == expected_parts
        assert res["counters"]["tailsDropped"] == dropped
        assert res["violations"] == 0, res["violationNotes"]
        assert res["state"] == "READY - PRESS BUTTON"
        assert "[SD-WRITER] Part 1 opened" in run.out

    def test_starting_a_new_recording_never_deletes_an_old_one(self, world: Sim) -> None:
        first = world.run(*BASE, "--server", "outage", "record=3")
        before = {p.name: p.read_bytes() for p in world.files("pending")}
        assert before
        second = world.run(*BASE, "--server", "outage", "record=3", "record=3")
        after = {p.name: p.read_bytes() for p in world.files("pending")}
        assert second.res["counters"]["sessions"] == 2
        for name, content in before.items():
            assert after.get(name) == content, f"{name} was changed or removed"
        assert len(after) > len(before)
        sessions = {parse(p)["session"] for p in world.files("pending")}
        assert len(sessions) == 3  # one from the first run, two from the second
        ids = [parse(p)["id"] for p in world.files("pending")]
        assert len(ids) == len(set(ids))  # no id is ever reused, even across a restart
        assert first.res["counters"]["sessions"] == 1

    def test_a_tail_under_half_a_second_is_dropped_and_said_so(self, world: Sim, settings: Settings) -> None:
        # The microphone goes quiet 10,000 bytes (0.3 s) into the third part: too short for the server.
        run = world.run(
            *BASE,
            "--server",
            "outage",
            "--pref",
            "maxclip=6",
            "--mic-stop-after",
            2 * 5 * BLOCK + 10000,
            "record=60",
        )
        res = run.res
        files = world.files("pending")
        assert len(files) == 2
        assert res["counters"]["tailsDropped"] == 1
        assert res["state"] == "ERROR"
        assert "microphone" in res["error"]  # the real reason is shown, not the tail
        assert audio_of(files) == expected_pcm(2 * 5 * BLOCK)
        assert res["dirs"]["recording"] == []
        for path in files:
            assert_valid_for_the_server(path, settings)

    def test_a_microphone_that_will_not_start_is_reported_and_nothing_is_written(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "--mic-fail", "record=3")
        res = run.res
        assert res["state"] == "ERROR"
        assert "microphone" in res["error"]
        assert res["dirs"]["pending"] == [] and res["dirs"]["recording"] == []

    def test_an_unpaired_locket_does_not_record(self, world: Sim) -> None:
        run = world.run("--scale", "20", "--server", "outage", "record=3")
        res = run.res
        assert res["state"] == "ERROR"
        assert "not paired" in res["error"]
        assert res["dirs"]["pending"] == []

    def test_a_card_with_no_room_is_refused_before_recording(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "--sd-capacity", 4000000, "record=3")
        res = run.res
        assert res["state"] == "ERROR"
        assert "full" in res["error"]
        assert res["dirs"]["pending"] == []

    def test_a_card_that_fills_up_stops_cleanly_and_keeps_everything(
        self, world: Sim, settings: Settings
    ) -> None:
        # 12 MB card, 8 MB kept in reserve: about 4 MB (125 s) can be recorded.
        run = world.run(
            *BASE,
            "--server",
            "outage",
            "--pref",
            "maxclip=60",
            "--sd-capacity",
            12000000,
            "record=400",
            timeout=420,
        )
        res = run.res
        assert res["state"] == "ERROR"
        assert "nearly full" in res["error"]
        files = world.files("pending")
        assert files
        for path in files:
            assert_valid_for_the_server(path, settings)
        audio = audio_of(files)
        assert len(audio) > 3_000_000
        assert audio == expected_pcm(len(audio))
        assert len(audio) == res["micBytes"]  # nothing captured was lost
        assert res["violations"] == 0, res["violationNotes"]

    def test_wifi_that_is_down_does_not_stop_recording(self, world: Sim) -> None:
        run = world.run(*BASE, "--wifi", "down", "record=6", "wait=5")
        res = run.res
        assert res["counters"]["partsSaved"] >= 1
        assert world.files("pending")
        assert res["server"]["requests"] == []  # nothing was attempted without a network
        assert res["state"] == "READY - PRESS BUTTON"

    def test_a_server_that_never_answers_does_not_disturb_recording(
        self, world: Sim, settings: Settings
    ) -> None:
        # Every upload takes 40 simulated seconds to be answered. Recording is on another task: no effect.
        run = world.run(
            *BASE,
            "--server",
            "slow",
            "--pref",
            "maxclip=6",
            "--server-max-clip",
            6,
            "record=60",
            "until-empty=600",
            timeout=420,
        )
        res = run.res
        assert res["state"] == "READY - PRESS BUTTON", res["error"]
        assert res["counters"]["sdRemounts"] == 0
        stored = world.server_files()
        assert stored
        audio = b"".join(
            read_wav(p)[1]
            for p in sorted(stored, key=lambda p: parse(p)["part"] if NAME.match(p.name) else 0)
        )
        assert audio == expected_pcm(len(audio))
        assert len(audio) == res["micBytes"]


# ---- the memory card's own faults ----------------------------------------------------------------


class TestCardFaults:
    def test_a_write_that_returns_zero_is_retried_and_no_audio_is_lost(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "--sd-zero-writes", 2, "record=8")
        res = run.res
        assert res["counters"]["sdWriteRetries"] == 2
        assert res["counters"]["sdRemounts"] == 0
        assert res["state"] == "READY - PRESS BUTTON"
        audio = audio_of(world.files("pending"))
        assert audio == expected_pcm(len(audio)) and len(audio) == res["micBytes"]

    def test_a_card_that_needs_remounting_is_remounted_and_recording_goes_on(
        self, world: Sim, settings: Settings
    ) -> None:
        run = world.run(
            *BASE, "--server", "outage", "--pref", "maxclip=100", "--sd-glitch-after", 400000, "record=20"
        )
        res = run.res
        assert res["counters"]["sdRemounts"] == 1
        assert res["counters"]["bytesLost"] == 0
        assert res["state"] == "READY - PRESS BUTTON", res["error"]
        files = world.files("pending")
        for path in files:
            assert_valid_for_the_server(path, settings)
        audio = audio_of(files)
        assert audio == expected_pcm(len(audio)) and len(audio) == res["micBytes"]
        assert res["violations"] == 0, res["violationNotes"]  # no handle from before the remount was touched
        assert "Recovered at audio byte" in run.out

    def test_audio_lost_across_a_remount_is_counted_and_reported_not_hidden(
        self, world: Sim, settings: Settings
    ) -> None:
        run = world.run(
            *BASE,
            "--server",
            "outage",
            "--pref",
            "maxclip=100",
            "--sd-glitch-after",
            1000000,
            "--sd-lose-unflushed",
            "record=40",
        )
        res = run.res
        lost = res["counters"]["bytesLost"]
        assert res["counters"]["sdRemounts"] == 1
        assert lost > 0
        assert res["state"] == "ERROR" and "mismatch" in res["error"]  # the discrepancy is shown to the owner
        files = world.files("pending")
        for path in files:
            assert_valid_for_the_server(path, settings)  # what was kept is still valid audio
        assert sum(read_wav(p)[0]["data"] for p in files) == res["micBytes"] - lost
        assert res["violations"] == 0, res["violationNotes"]

    def test_a_card_that_disappears_keeps_finished_parts_and_the_partial_is_recovered_at_the_next_start(
        self, world: Sim, settings: Settings
    ) -> None:
        run = world.run(
            *BASE, "--server", "outage", "--pref", "maxclip=6", "--eject-after", 700000, "record=60"
        )
        res = run.res
        assert res["state"] == "ERROR"
        assert "memory card" in res["error"]
        finished = [p.name for p in world.files("pending")]
        assert finished  # parts completed before the card went are already queued
        assert (
            len(world.files("recording")) == 1
        )  # the part that was being written stays where recovery finds it

        # The card is back after a restart. The partial part is given a correct header and queued.
        again = world.run(*BASE, "--server", "outage", "wait=2")
        assert "[RECOVER] Saved an interrupted recording" in again.out
        assert again.res["dirs"]["recording"] == []
        files = world.files("pending")
        assert len(files) == len(finished) + 1
        for path in files:
            assert_valid_for_the_server(path, settings)
        audio = audio_of(files)
        assert audio == expected_pcm(len(audio))
        assert (
            len(audio) >= 700000 - 44 * 3 - 2 * BLOCK
        )  # essentially everything written before the card went

    def test_a_power_cut_loses_only_what_was_never_flushed(self, world: Sim, settings: Settings) -> None:
        run = world.run(
            *BASE, "--server", "outage", "--pref", "maxclip=100", "--power-cut-after", 700000, "record=60"
        )
        assert run.code == 3  # the simulated power cut
        assert not run.results
        torn = world.files("recording")
        assert len(torn) == 1
        # The header of a torn part still says "no audio yet": only recovery can make it a clip.
        assert int.from_bytes(torn[0].read_bytes()[40:44], "little") == 0

        # After the restart the part is recovered, queued, and uploaded.
        again = world.run(*BASE, "--server", "ok", "until-empty=120")
        assert "[RECOVER] Saved an interrupted recording" in again.out
        stored = again.res["server"]["stored"]
        assert len(stored) == 1
        saved = world.server_files()[0]
        assert_valid_for_the_server(saved, settings)
        fields, audio = read_wav(saved)
        # Flushes happen every 8 blocks; the power went in the 22nd, so exactly 16 blocks survive.
        assert fields["data"] == 16 * BLOCK
        assert audio == expected_pcm(len(audio))
        assert again.res["dirs"]["pending"] == []

    def test_two_tasks_never_use_the_card_at_the_same_time(self, world: Sim) -> None:
        run = world.run(
            *BASE,
            "--server",
            "ok",
            "--server-max-clip",
            6,
            "--pref",
            "maxclip=6",
            "record=60",
            "until-empty=600",
            timeout=420,
        )
        res = run.res
        assert res["violations"] == 0, res["violationNotes"]
        assert res["uploaded"] >= 5
        # Uploads really did overlap the recording: the first receipt came before the recording was stopped.
        stop = run.out.index("[BUTTON] press: stop")
        assert run.out.index("stored by the server") < stop

    def test_the_overlap_check_does_catch_a_missing_lock(self, world: Sim) -> None:
        # The same run with the firmware's lock switched off: two tasks must be seen on the card at once.
        run = world.run(
            *BASE,
            "--server",
            "ok",
            "--server-max-clip",
            6,
            "--pref",
            "maxclip=6",
            "break-locks",
            "record=60",
            "until-empty=600",
            timeout=420,
        )
        assert run.res["violations"] > 0


# ---- uploading -----------------------------------------------------------------------------------


def put_requests(res: dict) -> list[dict]:
    return [r for r in res["server"]["requests"] if r["method"] == "PUT"]


def queued_outage(world: Sim, seconds: int = 14) -> list[str]:
    """Record with the server unreachable, so something waits on the card; returns the file names."""
    world.run(*BASE, "--server", "outage", "--pref", "maxclip=6", f"record={seconds}")
    names = [p.name for p in world.files("pending")]
    assert names
    return names


class TestUploading:
    def test_each_part_is_sent_as_documented_confirmed_and_only_then_deleted(
        self, world: Sim, settings: Settings
    ) -> None:
        run = world.run(
            *BASE,
            "--server",
            "ok",
            "--pref",
            "maxclip=6",
            "--server-max-clip",
            6,
            "record=14",
            "until-empty=300",
        )
        res = run.res
        stored = res["server"]["stored"]
        parts = res["counters"]["partsSaved"]
        assert parts >= 3 and len(stored) == parts and res["uploaded"] == parts
        assert (
            res["server"]["badRequests"] == 0
        )  # every request carried the documented headers and a valid WAV
        assert res["dirs"]["pending"] == [] and res["dirs"]["rejected"] == []

        by_sequence = sorted(stored, key=lambda s: s["sequence"])
        assert [s["sequence"] for s in by_sequence] == list(range(1, parts + 1))
        assert len({s["session"] for s in stored}) == 1
        first = datetime.strptime(by_sequence[0]["recordedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        for s in by_sequence:
            started = datetime.strptime(s["recordedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            assert (
                int((started - first).total_seconds())
                == ((s["sequence"] - 1) * 5 * BLOCK) // BYTES_PER_SECOND
            )

        # What the server holds is exactly what was recorded.
        files = {s["id"]: world.root / "server" / f"{s['id']}.wav" for s in stored}
        for path in files.values():
            assert_valid_for_the_server(path, settings)
        audio = b"".join(read_wav(files[s["id"]])[1] for s in by_sequence)
        assert audio == expected_pcm(len(audio))

        # HTTPS with the certificate checked, on every request.
        assert all(r["tls"] and not r["insecure"] and r["ca"] > 0 for r in res["server"]["requests"])
        assert all(r["status"] in (200, 201) for r in res["server"]["requests"])
        assert res["serverReachable"] is True

    def test_failed_uploads_are_retried_with_the_same_id_and_stored_once(self, world: Sim) -> None:
        run = world.run(
            *BASE,
            "--server",
            "error500:3",
            "--pref",
            "maxclip=6",
            "--server-max-clip",
            6,
            "record=14",
            "until-empty=600",
            timeout=420,
        )
        res = run.res
        puts = put_requests(res)
        failed_ids = {r["path"].rsplit("/", 1)[1] for r in puts if r["status"] == 500}
        assert len([r for r in puts if r["status"] == 500]) == 3
        stored_ids = {s["id"] for s in res["server"]["stored"]}
        assert failed_ids <= stored_ids  # the same ids were tried again, not new ones
        assert len(res["server"]["stored"]) == res["counters"]["partsSaved"]
        assert res["dirs"]["pending"] == []

    def test_a_reply_lost_after_the_server_stored_the_clip_does_not_create_a_second_copy(
        self, world: Sim
    ) -> None:
        run = world.run(
            *BASE,
            "--server",
            "lost-ack:2",
            "--pref",
            "maxclip=6",
            "--server-max-clip",
            6,
            "record=14",
            "until-empty=600",
            timeout=420,
        )
        res = run.res
        puts = put_requests(res)
        assert [r["status"] for r in puts[:3]] == [-11, -11, 200]  # lost, lost, then "already stored"
        assert len({r["path"] for r in puts[:3]}) == 1  # always the same id
        assert len(res["server"]["stored"]) == res["counters"]["partsSaved"]  # exactly one copy of each
        assert len(world.server_files()) == res["counters"]["partsSaved"]
        assert res["dirs"]["pending"] == []

    def test_an_id_the_server_already_holds_for_different_audio_gets_a_new_id(self, world: Sim) -> None:
        run = world.run(
            *BASE,
            "--server",
            "conflict-first",
            "--pref",
            "maxclip=6",
            "--server-max-clip",
            6,
            "record=8",
            "until-empty=600",
        )
        res = run.res
        puts = put_requests(res)
        conflicts = [r for r in puts if r["status"] == 409]
        assert len(conflicts) == 1
        conflict_ids = {r["path"].rsplit("/", 1)[1] for r in conflicts}
        stored_ids = {s["id"] for s in res["server"]["stored"]}
        assert not (conflict_ids & stored_ids)  # a fresh id was used for that clip
        assert len(stored_ids) == res["counters"]["partsSaved"]  # and every clip was stored, once
        assert res["dirs"]["pending"] == []

    def test_an_outage_is_ridden_out_without_a_restart(self, world: Sim) -> None:
        run = world.run(
            *BASE,
            "--server",
            "outage",
            "--pref",
            "maxclip=6",
            "record=10",
            "wait=30",
            "server=ok",
            "until-empty=900",
            timeout=420,
        )
        res = run.res
        assert res["dirs"]["pending"] == []
        assert len(res["server"]["stored"]) == res["counters"]["partsSaved"] >= 2
        # While the outage lasted, attempts were spaced out (back-off), not hammered.
        failed = [r for r in put_requests(res) if r["status"] == -1]
        assert 1 <= len(failed) <= 8

    def test_recordings_waiting_on_the_card_are_uploaded_after_a_restart(self, world: Sim) -> None:
        names = queued_outage(world)
        again = world.run(*BASE, "--server", "ok", "until-empty=300")
        res = again.res
        assert res["dirs"]["pending"] == []
        assert len(res["server"]["stored"]) == len(names)
        assert {s["id"] for s in res["server"]["stored"]} == {parse(Path(n))["id"] for n in names}

    def test_an_unaccepted_credential_stops_uploads_keeps_every_file_and_pauses_new_recordings(
        self, world: Sim
    ) -> None:
        names = queued_outage(world)
        again = world.run(*BASE, "--server", "auth401", "wait=10", "record=3")
        res = again.res
        assert [p.name for p in world.files("pending")][: len(names)] == names  # nothing deleted or renamed
        assert res["needsPairing"] is True
        assert res["state"] == "ERROR" and "pair" in res["error"]
        assert [r for r in res["server"]["requests"] if r["path"].endswith("/pair")] == []
        assert all(r["status"] == 401 for r in res["server"]["requests"])
        assert not [r for r in put_requests(res) if r["status"] in (200, 201)]
        assert res["uploaded"] == 0

    def test_a_gateway_refusal_keeps_the_files_and_retries_only_slowly(self, world: Sim) -> None:
        names = queued_outage(world)
        again = world.run(*BASE, "--server", "denied403", "wait=120")
        res = again.res
        assert [p.name for p in world.files("pending")] == names
        assert len(put_requests(res)) == 1  # one try, then a five-minute wait
        assert "403" in res["problem"]
        assert res["needsPairing"] is False  # pairing again would not help

    @pytest.mark.parametrize("mode", ["too-large413", "invalid422"])
    def test_clips_the_server_will_never_accept_are_set_aside_not_deleted_and_not_retried(
        self, world: Sim, mode: str
    ) -> None:
        names = queued_outage(world)
        again = world.run(*BASE, "--server", mode, "wait=60")
        res = again.res
        assert res["dirs"]["pending"] == []
        assert sorted(p.name for p in world.files("rejected")) == sorted(names)  # kept, every one
        assert len(put_requests(res)) == len(names)  # each tried once, never again
        assert res["uploaded"] == 0

    @pytest.mark.parametrize("mode", ["html200", "wrong-ack", "chunked-ack"])
    def test_an_answer_that_is_not_the_servers_receipt_never_deletes_the_clip(
        self, world: Sim, mode: str
    ) -> None:
        names = queued_outage(world, seconds=8)
        again = world.run(
            *BASE, "--server", mode, "wait=40", "dump", "server=ok", "until-empty=600", timeout=420
        )
        midway = again.results[0]
        assert [p["name"] for p in midway["dirs"]["pending"]] == sorted(
            names
        )  # all still there after "success" answers
        assert midway["uploaded"] == 0
        assert len(put_requests(midway)) >= 2  # and it tried again
        assert "receipt" in midway["problem"]
        final = again.res
        assert final["dirs"]["pending"] == []  # once a real receipt came, they went
        assert len(final["server"]["stored"]) == len(names)

    def test_a_damaged_clip_on_the_card_is_set_aside_without_being_sent(self, world: Sim) -> None:
        names = queued_outage(world)
        again = world.run(
            *BASE, "--server", "ok", "--wifi", "down", "corrupt-oldest", "wifi-up", "until-empty=300"
        )
        res = again.res
        assert [p["name"] for p in res["dirs"]["rejected"]] == [names[0]]  # the damaged one, kept
        assert len(res["server"]["stored"]) == len(names) - 1
        assert names[0].split(".")[0] not in {
            r["path"].rsplit("/", 1)[1] for r in put_requests(res)
        }  # never sent

    def test_a_server_address_without_https_is_refused(self, world: Sim) -> None:
        queued_outage(world)
        again = world.run(*BASE, "--url", "http://ask.test", "--server", "ok", "wait=30")
        res = again.res
        assert res["server"]["requests"] == []
        assert "https" in res["problem"]

    def test_without_a_certificate_authority_file_nothing_is_sent(self, world: Sim) -> None:
        queued_outage(world)
        again = world.run(*BASE, "--no-ca", "--server", "ok", "wait=30")
        res = again.res
        assert res["server"]["requests"] == []
        assert "certificate" in res["problem"]
        assert world.files("pending")  # still all there

    def test_pairing_then_recording_then_uploading(self, world: Sim) -> None:
        run = world.run(
            "--scale",
            "20",
            "--server",
            "ok",
            "--pref",
            "maxclip=6",
            "--server-max-clip",
            6,
            "type=server https://ask.test",
            "type=wifi simulated-network simulated-password",
            "type=pair ABCDE-FGHIJ",
            "record=8",
            "until-empty=300",
            timeout=420,
        )
        assert "paired as LKT-SIM-0001" in run.out
        res = run.res
        assert res["paired"] is True
        assert len(res["server"]["stored"]) == res["counters"]["partsSaved"] >= 1
        pair_calls = [r for r in res["server"]["requests"] if r["path"].endswith("/pair")]
        assert len(pair_calls) == 1

    def test_a_wrong_pairing_code_is_reported_and_pairs_nothing(self, world: Sim) -> None:
        run = world.run(
            "--scale",
            "20",
            "--server",
            "bad-code",
            "type=server https://ask.test",
            "type=wifi simulated-network simulated-password",
            "type=pair WRONG-CODE0",
        )
        assert "Failed:" in run.out
        assert run.res["paired"] is False

    def test_recordings_made_before_the_clock_was_set_are_timed_exactly_once_it_is(self, world: Sim) -> None:
        run = world.run(
            *BASE,
            "--clock",
            "unset",
            "--ntp",
            "broken",
            "--server",
            "ok",
            "--pref",
            "maxclip=6",
            "--server-max-clip",
            6,
            "record=8",
            "wait=5",
            "clock-on",
            "until-empty=600",
            timeout=420,
        )
        res = run.res
        assert res["dirs"]["pending"] == [] and res["dirs"]["untimed"] == []
        stored = sorted(res["server"]["stored"], key=lambda s: s["sequence"])
        assert stored
        # The file names carried seconds since start (u1234); the clock was set later by the network. The true
        # start time of part 1 is the boot time plus that number of seconds.
        started = [int(m) for m in re.findall(r"\.u(\d+)\.1\.", run.out)] or None
        first_u = int(re.search(r"\.u(\d+)\.1\.s", run.out).group(1))  # type: ignore[union-attr]
        assert stored[0]["recordedAt"] == iso(EPOCH_AT_BOOT + first_u)
        assert started is not None
        assert "start time worked out" in run.out

    def test_recordings_from_before_the_clock_was_ever_set_are_kept_not_guessed_at(self, world: Sim) -> None:
        world.run(
            *BASE,
            "--clock",
            "unset",
            "--ntp",
            "broken",
            "--server",
            "outage",
            "--pref",
            "maxclip=6",
            "record=8",
        )
        assert all(parse(p)["untimed"] for p in world.files("pending"))
        count = len(world.files("pending"))
        again = world.run(*BASE, "--clock", "set", "--server", "ok", "wait=20")
        res = again.res
        assert res["dirs"]["pending"] == []
        assert len(res["dirs"]["untimed"]) == count  # kept apart, never uploaded under an invented time
        assert put_requests(res) == []


# ---- the local dashboard and the serial console --------------------------------------------------


class TestDashboardAndConsole:
    def test_the_status_page_shows_health_and_no_secrets(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "ok", "--pref", "apon=1", "record=4", "web=/status", "type=ap")
        code, body = run.web()[0]
        assert code == 200
        status = json.loads(body)
        for key in (
            "state",
            "microphone",
            "card",
            "wifi",
            "server",
            "uploads",
            "recording",
            "firmware",
            "problem",
        ):
            assert key in status, key
        assert status["microphone"] == "ok"
        assert status["card"]["ready"] is True
        assert status["server"]["paired"] is True
        assert status["firmware"].startswith("0.2.0")
        password = re.search(r"password\s+(\S+)", run.out)
        assert password and len(password.group(1)) >= 12
        for secret in (CREDENTIAL, password.group(1), "simulated-password"):
            assert secret not in body, "a secret is on the status page"
        # The page itself never names parts or services.
        assert "Telegram" not in body and "Gemini" not in body

    def test_the_page_answers_only_on_the_lockets_own_network(self, world: Sim) -> None:
        run = world.run(
            *BASE, "--server", "outage", "--pref", "apon=1", "web=/status", "web-sta=/status", "web-sta=/"
        )
        answers = run.web()
        assert answers[0][0] == 200
        assert answers[1][0] == 403 and answers[2][0] == 403

    def test_the_maintenance_wifi_is_off_unless_switched_on(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "web=/status")
        assert run.web()[0][0] == 404
        assert "Off." in run.out

    def test_the_console_never_echoes_a_password(self, world: Sim) -> None:
        run = world.run(
            *BASE, "--server", "outage", "type=wifi homenet topsecretpass", "type=status", "type=help"
        )
        assert "topsecretpass" not in run.out
        assert "Wi-Fi saved" in run.out
        assert "firmware" in run.out and "microphone" in run.out

    def test_the_serial_console_can_start_and_stop_a_recording(self, world: Sim) -> None:
        run = world.run(*BASE, "--server", "outage", "type=record", "wait=6", "type=record", "wait=4")
        assert run.res["counters"]["sessions"] == 1
        assert run.res["counters"]["partsSaved"] >= 1
