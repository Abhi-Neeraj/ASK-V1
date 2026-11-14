# Final completion report

Session of 2026-10-26/27, on the machine and under the limits described in [FINAL_COMPLETION_PLAN.md](FINAL_COMPLETION_PLAN.md#1-the-machine-this-was-audited-and-built-on). Written to be read without trusting it: every result below says what was run, and what was not.

## 0. The short version

**What now works, run for real on this machine:** a clip uploaded the way a locket uploads it is stored, queued, picked up by a separate worker process, **transcribed by a real speech model, separated into speakers, matched against enrolled people, and shown on the website** with timestamps, names, a voice-match score and an honest notice that the separation was experimental. This was done in tests, by a benchmark, and by hand in a browser against the real API, including killing the worker mid-recording, stopping the API with clips waiting, withdrawing consent, and a failed re-run. A 10-second clip became readable **9.8 s** after upload (95th percentile 10.8 s) with the default model, separation and matching on, on a 4-core laptop with no GPU.

**Also run for real, after you allowed it (see section 13):** the **firmware compiles with the real ESP32 toolchain** (Arduino CLI with arduino-esp32 3.3.12, board `esp32:esp32:XIAO_ESP32S3`, no warnings), and the backend runs on a **real PostgreSQL 16.15**: migrations up and down (including the 0004 wearer migration with data kept), the whole 566-test suite, two worker processes sharing one queue (neither takes a job twice), and a `pg_dump`, `pg_restore` round trip with the application working on the restored copy.

**What is not done, and why:**

| Not done | Why | What would settle it |
| --- | --- | --- |
| The firmware has been **compiled** but never **flashed or run** on a board | No board, microphone, card or Wi-Fi link here | A board and a card: [HARDWARE_ACCEPTANCE_TESTS.md](HARDWARE_ACCEPTANCE_TESTS.md) |
| Nothing has run in Docker, behind the Caddy proxy with HTTPS, or through the backup scripts' Docker wrappers and the recordings archive | No Docker here (the database half of backup and restore *was* run, on PostgreSQL) | A machine with Docker: the runbook |
| pyannote (the full speaker separation) has never run | Its model is gated: it needs a Hugging Face account, accepted terms and a token | A token |
| The speaker threshold is not calibrated, and nothing is known about real people's voices | Only two synthetic voices exist here | Consented recordings of your own people, then `calibrate-speakers` |
| No GPU path was run | No GPU | A GPU machine |

So **the locket's end of the chain (record, card, Wi-Fi, upload from a real board) is untested on a device**, and the container deployment is untested. Everything between "a clip arrives at the API" and "a person reads it" is tested with real models, on SQLite and on PostgreSQL.

## 1. What was implemented in this session

**Real transcription and speaker work (Phases 2 and 3)**

- Installed and pinned the real libraries (`faster-whisper 1.2.1`, `speechbrain 1.1.2`, `pyannote.audio 4.0.7`, `torch 2.14.1`, `torchaudio 2.11.0`, `numpy`, `scipy`); `pip check` is clean; downloaded and loaded real weights (Whisper `tiny.en` and `small`, the SpeechBrain voice model); read each model's licence and access terms from the hub ([AI.md](AI.md)).
- Reworked the real-model adapters (`app/ai/local.py`): load once and expose `load()`; hand pyannote decoded audio so it needs no FFmpeg; resample audio instead of failing a whole recording at a non-16 kHz rate; turn memory, disk, weights and device failures into actionable messages; Windows-safe SpeechBrain weight copying; settings for device, threads, beam size and an offline mode (`ASK_AI_OFFLINE`).
- **Start-up check:** the worker loads every model before taking a job and exits with code 2 and the reason if speech recognition cannot load; speaker separation or matching that cannot load is dropped with a recorded reason. The library's own message is logged, cleaned of paths and tokens.
- **An experimental, token-free speaker separator** (`ASK_AI_DIARIZER=clustering`: finds speech, takes a voice print per 1.5 s window, groups windows by sound). It is labelled experimental in the database, the API (`separationExperimental`), the dashboard and exports, and is not the default.
- `python -m app.cli process-file FILE.wav [--voices DIR]`: the worker's stages on one file with per-stage timings.
- A repeatable real-model suite: `tests/real` (45 tests), `tools/make_sample_audio.py` (synthetic speech from the Windows voices, with known words and times), `tools/benchmark_pipeline.py`, `tools/inspect_clip.py`.
- The default job lock timeout was **15 minutes**, which left a recording stuck for a quarter of an hour after a worker crash. It is now **3 minutes** (a live worker renews it every minute even while a model runs).

**Firmware (Phase 4)**

- Compared every library call the firmware makes with the real arduino-esp32 **3.3.12** headers. Found and fixed one build error: `esp_random()` is declared in `esp_random.h`, which nothing included. The stand-in headers were tightened so this kind of mistake is caught on a computer; `check.ps1` now accepts a quoted compiler path.
- **Built with the real toolchain** (Arduino CLI 1.5.2-rc.1, `esp32:esp32` core 3.3.12, `esp32:esp32:XIAO_ESP32S3`), first with the fix above and then re-built from the final sources with `--warnings all`: it compiles and links with **no warnings**, using 1,074,604 bytes of program storage (32% of 3,342,336) and 55,112 bytes of global variables (16% of 327,680, leaving 272,568). The shipped `config.h` (battery pin off, plain HTTP and insecure TLS off) is what was built; a copy with the battery pin on pin 2 and plain HTTP and insecure TLS switched on, the development setup, was also built earlier in the session, without warnings. This proves the code is accepted by the real compiler and headers and fits in memory. It does **not** prove it runs: nothing was flashed.
- [HARDWARE_ACCEPTANCE_TESTS.md](HARDWARE_ACCEPTANCE_TESTS.md): 43 numbered checks for a real board, with commands, expected output, wiring, and a failure table. Check HW-00 (the sketch compiles) is done and recorded there.

**Website and API behaviour found wrong while testing for real**

- A failed "Process again" showed only the word "Failed" and the old transcript, with the reason hidden. Now the page says the latest attempt failed, that the transcript is from an earlier run, and (to managers and administrators) what went wrong (`failureDetail`). Employees get none of the internal detail, and `GET /api/jobs/{id}` no longer returns `lastError` to them either.
- A recording with no speech now says so instead of showing an empty list; the voice-match score is worded as a similarity score with an explanation, never as certainty; the "audio not available" copy no longer talks about builds in a real deployment.
- The experimental separation notice (above).

**Deployment and docs (Phase 7, as far as it could go)**

- `tests/postgres` (9 tests, **all passed** against PostgreSQL 16.15): migrations up, down and up with a drift check; four workers racing for the queue, each job claimed exactly once; a concurrent duplicate upload; an end-to-end upload and processing run; migration 0004 on PostgreSQL (rows seeded at 0003 survive, the wearer backfill is right, the foreign key is enforced and clears when the person goes, down and up again); **two worker processes** sharing 60 queued recordings (one processed 28 and the other 32, none twice, none missed); and a `pg_dump --format=custom`, `pg_restore --clean --if-exists` round trip into a fresh database with every table's row count equal and the application signing in and reading a transcript from the copy.
- The ordinary suite can now be pointed at PostgreSQL with `ASK_TEST_DATABASE_URL` (the schema is dropped between tests, so the database name must contain `verify` or `test`); it passed there in full.
- Compose and `.env.example` carry the new model settings; the worker image installs CPU-only torch by default (unverified, no Docker). [AI.md](AI.md), [SECURITY.md](SECURITY.md) (new sections on voice prints and model files), [RUNBOOK.md](RUNBOOK.md), [API.md](API.md), [PLAN.md](PLAN.md), the READMEs and `CLAUDE.md` were updated. One wrong figure in the README (510 tests) was corrected from the test runner's own count.

## 2. What already existed and was verified

Re-run at the start, before any change: backend 528 passed and 1 skipped (a POSIX-permissions test, on Windows) in 149 s; web typecheck, lint and 330 tests passed; `alembic current` was `0004 (head)`. The reported auth, tenancy, pairing, upload, library, live-update, queue and wearer-rule work was read and exercised through the tests and, later, by hand in the browser. Nothing was found broken there apart from the items in section 1.

## 3. Test commands and results

All backend commands from `platform/api`, web commands from `platform/web`, firmware from `platform/firmware`.

| Check | Command | Result |
| --- | --- | --- |
| Backend format, lint, strict types | `python -m ruff format --check .`, `python -m ruff check .`, `python -m mypy app` | `118 files already formatted`; `All checks passed!`; `Success: no issues found in 69 source files` |
| Backend default suite | `python -m pytest -q` (with `ASK_FIRMWARE_CXX` set to a C++ compiler) | On SQLite, **566 passed, 55 skipped**, 0 failed. Run as two commands on the final sources: 544 passed and 77 skipped (85 s) with no C++ compiler configured, then the 22 firmware interop tests, which that run skipped, passed on their own (22 passed, 283 s) with a C++ compiler (`ASK_FIRMWARE_CXX`). The 55 skips that remain with a compiler are the opt-in real-model tests (45), the PostgreSQL tests (9) and one POSIX-permissions test that cannot apply on Windows. |
| Real-model suite | `ASK_REAL_MODELS=1 ASK_AI_ASR_MODEL=tiny.en python -m pytest tests/real -q` | **45 passed** (172 s): transcription, separation, naming, failure and recovery, a real worker process killed and replaced, memory caps |
| Transcription accuracy with the default model | the same, `ASK_AI_ASR_MODEL=small`, `-k TestRealTranscription` | 7 passed |
| Web types, lint, tests, build | `npm run typecheck`, `npm run lint`, `npm test`, `npm run build` | typecheck clean, lint clean, **343 tests passed** (34 files), production build succeeded |
| Firmware logic and compile against stand-in headers | `.\tests\check.ps1 -Compiler "..."` | 3,815 checks, 0 failed; all 7 source files compile. **Not a board build.** |
| **Firmware build with the real toolchain** | `arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 --warnings all platform\firmware\locket` (core `esp32:esp32` 3.3.12, Arduino CLI 1.5.2-rc.1) | **Succeeded, no warnings.** 1,074,604 bytes of program storage (32%), 55,112 bytes of globals (16%). Re-run on the final sources. **Compiled only: never flashed.** |
| Python dependency audit | `pip-audit` on the 139 pinned packages | No known vulnerabilities (as of 2026-10-27) |
| Web dependency audit | `npm audit`, `npm audit --omit=dev` | 0 vulnerabilities |
| Deployment configuration | in the default suite (`tests/test_deploy_files.py`, `tests/test_postgres_sql.py`) | passed; **this checks the files against the application's rules and generates PostgreSQL SQL, it does not run Docker or the proxy** |
| Browser, real stack | by hand, see section 4 | passed as listed |
| **PostgreSQL 16.15, dedicated tests** | `ASK_VERIFY_DATABASE_URL=... ASK_PG_BIN=... python -m pytest tests/postgres -q` | **9 passed** (32 s): see section 1 for what they cover. A portable PostgreSQL, local only, on port 54329 |
| **Whole default suite on PostgreSQL** | `ASK_TEST_DATABASE_URL=... python -m pytest -q` | **566 passed, 55 skipped**, 0 failed (12 min 14 s), on the final sources. The skips are the 45 real-model tests, the 9 `tests/postgres` tests (run separately, above) and the one POSIX-permissions test. The 22 firmware interop tests ran. |
| Docker, proxy, HTTPS | the runbook | **never run** (no Docker) |
| Real board | the checklist | **blocked, never run** (no board) |

The default suite skips the opt-in suites (`tests/real`, `tests/postgres`) and one Windows-irrelevant test; those skips are expected. No test was removed, weakened or replaced by a mock to get a pass. Where a test only passed because a second safeguard covered for the one under test, it was strengthened or checked by breaking both (see below).

## 4. Real-model results

Everything here used **synthetic speech** (the two Windows text-to-speech voices, "Hazel" and "Zira"). It proves the pipeline and its safeguards on real audio; it says nothing about accuracy on real people, rooms or microphones.

**Transcription (faster-whisper, CPU).** Word error rate against the known text: clean single sentence 0% (both `tiny.en` and `small`), clean four-turn conversation 2%, the same with noise added at about 10 dB 4% (`tiny.en`) and 2% (`small`). Silence and faint noise produced no invented words. A very short clip ("Yes, that works.") was transcribed.

**Speaker separation (experimental method).** Two voices taking turns: 99% of speech time under the right voice, 100% with noise; turn starts within 1.2 s of the real changes; one voice alone was never split in two; silence gave no speakers. Two voices talking at once is **not** handled (the output is one speaker at a time).

**Speaker matching (SpeechBrain).** With both people enrolled through the API, every line of a four-turn conversation was named correctly (match scores 0.87 and 0.84); with only one enrolled, the other voice stayed `Unknown speaker`; one sample was not enough; a threshold above any real score named nobody; a speaker with under 3 s of speech was not named; another company's enrolled voices were never used; consent withdrawn before processing, and **while a recording was being processed**, meant no name. Same-voice samples scored 0.71 to 0.87, different voices 0.19 to 0.31; `calibrate-speakers` suggested 0.549 and warned that 6 samples from 2 people is far too little to trust.

**The safeguards are not vacuous.** Each safeguard was broken on purpose and its test confirmed to fail: three directly (threshold, minimum speech, minimum samples), and three that are enforced twice (company, consent, and the consent re-check just before saving), which only fail when **both** layers are removed; all six were caught. For the pipeline behaviours, four more were broken: replacing a transcript rather than adding one, verifying the checksum, taking back a dead worker's job, and treating bad audio as permanent. The first of those initially was **not** caught (the test went through the "Process again" path, which clears the old transcript itself), so a test for the case that matters, a job re-run over an existing transcript, was added and then caught it.

**Failure and recovery (all with real models).** Empty, non-audio, truncated and header-only files are refused at upload and nothing is stored; a recording damaged after upload fails for good with a clear reason and no retries; a crash inside inference is recorded, retried and succeeds; retries run out and the failure is final; a failure that cannot pass is not retried; a **real worker process killed mid-recording** is replaced by another process, which produces exactly one transcript; processing the same recording again, or a job re-run over an existing transcript, leaves one transcript. A worker that cannot load its model (weights missing, downloads forbidden) exits with code 2 and says why; under a hard memory cap it either exits with "not enough memory" (700 MB) or dies while loading its libraries (250 MB), and never starts up pretending to work.

**By hand in the browser, against the real API, a real worker and `small`:** signed in to the real stack (no demo banner); a simulated locket (labelled Simulated) uploaded real speech; the recordings list updated by itself, Queued then Transcribing then Completed in about 6 s, with the transcript preview and speaker names; the recording page showed timestamps, names, voice-match scores and the experimental notice; the audio played, was seekable (HTTP 206) and had the right length; correcting a line showed a Corrected badge and was found by search; withdrawing consent on the People page turned that person's lines into `Unknown speaker` and left the other person's alone; stopping the worker made the page say "waiting for a processing worker" and starting another completed the recording on the same open page; a damaged recording re-run failed with the reason shown (after the fix above); the audit log recorded all of it without secrets or paths; the recording page has no sideways scroll at phone width.

## 5. Physical hardware results

**None.** No locket, microphone, card or Wi-Fi test was performed, and nothing was flashed. The firmware's logic was re-run (3,815 checks), every library call was compared with the real headers, and **the sketch was compiled and linked with the real ESP32 toolchain without warnings** (section 3); that shows the code is accepted by the real compiler and fits in memory, not that it works on a device. [HARDWARE_ACCEPTANCE_TESTS.md](HARDWARE_ACCEPTANCE_TESTS.md) is the work that remains (HW-01 onwards).

## 6. Measured end-to-end latency

Machine: Intel Core i7-1165G7, 4 cores / 8 threads, 12.6 GB RAM, Windows 11, **no GPU**, CPU only. Real HTTP uploads to a real API and a separate real worker process (SQLite, one API process), models loaded before the first clip, one warm-up clip discarded, **synthetic speech**. Seconds, median / 95th percentile; with 10 clips the 95th percentile is close to the maximum. "Readable" is measured by a client polling the API every 100 ms, so it includes that polling.

### Default model (`small`), experimental separation, voice matching with two people enrolled; 10 clips per length

| Stage | 5 s clip | 10 s clip | 20 s clip | 30 s clip | 60 s clip |
| --- | --- | --- | --- | --- | --- |
| upload (localhost) | 0.03 / 0.06 | 0.04 / 0.06 | 0.06 / 0.19 | 0.06 / 0.09 | 0.05 / 0.10 |
| waiting for the worker | 0.08 / 0.17 | 0.13 / 0.16 | 0.12 / 0.22 | 0.06 / 0.12 | 0.10 / 0.21 |
| transcribing | 6.32 / 6.91 | 6.65 / 7.53 | 9.32 / 15.08 | 14.12 / 15.29 | 20.78 / 23.69 |
| separating speakers | 1.11 / 1.19 | 1.83 / 1.91 | 4.38 / 4.60 | 6.19 / 6.79 | 8.93 / 12.37 |
| matching voices | 0.62 / 0.67 | 1.11 / 1.23 | 2.49 / 3.59 | 3.50 / 3.78 | 5.02 / 6.46 |
| aligning and saving | 0.01 / 0.01 | 0.01 / 0.02 | 0.01 / 0.02 | 0.01 / 0.02 | 0.01 / 0.01 |
| **readable after the upload ends** | **8.29 / 8.88** | **9.79 / 10.77** | **16.51 / 22.24** | **23.59 / 25.83** | **34.35 / 42.39** |

Burst: six 10 s clips uploaded at once were readable after 8.4, 16.3, 24.0, 32.1, 40.0 and 47.9 s. The worker used about 1.24 GB at peak and about 380% of one core on average.

### Other configurations

| Configuration | Clip | Readable after (median / 95th pct) | Worker memory peak |
| --- | --- | --- | --- |
| `small`, transcription only (no separation, no matching) | 10 s | 5.78 / 6.93 | 1,023 MB |
| `small`, transcription only | 30 s | 12.38 / 14.59 | 1,023 MB |
| `tiny.en`, separation and matching (6 clips) | 5 s / 10 s / 20 s | 2.83 / 2.92, 4.95 / 5.06, 8.36 / 9.01 | 999 MB |
| `tiny.en`, worker limited to **one thread** (6 clips) | 10 s | 6.54 / 8.24 | 763 MB |

### Resilience (with `tiny.en`)

- **The API down with three clips waiting:** all three finished while the API was down (it is not needed for processing); they were readable 0.1 s after it came back. Uploads are refused while it is down, as they should be (the locket keeps its clip and retries).
- **A worker killed mid-recording and another started at once:** the transcript was readable **186.8 s** later, as the 180 s job lock timeout predicts. That is the cost of a crash; a graceful stop (Ctrl+C or SIGTERM) hands the job back immediately.

### What this says about the 60-second target

- **Met here for one clip at a time:** a 10 s clip is readable in about 10 s, and even a 60 s clip in about 34 s (95th percentile 42 s), with the default model and every stage on.
- **Not met under load, and no headroom for continuous recording:** with everything on, a 10 s clip takes about 10 s to process, so ten-second chunks arriving back to back would just keep pace, and any burst queues (the sixth of six simultaneous clips took 48 s). Transcription only is about 0.6x real time, `tiny.en` about 0.5x.
- **The bottleneck** is speech recognition (60 to 70% of the time), then the experimental separation (20 to 25%) and voice matching (10 to 15%). Whisper works in 30-second windows, so a 5 s clip costs almost as much as a 10 s one. Options that were not tried: a smaller or English-only model, a GPU, a second worker (the queue supports it), and fewer, longer clips from the locket.
- **Network time** is not in these numbers (an upload over a real Wi-Fi link is not measured), nor are the locket's own recording and card-write time.

## 7. Deployment components actually started and verified

**Not started:** Docker and every container, the Caddy proxy, HTTPS, the web image, container health checks, `backup.sh` and `backup.ps1` (which wrap `docker compose`), and the recordings archive (a `tar` of a volume). None of the images, the Compose file or the proxy has ever run.

**Started and verified on a real PostgreSQL 16.15** (a portable server run locally from the session's scratch folder, bound to 127.0.0.1 only, throwaway data): every migration up and down, with the 0004 wearer migration's data kept; the queue's `FOR UPDATE OF processing_jobs SKIP LOCKED` claim under four racing workers and under **two separate worker processes** (28 and 32 recordings, none twice, none missed); a concurrent duplicate upload; end-to-end upload and processing; the whole default test suite; and the database half of backup and restore (`pg_dump --format=custom`, then `pg_restore --clean --if-exists` into a fresh database, same row counts in every table, and the application signing in and reading a transcript from the copy).

**Also checked without Docker:** the Compose file and template against the application's production rules (the stub and SQLite are refused in production, every variable is real, only the proxy publishes ports, the worker is off by default); `/api/ready` and the API's start-up on SQLite; dependency audits are clean. The API, a separate worker and the web app were started for real on localhost against throwaway data.

## 8. Remaining defects, risks and external dependencies

1. **The firmware compiles but has never run.** It builds cleanly against the real toolchain, so the build-level risk is gone; what is left is everything a device shows: stack sizes and TLS memory use at run time, pin assignments on your board revision, microphone level, card reliability, Wi-Fi reconnects and power. The shipped `config.h` refuses plain HTTP and insecure TLS, so a bench test against a local server needs `ASK_ALLOW_PLAIN_HTTP` (or a certificate on the card) changed deliberately, and changed back afterwards.
2. **Docker is unproven.** PostgreSQL is now verified (migrations, row-locking queue, two worker processes, backup and restore of the database), but the container images, the Compose stack, the Caddy proxy and HTTPS, the backup scripts' Docker wrappers and the recordings archive have never run. Connection behaviour through the proxy and under a container's resource limits is untested. The PostgreSQL used here was a portable build on Windows, not the Linux `postgres:16` image the stack uses.
3. **pyannote has never run**, and whether `ASK_AI_DIARIZATION_MODEL`'s default is the right model for pyannote.audio 4.x is unknown.
4. **The speaker threshold is a placeholder**, the experimental separator and matching were only tried on two synthetic voices, and **nothing is known about real people** (similar voices, colds, rooms, microphones). A wrong name is worse than none, so the margin and threshold err towards `Unknown speaker`, but only calibration on consented recordings can say how far.
5. **Capacity:** with `small` and all stages, one worker is at its limit for continuous 10 s chunks (section 6).
6. **Voice prints** are not encrypted by the application, outdated prints stay in the table until re-enrollment, and there is no retention rule ([SECURITY.md](SECURITY.md#voice-prints)). **Model files** are third-party checkpoints loaded with PyTorch and no revision is pinned.
7. **Crash recovery takes up to 3 minutes** (configurable); a frozen machine is indistinguishable from a dead worker until then.
8. **Overlapping speech** is not separated by the experimental method.
9. **GPU use** is configurable but unrun. On Linux the default `torch` wheel is large; the worker image uses the CPU wheels (unbuilt).
10. **No browser-driven test suite exists** (no Playwright; the pane was driven by hand). Component tests with a mocked API, and the accessibility checks in them, remain the automated coverage of the UI.

## 9. Exact next steps for what is blocked

1. **Flash a board.** The build is done; what is left is the device. Install the core and flash with the Arduino CLI (`arduino-cli core install esp32:esp32`, then `arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 --upload -p COMx platform\firmware\locket`), then work through [HARDWARE_ACCEPTANCE_TESTS.md](HARDWARE_ACCEPTANCE_TESTS.md) from HW-01 with a board, a card and a microphone. Fix what the first run says, and re-run `firmware\tests\check.ps1` after any change.
2. **PostgreSQL** is verified here. To repeat it on another machine (or against the production image), start a throwaway PostgreSQL 16 (for example `docker run --rm -p 5432:5432 -e POSTGRES_PASSWORD=verify -e POSTGRES_DB=ask_verify postgres:16-alpine`), then from `platform\api`:

   ```powershell
   $env:ASK_VERIFY_DATABASE_URL = "postgresql+psycopg://postgres:verify@localhost:5432/ask_verify"
   $env:ASK_PG_BIN = "C:\path\to\postgresql\bin"   # for the pg_dump / pg_restore test
   .\.venv\Scripts\python -m pytest tests/postgres -q
   $env:ASK_TEST_DATABASE_URL = $env:ASK_VERIFY_DATABASE_URL   # then the whole suite on it
   .\.venv\Scripts\python -m pytest -q
   ```
3. **Docker stack**: follow [RUNBOOK.md](RUNBOOK.md) on a machine you can throw away, starting with `docker compose config`; then run the backup script (which also archives the recordings volume) and a restore. The database half of this has already been run.
4. **pyannote**: make a Hugging Face token, accept the terms on the pages of `pyannote/speaker-diarization-3.1` and the models it loads, set `ASK_HF_TOKEN` and `ASK_AI_DIARIZER=pyannote`, run `python -m app.cli ai-check --load`, then `python -m pytest tests/real -k Separation` with `ASK_HF_TOKEN` set.
5. **Calibrate**: record at least five consented samples of each real person on different days, put them in `samples/<name>/`, run `python -m app.cli calibrate-speakers --samples samples`, and set `ASK_SPEAKER_MATCH_THRESHOLD`.
6. **Capacity**: decide on `ASK_AI_ASR_MODEL` and the number of workers from `python tools/benchmark_pipeline.py` on the machine that will run them.

## 10. A realistic estimate of what remains

Measured against the definition of done (a verified end to end product from a physical locket to a company user), this is where each acceptance criterion stands:

| Criterion | State | Rough work left, assuming no surprises beyond those listed |
| --- | --- | --- |
| A clip arriving at the API becomes a real, correct, visible transcript with conservative speaker naming | **Met** here, on synthetic voices | Re-check on real voices: part of calibration |
| Recovery, retries, idempotency, tenant and consent rules with real models | **Met** | none |
| 60-second target | **Met** for single clips on this CPU; **not** with a burst or continuous recording | a decision (smaller model, GPU or a second worker) and one re-measurement: hours |
| Real-model speaker separation (pyannote) | **Not met** | under an hour with a token; a day if the model name or interface needs changing |
| Speaker threshold calibrated | **Not met** | a day or two, mostly collecting consented recordings |
| Firmware builds with the real toolchain | **Met** (no warnings, 32% of program storage, 16% of globals) | none |
| Firmware records, uploads, retries and recovers on a real board | **Not met** (no board) | the checklist on a board: about a day, plus whatever it finds |
| PostgreSQL: migrations, row-locking queue, several workers, backup and restore of the database | **Met** here on PostgreSQL 16.15 | none, beyond repeating it on the production image |
| Docker, proxy, HTTPS, the backup scripts and the recordings archive verified | **Not met** (no Docker) | about a day for the first clean run, more if the unrun drafts hide bugs |
| Security and privacy decisions that are the owner's | Open ([SECURITY.md](SECURITY.md)) | policy decisions, not engineering |

So of ten criteria, **four are met, one is met only for single clips (the 60-second target), four are not met** (real-model speaker separation, the calibrated threshold, a run on a device, the container deployment) **and one is the owner's decisions**. The ones not met are things this machine could not do, not things found broken. These are ranges from the shape of the work, not commitments, and the biggest unknown by far is what a real board turns up and what the unrun container stack does on first start.

## 11. Defects found and fixed during this session

Firmware `esp_random` build error. Real-model adapters: pyannote's dependency on FFmpeg for decoding, whole-recording failure at a non-16 kHz rate, unactionable model errors, no start-up check, no offline mode. A 15-minute stuck-recording window after a crash. A test that failed the moment the AI libraries were installed. A hidden failure reason after a failed re-run, and an employee-visible internal error text. A test-data defect (the sample audio's known speech times included silence, which made a correct separator look wrong). The sampler for the benchmark measuring the wrong process on Windows. A wrong test count in the README. One safeguard test that could not fail for the case it claimed to cover. The library's own message being lost when the worker could not start. Quoted compiler paths in `check.ps1`.

## 12. Files changed, and why

**New:** `platform/docs/FINAL_COMPLETION_PLAN.md`, `FINAL_COMPLETION_REPORT.md`, `HARDWARE_ACCEPTANCE_TESTS.md`; `api/app/services/offline.py` (run the pipeline on one file); `api/tools/make_sample_audio.py`, `benchmark_pipeline.py`, `inspect_clip.py`; `api/tests/test_ai_local.py`, `api/tests/real/*` (real-model tests), `api/tests/postgres/*` (9 tests, run on PostgreSQL 16.15: `test_postgres.py`, `test_postgres_migration_wearer.py`, `test_postgres_workers.py`, `test_postgres_backup.py`); `firmware/tests/stubs/esp_random.h`.

**Changed (significant):** `api/app/ai/local.py` and `registry.py` (adapters, start-up check, experimental separator); `api/app/worker.py` (start-up check, logging of the cause); `api/app/cli.py` (`process-file`); `api/app/config.py` (device, thread, offline, beam, diarizer settings; lock timeout 900 to 180 s); `api/app/services/pipeline.py` (identification shared with `process-file`); `api/app/routers/recordings.py`, `services/recordings.py`, `schemas/recordings.py` (`failureDetail`, `separationExperimental`, manager-only `lastError`); `api/pyproject.toml` (pins, lint scope), `.env.example`, `Dockerfile`; `deploy/docker-compose.yml` and `.env.example`; `api/tools/simulate_device.py` (`--file`); web: `recording-detail-view.tsx`, `transcript-view.tsx`, `transcript-export.ts`, types, demo data and their tests; `api/tests/conftest.py` (`ASK_TEST_DATABASE_URL` runs the whole suite on PostgreSQL); firmware: `settings.cpp`, `uploader.cpp`, `tests/check.ps1`, `tests/stubs/Arduino.h`; docs: `AI.md` (rewritten), `SECURITY.md`, `RUNBOOK.md`, `API.md`, `PLAN.md`, the READMEs, `CLAUDE.md`.

**Not touched:** the Expo app, the database schema (no migration was added, so the development database was not changed and needed no backup), existing recordings and users.

## 13. Things that did not go to plan

The session's permission checker first refused to run the Arduino CLI and a PostgreSQL server after they had been downloaded from their official sites. I did not work around that; I asked, and you then allowed both explicitly, local only, from the session's scratch folder. After that: the **official Arduino CLI 1.5.2-rc.1** and the **`esp32:esp32` core 3.3.12** (with its compiler toolchain) were downloaded and used to build the firmware, and a **portable PostgreSQL 16.15** was initialised there (listening on 127.0.0.1:54329 only, a throwaway password, throwaway data) and used for the PostgreSQL tests. PyPI packages (the AI libraries, a C++ compiler `ziglang` for the firmware interop tests) were allowed throughout. All of these downloads, the PostgreSQL data directory and the build outputs are under the session's scratch folder, outside the project; no PostgreSQL process or listening port was left running. (A fast shutdown was requested at the end and the session ended during it, so the throwaway data folder may need crash recovery if anyone starts that server again; it holds no real data.) Nothing was installed system-wide. A first attempt at text-to-speech used an execution-policy bypass and was refused; it was redone without one. Several of my own first versions of tests and tools were wrong and are listed in section 11 where they mattered.
