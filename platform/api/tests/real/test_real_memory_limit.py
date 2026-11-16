"""The real worker under a hard memory cap: what happens when the machine cannot hold the models.

Windows only (it uses a job object to cap one process's memory). It checks the outcomes that matter, not an
exact threshold: the worker must never start up "successfully" without being able to transcribe, must never
hang, and when it can still report a reason it must say that memory is the problem. Measured here with the
tiny model: at 250 MB it dies while importing the math library, at 700 MB it exits with code 2 and "not
enough memory", and at 1200 MB it cannot even load the database driver ("the paging file is too small").
"""

from __future__ import annotations

import ctypes
import os
import secrets
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

import pytest

from tests.real.conftest import MODELS

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="uses a Windows job object")

API_DIR = Path(__file__).resolve().parents[2]


class _Basic(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _Io(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("ro", "wo", "oo", "rb", "wb", "ob")]


class _Extended(ctypes.Structure):
    _fields_ = [
        ("Basic", _Basic),
        ("Io", _Io),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def run_worker_with_memory_cap(limit_mb: int, work: Path) -> tuple[int, str]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]

    env = {k: v for k, v in os.environ.items() if not k.startswith("ASK_")}
    env.update(
        {
            "ASK_ENV": "test",
            "ASK_SECRET_KEY": secrets.token_urlsafe(48),
            "ASK_DATABASE_URL": f"sqlite:///{(work / 'x.sqlite3').as_posix()}",
            "ASK_STORAGE_DIR": str(work / "rec"),
            "ASK_AI_PROVIDER": "local",
            "ASK_AI_MODEL_DIR": str(MODELS),
            "ASK_AI_ASR_MODEL": "tiny.en",
            "ASK_AI_DIARIZER": "clustering",
            "ASK_EMAIL_BACKEND": "memory",
            "ASK_COOKIE_SECURE": "false",
            "PYTHONWARNINGS": "ignore",
        }
    )
    job = kernel32.CreateJobObjectW(None, None)
    info = _Extended()
    info.Basic.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    info.ProcessMemoryLimit = limit_mb * 1024 * 1024
    assert kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
    log = work / "worker.log"
    with log.open("w") as out:
        # Start it suspended, put it in the job, then let it run, so that nothing it starts escapes the cap.
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.worker", "--once"],
            cwd=API_DIR,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            creationflags=0x4,  # CREATE_SUSPENDED
        )
        handle = wintypes.HANDLE(int(proc._handle))  # type: ignore[attr-defined]
        assert kernel32.AssignProcessToJobObject(job, handle)
        ctypes.WinDLL("ntdll").NtResumeProcess(handle)
        try:
            code = proc.wait(timeout=240)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("the worker hung instead of failing when it ran out of memory")
    return code, log.read_text(errors="replace")


@pytest.mark.parametrize("limit_mb", [250, 700])
def test_a_worker_that_cannot_hold_its_models_fails_loudly_and_never_pretends_to_work(
    limit_mb: int, tmp_path: Path
) -> None:
    code, log = run_worker_with_memory_cap(limit_mb, tmp_path)
    print(f"\n  cap {limit_mb} MB: exit code {code}")
    assert code != 0, "a worker that cannot load a speech model must not report success"
    if code == 2:  # it got far enough to explain itself
        assert "cannot start" in log and "not enough memory" in log.lower()
