# ASK voice locket platform

Company web app, backend, local AI worker and locket firmware. Separate from the Expo app at the repo root. Plan, decisions and milestones: [docs/PLAN.md](docs/PLAN.md). Endpoint reference: [docs/API.md](docs/API.md).

## Status

| Part | State |
| --- | --- |
| `web/` marketing site, auth pages, dashboard UI | Built. Lint, types, production build and 344 tests pass. Runs against the real API, or against labelled demo data with `npm run dev:demo` (see [docs/DEMO.md](docs/DEMO.md)). Includes team invitations and controls (role, switch off, sign out everywhere), locket assignment and revoking, consent management, voice enrollment, clear labelling of placeholder transcripts, and the audit log page for administrators. |
| `api/` backend: database, migrations, accounts, tenancy, roles | Built. 675 tests pass in the default suite (including 22 firmware interop tests and 90 for the button recorder when a C++ compiler is configured), plus 45 real-model tests (`tests/real`, opt-in) and 9 PostgreSQL tests (`tests/postgres`, opt-in, **passed** on PostgreSQL 16.15); `ruff` and strict `mypy` pass. The default suite runs on SQLite and was also run in full against PostgreSQL 16.15 (`ASK_TEST_DATABASE_URL`). An independent security review was done and its confirmed findings fixed: [docs/SECURITY.md](docs/SECURITY.md). |
| Locket pairing, heartbeats and online status | Built and run end to end in a browser with a simulated locket. No real locket has been tested. |
| Upload pipeline and recording library | Built: durable, idempotent WAV upload, search, playback with range requests, transcript corrections, retention. |
| Live updates (SSE) and the connection popup | Built and run end to end in a browser with the simulator: status, lists and prompts update with no reload; a polling fallback covers a dropped connection. |
| Locket simulator (`api/tools/simulate_device.py`) | Built. Development only: it declares itself simulated, the dashboard labels it, and production refuses it. |
| Processing worker (transcription, speaker separation, speaker matching, voice enrollment) | Built and **run with real models on real audio, on a CPU**: faster-whisper transcription and SpeechBrain voice matching (synthetic voices only), the experimental token-free speaker separator, a durable job queue with retries, leases and crash recovery (a real worker process was killed mid-recording and replaced), and a clearly labelled placeholder stub. **Not run:** pyannote speaker separation (its model is gated; no token), a GPU, any real person's voice, and the speaker-matching threshold is uncalibrated. With no worker, uploads stay `queued` and no transcript is ever invented. See [docs/AI.md](docs/AI.md). |
| `firmware/` locket firmware | Written and **compiled with the real toolchain** (Arduino CLI, arduino-esp32 3.3.12, `esp32:esp32:XIAO_ESP32S3`: no warnings, 32% of program storage), but **never flashed and never run on a device**. Its decision logic is compiled and tested on a computer, including against the real server, and every library call it makes was compared with the real headers (which found and fixed one build error before the first real build). Acceptance checklist for a real board: [docs/HARDWARE_ACCEPTANCE_TESTS.md](docs/HARDWARE_ACCEPTANCE_TESTS.md). See [firmware/README.md](firmware/README.md). |
| `firmware/locket_web_upload` button-controlled recorder (press to start, press to stop; parts uploaded to the server) | Written and **compiled with the real toolchain** (`PSRAM: OPI`: no warnings, 34% of program storage). Its real sources were run against **simulated** hardware (76 scenarios with faults injected, including the whole two-press button cycle and joining the built-in Wi-Fi network by itself at power-on) and its upload code against the **real API** over a local socket (5 tests). **Never flashed, never run on a board** (so whether it really joins a real hotspot is untested). Derived from the original recorder, with its speech-to-text, summary and chat-message code removed. See [firmware/locket_web_upload/README.md](firmware/locket_web_upload/README.md) and [docs/WEB_UPLOAD_FIRMWARE.md](docs/WEB_UPLOAD_FIRMWARE.md). |
| `deploy/` Docker Compose, proxy, backups; [docs/RUNBOOK.md](docs/RUNBOOK.md) | Written, **never run** (no Docker here): no image was built, no container or proxy started, no backup script run. Its configuration is checked against the application's own production rules. The database side was run for real on PostgreSQL 16.15 (migrations, the multi-worker queue, `pg_dump` and `pg_restore`), but not through Docker. |

This machine has no Docker, FFmpeg, GPU or ESP32 hardware, so nothing that depends on them has been run. The backend has been exercised on SQLite and on a real PostgreSQL 16.15; the speech and voice models have run on real audio (CPU only, synthetic voices), and the gated speaker-separation model (pyannote) has not.

## Run it

Backend setup (Windows, Python 3.11) is in [api/README.md](api/README.md). In short:

```powershell
cd platform\api
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m app.cli generate-env
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m app.cli bootstrap --company "Your Company" --name "Your Name" --email you@yourcompany.com
.\.venv\Scripts\python -m uvicorn app.main:create_app --factory --reload --port 8000
```

Then, from the repo root, in a second terminal:

```bash
npm install --prefix platform/web
npm run dev --prefix platform/web
```

Open http://localhost:3000 and sign in. To look at the interface without a backend, use `npm run dev:demo --prefix platform/web`; every page then carries a visible demo-mode label. See [web/README.md](web/README.md) for all web commands.
