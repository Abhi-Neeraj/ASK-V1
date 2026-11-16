# Final completion plan

Written at the start of the final-completion session, from an audit of the repository as it actually is, not
from the previous build report. Statuses are updated as work lands; the outcome, with measured results, is in
[FINAL_COMPLETION_REPORT.md](FINAL_COMPLETION_REPORT.md). Anything marked **blocked** says what it needs.

## 1. The machine this was audited and built on

| Item | Found |
| --- | --- |
| OS / hardware | Windows 11, Intel Core i7-1165G7 (4 cores, 8 threads), 11.7 GB RAM (about 2 GB free at the time), no GPU, 36 GB free disk |
| Python | 3.11.9 in `platform/api/.venv` (3.10, 3.12 and 3.14 also installed; the API requires `>=3.11,<3.12`) |
| Node | 24.11.1, npm 11.6.2. Web app: Next.js 16.4.3, React 19.3.0, TypeScript 5.9.3, Tailwind 4.3.3, Vitest 5.0.5 |
| Database | SQLite file at `platform/data/ask.sqlite3`, on migration `0004` (head). **No PostgreSQL** |
| Containers | **No Docker** |
| Audio tools | **No FFmpeg** (not needed by the real-model path: faster-whisper decodes with PyAV, which bundles its own libraries) |
| Firmware toolchain | **No Arduino CLI, PlatformIO or esptool** |
| Git | Not a repository. Nothing can be diffed or committed |
| Network | Reachable (PyPI and the Hugging Face hub answered) |
| Real AI libraries | **Not installed** at the start (`faster-whisper`, `pyannote.audio`, `speechbrain`, `torch`) |
| Hugging Face token | **None.** The speaker-separation model is gated, so it cannot be downloaded without one |
| Physical locket | **None** |

**Tool-permission limit found during the audit.** The session's permission checker refuses to *run* programs that
were downloaded from the internet (it refused the Arduino CLI and the PostgreSQL server binaries, after they were
fetched from their official download sites). Installing Python packages from PyPI was allowed. So anything that
needs a downloaded program to run was **blocked pending the owner's decision**. The owner later allowed the
Arduino CLI and a portable PostgreSQL, local only; nothing else was changed about that rule.

## 2. Baseline, run before any change

| Check | Result |
| --- | --- |
| Backend `pytest` (SQLite, with a C++ compiler for the firmware interop tests) | 528 passed, 1 skipped (a POSIX-permissions test, on Windows), 149 s |
| Web `typecheck`, `lint`, `test` | see the report |
| Migrations | `alembic current` = `0004 (head)` |

## 3. Confirmed completed work (verified by reading the code and running the checks)

- Auth, sessions, CSRF, roles, tenancy and the audit log; the locket pairing and heartbeat API; idempotent, durable
  WAV upload with checksum verification; the recording library; live updates (SSE); invitations; consent and people
  management; the placeholder labelling; "who wore the locket" (`recordings.assigned_user_id`, migration `0004`).
- The processing queue: atomic claim, leases that survive a long job and lapse on a crash, retries with delay,
  permanent-versus-temporary failures, graceful shutdown, one transaction to save a transcript.
- The speaker rules: a name only above a threshold and clear of the runner-up, same-company and consented people
  only, consent re-checked just before saving, `Unknown speaker` otherwise.
- Firmware decision logic (`core.cpp`), tested on a computer and against the real server.

## 4. Verified defects and gaps (found in the audit, before fixing)

| # | Finding | Where | Phase |
| --- | --- | --- | --- |
| D1 | The real-model libraries were never installed, so **no real model has ever run**; the `[ai]` extra is lower bounds only | `pyproject.toml`, `app/ai/local.py` | 2 |
| D2 | A unit test asserts the AI libraries are *absent*, so it fails the moment they are installed | `tests/test_processing_api.py` | 2 |
| D3 | The speaker-separation adapter hands pyannote a file path; recent pyannote releases decode with FFmpeg-backed `torchcodec`, which is not installed here | `app/ai/local.py` | 2 |
| D4 | The voice-print adapter refuses any rate except 16 kHz with a *permanent* error, which would fail a whole recording if an operator allowed another rate | `app/ai/local.py` | 2 |
| D5 | Models load on the first recording: the first customer pays the load time, and a missing or unloadable model is found on a real recording, not at start-up | `app/worker.py` | 2 |
| D6 | Model failures (no memory, full disk, missing weights, unusable GPU) surface as a bare exception line | `app/ai/local.py` | 2 |
| D7 | No offline mode, no device choice for the torch models, no thread limit, no beam size setting | `app/config.py` | 2 |
| D8 | No command to run the real pipeline on one file and see timings | `app/cli.py` | 2 |
| D9 | The speaker-separation model is gated; with no token, **no diarization can be verified at all** | `app/ai/local.py` | 3 |
| D10 | Firmware was never compiled against the real board libraries; its stand-in headers were written from memory | `platform/firmware` | 4 |
| D11 | PostgreSQL and Docker were never run; the queue's `SKIP LOCKED` path, the migrations and the Compose stack are unproven | `platform/deploy`, `app/services/jobs.py` | 7 |
| D12 | No browser-driven tests exist for the website (only component tests with a mocked API) | `platform/web` | 5 |

More are added to the report as they are found.

## 5. Dependencies and blockers

| Need | Status |
| --- | --- |
| Real model weights (Whisper, speaker model) | Public; downloadable from the hub. Done as part of Phase 2 |
| Hugging Face token and accepting the separation model's terms | **Owner action.** Cannot be done by this session (credentials and an account) |
| Run a downloaded PostgreSQL or Arduino toolchain | Was blocked by the session's permission policy; **the owner then allowed both** (local only, from the scratch folder), and both were used: see the report |
| Docker | **Not installed.** Owner action |
| A physical locket, a microSD card, Wi-Fi | **Owner action.** A step-by-step checklist is delivered in `HARDWARE_ACCEPTANCE_TESTS.md` |
| Consented recordings of real people, to calibrate the speaker threshold | **Owner action.** The command and the data layout exist; this session can only calibrate on synthetic voices, which proves the tool, not the number |

## 6. Execution plan and acceptance criteria

| Phase | Work | Done when |
| --- | --- | --- |
| 1 | This audit and baseline | This file exists; baseline recorded |
| 2 | Real transcription: install pinned libraries, load real weights, warm-up, error handling, `process-file` command, real-audio tests | A real transcript of real speech audio is produced by the worker; empty/corrupt WAVs are refused; a failed inference is recorded and retried; a restarted worker recovers a job; processing twice leaves one transcript; the dashboard shows the real text and never calls placeholder text genuine; one command repeats the whole thing |
| 3 | Diarization and identification | Speakers are separated into timed turns (by pyannote if a token exists, otherwise by an ungated, clearly labelled experimental method); naming obeys threshold, margin, consent and company rules; short, silent, overlapping and unknown-speaker cases are tested; thresholds are measured, and described as measured on synthetic voices |
| 4 | Firmware | Firmware API usage checked against the real core's headers; host-side tests and interop tests re-run; build commands and a hardware checklist delivered. Not "complete" without a device |
| 5 | End to end | The whole path run with the real worker and models against the real API and the website in a browser, including recovery cases |
| 6 | Performance | A reproducible benchmark with real audio and real models, giving median and 95th percentile; the 60-second target reported as met or not, with the bottleneck |
| 7 | Deployment and hardening | Every check that can run without Docker or PostgreSQL; the rest listed and given exact commands |
| 8 | UX | Real-mode flows free of misleading demo marks; processing states and failures understandable; keyboard and responsive checks |
| 9 | Final verification and report | All checks re-run; passed / failed / skipped / blocked separated; `FINAL_COMPLETION_REPORT.md` and `HARDWARE_ACCEPTANCE_TESTS.md` written |

## 7. Outcome (added at the end of the session)

Results, with numbers and what was not run: [FINAL_COMPLETION_REPORT.md](FINAL_COMPLETION_REPORT.md).

| Defect | Outcome |
| --- | --- |
| D1 real libraries never installed | **Fixed.** Installed, pinned (`pyproject.toml`), run on real audio on a CPU |
| D2 test assumed the libraries were absent | **Fixed.** It now pretends they are absent whatever is installed |
| D3 pyannote handed a file path | **Fixed in code** (decoded audio is passed); **never run** (gated model, no token) |
| D4 non-16 kHz audio failed a whole job | **Fixed** (resampled) |
| D5 models loaded on the first recording | **Fixed.** Start-up check; the worker exits with the reason if speech recognition cannot load |
| D6 model failures were bare exception lines | **Fixed** (memory, disk, weights, device messages; checked under a real memory cap) |
| D7 no offline mode, device choice, thread limit, beam size | **Fixed** (`ASK_AI_OFFLINE`, `ASK_AI_TORCH_DEVICE`, `ASK_AI_CPU_THREADS`, `ASK_AI_ASR_BEAM_SIZE`); offline loading was run with the hub cache hidden. GPU unrun |
| D8 no command to try the real pipeline | **Fixed** (`process-file`, `tests/real`, `tools/benchmark_pipeline.py`) |
| D9 no way to verify separation without a token | **Worked around**, labelled experimental; the full separation is still **blocked** on a token |
| D10 firmware never compared with the real libraries | **Mostly fixed.** Compared with the real 3.3.12 headers (one build error found and fixed), then **built with the real toolchain** after the owner allowed the Arduino CLI: no warnings, 32% of program storage. **Never flashed or run**: blocked on a board |
| D11 PostgreSQL and Docker never run | **PostgreSQL fixed** (after the owner allowed a portable server): migrations, `SKIP LOCKED` queue with racing workers and two worker processes, the whole suite, database backup and restore all ran on PostgreSQL 16.15. **Docker, the proxy and HTTPS still never run**: blocked, no Docker |
| D12 no browser-driven tests | **Partly.** The real stack was driven by hand in the browser pane; no automated browser suite (installing one means running downloaded browsers, which the session's permission checker refuses) |

| Phase | Outcome |
| --- | --- |
| 1 Audit and baseline | Done |
| 2 Real transcription | **Met on this machine** (CPU, synthetic voices) |
| 3 Separation and identification | **Met with the experimental separator**; pyannote and calibration **not met** |
| 4 Firmware | Checklist and header comparison done; **built with the real toolchain, no warnings; not flashed, not run** |
| 5 End to end | Done for everything from the API onwards; the locket's end **not done** |
| 6 Performance and reliability | Measured; the 60-second target met for single clips, not under load |
| 7 Deployment and hardening | Everything that runs without Docker, including PostgreSQL; Docker, the proxy and HTTPS **blocked** |
| 8 User experience | Fixes made where real use showed a gap; responsive and keyboard checks as far as the browser pane and the automated accessibility tests allow |
| 9 Final verification | Done; see the report |
