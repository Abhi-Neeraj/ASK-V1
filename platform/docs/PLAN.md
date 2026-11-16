# ASK Voice Locket Platform: architecture and implementation plan

Status: milestones A to D are done; E has now run with real models on real audio (CPU; pyannote, a GPU and real people's voices are still untried); and F (hardening, audit viewer, firmware, runbooks, deployment files) is written, with the deployment files and the firmware **not run on real infrastructure or hardware**. The last session's audit, work and measurements are in [FINAL_COMPLETION_PLAN.md](FINAL_COMPLETION_PLAN.md) and [FINAL_COMPLETION_REPORT.md](FINAL_COMPLETION_REPORT.md). Updated as milestones complete.

## 1. Scope and relationship to the existing app

The repo root is the **ASK Expo / React Native app**. It is left untouched. The platform is a new, self-contained subsystem under `platform/`:

```text
platform/
  docs/        this plan, architecture, API contract, runbooks
  api/         FastAPI backend, Alembic migrations, background worker, tests (Python)
  web/         the ASK app frontend (Expo Router, React Native for Web), exported to static files
  firmware/    Arduino sketch for the XIAO ESP32S3 Sense locket
  data/        local durable audio storage in dev (gitignored)
```

Separate toolchains, separate dependency trees, separate env files. The Expo app's `tsconfig.json` and Metro config exclude `platform/` so neither tool crawls it.

The platform's device API is the HTTP WAV-upload path specified for the company web product. It is unrelated to the binary frame protocol in `src/domain/device/` (the mobile-app gateway). The two are not merged.

## 2. Decisions and defaults

| Area | Decision | Reason |
| --- | --- | --- |
| Python | 3.11.9 (`py -3.11`) | Stable on this machine; widest PyTorch / CTranslate2 / SpeechBrain wheel support. 3.12 here is a release candidate and 3.14 is too new for the ML stack. |
| API | FastAPI, Pydantic v2, SQLAlchemy 2, Alembic | As specified. |
| Database | PostgreSQL in production and Compose. SQLite for local dev and tests. | Docker is not installed on this machine, so SQLite is the documented dev mode (PostgreSQL 16 has been run from a portable build to verify the production path). Models and migrations stay portable. |
| Sessions | Opaque random token in an HTTP-only, SameSite cookie. Only a hash is stored. | Revocable, no custom crypto. |
| CSRF | Double-submit token header on every state-changing cookie-authenticated request | Standard pattern. |
| Passwords | argon2-cffi | Proven library. |
| Browser to API | Next.js rewrites `/api/*` to FastAPI so the browser sees one origin | Keeps cookies same-origin. Strict CORS stays on as defense in depth. |
| Device auth | Per-device random 256-bit secret, shown once at pairing, stored hashed. `Authorization: Bearer <device_id>.<secret>`. | Never a company-wide firmware secret. TLS is required outside the local network. |
| Pairing | Admin creates a short-lived, single-use pairing code. The device redeems it. A device on the same Wi-Fi is not trusted automatically. | Per the spec. |
| Job queue | Durable DB-backed `processing_jobs` table, atomic claim (`SKIP LOCKED` on Postgres), separate worker process | No Redis on Windows, survives restarts, easy to swap for Celery/Redis at scale. |
| Live updates | SSE reading an append-only `live_events` table (tenant- and role-scoped, `Last-Event-ID` resume), with polling fallback in the browser | Works across the API and worker processes on SQLite and Postgres. Each stream polls the table about once a second and renews itself every 10 minutes; the cost and the scaling path (database notifications) are in `api/README.md`. |
| Audio storage | Filesystem behind a `StorageBackend` interface. Layout: `<company>/<yyyy>/<mm>/<recording_id>.wav`. | S3-compatible backend can be added later. No paths in API responses. |
| ASR | faster-whisper (word timestamps) | Local, free. |
| Diarization | pyannote.audio. Needs a Hugging Face token and accepted model terms. | Gated model. Pipeline degrades to "single speaker, not diarized" when unavailable, and says so. |
| Speaker ID | SpeechBrain ECAPA embeddings, cosine match, calibrated threshold, `Unknown speaker` below it | Per the spec. |
| AI deps | Optional extras group `[ai]`. Core API and tests run without them. A clearly labelled stub provider (`ASK_AI_PROVIDER=stub`) is for development and tests only and is refused in production. | Keeps the base install light and CI deterministic. See [AI.md](AI.md). |
| Deployment | Docker Compose: PostgreSQL, API, optional worker, web app and a Caddy reverse proxy that does TLS and sends `/api` straight to the API. Files in `platform/deploy`. | One command to bring up the whole stack, with only the proxy exposed. **Never run: see [RUNBOOK.md](RUNBOOK.md).** |
| Audit log | Append-only `audit_events`, read by administrators through `GET /api/audit` and a dashboard page, scrubbed again on the way out. | Accountability without ever showing a secret, a path or a voice print. |
| Frontend | The loki1514/ASK-V1 app: Expo Router, React Native for Web, Lucide. Built with `expo export --platform web`, served by `serve.mjs` | Replaced 2026-11-14 at the owner's request (was a Next.js marketing site and dashboard). Not yet connected to the API: it runs on its own sample data until the owner says what to wire up. |
| Palette | The ASK app's design system: `#080808` background, `#111113`/`#18181B` surfaces, `#27272A` borders, violet accent `#5B2EFF`, system font | Changed 2026-11-14 at the owner's request so the website matches the app (was Jet Black, Cerulean and Neon Rose with Michroma headlines). |

Ports: frontend 3000, FastAPI 8000. The root Expo app's own web preview keeps 8081.

## 3. Architecture

```text
 XIAO ESP32S3 locket                          Windows laptop (v1)
 mic -> WAV on microSD                  +---------------------------------+
 upload queue (retry/backoff)  --Wi-Fi->|  FastAPI  (auth, devices,       |
 per-device bearer token       HTTPS    |   upload, library, SSE)         |
                                        |     |                           |
 Browser (Next.js)  --cookie+CSRF-----> |  DB (SQLite dev / Postgres)     |
   SSE <-- live_events ---------------- |  Audio store (filesystem)       |
                                        |     ^                           |
                                        |  Worker: claim job -> validate  |
                                        |   -> ASR -> diarize -> identify |
                                        |   -> align -> save transcript   |
                                        +---------------------------------+
```

State machine: `uploaded -> queued -> validating -> transcribing -> diarizing -> identifying_speakers -> aligning -> completed`, plus `failed`, `retrying`, `needs_review`.

Diarization (who spoke when, anonymous turns) and speaker recognition (matching a turn to a registered person) are separate stages with separate models and separate failure modes.

### Tenancy rule

Every company-owned query goes through a tenant-scoped repository helper that requires the authenticated `company_id`. Object lookups by ID filter on `company_id` and return 404 (not 403) across tenants. Membership is created only through invitation or company bootstrap, never by email domain.

## 4. Data model (migration 0001)

22 tables (migrations `0001` initial schema, `0002` adds `devices.is_simulated`, `0003` adds `workers` and `transcripts.is_placeholder` and `language`, `0004` adds `recordings.assigned_user_id`: who wore the locket when the clip arrived): `companies`, `users`, `memberships`, `invitations`, `devices`, `device_credentials`, `device_pairings`, `recordings`, `processing_jobs`, `transcripts`, `transcript_segments`, `people`, `voice_profiles`, `consent_records`, `audit_events`, plus additions to the required list: `auth_sessions` (login sessions), `email_tokens` (verification and reset), `live_events` (SSE feed), `device_connections` (who has connected to a locket), `device_events` (locket lifecycle history), `contact_messages` (the marketing form) and `workers` (heartbeats, so the dashboard can say honestly whether anything is processing).

`upload_sessions` was dropped. Uploads are one idempotent request per clip (`PUT /api/device/v1/recordings/{clientId}`, keyed by a locket-chosen ID and a SHA-256), so there is no multi-request session to track. Long recordings are split by the locket and grouped with `X-Session-Ref` and `X-Sequence`.

All timestamps are UTC. Deleting a company cascades. Deleting a person deletes the voice profile and embeddings but keeps transcript text, with the speaker set to `Unknown speaker`. Recordings are soft-deleted first, then purged after the retention window.

## 5. Milestones and acceptance criteria

| Phase | Milestone | Status | Done when |
| --- | --- | --- | --- |
| A | Inspect and plan | **Done** | This document, `CLAUDE.md` updated, Expo app protected from the new tree. |
| B | Design system, marketing site, auth pages, dashboard shell | **Done** | All public pages and dashboard routes render. `lint`, `tsc` and `next build` pass. Responsive and reduced-motion checked in the browser. Dashboard shows data only in a visibly labelled demo mode. |
| C | Backend data, auth, devices, upload, library | **Done** (SQLite only) | Config validation, migration up/down, auth with CSRF, RBAC, tenant isolation, pairing, idempotent upload, library search and pagination. `pytest`, `ruff`, `mypy` pass. Heartbeat presence and the web-to-API flow were also run in a browser against a real API with a simulated locket. |
| D | SSE, connection popup, simulator | **Done** (SQLite only) | SSE is tenant- and role-scoped and resumes from `Last-Event-ID`. The popup fires once per locket per browser session. The simulator is clearly separate from physical mode: it declares itself `simulated`, the dashboard labels it, and production refuses it. Run end to end in a browser against a real API: a simulated locket came online and was offered a connection with no page reload. |
| E | AI pipeline | **Built; transcription and voice matching run on real audio (CPU); pyannote not run** | A worker (`python -m app.worker`) runs the state machine with retries, leases and crash recovery. Transcript segments, timestamps and speakers persist. Enrollment and matching work, with a calibration command. In the final-completion session the real models were installed (pinned), downloaded and run on real audio on a CPU: transcription (faster-whisper) and voice matching (SpeechBrain), with an experimental token-free speaker separator; `tests/real` and `tools/benchmark_pipeline.py` repeat that. **Still unverified:** pyannote speaker separation (gated model, no token), GPU use, real people's voices, and the speaker threshold is uncalibrated. A clearly labelled placeholder stub exercises everything else. Details: [AI.md](AI.md); results: [FINAL_COMPLETION_REPORT.md](FINAL_COMPLETION_REPORT.md). |
| F | Hardening, audit viewer, firmware, deployment | **Written; not run on infrastructure or hardware** | Lint, types, migrations, tests and builds all run (backend, web and the firmware's logic). An independent security review was done and its confirmed findings fixed, each with a regression test ([SECURITY.md](SECURITY.md)). The audit-log viewer exists (API and page, administrators only), and so do team controls (role, switch off, sign out everywhere). Firmware ([../firmware/README.md](../firmware/README.md)), runbook ([RUNBOOK.md](RUNBOOK.md)) and deployment files (`platform/deploy`) are written. **Since run, in the final-completion session:** the firmware builds with the real board toolchain (no warnings), and the backend, migrations, queue locking with several workers, and the database half of backup and restore ran on a real PostgreSQL 16.15. **Still not run:** Docker, the Caddy proxy and HTTPS, the backup scripts and the recordings archive, the web image's start, the firmware on a device, pyannote and a GPU. Each is listed in the document that covers it. What the final-completion session added and verified, and what stayed blocked: [FINAL_COMPLETION_REPORT.md](FINAL_COMPLETION_REPORT.md) |

Definition of done per milestone follows section 21 of the brief: core flow, passing tests, handled errors, persistence, enforced access control, UI driven by real backend state, accurate setup docs, documented limits.

## 6. Risks and constraints

- **No Docker or FFmpeg here.** Compose files can be written but not executed on this machine and are reported as unverified. (PostgreSQL was later run from a portable build, with the owner's permission, which verified the `SKIP LOCKED` path and the migrations.)
- **No ESP32 hardware.** Firmware is written to the Arduino-ESP32 API and compiles with the real toolchain, but is not tested on a device, and the docs say so.
- **Model weights and tokens.** Whisper, pyannote (gated) and SpeechBrain weights are not downloaded. Real-model integration tests run only when they are present. The 60-second target is a design goal, not a claim, until benchmarked on this machine.
- **Very new package majors.** Versions reported by npm and PyPI today are newer than commonly documented (Next 16, React 19.3, TypeScript 7, SQLAlchemy 2.1). Scaffolding uses the framework's own generator, versions are pinned only after the install and checks pass, and framework docs shipped in `node_modules` are read before using unfamiliar APIs.
- **Device discovery is heartbeat-based.** A web page cannot see a Wi-Fi device directly. "Online" means "authenticated heartbeat within the expiry window." No Bluetooth or OS-proximity claims.
- **Local-network limits for v1.** Browser and laptop must be able to reach the API. Secure remote hosting is documented as the later path.
- **No git repository** in this folder. Nothing was initialised.

## 7. Missing configuration (filled in by the user, never in chat)

`platform/api/.env`: secret key, database URL, storage path, SMTP settings (optional in dev), Hugging Face token (optional, for diarization). `platform/web/.env.local`: API origin. Templates ship as `.env.example` with placeholders only.
