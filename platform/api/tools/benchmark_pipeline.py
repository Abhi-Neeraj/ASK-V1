"""Measure how long a recording takes to become a visible transcript, with the REAL models. Dev tool.

    python tools/benchmark_pipeline.py --model tiny.en --lengths 5,10,20 --repeat 5
    python tools/benchmark_pipeline.py --model small --diarizer clustering --voices

It starts a throwaway API server and a real worker process (their own database and storage in a temporary
folder, local ports only, never your data), pairs a simulated locket, enrolls two synthetic voices if asked,
then uploads real speech clips (made by tools/make_sample_audio.py) over HTTP and times each stage:

    upload      the HTTP request that carries the clip
    queue       from stored to the worker starting on it
    validating, transcribing, diarizing, identifying_speakers, aligning and saving
    visible     from the end of the upload until the transcript can be read through the API

The stage times come from the server's own state events (microsecond timestamps in the database); `visible`
is measured by the client polling the API every 100 ms, so it includes that polling. Models are loaded before
the first clip (a worker warms up at start-up), and one clip is run and thrown away first. It also runs a
burst of clips at once to show queueing, and samples the worker's processor and memory use.

It reports the machine, the model configuration and the number of samples, and the median and 95th
percentile of each figure. With few samples the 95th percentile is just close to the maximum: it says so.
Nothing here is a promise about another machine or about recordings that are not like these.
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import io
import itertools
import json
import os
import platform
import re
import secrets
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from array import array
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

API_DIR = Path(__file__).resolve().parents[1]
PLATFORM_DIR = API_DIR.parent
SAMPLES = PLATFORM_DIR / "data" / "samples" / "real-audio"
ORIGIN = "http://localhost:3000"  # the API only accepts browser-style requests from its configured web origin
RATE = 16_000
DONE_STATES = {"completed", "needs_review", "failed"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def clip_of(seconds: float, source: Path) -> bytes:
    """A WAV of the requested length, cut from (or repeating) real speech."""
    with wave.open(str(source), "rb") as handle:
        base: array[int] = array("h")
        base.frombytes(handle.readframes(handle.getnframes()))
    need = int(seconds * RATE)
    samples: array[int] = array("h")
    while len(samples) < need:
        samples.extend(base)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(samples[:need].tobytes())
    return buffer.getvalue()


class Client:
    """A browser-like client (cookies and CSRF) or a locket (bearer credential) for one API."""

    def __init__(self, base: str, credential: str | None = None) -> None:
        self.base, self.credential = base, credential
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        raw: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        merged = {"Origin": ORIGIN, **(headers or {})}
        if raw is None and body is not None:
            merged["Content-Type"] = "application/json"
        csrf = next((c.value for c in self.jar if c.name == "ask_csrf"), None)
        if csrf and not self.credential:
            merged["x-csrf-token"] = csrf
        if self.credential:
            merged["Authorization"] = f"Bearer {self.credential}"
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=merged)  # noqa: S310
        try:
            with self.opener.open(request, timeout=60) as response:
                payload = response.read()
                return response.status, (json.loads(payload) if payload else None)
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                return error.code, (json.loads(payload) if payload else None)
            except ValueError:
                return error.code, payload.decode("utf-8", "replace")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(fraction * len(ordered) + 0.5) - 1))]


def summarize(values: list[float]) -> dict[str, float | int]:
    return {
        "n": len(values),
        "median": round(statistics.median(values), 3),
        "p95": round(percentile(values, 0.95), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


class Sampler(threading.Thread):
    """Samples a process tree's processor and memory use while a benchmark runs."""

    def __init__(self, pid: int) -> None:
        super().__init__(daemon=True)
        self.process = psutil.Process(pid)
        self.stop = threading.Event()
        self.cpu: list[float] = []
        self.rss_mb: list[float] = []

    def run(self) -> None:
        # On Windows the virtual-environment `python.exe` is a small launcher that starts the real interpreter
        # as a child, so the work happens in a child process. Keep one Process object per pid: a fresh object
        # always reports 0.0 for its first processor reading.
        known: dict[int, psutil.Process] = {}
        while not self.stop.wait(0.25):
            try:
                for child in self.process.children(recursive=True):
                    if child.pid not in known:
                        known[child.pid] = child
                        child.cpu_percent(None)
                alive = [p for p in [self.process, *known.values()] if p.is_running()]
                self.cpu.append(sum(p.cpu_percent(None) for p in alive))
                self.rss_mb.append(sum(p.memory_info().rss for p in alive) / 1_048_576)
            except psutil.Error:
                continue


def stage_times(database: Path, recording_id: str) -> dict[str, float]:
    """Seconds spent in each state, from the server's own recording.state events."""
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT type, payload, created_at FROM live_events "
            "WHERE type IN ('recording.uploaded', 'recording.state') ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    events: list[tuple[str, datetime]] = []
    for kind, payload, created in rows:
        data = json.loads(payload) if isinstance(payload, str) else payload
        if data.get("recordingId") == recording_id:
            events.append(
                (
                    "uploaded" if kind == "recording.uploaded" else data["state"],
                    datetime.fromisoformat(str(created)),
                )
            )
    out: dict[str, float] = {}
    for (name, started), (_next, ended) in itertools.pairwise(events):
        out[name] = out.get(name, 0.0) + (ended - started).total_seconds()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model", default="tiny.en", help="faster-whisper model (tiny.en, base.en, small, ...)"
    )
    parser.add_argument("--diarizer", default="clustering", choices=["none", "clustering", "pyannote"])
    parser.add_argument(
        "--voices", action="store_true", help="enroll two synthetic voices so speaker matching runs"
    )
    parser.add_argument("--lengths", default="5,10,20", help="clip lengths in seconds, comma separated")
    parser.add_argument("--repeat", type=int, default=5, help="clips per length")
    parser.add_argument(
        "--burst", type=int, default=5, help="clips uploaded at once to show queueing (0 to skip)"
    )
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument(
        "--cpu-threads", type=int, default=0, help="limit the worker's CPU threads (0: every core)"
    )
    parser.add_argument(
        "--resilience",
        action="store_true",
        help="also stop the API for a while with clips queued, then kill the worker mid-recording and start "
        "another "
        "(takes as long as the job lock timeout, 3 minutes by default)",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    if not (SAMPLES / "conversation.wav").is_file():
        raise SystemExit("No sample audio: run  python tools/make_sample_audio.py  first.")

    work = Path(tempfile.mkdtemp(prefix="ask-bench-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    password = secrets.token_urlsafe(18)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ASK_")}
    env.update(
        {
            "ASK_ENV": "development",
            "ASK_SECRET_KEY": secrets.token_urlsafe(48),
            "ASK_DATABASE_URL": f"sqlite:///{(work / 'bench.sqlite3').as_posix()}",
            "ASK_STORAGE_DIR": str(work / "recordings"),
            "ASK_WEB_ORIGINS": ORIGIN,
            "ASK_WEB_BASE_URL": ORIGIN,
            "ASK_EMAIL_BACKEND": "console",
            "ASK_TRUST_FORWARDED_FOR": "false",
            "ASK_RUN_BACKGROUND_TASKS": "false",
            "ASK_AI_PROVIDER": "local",
            "ASK_AI_MODEL_DIR": str(PLATFORM_DIR / "data" / "models"),
            "ASK_AI_ASR_MODEL": args.model,
            "ASK_AI_ASR_COMPUTE_TYPE": args.compute_type,
            "ASK_AI_ASR_BEAM_SIZE": str(args.beam_size),
            "ASK_AI_CPU_THREADS": str(args.cpu_threads),
            "ASK_WORKER_POLL_SECONDS": "0.2",
            "PYTHONWARNINGS": "ignore",
        }
    )
    if args.diarizer in ("clustering", "pyannote"):
        env["ASK_AI_DIARIZER"] = args.diarizer
    else:
        env["ASK_AI_DIARIZER"] = "pyannote"  # without a token it is simply unavailable: no separation at all
        env.pop("ASK_HF_TOKEN", None)

    def run(*command: str, stdin: str | None = None) -> None:
        done = subprocess.run(
            [sys.executable, *command],
            cwd=API_DIR,
            env=env,
            input=stdin,
            text=True,
            capture_output=True,
            check=False,
        )
        if done.returncode != 0:
            raise SystemExit(f"{' '.join(command)} failed:\n{done.stdout[-600:]}\n{done.stderr[-600:]}")

    print(f"working in {work} (temporary), API on {base}")
    run("-m", "alembic", "upgrade", "head")
    run(
        "-m",
        "app.cli",
        "bootstrap",
        "--company",
        "Benchmark",
        "--name",
        "Bench Admin",
        "--email",
        "bench@example.com",
        "--password-stdin",
        stdin=password + "\n",
    )

    def start_api() -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:create_app",
                "--factory",
                "--port",
                str(port),
                "--host",
                "127.0.0.1",
                "--no-access-log",
            ],
            cwd=API_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def start_worker() -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [sys.executable, "-m", "app.worker"],
            cwd=API_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def wait_ready(seconds: float = 60) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if urllib.request.urlopen(base + "/api/ready", timeout=2).status == 200:  # noqa: S310
                    return
            except OSError:
                time.sleep(0.3)
        raise SystemExit("The API did not start.")

    def kill_tree(process: subprocess.Popen[bytes]) -> None:
        """An abrupt death, as a crash or a power cut would be: no chance to hand anything back."""
        try:
            parent = psutil.Process(process.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.Error:
            pass

    procs = {"api": start_api(), "worker": start_worker()}
    sampler: Sampler | None = None
    try:
        wait_ready()

        admin = Client(base)
        status, _ = admin.call(
            "POST", "/api/auth/login", {"email": "bench@example.com", "password": password}
        )
        assert status == 200, status
        code = admin.call("POST", "/api/devices", {"name": "Benchmark locket"})[1]
        paired = Client(base).call(
            "POST",
            "/api/device/v1/pair",
            {"code": code["code"], "firmwareVersion": "bench", "simulated": True},
        )[1]
        locket = Client(base, paired["credential"])

        print("waiting for the worker to load its models ...")
        started = time.monotonic()
        while time.monotonic() - started < 600:
            info = admin.call("GET", "/api/processing")[1]
            if info and info.get("available"):
                break
            time.sleep(0.5)
        else:
            raise SystemExit("The worker never came online (see platform/docs/AI.md).")
        found = {k: info.get(k) for k in ("placeholder", "transcription", "diarization", "speakerMatching")}
        print(f"worker online after {time.monotonic() - started:.0f}s: {json.dumps(found)}")
        if info.get("placeholder"):
            raise SystemExit("The worker is in placeholder mode: that would measure nothing real.")

        if args.voices:
            for name, folder in (("Hazel", "hazel"), ("Zira", "zira")):
                person = admin.call("POST", "/api/people", {"displayName": f"{name} (synthetic)"})[1]
                admin.call("POST", f"/api/people/{person['id']}/consent", {"granted": True})
                for n in (1, 2, 3):
                    sample = (SAMPLES / "voices" / folder / f"enroll-{n}.wav").read_bytes()
                    result = admin.call(
                        "POST",
                        f"/api/people/{person['id']}/enroll",
                        raw=sample,
                        headers={"Content-Type": "audio/wav"},
                    )
                    assert result[0] == 200, result

        def upload(data: bytes) -> tuple[str, float, float]:
            t0 = time.perf_counter()
            headers = {
                "Content-Type": "audio/wav",
                "X-Content-SHA256": hashlib.sha256(data).hexdigest(),
                "X-Recorded-At": datetime.now(UTC).isoformat(),
            }
            status, body = locket.call(
                "PUT", f"/api/device/v1/recordings/bench-{uuid.uuid4().hex[:16]}", raw=data, headers=headers
            )
            t1 = time.perf_counter()
            if status != 201:
                raise SystemExit(f"upload refused: {status} {body}")
            return body["recordingId"], t0, t1

        def wait_visible(recording_id: str, timeout: float = 600) -> tuple[float, dict[str, Any]]:
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                detail = admin.call("GET", f"/api/recordings/{recording_id}")[1]
                if detail["state"] in DONE_STATES:
                    return time.perf_counter(), detail
                time.sleep(0.1)
            raise SystemExit("A recording was not processed in time.")

        source = SAMPLES / "conversation.wav"
        print("warm-up clip (discarded) ...")
        warm, _, _ = upload(clip_of(10, source))
        wait_visible(warm)

        sampler = Sampler(procs["worker"].pid)
        sampler.start()
        results: dict[str, Any] = {}
        for length in [float(x) for x in args.lengths.split(",") if x]:
            rows: list[dict[str, float]] = []
            for _ in range(args.repeat):
                data = clip_of(length, source)
                recording_id, t0, t1 = upload(data)
                visible, detail = wait_visible(recording_id)
                assert detail["state"] != "failed", detail
                stages = stage_times(work / "bench.sqlite3", recording_id)
                rows.append({"upload": t1 - t0, "visible": visible - t1, **stages})
            keys = sorted({k for row in rows for k in row})
            results[f"{length:g}s"] = {k: summarize([row[k] for row in rows if k in row]) for k in keys}
            seen = results[f"{length:g}s"]["visible"]
            print(
                f"  {length:g} s clips x{len(rows)}: visible median "
                f"{seen['median']:.1f}s, p95 {seen['p95']:.1f}s"
            )

        burst: dict[str, Any] = {}
        if args.burst:
            data = clip_of(10, source)
            sent = [(upload(data), time.perf_counter()) for _ in range(args.burst)]
            waits = []
            for (recording_id, _t0, t1), _ in sent:
                visible, _detail = wait_visible(recording_id)
                waits.append(visible - t1)
            burst = {
                "clips": args.burst,
                "clipSeconds": 10,
                "visibleAfterUpload": [round(w, 2) for w in sorted(waits)],
            }
            first, last = min(waits), max(waits)
            print(
                f"  burst of {args.burst} x 10 s clips: first visible after {first:.1f}s, "
                f"last after {last:.1f}s"
            )

        sampler.stop.set()
        sampler.join(timeout=2)

        resilience: dict[str, Any] = {}
        if args.resilience:
            database = work / "bench.sqlite3"

            def jobs_done() -> int:
                connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
                try:
                    return int(
                        connection.execute(
                            "SELECT count(*) FROM processing_jobs WHERE status = 'done'"
                        ).fetchone()[0]
                    )
                finally:
                    connection.close()

            # 1. The API is down while clips are waiting. The worker does not need it, only the database.
            before = jobs_done()
            ids = [upload(clip_of(10, source))[0] for _ in range(3)]
            kill_tree(procs["api"])
            down_at = time.perf_counter()
            time.sleep(25)
            finished_while_down = jobs_done() - before
            try:
                locket.call("PUT", "/api/device/v1/recordings/bench-while-down", raw=b"x")
                refused = False
            except OSError:
                refused = (
                    True  # nobody can upload while the API is down: the locket would keep the clip and retry
                )
            procs["api"] = start_api()
            wait_ready()
            back_at = time.perf_counter()
            for rid in ids:
                wait_visible(rid)
            resilience["apiOutage"] = {
                "clipsWaiting": 3,
                "secondsApiWasDown": round(back_at - down_at, 1),
                "finishedWhileApiWasDown": finished_while_down,
                "uploadRefusedWhileDown": refused,
                "secondsAfterApiReturnedUntilAllVisible": round(time.perf_counter() - back_at, 1),
            }
            after = resilience["apiOutage"]["secondsAfterApiReturnedUntilAllVisible"]
            print(
                f"  API outage: {finished_while_down} of 3 queued clips finished while it was down; "
                f"all visible {after}s after it returned"
            )

            # 2. The worker dies in the middle of a recording; a new one is started straight away.
            rid, _t0, _t1 = upload(clip_of(20, source))
            end = time.monotonic() + 120
            while time.monotonic() < end and admin.call("GET", f"/api/recordings/{rid}")[1]["state"] not in (
                "diarizing",
                "identifying_speakers",
            ):
                time.sleep(0.05)
            kill_tree(procs["worker"])
            killed_at = time.perf_counter()
            procs["worker"] = start_worker()
            visible, detail = wait_visible(rid, timeout=1200)
            resilience["workerKilledMidRecording"] = {
                "stateWhenKilled": "separating speakers",
                "jobLockTimeoutSeconds": float(env.get("ASK_JOB_LOCK_TIMEOUT_SECONDS", "180")),
                "secondsFromKillUntilTranscriptVisible": round(visible - killed_at, 1),
                "finalState": detail["state"],
                "transcriptSegments": len(detail["transcript"]["segments"])
                if detail.get("transcript")
                else 0,
            }
            later = resilience["workerKilledMidRecording"]["secondsFromKillUntilTranscriptVisible"]
            print(f"  worker killed mid-recording: transcript visible {later}s later ({detail['state']})")

        machine = {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logicalCpus": psutil.cpu_count(logical=True),
            "physicalCpus": psutil.cpu_count(logical=False),
            "ramGb": round(psutil.virtual_memory().total / 1e9, 1),
            "gpu": False,
        }
        report = {
            "when": datetime.now(UTC).isoformat(timespec="seconds"),
            "machine": machine,
            "configuration": {
                "whisperModel": args.model,
                "computeType": args.compute_type,
                "beamSize": args.beam_size,
                "cpuThreads": args.cpu_threads or "all",
                "separation": args.diarizer,
                "speakerMatching": bool(args.voices),
                "audio": "synthetic speech (Windows text-to-speech), 16 kHz mono 16-bit",
                "samplesPerLength": args.repeat,
                "warmedUp": True,
                "database": "SQLite",
                "api": "uvicorn, one process",
            },
            "latencySeconds": results,
            "burst": burst,
            "resilience": resilience,
            "worker": {
                "peakRssMb": round(max(sampler.rss_mb), 0) if sampler.rss_mb else None,
                "meanCpuPercentOfOneCore": round(statistics.mean(sampler.cpu), 0) if sampler.cpu else None,
                "peakCpuPercentOfOneCore": round(max(sampler.cpu), 0) if sampler.cpu else None,
            },
            "note": "p95 from fewer than 20 samples is approximately the maximum.",
        }
        out = (
            args.out
            or PLATFORM_DIR
            / "data"
            / "benchmarks"
            / f"{datetime.now():%Y%m%d-%H%M%S}-{re.sub('[^a-z0-9.]+', '-', args.model)}.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwritten to {out}")
        print(json.dumps(report["worker"]))
        return 0
    finally:
        if sampler is not None:
            sampler.stop.set()
        for process in procs.values():
            kill_tree(process)  # a throwaway server and worker: nothing in them needs a graceful stop


if __name__ == "__main__":
    raise SystemExit(main())
