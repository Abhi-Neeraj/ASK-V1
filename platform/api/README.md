# ASK platform API

FastAPI backend for the voice locket platform: company accounts, locket pairing and heartbeats, durable audio upload, and the recording library. Endpoint reference: [`../docs/API.md`](../docs/API.md). Plan and milestones: [`../docs/PLAN.md`](../docs/PLAN.md).

**Status:** accounts, devices, upload, the library, live updates, and the processing worker (transcription, speaker separation, speaker matching, voice enrollment) are built and tested on SQLite. The worker's **real-model path is unverified**: no model weights, torch or Hugging Face token were available, so only a clearly labelled placeholder stub and test doubles have run. Read [`../docs/AI.md`](../docs/AI.md) before relying on it. Security review, fixes and what is left for you to decide: [`../docs/SECURITY.md`](../docs/SECURITY.md). Running it for real: [`../docs/RUNBOOK.md`](../docs/RUNBOOK.md). With no worker running, uploads stay `queued` and no transcript is ever invented.

## Set up on Windows

Python **3.11** is required (`requires-python` rejects anything else). From the repo root, in PowerShell:

```powershell
cd platform\api
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m app.cli generate-env     # writes .env with a fresh random secret (not shown)
.\.venv\Scripts\python -m alembic upgrade head     # creates the SQLite database in platform\data\
.\.venv\Scripts\python -m app.cli check            # config, database, migrations, storage
```

The editable install only installs the pinned dependencies; the application itself runs from this folder. Add `[postgres]` to the extras (`".[dev,postgres]"`) when you point it at PostgreSQL.

Create the first company and its administrator. The password is prompted for and never echoed:

```powershell
.\.venv\Scripts\python -m app.cli bootstrap --company "Your Company" --name "Your Name" --email you@yourcompany.com
```

For scripted setup, `--password-stdin` reads one line from standard input (a leading byte-order mark, which PowerShell 5.1 adds to piped text, is ignored). Everyone else joins by invitation; there is no public company sign-up.

## Run

```powershell
.\.venv\Scripts\python -m uvicorn app.main:create_app --factory --reload --port 8000 --timeout-graceful-shutdown 3
```

`--factory` is required: nothing is built at import time. `--timeout-graceful-shutdown` stops open live-update streams from holding up a restart: without it, Ctrl+C waits for every browser tab to let go. Then start the web app from the repo root with `npm run dev --prefix platform/web` and open <http://localhost:3000>. The web app proxies `/api/*` to `http://localhost:8000` (override with `API_ORIGIN`). Without the API, use `npm run dev:demo --prefix platform/web` for the visibly labelled demo data.

Interactive docs are at <http://localhost:8000/api/docs> and the schema at `/api/openapi.json` (both disabled when `ASK_ENV=production`).

## Process recordings

The worker is its own process. Run it in a second terminal, from this folder:

```powershell
.\.venv\Scripts\python -m app.worker           # keeps running; Ctrl+C finishes the stage in progress and stops
.\.venv\Scripts\python -m app.worker --once    # processes what is due, then exits
```

It needs `ASK_AI_PROVIDER` set in `.env`; the default `none` makes it refuse to start (exit code 2), and recordings wait in `queued` while the dashboard says no worker is running.

- `ASK_AI_PROVIDER=stub` is for development. It writes **placeholder** text (not what was said), names nobody, and every screen and export is labelled. Production refuses it.
- `ASK_AI_PROVIDER=local` runs real models and needs the `[ai]` extra (`pip install -e ".[dev,ai]"`, about 1.4 GB), downloaded weights and, for the full speaker separation, a Hugging Face token. The worker loads its models when it starts and exits with the reason if speech recognition cannot load. Setup, licences, calibration and exactly what was and was not verified: [`../docs/AI.md`](../docs/AI.md).

Try the real models on one file, without a database, and see how long each stage takes:

```powershell
.\.venv\Scripts\python tools\make_sample_audio.py        # once: synthetic speech (Windows voices) in ..\data\samples\real-audio
$env:ASK_AI_PROVIDER = "local"; $env:ASK_AI_ASR_MODEL = "tiny.en"; $env:ASK_AI_DIARIZER = "clustering"
.\.venv\Scripts\python -m app.cli process-file ..\data\samples\real-audio\conversation.wav --voices ..\data\samples\real-audio\voices
```

**Voice signatures, pitch and the summary** (no model needed): people's voices can be measured from WAV uploads (`POST /api/people/{id}/signature`), checked against (`POST /api/people/signature-matches`), used to name speakers in the worker, and every transcript gets a rule-built summary with each speaker's base, low and high pitch. `ASK_AI_DIARIZER=pitch` also separates speakers by pitch with no model or token (experimental). What it does, its settings, and what was and was not verified: [`../docs/VOICE_SIGNATURE.md`](../docs/VOICE_SIGNATURE.md).

Run the real-model tests (`tests/real`) and the latency benchmark (`tools/benchmark_pipeline.py`) as described in [`../docs/AI.md`](../docs/AI.md#the-real-model-tests).

## Try it without a locket

`tools/simulate_device.py` is a **development simulator, not the firmware**. It pairs with a real code from the Devices page, sends real heartbeats and uploads a synthetic tone, so you can watch a locket go online, get offered a connection, and a recording arrive, all live:

```powershell
# 1. Dashboard -> Devices -> Pair a locket. Copy the code it shows (it works once).
.\.venv\Scripts\python tools\simulate_device.py pair --code ABCDE-FGHIJ
# 2. Keep it online and watch the dashboard. Ctrl+C stops it; the locket then goes offline by itself.
.\.venv\Scripts\python tools\simulate_device.py run --duration 0 --upload-every 4
# One-off commands:
.\.venv\Scripts\python tools\simulate_device.py heartbeat --battery 87
.\.venv\Scripts\python tools\simulate_device.py upload --seconds 4
# Real speech instead of a tone (see tools\make_sample_audio.py), so the real models have something to hear:
.\.venv\Scripts\python tools\simulate_device.py upload --file ..\data\samples\real-audio\conversation.wav
```

It declares itself `simulated` when it pairs. From then on the dashboard marks the locket, its recordings and its connection prompt as **Simulated**, so synthetic audio is never mistaken for a real recording. A server that should only ever hold real lockets refuses simulators: that is the default when `ASK_ENV=production` (override with `ASK_ALLOW_SIMULATED_DEVICES=true`). Its credential is stored in `platform\data\simulated-device.json` (gitignored) and never printed. Anything it uploads is real data in whatever database the API points at, so use it only against a development API.

## Operator commands

`python -m app.cli <command>`

| Command | What it does |
| --- | --- |
| `generate-env [--force]` | Writes `.env` with a new secret key and locket master key. Refuses to overwrite without `--force`. |
| `check` | Loads and validates settings, tests the database, confirms migrations are applied and storage is writable. Exit code 1 if anything is wrong. |
| `bootstrap --company --name --email` | Creates a company and its first administrator. |
| `register-locket --company-id UUID --name NAME` | Registers a new Bluetooth locket at manufacture: prints its permanent product ID and, once, its secret, plus the `provision` line to type on the locket's USB serial console. Needs `ASK_LOCKET_MASTER_KEY`. |
| `reset-password --email` | Recovery for a locked-out account. Ends all of its sessions and writes an audit entry. |
| `housekeeping` | One pass of upkeep: marks silent lockets offline, enforces retention, purges deleted recordings and stale worker records. Use it from a scheduler when `ASK_RUN_BACKGROUND_TASKS=false`. |
| `ai-check [--load]` | Shows what the processing provider can do on this machine and why anything is missing. With `--load` it also loads each model and runs it once on a generated tone. Exit code 1 if transcription is unavailable or a model fails. |
| `calibrate-speakers --samples DIR [--max-false-accept 0.01]` | Suggests the speaker-matching threshold from a folder of `.wav` files, one sub-folder per person. Changes nothing by itself. |
| `process-file FILE.wav [--voices DIR] [--json]` | Runs the worker's stages on one WAV file outside the database and prints each line, who (if anyone) the voice matched, and each stage's time. `--voices` enrolls the people in a folder in memory for that run only. |

## Configuration

Environment variables with the `ASK_` prefix, or a `.env` in this folder. [`.env.example`](.env.example) lists them with placeholders. Settings are validated at startup, so a bad deployment fails immediately with a message that names the setting.

| Variable | Default | Notes |
| --- | --- | --- |
| `ASK_SECRET_KEY` | none, **required** | At least 32 random characters. Placeholder-looking values are rejected. |
| `ASK_ENV` | `development` | `production` enforces PostgreSQL, secure cookies and SMTP email, and hides `/docs`. |
| `ASK_DATABASE_URL` | SQLite in `platform/data` | PostgreSQL: `postgresql+psycopg://user:password@host:5432/ask`. |
| `ASK_STORAGE_DIR` | `platform/data/recordings` | Audio is written here and never served by path. |
| `ASK_WEB_ORIGINS`, `ASK_WEB_BASE_URL` | `http://localhost:3000` | Allowed browser origins (never `*`) and the base of emailed links. |
| `ASK_TRUST_FORWARDED_FOR` | `false` | Turn on only behind a proxy you control, otherwise anyone can spoof their address and dodge rate limits. |
| `ASK_HEARTBEAT_EXPIRY_SECONDS` | 90 | A locket is "online" while its last heartbeat is younger than this. |
| `ASK_MAX_UPLOAD_BYTES` | 25 MiB | Larger uploads are refused with `413`. |
| `ASK_ALLOW_SIMULATED_DEVICES` | allowed except in production | Whether `tools/simulate_device.py` may pair. |
| `ASK_MAX_EVENT_STREAMS_PER_USER`, `ASK_MAX_EVENT_STREAMS` | 5, 500 | Live-update connections allowed per person and per process. |
| `ASK_EVENT_STREAM_MAX_SECONDS`, `ASK_EVENT_KEEPALIVE_SECONDS`, `ASK_EVENT_POLL_INTERVAL_SECONDS` | 600, 15, 1 | How long a stream lives before renewing, how often a quiet one sends a keep-alive, and how often it looks for new events. |
| `ASK_LIVE_EVENT_RETENTION_HOURS` | 24 | How long events are kept for reconnecting browsers. |
| `ASK_SESSION_TTL_HOURS`, `ASK_SESSION_IDLE_HOURS` | 168, 24 | Absolute and idle session lifetime. |
| `ASK_TRUSTED_PROXY_HOPS` | 1 | With `ASK_TRUST_FORWARDED_FOR=true`: how many proxies you control sit in front. The client address is the entry that many places from the end of `X-Forwarded-For`, so a value the client sent itself is never used |
| `ASK_UPLOAD_TOTAL_SECONDS` | 300 | The whole of an upload (and 60 s of it for a voice sample), not only the gap between pieces |
| `ASK_MAX_JSON_BYTES` | 1 MiB | Ordinary request bodies. Audio uploads have their own limit |
| `ASK_INVITES_PER_HOUR` | 30 | Invitations one person may send per hour |
| `ASK_LOCKET_MASTER_KEY` | none | At least 32 characters, different from `ASK_SECRET_KEY`. Each locket's secret is derived from it; keep it for good. `generate-env` makes one. |
| `ASK_LOCKET_CODE_TTL_MINUTES`, `ASK_LOCKET_CODE_REQUESTS_PER_HOUR`, `ASK_LOCKET_CODE_REQUESTS_PER_LOCKET_PER_HOUR` | `10`, `5`, `10` | Pairing codes: how long a request stays valid, and requests per person and per locket per hour. |
| `ASK_FIRMWARE_LINK_MINUTES`, `ASK_FIRMWARE_MAX_BYTES` | `30`, `4194304` | Firmware updates: download link lifetime and largest file. |
| `ASK_FIRMWARE_PUBLISHER_COMPANY_ID` | none | The operator's company: only its administrators may upload firmware. Unset: nobody can. |
| `ASK_AI_PROVIDER` | `none` | `none`, `stub` (development only, refused in production), `local` or `google` (Google Speech-to-Text and Gemini; see [`../docs/AI.md`](../docs/AI.md)). The other processing settings (models, token, speaker threshold, sample lengths, retry timing) are listed in [`../docs/AI.md`](../docs/AI.md#settings). |
| `ASK_EMAIL_BACKEND` | `console` | `console` prints messages to the log (development only); `smtp` needs `ASK_SMTP_HOST`. |

## Quality gate

```powershell
.\.venv\Scripts\python -m ruff format --check .
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m mypy app
.\.venv\Scripts\python -m pytest -q
```

Tests use an in-memory SQLite database, a temporary audio folder and a capturing email sender; they never read your real `.env` or touch real data. A separate test applies every migration, checks that models and migrations have not drifted, and runs a full downgrade and upgrade.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `ValidationError ... secret_key Field required` | No `.env` yet. Run `python -m app.cli generate-env`. |
| `no such table` or `check` reports migrations not applied | Run `python -m alembic upgrade head`. |
| `Could not import module "app.main"` or `Factory not found` | Run uvicorn from `platform\api` and keep `--factory`. |
| Sign-in is refused for a password you are sure of | If it was piped into an old `bootstrap`, a hidden character may be stored with it. Run `reset-password`. |
| Sign-in answers `429` | Five failed attempts per minute per address and email. Wait for the `Retry-After` seconds. |
| Dashboard shows a locket as offline | Heartbeats stopped for longer than `ASK_HEARTBEAT_EXPIRY_SECONDS`. A browser cannot see the device directly. |
| The dashboard says "Reconnecting" and refreshes every 15 seconds | The live stream (`/api/events`) cannot connect. Check that the API is running, that you are still signed in, and that no proxy in front of it buffers or compresses `text/event-stream` responses. The page keeps working by polling meanwhile. |
| `422 simulation_not_allowed` from the simulator | The server only accepts real lockets (production). Use a development server. The pairing code is not used up. |
| Ctrl+C on uvicorn hangs | Open live streams. Start it with `--timeout-graceful-shutdown 3`. |
| `413 body_too_large` | An ordinary (JSON) request was larger than `ASK_MAX_JSON_BYTES`. Audio goes to the upload routes, which have their own limit |
| Someone cannot sign in and the password is right | They may have been switched off (Settings, Team). An administrator can switch them back on |
| Uploads stay `queued`; the dashboard says "waiting for a processing worker" | No worker is running. Start `python -m app.worker`. If it exits at once with code 2, `ASK_AI_PROVIDER` is `none` (the default) or the provider cannot start; the message says why. |
| The worker starts but transcripts say "placeholder" | `ASK_AI_PROVIDER=stub`. That is deliberate development output, not speech recognition. Use `local` with the `[ai]` extra. |
| `ai-check` says speaker separation is unavailable | No `ASK_HF_TOKEN`, or the model's terms were not accepted on Hugging Face. Transcription still works and every recording is treated as one speaker. |
| Voice enrollment is "not available on this server" | No speaker model is set up (provider `none` or `stub`, or the speaker libraries are missing). `ai-check` lists what is missing. |
| A recording is stuck in a processing state | The worker running it died. Another worker takes it back after `ASK_JOB_LOCK_TIMEOUT_SECONDS` (3 min by default); a replacement worker started sooner does not steal a live job. |
| The worker exits at once with code 2 and a message about a model | Speech recognition could not load: missing weights with downloads off (`ASK_AI_OFFLINE`), no network, a wrong `ASK_AI_ASR_MODEL`, or not enough memory or disk. The message says which; see [`../docs/AI.md`](../docs/AI.md#troubleshooting). |
| `403 bad_origin` or `csrf_failed` from a script | State-changing browser calls need an `Origin` in `ASK_WEB_ORIGINS` and the `x-csrf-token` header. Device calls use the bearer credential instead. |

## Layout

```text
app/
  main.py        application factory, health and readiness, background upkeep
  config.py      validated settings
  models/        SQLAlchemy tables (22)
  schemas/       request and response models (camelCase)
  routers/       HTTP endpoints
  services/      business rules: accounts, devices, uploads, recordings, storage, audit, email, and the processing pipeline, job queue, matching, alignment and voice enrollment
  security/      passwords, tokens, CSRF and sessions, device credentials, rate limits
  ai/            transcription, separation and speaker-model providers (none, stub, local)
  worker.py      the processing worker process
  cli.py         operator commands
migrations/      Alembic (the schema is created only by migrations)
tools/           simulate_device.py, make_sample_audio.py, benchmark_pipeline.py, inspect_clip.py (development only)
tests/           pytest suites; tests/real runs the real models (ASK_REAL_MODELS=1); tests/postgres needs a PostgreSQL (ASK_VERIFY_DATABASE_URL, and ASK_PG_BIN for the pg_dump test); ASK_TEST_DATABASE_URL runs the whole suite on one
```

## Known limits

- Rate limits and the open-stream counter are in memory **per process**. Run one API process, or put a shared limiter in front, before scaling out. Events themselves live in the database, so a second process (or the future worker) shares one feed.
- Each open live stream looks for new events about once a second (a small indexed query). That is comfortable for a company's worth of browser tabs; a very large deployment would swap the polling for database notifications.
- SQLite migrations: SQLite changes some tables by rebuilding them, which with foreign keys on would delete the rows of every table that points at the rebuilt one. `migrations/env.py` therefore switches enforcement off for the migration and checks for orphaned rows afterwards, and `tests/test_migrations.py` proves rows survive. Keep that when editing `env.py`.
- PostgreSQL 16.15 (a portable Windows build, local only) has been exercised: all migrations up and down, the whole test suite (`ASK_TEST_DATABASE_URL`), `tests/postgres` (9 tests) and a `pg_dump`/`pg_restore` round trip. It has **not** been run through Docker or the `postgres:16` image the deploy stack uses, with TLS to the database, or under real load.
- Speech recognition and speaker matching have run on real audio (synthetic voices, CPU only); full speaker separation (pyannote, gated) never has. The speaker-matching threshold is a default, not a measured value, until `calibrate-speakers` has been run on your own recordings. Exactly what was and was not verified: [`../docs/AI.md`](../docs/AI.md).
- The job queue's PostgreSQL path (`FOR UPDATE SKIP LOCKED`) has run: four racing workers and two separate worker processes each claimed every job exactly once (`tests/postgres`). On SQLite, run one worker.
- Single sign-on (OIDC) is not implemented.
- The audit log is never pruned by the application, and it holds sign-in addresses. Decide your own rule and prune with a database job if you need one.
- Production has never been exercised: the deployment files, the proxy and backups are in `../deploy` and unrun. See [`../docs/RUNBOOK.md`](../docs/RUNBOOK.md).
- What the security review left as decisions or known limits (a clip that arrives after a locket changes hands belongs to the new wearer, no per-company quotas, timing differences on password reset) is in [`../docs/SECURITY.md`](../docs/SECURITY.md).
