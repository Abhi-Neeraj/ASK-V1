# ASK-V1 — repository overview

Cloned from `https://github.com/Abhi-Neeraj/ASK-V1` (branch `main`, 1 commit: `18d04e3 Add the ASK platform and the voice-to-summary pipeline`). 598 tracked files.

The repo holds **two separate products that do not import from each other**:

| Part | Where | Stack | What it is |
| --- | --- | --- | --- |
| ASK mobile app | repo root (`src/`, `app.json`, `package.json`) | Expo 57, React Native 0.86, TypeScript, Vitest | Local-first app: conversation transcripts → guarded "canonical memory" events → answers with evidence; on-device voice enrollment/verification |
| Voice Locket platform | `platform/` | Next.js 16 web, FastAPI backend + worker (Python 3.11), ESP32-S3 firmware | Company product: a wearable locket records audio, uploads it, the server transcribes, separates speakers, matches names and shows it in a web dashboard |

## 1. Mobile app (root)

```
src/
  app/            Expo Router screens: tabs (home, ask, activity, you), record, people,
                  person/, conversation/, onboarding/, memory-graph, needs-you, team, settings
  domain/         Contracts and pure logic: function registry, guards, events, identity,
                  audio quality, NLP, RAG, device protocol
  application/    Use cases: audio, device, identity (voice enroll/verify), nlp, parsing
  infrastructure/ Adapters: asr, audio, auth, crypto, db (+migrations), inference (ONNX),
                  memory, secure storage, storage, sync
  data/           Demo transcript fixtures
  features/voice, components/, lib/, theme/, config/
models/           voxceleb_ECAPA512.onnx (24 MB speaker-embedding model) + manifest
scripts/          Voice threshold calibration + model verification (Python)
docs/             MVP design spec, design system, device protocol, Phase 1 status, voice UI spec
PRD.md            Full product/engineering requirements (45 KB)
```

Pipeline: transcript → semantic draft → closed function registry → ordered guard engine → canonical-event compiler → repository → memory view → lexical retrieval → cited answer.

Status (per README): Phase 0 runs on fixtures; Phase 1 voice capture/enrollment/matching is built, but uses a **non-biometric prototype vector** until a reviewed model and calibrated thresholds ship. SQLite, ASR, BLE, cloud sync and auth are not done yet.

Run: `npm install`, `npm start` · Check: `npm run check`, `npm test`.

## 2. Voice Locket platform (`platform/`)

```
platform/
  web/       Next.js App Router site: marketing (product, technology, privacy, contact),
             auth (login, register, forgot/reset password), dashboard (overview, recordings,
             recording detail, devices, people, audit, settings). Demo mode available.
  api/       FastAPI: routers (auth, devices, device_api, recordings, people, processing,
             events/SSE, invitations, members, audit, workspace); services (pipeline, jobs,
             matching, signatures, insights, storage, uploads, calibration...); ai/, voiceid/,
             insights/; Alembic migrations 0001-0004; ~620 tests incl. real-model & Postgres suites
  firmware/  Two Arduino sketches for XIAO ESP32S3: `locket` (continuous 10 s clips) and
             `locket_web_upload` (button start/stop recorder) + host-simulation tests
  deploy/    Docker Compose (Postgres, API, worker, web, Caddy) + backup scripts
  docs/      PLAN, API, AI, VOICE_SIGNATURE, SECURITY, RUNBOOK, DEMO, hardware tests,
             final completion plan/report
```

Processing state machine: `uploaded → queued → validating → transcribing → diarizing → identifying_speakers → aligning → completed` (+ failed / retrying / needs_review).

Key tech: SQLite (dev) / PostgreSQL (prod), argon2 passwords, cookie sessions + CSRF, per-device bearer secrets, DB-backed job queue, SSE live updates; faster-whisper (speech-to-text), SpeechBrain (voice matching), pyannote (optional, gated). Summary is rule/template-built, **not generative AI**.

### What is verified vs. not (from `platform/docs/FINAL_COMPLETION_REPORT.md`)

| Verified on the dev machine | Never run |
| --- | --- |
| Backend 566 tests on SQLite and PostgreSQL 16; web 343 tests + build | Firmware on a real board (compiled only) |
| Real transcription + voice matching on CPU (synthetic voices) — 10 s clip readable ~9.8 s after upload | Docker / Caddy / HTTPS deployment |
| Firmware compiles with real ESP32 toolchain, no warnings | pyannote (needs HF token), GPU |
| Migrations, multi-worker queue, pg_dump/restore | Speaker threshold calibration on real people |

Run (from `platform/web`): `npm run dev:demo` → http://localhost:3000 (no backend needed). Full API setup is in `CLAUDE.md` and `platform/api/README.md`.

## 3. Things worth knowing

- No real secrets are committed; only `.env.example` templates. The one token-like string found is a fake used in a scrubbing test.
- `platform/firmware/locket_web_upload/config.h` intentionally contains a throwaway test hotspot (`ESPTEST`) per the owner's request — documented in `platform/docs/SECURITY.md`.
- Brand palette for web: Jet Black `#0A0A0A`, Cerulean `#007BA7`, Neon Rose `#FF2D95`. Mobile app uses its own violet system (`docs/DESIGN_SYSTEM.md`).
- `CLAUDE.md` in the repo root has the full command table and project rules.
