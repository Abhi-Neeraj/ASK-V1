"""The firmware's own logic, run against the server's.

`platform/firmware/locket/core.cpp` holds the rules a locket must follow so the server accepts what it sends:
the WAV header, the SHA-256 the server checks, the timestamp format, the clip id, and what to do after each
answer. These tests compile that file and check it against the real API code and against Python's own
`hashlib` and `datetime`. They need a C++ compiler and are SKIPPED without one.

Point `ASK_FIRMWARE_CXX` at a compiler command if `c++`, `g++` or `clang++` is not on the path, for example
`ASK_FIRMWARE_CXX="python -m ziglang c++"`.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.audio import inspect_wav
from app.services.uploads import CLIENT_ID_PATTERN
from tests.conftest import new_web, provision_device
from tests.helpers import DeviceClient

FIRMWARE = Path(__file__).resolve().parents[2] / "firmware"


def find_compiler() -> list[str] | None:
    configured = os.environ.get("ASK_FIRMWARE_CXX")
    if configured:
        # On Windows, POSIX splitting would swallow the backslashes in a path.
        return [part.strip('"') for part in shlex.split(configured, posix=os.name != "nt")]
    for name in ("c++", "g++", "clang++"):
        found = shutil.which(name)
        if found:
            return [found]
    return None


@pytest.fixture(scope="module")
def interop(tmp_path_factory: pytest.TempPathFactory) -> Path:
    compiler = find_compiler()
    if compiler is None:
        pytest.skip("no C++ compiler (set ASK_FIRMWARE_CXX to run the firmware interop tests)")
    binary = tmp_path_factory.mktemp("firmware") / ("interop.exe" if os.name == "nt" else "interop")
    build = subprocess.run(
        [
            *compiler,
            "-std=c++11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            str(FIRMWARE / "tests" / "interop.cpp"),
            str(FIRMWARE / "locket" / "core.cpp"),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert build.returncode == 0, build.stderr[-2000:]
    return binary


def run(interop: Path, *args: object) -> str:
    done = subprocess.run(
        [str(interop), *map(str, args)], capture_output=True, text=True, timeout=60, check=True
    )
    return done.stdout.strip()


class TestTheFirmwaresOwnTests:
    def test_the_c_plus_plus_unit_tests_pass(self, interop: Path, tmp_path: Path) -> None:
        # These pin things the server is lenient about, such as the exact size fields in the WAV header, that
        # other software reading the clip later would depend on. Built with the same strict warnings as the
        # rest.
        compiler = find_compiler()
        assert compiler is not None
        binary = tmp_path / ("unit.exe" if os.name == "nt" else "unit")
        build = subprocess.run(
            [
                *compiler,
                "-std=c++11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-pedantic",
                f"-I{FIRMWARE / 'locket'}",
                str(FIRMWARE / "tests" / "test_core.cpp"),
                str(FIRMWARE / "locket" / "core.cpp"),
                "-o",
                str(binary),
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert build.returncode == 0, build.stderr[-2000:]
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=120, check=False)
        assert result.returncode == 0, result.stdout[-2000:]
        assert result.stdout.strip().endswith("0 failed")


class TestTheServerAcceptsWhatTheLocketWrites:
    def test_a_finished_clip_passes_the_servers_own_validation(
        self, interop: Path, tmp_path: Path, settings
    ) -> None:  # type: ignore[no-untyped-def]
        clip = tmp_path / "clip.wav"
        run(interop, "wav", clip, 3)
        info = inspect_wav(clip, settings)
        assert (info.sample_rate, info.channels, info.bits_per_sample) == (16000, 1, 16)
        assert info.duration_ms == 3000
        assert info.data_bytes == 3 * 16000 * 2

    def test_stereo_is_also_a_valid_header(self, interop: Path, tmp_path: Path, settings) -> None:  # type: ignore[no-untyped-def]
        clip = tmp_path / "stereo.wav"
        run(interop, "wav", clip, 1, 16000, 2)
        assert inspect_wav(clip, settings).channels == 2

    def test_a_rate_the_server_does_not_allow_is_refused_by_the_server_not_hidden(
        self, interop: Path, tmp_path: Path, settings
    ) -> None:  # type: ignore[no-untyped-def]
        clip = tmp_path / "rate.wav"
        run(interop, "wav", clip, 1, 44100)
        with pytest.raises(Exception, match="sample rate"):
            inspect_wav(clip, settings)

    def test_a_clip_recovered_after_a_power_cut_is_accepted(
        self, interop: Path, tmp_path: Path, settings
    ) -> None:  # type: ignore[no-untyped-def]
        clip = tmp_path / "torn.wav"
        run(interop, "torn", clip, 2)
        assert clip.stat().st_size == 44 + 2 * 16000 * 2 + 1  # a stray half sample after the audio
        info = inspect_wav(clip, settings)
        assert info.duration_ms == 2000

    def test_it_uploads_for_real_and_the_server_checks_the_locket_computed_hash(
        self,
        interop: Path,
        tmp_path: Path,
        app,
        acme,  # type: ignore[no-untyped-def]
    ) -> None:
        manager = new_web(app)
        manager.login("manager@acme.example.com")
        _, locket = provision_device(app, manager)
        clip = tmp_path / "upload.wav"
        run(interop, "wav", clip, 2)
        digest = run(interop, "sha256", clip)
        assert digest == hashlib.sha256(clip.read_bytes()).hexdigest()

        client_id = run(interop, "clientid", 1, 3735928559, 1)
        assert CLIENT_ID_PATTERN.match(client_id)
        stamp = run(interop, "iso", 1793006846)
        response = put_clip(locket, client_id, clip, digest, stamp)
        assert response.status_code == 201, response.text
        # A resend after a lost answer is recognised, not stored twice.
        again = put_clip(locket, client_id, clip, digest, stamp)
        assert again.status_code == 200 and again.json()["duplicate"] is True
        # A hash that is not the file's is refused, and the locket's rule says to send it again.
        wrong = put_clip(locket, run(interop, "clientid", 1, 3735928559, 2), clip, "0" * 64, stamp)
        assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "checksum_mismatch"
        assert run(interop, "classify", wrong.status_code, "checksum_mismatch") == "retry"


def put_clip(locket: DeviceClient, client_id: str, clip: Path, digest: str, recorded_at: str):  # type: ignore[no-untyped-def]
    return locket.request(
        "PUT",
        f"/api/device/v1/recordings/{client_id}",
        content=clip.read_bytes(),
        headers={"Content-Type": "audio/wav", "X-Content-SHA256": digest, "X-Recorded-At": recorded_at},
    )


class TestAgainstPython:
    def test_sha256_agrees_with_hashlib_at_the_sizes_where_padding_changes(
        self, interop: Path, tmp_path: Path
    ) -> None:
        for size in (0, 1, 55, 56, 57, 63, 64, 65, 119, 120, 128, 1000, 65536, 100003):
            path = tmp_path / f"{size}.bin"
            path.write_bytes(os.urandom(size))
            assert run(interop, "sha256", path) == hashlib.sha256(path.read_bytes()).hexdigest(), size

    def test_timestamps_match_what_python_writes(self, interop: Path) -> None:
        moments = [
            1704067200,  # 2024-01-01
            1709210096,  # a leap day
            1735689599,  # the last second of 2024
            1740787200,  # 2025-03-01
            1767225600,  # 2026-01-01
            1793006846,
            1798761599,  # the last second of 2026
            2000000000,
            4102444799,  # the last second of 2099
        ]
        for moment in moments:
            expected = datetime.fromtimestamp(moment, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            assert run(interop, "iso", moment) == expected, moment
        assert run(interop, "iso", 0) == "none"

    def test_the_server_reads_the_timestamp_it_is_given(self, interop: Path) -> None:
        from app.services.uploads import parse_recorded_at

        stamp = run(interop, "iso", 1793006846)
        parsed = parse_recorded_at(stamp, datetime(2026, 10, 26, 10, 0, tzinfo=UTC))
        assert parsed == datetime(2026, 10, 26, 9, 27, 26, tzinfo=UTC)

    def test_every_client_id_it_makes_is_one_the_server_accepts(self, interop: Path) -> None:
        for boot, salt, sequence in [
            (0, 0, 0),
            (1, 2, 3),
            (4294967295, 4294967295, 4294967295),
            (77, 123456789, 42),
        ]:
            assert CLIENT_ID_PATTERN.match(run(interop, "clientid", boot, salt, sequence))

    @pytest.mark.parametrize(
        ("status", "code", "expected"),
        [
            (201, "", "done"),
            (200, "", "done"),
            (401, "invalid_device_credential", "repair"),
            (409, "recording_conflict", "newid"),
            (422, "checksum_mismatch", "retry"),
            (422, "invalid_audio", "reject"),
            (422, "silent_audio", "reject"),
            (413, "audio_too_long", "reject"),
            (429, "rate_limited", "retry"),
            (503, "database_unavailable", "retry"),
            (0, "none", "retry"),
        ],
    )
    def test_what_it_does_after_each_answer(
        self, interop: Path, status: int, code: str, expected: str
    ) -> None:
        assert run(interop, "classify", status, code if code != "none" else "") == expected


class TestEveryErrorTheServerCanSendIsHandled:
    def test_audio_codes_the_server_can_return_are_never_retried(self, interop: Path) -> None:
        # Read the codes out of the server's validation code, so a new one added there cannot be forgotten
        # here.
        import re

        source = (Path(__file__).resolve().parents[1] / "app" / "services" / "audio.py").read_text(
            encoding="utf-8"
        )
        codes = set(re.findall(r'_bad\(\s*"([a-z_]+)"', source))
        assert {"invalid_audio", "silent_audio", "audio_too_short"} <= codes
        for code in sorted(codes):
            status = 413 if code == "audio_too_long" else 422
            assert run(interop, "classify", status, code) == "reject", code
