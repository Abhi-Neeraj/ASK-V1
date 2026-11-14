"""DEVELOPMENT SIMULATOR. This is not the locket firmware, and it never pretends to be.

It stands in for a locket so the dashboard, pairing, live updates and upload paths can be exercised
on a machine with no hardware. It pairs with a real pairing code (declaring itself `simulated`, so
the dashboard labels it and a production server refuses it), sends real heartbeats, and uploads a
synthetic tone, not speech, through the same endpoints the firmware uses. Everything it uploads is
genuine data in the database it points at, so use it only against a development API.

    python tools/simulate_device.py pair --code ABCDE-FGHIJ
    python tools/simulate_device.py heartbeat --battery 87 --queue 0
    python tools/simulate_device.py upload --seconds 4
    python tools/simulate_device.py upload --file ../data/samples/real-audio/conversation.wav   (real speech)
    python tools/simulate_device.py run                       (stay online for 2 minutes)
    python tools/simulate_device.py run --duration 0          (stay online until Ctrl+C)
    python tools/simulate_device.py run --upload-every 3      (upload a clip every third heartbeat)

To watch the dashboard react, leave `run` going, then press Ctrl+C: the locket shows as offline once
the server stops hearing heartbeats.

The credential is stored in a state file (default platform/data/simulated-device.json, gitignored)
and is never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_STATE = Path(__file__).resolve().parents[2] / "data" / "simulated-device.json"
DEFAULT_API = "http://localhost:8000"
FIRMWARE = "simulator-0.2"
SAMPLE_RATE = 16_000
BANNER = "[SIMULATOR: not a real locket]"


def synthetic_wav(seconds: float) -> bytes:
    """A quiet two-tone sweep, 16 kHz mono 16-bit PCM. Obviously not speech."""
    frames = bytearray()
    total = int(SAMPLE_RATE * seconds)
    for index in range(total):
        t = index / SAMPLE_RATE
        envelope = min(1.0, t * 8, (seconds - t) * 8)
        value = 0.25 * envelope * (math.sin(2 * math.pi * 330 * t) + 0.5 * math.sin(2 * math.pi * 495 * t))
        frames += struct.pack("<h", int(value * 32767 / 1.5))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


class RejectedError(Exception):
    """The server answered, and the answer was no."""

    def __init__(self, status: int, code: str, message: str, retry_after: float | None = None) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.retry_after = retry_after


class Api:
    def __init__(self, base_url: str, credential: str | None = None) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise SystemExit("--api must start with http:// or https://")
        self.base_url = base_url.rstrip("/")
        self.credential = credential

    def call(
        self, method: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None
    ) -> Any:
        merged = dict(headers or {})
        if self.credential:
            merged["Authorization"] = f"Bearer {self.credential}"
        request = urllib.request.Request(self.base_url + path, data=body, method=method, headers=merged)  # noqa: S310
        retry_after: float | None = None
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                raw, status = response.read(), response.status
        except urllib.error.HTTPError as error:
            raw, status = error.read(), error.code
            header = error.headers.get("Retry-After")
            retry_after = float(header) if header and header.isdigit() else None
        except urllib.error.URLError as error:
            raise SystemExit(f"Cannot reach the API at {self.base_url}: {error.reason}") from error

        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = raw.decode("utf-8", "replace")
        if status >= 400:
            error_body = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error_body, dict):
                raise RejectedError(
                    status, str(error_body.get("code")), str(error_body.get("message")), retry_after
                )
            raise RejectedError(status, "http_error", str(payload), retry_after)
        return payload

    def json_call(self, method: str, path: str, payload: dict[str, Any]) -> Any:
        return self.call(
            method, path, body=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"No paired simulator found at {path}. Run the `pair` command first.")
    state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return state


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def explain(error: RejectedError) -> str:
    """The server's reason, plus what to do about the ones people actually hit."""
    hints = {
        "simulation_not_allowed": (
            "This server only accepts real lockets. Use a development server, "
            "or set ASK_ALLOW_SIMULATED_DEVICES=true."
        ),
        "invalid_pairing_code": "Create a new code on the Devices page (codes work once and expire).",
        "invalid_device_credential": (
            "The server no longer accepts this locket. If it was revoked, "
            "add it as a new locket and pair again."
        ),
    }
    hint = hints.get(error.code)
    return f"{error}" + (f"\n  -> {hint}" if hint else "")


def pair(args: argparse.Namespace) -> int:
    api = Api(args.api or DEFAULT_API)
    try:
        payload = api.json_call(
            "POST",
            "/api/device/v1/pair",
            {"code": args.code, "firmwareVersion": FIRMWARE, "simulated": True},
        )
    except RejectedError as error:
        print(f"Pairing failed: {explain(error)}", file=sys.stderr)
        return 1
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(
        json.dumps(
            {
                "api": api.base_url,
                "deviceId": payload["deviceId"],
                "credential": payload["credential"],
                "heartbeatIntervalSeconds": payload["config"]["heartbeatIntervalSeconds"],
            }
        ),
        encoding="utf-8",
    )
    print(
        f"Paired as {payload['deviceId']} (marked simulated). Credential saved to {args.state} (not shown)."
    )
    return 0


def heartbeat_once(api: Api, battery: int, queue: int) -> None:
    api.json_call(
        "POST",
        "/api/device/v1/heartbeat",
        {"firmwareVersion": FIRMWARE, "batteryPercent": battery, "uploadQueueDepth": queue},
    )


def upload_once(api: Api, seconds: float, file: Path | None = None) -> dict[str, Any]:
    """Send a clip: a quiet synthetic tone, or (with `file`) the bytes of a WAV you supply, for example speech
    from tools/make_sample_audio.py. The recording is still flagged as made by a simulator."""
    body = file.read_bytes() if file is not None else synthetic_wav(seconds)
    client_id = f"sim-{uuid.uuid4().hex[:20]}"
    stored: dict[str, Any] = api.call(
        "PUT",
        f"/api/device/v1/recordings/{client_id}",
        body=body,
        headers={
            "Content-Type": "audio/wav",
            "X-Content-SHA256": hashlib.sha256(body).hexdigest(),
            "X-Recorded-At": datetime.now(UTC).isoformat(),
        },
    )
    return stored


def run(api: Api, args: argparse.Namespace, default_interval: float) -> int:
    """Stay online: heartbeat on the server's schedule, upload now and then, slowly 'use up' the battery."""
    interval = args.interval or default_interval
    deadline = None if args.duration == 0 else time.monotonic() + args.duration
    battery, beat = args.battery, 0
    print(f"{BANNER} online as {args.state.name}; heartbeat every {interval:g}s. Ctrl+C to stop.")
    try:
        while deadline is None or time.monotonic() < deadline:
            beat += 1
            try:
                heartbeat_once(api, battery, 0)
                print(f"{stamp()} heartbeat {beat}  battery {battery}%")
                if args.upload_every and beat % args.upload_every == 0:
                    stored = upload_once(api, args.seconds, args.file)
                    print(
                        f"{stamp()} uploaded {stored['durationMs']} ms as recording {stored['recordingId']}"
                    )
            except RejectedError as error:
                if error.status == 429:
                    wait = error.retry_after or interval
                    print(f"{stamp()} slow down: waiting {wait:g}s")
                    time.sleep(wait)
                    continue
                print(f"{stamp()} rejected: {explain(error)}", file=sys.stderr)
                if error.status in {401, 403}:
                    return 2
            if beat % 5 == 0 and battery > 5:
                battery -= 1
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n{stamp()} stopped. The dashboard shows the locket as offline once heartbeats stop.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--api",
        default=None,
        help=f"API origin (never the web app). Default: the paired one, else {DEFAULT_API}",
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = parser.add_subparsers(dest="command", required=True)

    pairing = commands.add_parser("pair", help="redeem a pairing code from the dashboard")
    pairing.add_argument("--code", required=True)

    beat = commands.add_parser("heartbeat", help="send one heartbeat")
    beat.add_argument("--battery", type=int, default=90)
    beat.add_argument("--queue", type=int, default=0)

    upload = commands.add_parser(
        "upload", help="upload one clip (a synthetic tone, or a WAV you give with --file)"
    )
    upload.add_argument("--seconds", type=float, default=3.0)
    upload.add_argument("--file", type=Path, help="send this WAV (16 kHz mono 16-bit) instead of a tone")

    loop = commands.add_parser("run", help="stay online: heartbeats, with an occasional clip")
    loop.add_argument(
        "--duration", type=float, default=120.0, help="seconds to stay online; 0 means until Ctrl+C"
    )
    loop.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="seconds between heartbeats (default: what the server asks for)",
    )
    loop.add_argument(
        "--upload-every", type=int, default=0, help="upload a clip every N heartbeats (default: never)"
    )
    loop.add_argument("--seconds", type=float, default=3.0, help="length of each uploaded clip")
    loop.add_argument("--file", type=Path, help="send this WAV in each upload instead of a tone")
    loop.add_argument("--battery", type=int, default=90)

    args = parser.parse_args(argv)
    print(BANNER)
    if args.command == "pair":
        return pair(args)

    state = load_state(args.state)
    api = Api(args.api or state["api"], state["credential"])
    try:
        if args.command == "heartbeat":
            heartbeat_once(api, args.battery, args.queue)
            print("Heartbeat accepted.")
        elif args.command == "upload":
            stored = upload_once(api, args.seconds, args.file)
            print(f"Stored {stored['bytes']} bytes as recording {stored['recordingId']}.")
        else:
            return run(api, args, float(state.get("heartbeatIntervalSeconds", 10)))
    except RejectedError as error:
        print(f"Rejected: {explain(error)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
