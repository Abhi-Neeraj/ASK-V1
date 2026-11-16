# Button-recorder firmware: what was found, what was built, what was tested

This is the report for the firmware in [`platform/firmware/locket_web_upload`](../firmware/locket_web_upload/README.md): the user's original button-controlled recorder, with its speech-to-text, summary and chat-message steps removed and an upload to the ASK server in their place. The sketch's README has the setup, build, pairing and operating steps; this page has the audit, the file list, the test results and what is still open.

**One-line status.** The firmware **compiles and links with the real ESP32 toolchain, runs correctly against simulated hardware and against the real API, and has never been flashed or run on a board.** Do not describe it as working on a device until the checks in [HARDWARE_ACCEPTANCE_TESTS.md](HARDWARE_ACCEPTANCE_TESTS.md#7-the-button-recorder-locket_web_upload) have been done.

## 1. Audit

| Question | Finding |
| --- | --- |
| Is this a git repository? | **No** (the folder has no `.git`). So there is no branch to make. The baseline is preserved by building in a **new folder**, `platform/firmware/locket_web_upload/`; the other sketch, `locket/`, is unchanged (one test file's `#include` was changed so the same unit tests can be run against both copies of the shared logic). The user's original source was never in the repository, so nothing of it was modified |
| Did the backend already have a device API? | **Yes, all of it**, so no endpoint was invented: `POST /api/device/v1/pair`, `POST /heartbeat`, `PUT /recordings/{clientId}` (raw WAV body, `X-Content-SHA256`, `X-Recorded-At`, optional `X-Session-Ref` and `X-Sequence`), `GET /recordings/{clientId}`; a per-device bearer credential stored only as a hash; tenant scoping from the credential; 25 MiB and 600 s limits; WAV validation; idempotent storage by (device, id) with `201`/`200`/`409`; a database record committed only after the file is durable; a processing job created with it; live events and the "locket connected" prompt driven by authenticated heartbeats |
| Did the website already show it? | Yes: recordings list and detail with the processing states `uploaded`, `queued`, `validating`, `transcribing`, `diarizing`, `identifying_speakers`, `aligning`, `completed`, `failed`, `retrying`, `needs_review`; device cards with online/offline from heartbeats and the upload queue depth; company/role scoping. "Uploading" is a device-side state (the device's queue depth is shown on the device card) |
| Toolchain | arduino-esp32 **3.3.12** (`ESP_I2S`, `SD`, `HTTPClient`, `WiFiClientSecure`, `Preferences`, `WebServer` checked against the real headers by building). The board's menu defaults **PSRAM to Disabled**; this firmware needs **OPI PSRAM** |
| Existing firmware | `locket/` records continuously in 10-second clips with no button, deletes a clip on any `200`/`201` without reading the receipt, and requires a clock before recording. None of that is suitable for the button recorder, so the new sketch is separate |
| Gaps found and closed | (1) the server did not tell a locket its clip-length limit: **`maxClipSeconds` added to the device config**; (2) the website listed the parts of a long recording as unrelated recordings: **`part` added to the recording** and shown as "Part N"; (3) a bare `200` was enough to delete a file: the new firmware **checks the receipt**; (4) the original recorder refused to record offline and deleted the previous recording when a new one started: both gone |

## 2. What changed

**Added**

* `platform/firmware/locket_web_upload/`: `locket_web_upload.ino`, `config.h`, `core.h/.cpp` (pure logic), `status`, `settings`, `storage` (the lock and the queue folders), `sdwriter` (the PSRAM ring and writer task), `recorder` (button and capture), `uploader`, `dashboard` (local maintenance page), `console` (serial commands), `README.md`.
* `platform/firmware/tests/`: `test_web_upload_core.cpp` (61,239 checks), `check_web_upload.ps1`, and `hostsim/` (`hostsim.h`, `hostsim_main.cpp`, `hostsim_http.cpp/.h`, and forwarding stubs): the simulated hardware.
* `platform/api/tests/`: `test_firmware_web_upload.py` (simulated hardware), `test_firmware_real_server.py` (the real API over a socket), `test_recording_parts.py`.
* `platform/docs/WEB_UPLOAD_FIRMWARE.md` (this page).

**Modified**

* `platform/api/app/schemas/devices.py`, `app/services/devices.py`: `maxClipSeconds` in the device config (additive; older firmware ignores it). `tests/test_devices.py`.
* `platform/api/app/schemas/recordings.py`, `app/services/recordings.py`: `part` on a recording. `platform/web/src/lib/api/types.ts`, `components/dashboard/recording-row.tsx`, `views/recording-detail-view.tsx` and `recording-row.test.tsx`: "Part N".
* `platform/firmware/tests/test_core.cpp` (include path only), `tests/check.ps1`, `platform/api/tests/test_firmware_interop.py` (the matching `-I`).
* Docs: `platform/docs/API.md`, `HARDWARE_ACCEPTANCE_TESTS.md` (section 7), `SECURITY.md`, `platform/firmware/README.md`, root `CLAUDE.md`.

**Removed from the device's workflow** (never carried over): Google speech-to-text, the Gemini summary, every Telegram call and its text splitting and retries, Base64 conversion, the chunked STT buffers and worker task, `setInsecure()` as a default, and every API key, token and password in the source. A search of the firmware, API, website and deployment folders for those names finds them only in the README's comparison table and in one test that checks the old file name is refused.

## 3. The contract the firmware uses

Full request/response tables are in the [sketch's README](../firmware/locket_web_upload/README.md#what-the-locket-sends-and-expects) and [API.md](API.md#locket-api-apidevicev1-bearer-credential). In short: `POST /api/device/v1/pair` once; `POST /api/device/v1/heartbeat` every 30 s; `PUT /api/device/v1/recordings/{clientId}` with the raw `audio/wav` body streamed from the card, `Authorization: Bearer <credential>`, `X-Content-SHA256`, `X-Recorded-At`, `X-Session-Ref`, `X-Sequence`. Success is `201` (stored) or `200` (already stored) **plus** a receipt whose `status`, `clientRecordingId`, `recordingId`, `sha256` and `bytes` the locket checks before it deletes anything.

## 4. How the brief's requirements are met

| Requirement | Where it stands |
| --- | --- |
| Keep the board, pins, button, card wiring, 16 kHz 16-bit mono WAV, PSRAM ring, writer task, retries, remount recovery, byte-count and WAV checks, logs, local page | Kept (`config.h`, `recorder.cpp`, `sdwriter.cpp`, `dashboard.cpp`). **Run in simulation and compiled; not on a board** |
| Remove STT, Gemini, Telegram, Base64 | Done; searched for |
| Unique id and file name per recording; never overwrite; never delete old recordings at the start of a session | Done (`<clipId>.<when>.<part>.<session>.wav`, existence-checked); tested, and the test fails if the old behaviour is put back |
| Upload the finished WAV directly, streamed, never whole in RAM | Done (`ClipStream` reads 4 KB slices through `storage::Reader`) |
| Success means HTTP status **and** a valid receipt; delete only then | Done (`ask::ack_validate`); six scenarios (captive-portal page, wrong receipt, chunked reply, …) keep the file |
| Persistent queue, reboot recovery, bounded back-off with jitter, idempotent retries | Done (the folder is the queue; names carry the id; 2 s to 5 min, jittered, `Retry-After` honoured) |
| Don't block capture with Wi-Fi or uploads; protect the card with a mutex | Done: three tasks; `storage::Lock`; uploads stand down when the ring is over half full. A simulator check counts two tasks on the card at once (zero), and it was shown to notice when the lock is removed |
| Missing card, PSRAM failure, Wi-Fi loss, timeouts, 401/403/413, malformed reply, incomplete WAV, full card, interrupted upload | Each is a test scenario (see section 5) |
| HTTPS with certificate validation; plain http only as an explicit development switch | Done: `/ca.pem` on the card; `ASK_ALLOW_PLAIN_HTTP` and `ASK_INSECURE_TLS` default to 0 and print warnings. **TLS on the chip has never run** |
| Local dashboard distinct from the website; state, microphone/card health, Wi-Fi/server, queue, last upload, last error, firmware version; no secrets; strong configurable AP password | Done; off by default, password generated and shown on serial only, answers only on its own network |
| Website distinguishes states; online prompt from authenticated heartbeats; tenant isolation | Already present; one addition ("Part N"); isolation checked again against the real server |
| Never fake transcripts or successful states | A recording stays `queued` until a worker processes it; the real-server test asserts no snippet exists |
| Report what was not run | Section 6 |

## 5. Test report

All results below are from this computer (Windows 11, Arduino CLI 1.5.2-rc.1 with `esp32:esp32` 3.3.12, a `zig c++` host compiler, Python 3.11 for the API). **No test here ran on an ESP32.**

### Passed

| What | Result |
| --- | --- |
| **Real toolchain build, as shipped** (`esp32:esp32:XIAO_ESP32S3:PSRAM=opi`, `--warnings all`) | `Sketch uses 1143850 bytes (34%) of program storage space. Maximum is 3342336 bytes. Global variables use 55668 bytes (16%) of dynamic memory, leaving 272012 bytes for local variables.` **No warnings.** Compiled and linked; **not flashed**. (Re-run in the third pass, section 9; it was 1,135,146 bytes before the Wi-Fi manager) |
| Same build with every development switch on (plain http, no certificate check, upload only after stop, audio download) | 1,144,674 bytes (34%), globals 57,716 bytes (17%), no warnings (re-run in the third pass; 1,135,986 before). Proves those code paths build; they are off in the shipped config |
| Rule tests, shared half (`tests/test_core.cpp` against `locket_web_upload/core.cpp`; the same file passes against `locket/core.cpp`) | 3,815 checks, 0 failed, built with `-std=c++11 -Wall -Wextra -Werror -pedantic` |
| Rule tests, button-recorder half (`tests/test_web_upload_core.cpp`) | 61,239 checks, 0 failed (670 added in the third pass for the Wi-Fi rules): part names, WAV check, receipt check, time resolving, planning, debounce (bounce, glitch, two presses, counter wrap-around), JSON helpers, **the recording state machine** (all 5 states x 8 events, the full press/stop/save/ready cycle, and 200,000 random events) and the start-up checks |
| **Firmware sources against simulated hardware** (`api/tests/test_firmware_web_upload.py`, 41 scenarios, plus the 14 in `test_firmware_button_workflow.py`: section 8, and the 21 in `test_firmware_wifi.py`: section 9) | All pass. Recording: numbered parts with the audio checked sample by sample across part boundaries and valid to the server's own WAV validator; a new recording never deletes an old one; a tail under half a second dropped and said so; a microphone that will not start or goes quiet; unpaired; card too full to start; card that fills up mid-recording; Wi-Fi down; a server that takes 40 s to answer. Card: zero-length writes retried; remount and carry on with no loss; a remount that loses unflushed data (counted and reported, never hidden); a card that disappears (finished parts kept, partial recovered at the next start); a power cut (only unflushed data lost, the rest recovered and uploaded); two tasks never on the card at once, and that check *does* fire when the firmware's lock is removed. Uploads: the documented request, receipt checked, then deleted; 5xx retried with the same id and stored once; replies lost after the server stored the clip (no second copy); id conflict gets a new id; outage ridden out without a restart; queue survives a reboot; 401 stops uploads, keeps every file and pauses new recordings; 403 retried slowly; 413 and 422 set aside, not deleted, not retried; a captive-portal page, a wrong receipt and a chunked reply never delete; a damaged file set aside unsent; a non-https address and a missing certificate file send nothing; pairing, and a wrong code; a clock not yet set (timed exactly once it is, or kept in `/untimed`, never guessed); the maintenance page (fields, no secrets, 403 off its own network, off by default) and the console (no password echoed) |
| **Firmware upload code against the real API** (`test_firmware_real_server.py`, 3 tests: a real uvicorn server and database over a local socket) | All pass: pairing through the real endpoint; every part accepted and stored in one session in order, 16 kHz mono, audio identical to what was recorded, start times as sent; processing jobs created; recordings stay `queued` with no invented transcript; the device shown online; "Part N" on the website; a second company sees nothing; the same parts sent again are acknowledged `200` with no second row; a revoked locket keeps every file, stops recording and uploads nothing |
| Backend (`tests/test_recording_parts.py` 7, `tests/test_devices.py`) | Pass: parts numbered, a lone clip not called a part, two lockets with the same reference not mixed up, another company sees none, the session filter, `maxClipSeconds` in the config |
| **Whole backend suite** | **656 passed, 55 skipped** (the opt-in real-model and PostgreSQL files), 7 min 39 s on SQLite, with a C++ compiler configured (re-run in the third pass; 635 before it). `ruff check`, `ruff format --check` and strict `mypy` (69 files) clean |
| **Website** | 344 tests pass (34 files), `npm run typecheck` and `npm run lint` clean, `npm run build` succeeds |
| `tests/check.ps1` and `tests/check_web_upload.ps1` | Both run and pass (the second also compiles the shipped and the development variants against the simulated hardware and makes one recording end to end: 3 parts, 3 uploaded, 0 left, 0 bad requests, 0 card-access violations) |
| **Safeguards broken on purpose**, one at a time in a copy of the sketch, to see whether a test notices | **10 of 10 noticed**: delete on a bare 200; delete the queue when a recording starts; retry refused clips for ever; send a damaged file; keep a refused id; delete a part from an earlier start-up; stop counting audio lost across a remount; switch certificate checking off; omit the session header; write to the card without the lock |
| Old services in the firmware | None: a search of `platform/firmware`, `api/app`, `web/src` and `deploy` for Telegram, Gemini, Google speech and the old key names finds them only in the sketch README's comparison table |

### Failed

Nothing fails in the final runs. Problems found and fixed on the way, so the history is not hidden: the first full backend run failed one docs test because new wording in `API.md` looked like a route that does not exist (reworded); the simulator's first button presses were missed because the program pressed before the capture task had started watching, and a simulated bounce was longer than the debounce time (both were mistakes in the simulator, not the firmware; the press is now held long enough in real time and the simulated bounce is short); a first run of the safeguard checks gave two false "not noticed" results because a second copy of the script was running in the same folder (re-run alone: all 10 noticed); three early test expectations were wrong (the fake server's conflict rule, a leftover certificate file, an outage assertion); two build warnings (`++` on a `volatile`) were fixed in the firmware.

### Not run (hardware, or things this machine does not have)

See the next section. In short: **nothing was run on an ESP32.** "Simulated" above means exactly that: stand-ins for the card, microphone, Wi-Fi, HTTP client and RTOS, written for these tests (`platform/firmware/tests/hostsim`), running faster than real time.

## 6. Not run, and open

| Item | Why | What settles it |
| --- | --- | --- |
| **Everything on the board**: flashing, the real microphone level and quality, the real card's speed, a real pull-out or power cut, the pin assignments, the button's real bounce | No board | [HARDWARE_ACCEPTANCE_TESTS.md](HARDWARE_ACCEPTANCE_TESTS.md#7-the-button-recorder-locket_web_upload), HW-70 to HW-7E |
| **TLS on the chip** with a real certificate, its heap and stack use while recording, and whether a 9.6 MB upload completes within the server's 300 s limit on real Wi-Fi | No board, no HTTPS server was put in front of it | HW-79 |
| Whether the card writer keeps up with the microphone at 4 MHz on a real card, and the effect of uploading during a recording | The simulator runs faster than real time and its card has no speed | HW-73, HW-74. If a card is too slow, switch `ASK_UPLOAD_WHILE_RECORDING` to 0 or raise `ASK_SD_SPI_FREQUENCY` after checking the wiring |
| The 24-hour and 8-hour soak | Needs a board | HW-7D |
| Docker, the proxy, real HTTPS, the pairing and upload through the proxy | No Docker here (unchanged from the platform's earlier reports) | [RUNBOOK.md](RUNBOOK.md) |
| The processing worker with real models on audio recorded by this firmware | No real recordings exist; the pipeline was verified earlier on synthetic voices | Record real consented speech, run the worker |

Known limits that follow from the design: speaker labels are not tied across the parts of one recording; a part boundary can fall mid-sentence; up to about 8 seconds of audio can be lost to a power cut (the flush interval); the clock is set only over the network.

## 7. Next steps

1. Buy or borrow a board and a card; build with **PSRAM: OPI**; do HW-70 to HW-72 first (boot, one press, a 12-minute recording checked on a computer).
2. Put the server behind HTTPS, copy its authority's certificate to the card as `/ca.pem`, and do HW-79 (TLS under load) before anything else involving the network.
3. Rotate the secrets that were in the original source (see [SECURITY.md](SECURITY.md)).
4. Decide whether long recordings should be joined for processing so a speaker keeps one label across parts. That is a server-side change and is not done.

## 8. The two-press button workflow (second pass)

A second brief asked for exactly this behaviour: **first press = start recording, second press = stop and queue the audio for upload and server-side transcription, then wait for the next press**, with an explicit state machine and 18 named test cases. The first pass already had most of it, so the code was inspected first and only what was missing was changed.

**Confirmed correct, left alone:** the button is `ASK_PIN_BUTTON 2` (D1 / GPIO2) with `INPUT_PULLUP`, pressed = LOW, released = HIGH, no resistor; 50 ms debounce that reports one event per press (never for a bounce, a release, or a hold); the PSRAM ring, writer task, storage lock, remount recovery, WAV finalization and check, unique names, the persistent queue, recovery of interrupted recordings, the receipt check, back-off, and the independence of uploading from capture.

**Gaps found, and fixed:**

| Gap | Fix |
| --- | --- |
| No single place decided what a press means; state was set from six places | One table, `ask::rec_step` in `core.cpp` (Booting, Idle, Recording, Finalizing, Error x eight events), reached through `status::send`, under a lock. Tested for every state and event and with 200,000 random events |
| A `record` typed on the serial console, or a stale request, could be left pending and start a recording the moment the previous one ended | One `requestPress()` that goes through the same machine as the button, and is dropped if it arrives while saving |
| The button was not looked at at all while the last part was being saved, so a press then could be found afterwards and acted on | The wait for the card now watches the button: a press while `FINALIZING` is logged (`press ignored while FINALIZING`), counted, and thrown away |
| Recording started only after waiting up to 3 s for the button to be let go | Removed: recording starts as soon as the press is recognised, even if the button stays held. A held press cannot be seen again (the debouncer treats the held level as settled) |
| No `RECORDING` / `RECORDING_STOPPED` / `UPLOAD_QUEUED` states in the log, and the "recording started" banner printed before the start had succeeded | `[STATE] IDLE / RECORDING / FINALIZING / ERROR`, `[EVENT] RECORDING_STOPPED`, `[EVENT] UPLOAD_QUEUED`, `[EVENT] UPLOADING`, `[EVENT] UPLOADED`; "recording" is announced only after the card writer and microphone are really running |
| Only free space limited how much could be waiting | A start is refused when too many parts (1000) are already waiting to upload, with a clear message (`ask::start_blocker`, tested) |
| No way to check the wiring from the serial monitor | `button` shows whether the pin reads up (HIGH) or down (LOW) |

**The cases, and the test for each.** All are *simulated*: the sketch's real code against a simulated button on GPIO2 with the pull-up, card, microphone and network. Items marked **real API** also run against a real server on a local socket. None ran on a board.

| # | Case | Test (`button_workflow` = `test_firmware_button_workflow.py`) | Level |
| --- | --- | --- | --- |
| 1 | Power-on leaves the device idle | `button_workflow`: `power_on_leaves_the_locket_idle_and_recording_nothing`; `a_button_held_down_at_power_up_is_not_a_press` | simulated |
| 2 | First press starts exactly one recording | `button_workflow`: `the_first_press_starts_exactly_one_recording...`; unit `test_state_machine_cycle` | simulated, unit |
| 3 | Releasing the button does not stop it | the same test (button held 4 s, let go, still recording, exactly two transitions) | simulated |
| 4 | Second press stops it | `the_second_press_stops_it_and_the_part_is_queued_then_the_locket_is_ready_again` | simulated |
| 5 | Bounce makes no extra transitions | `contact_bounce_makes_exactly_one_transition_per_press`; unit `test_debounce` | simulated, unit |
| 6 | Holding does not toggle repeatedly | `holding_the_button_down_does_not_toggle_repeatedly` | simulated |
| 7 | Finalized WAV: header, duration, audio | the stop test above (exact audio, header sizes, the server's own validator) and `a_recording_is_saved_as_numbered_parts_of_valid_audio` | simulated |
| 8 | The next press starts a new recording with a new id | `the_next_press_starts_a_new_recording_with_its_own_id_and_the_old_one_stays` | simulated |
| 9 | A new recording does not delete the previous one | the same, and `starting_a_new_recording_never_deletes_an_old_one` | simulated |
| 10 | A failed upload preserves the file | `failed_uploads_are_retried...`, `an_outage_is_ridden_out...`, the 401, 403, 413 and 422 tests | simulated |
| 11 | An interrupted upload retries without duplicates | `a_reply_lost_after_the_server_stored_the_clip...` (simulated); `sending_the_same_parts_again_is_acknowledged_and_creates_no_second_copy` (**real API**) | simulated, real API |
| 12 | A valid receipt allows deleting the local copy | `each_part_is_sent_as_documented_confirmed_and_only_then_deleted` | simulated, real API |
| 13 | A malformed receipt does not | `an_answer_that_is_not_the_servers_receipt_never_deletes_the_clip` (a captive-portal page, a wrong receipt, a chunked reply) | simulated |
| 14 | The next recording can start while an upload is pending | `the_next_recording_can_start_while_an_earlier_upload_is_still_in_flight`; `a_locket_with_too_many_recordings_waiting_refuses_a_new_one_clearly` (the limit) | simulated |
| 15 | A transcription failure does not corrupt the audio or stop future recordings | `test_firmware_real_server_workflow.py`: `a_failed_transcription_leaves_the_audio_intact_and_does_not_stop_later_recordings` | **real API** |
| 16 | A missing or full card gives a clear error | `a_missing_memory_card_gives_a_clear_error...`, `without_psram_the_locket_says_so...`, `a_card_with_no_room_is_refused_before_recording`, `a_card_that_fills_up_stops_cleanly...` | simulated |
| 17 | Power interruption keeps everything that was flushed | `a_power_cut_loses_only_what_was_never_flushed` (now exactly the 16 flushed blocks), `a_card_that_disappears...` | simulated |
| 18 | Recordings appear only in the right company | `test_firmware_real_server_workflow.py`: `each_companys_locket_only_ever_fills_its_own_account`; the earlier real-server and API tests | **real API** |

Also tested: presses while the last part is being saved neither start nor stop anything (a deliberately slow card, three more presses); a refused start is recoverable by pairing and pressing again; the pin is GPIO2 with the pull-up (the simulated board has only that wiring: with no pull-up, or another pin, the tests fail).

**Results (this computer).** Real build, as shipped: `Sketch uses 1135146 bytes (33%)... Global variables use 55508 bytes (16%)`, no warnings; with the development switches on, 1,135,986 bytes, no warnings. Rule tests 3,815 and 60,569 checks, 0 failed. Whole backend suite **635 passed, 55 skipped**; `ruff`, `ruff format` and strict `mypy` clean; website 344 tests, lint and types clean (the website was not changed in this pass). Six more safeguards were broken on purpose, one at a time (a press while saving starts a recording; a second press does not stop; a button held at power-up counts; letting go counts; no debounce time; a new recording starts by itself after saving), and **a test failed every time**. One older test (`two_tasks_never_use_the_card_at_the_same_time`) failed in the first full run because it looked for the old log text `[BUTTON] STOP`; it was fixed and re-run.

**What still needs the board:** the button's real contact bounce and wiring (HW-7G), the real feel of a press during saving (HW-7I), and everything in section 6.

**Backend or website changes needed for real transcription and speaker names: none in code.** The website already shows the real processing state, and nothing marks a recording *completed* unless the worker finished it. What is needed is configuration and data: run the worker with `ASK_AI_PROVIDER=local` (with the AI extras and model weights installed) instead of the development stub, whose placeholder text is labelled as such; for named speakers, enroll consenting people and calibrate the match threshold on real consented recordings (the default is uncalibrated); for automatic separation of speakers, either a Hugging Face token for pyannote (never run here) or the experimental clustering separator. Speaker labels are also not tied across the parts of one long recording (section 6). See [AI.md](AI.md).

## 9. A built-in Wi-Fi network, joined at power-on (third pass)

A third brief asked that the locket **join a fixed test hotspot by itself at start-up** (name `ESPTEST`, password `12345678`) so that nobody has to type `wifi` in the serial monitor, **without** printing the password, blocking the recording state machine, bypassing pairing, weakening certificate checking, or reporting an upload that did not happen. This is the same sketch (`locket_web_upload`, XIAO ESP32S3 Sense, PSRAM: OPI); the continuous-clip `locket` sketch is unchanged and still needs the `wifi` command.

### Audit: why a fresh locket never joined Wi-Fi

| Finding | Detail |
| --- | --- |
| **Nothing joined Wi-Fi at start-up** | The only code that called `WiFi.begin` was inside the uploader (`wifiUp()`), run on demand, and it used the network saved in the chip's storage by the `wifi` command. With nothing saved (a freshly flashed chip) it never tried. Pairing also joined from inside the `pair` command |
| **No cause when it failed** | The failure message was a fixed `could not join Wi-Fi`. The reason the chip reports (no such network, password refused, out of range) was dropped, and there was no IP address or signal strength anywhere |
| **Saved settings would have overridden anything built in** | The network came only from storage. A stale test network typed or saved long ago would silently win over a value in `config.h` |
| **Joining blocked the uploader** | A join was a wait of up to 20 s inside the upload task; a slow or absent network held up pairing and the upload loop (not recording: capture was already a separate task) |

### What changed

| File (`platform/firmware/locket_web_upload/` unless stated) | Change |
| --- | --- |
| `config.h` | `ASK_DEFAULT_WIFI_SSID` `"ESPTEST"` and `ASK_DEFAULT_WIFI_PASSWORD` `"12345678"` (overridable with `-D`; both empty = no built-in network), `kWifiRetryBaseMs` 5 s, `kWifiRetryMaxMs` 60 s, `kNetworkStackBytes`. The join time limit stays 20 s |
| `wifi_manager.h`, `wifi_manager.cpp` (**new**) | The Wi-Fi manager: its own task, started in `setup()` before the card or microphone, independent of pairing. Joins, reports, retries with back-off and jitter, notices a dropped link and rejoins. Reads the reason from the chip's disconnect event. Never prints the password. (First called `network.h`; **renamed because on Windows the core's `WiFi.h` includes `Network.h`, which resolved to this file and broke the real build**) |
| `settings.h`, `settings.cpp` | Which network is in use: the built-in one, unless one was typed with `wifi` **on this build**. A build stamp (compile date and time, mixed with the built-in name and password) is saved with the settings; a new build clears an older saved network at its first start and says so. `wifi default` goes back |
| `core.h`, `core.cpp` | Pure, tested rules: `wifi_reason_hint` (the chip's code in words), `wifi_reason_ends_attempt` (which reasons mean this try is over), `wifi_credentials_problem` (a name is 1 to 32 characters, a password 8 to 63 or empty; the words never repeat the input) |
| `uploader.cpp` | No longer joins Wi-Fi. It waits for the manager's link (`wifi_manager::connected()`), and `pair` waits up to 20 s for it. Uploads, receipts and deletion are as before |
| `console.cpp`, `dashboard.cpp` | `status` and the maintenance page show the network, whether it is the built-in one, the IP address, signal strength and the last reason; `wifi default`; the password is never echoed or shown |
| `locket_web_upload.ino` | `wifi_manager::begin()` right after the settings are loaded |
| `README.md` | Setup without the `wifi` command, how Wi-Fi works, the log lines, the plain-text-password warning |
| `tests/hostsim/hostsim.h`, `hostsim_main.cpp`, `tests/test_web_upload_core.cpp`, `tests/check_web_upload.ps1` | The simulated radio (below); unit tests of the three rules; the new module added to the build list |
| `api/tests/test_firmware_wifi.py` (**new**), `api/tests/test_firmware_web_upload.py` | 21 tests of the behaviour below; the shared fixtures gain a build stamp and a check that no Wi-Fi password is ever printed |
| `CLAUDE.md`, `SECURITY.md`, `HARDWARE_ACCEPTANCE_TESTS.md` | The one exception to "no Wi-Fi passwords in a sketch" and its limits; HW-7L to HW-7S for the board |

**Not touched:** the microphone, PSRAM ring, card writer, button wiring, the recording state machine, the audio format, the upload queue and receipt check, the server address and the pairing code. Certificate checking and pairing are unchanged (a test confirms the manager never touches them).

### Behaviour

* At power-on, in the background: `[WiFi] Connecting to ESPTEST...`, then `[WiFi] Connected successfully` and `[WiFi] IP address: … signal strength (RSSI): … dBm`. On failure: `[WiFi] Connection failed: <reason in words> (<chip's name>, code N). Trying again in N s.` The wait is random between half of 5, 10, 20, 40, 60 s and the full value, then stays at 60 s. One try waits at most 20 s, and gives up at once when the chip says the network is not there or the password was refused. A dropped link is reported (`Connection lost: …`) and rejoined.
* **The password is never printed**, in any message, `status`, the maintenance page or an error. Every simulated run is checked for it, and the sources are scanned for any print that could reach it.
* **Recording never waits for Wi-Fi.** The manager is its own task; `[READY]` appears before the network answers; with the network failing, a recording is saved and queued (`UPLOAD_QUEUED`), nothing says uploaded, and it is sent once the network is joined. (A recording made before the clock was ever set, if the locket is restarted before the network is joined, stays in `/untimed`: unchanged behaviour.)
* **Saved settings cannot override the built-in network by accident.** A network typed with `wifi` replaces it until `wifi default` or a new build is flashed. A network saved by an older firmware, or typed on an earlier build, is cleared at the first start of the new build.

### Two things only the real toolchain and the real core's source could show

1. **A header name collision (found by the real build).** The first version of the module was `network.h`. The simulator compiled it; the real build failed, because the ESP32 core's own `WiFi.h` includes `Network.h` and on Windows the sketch folder's `network.h` answered. Renamed to `wifi_manager`.
2. **`WiFi.status()` goes stale (found by reading the core's `STA.cpp`, arduino-esp32 3.3.12).** The core changes the status only when the chip reports an event, and `WiFi.begin()` does not reset it. After one failed try it keeps reading "no network" or "failed", and a join that decides by that status gives up at once on the next try, claiming a failure the chip never reported, and can miss a connection that was succeeding. The first version did this. It now decides a try has failed only from the chip's own disconnect event for that try (and ignores the "leave" the locket causes itself when it drops a link to start another), and checks that the link is to the network asked for. The simulator originally reset the status on `begin()`, which hid the problem; it now behaves like the core, and a test fails if the status is trusted again (see the safeguard checks).

### Results (this computer)

All from this computer (Windows 11, Arduino CLI 1.5.2-rc.1 with `esp32:esp32` 3.3.12, a `zig c++` host compiler, Python 3.11). **Nothing ran on an ESP32.**

| What | Result |
| --- | --- |
| **Real toolchain build, as shipped** (`arduino-cli compile --fqbn "esp32:esp32:XIAO_ESP32S3:PSRAM=opi" --warnings all platform/firmware/locket_web_upload`) | `Sketch uses 1143850 bytes (34%) of program storage space. Maximum is 3342336 bytes.` `Global variables use 55668 bytes (16%) of dynamic memory, leaving 272012 bytes for local variables. Maximum is 327680 bytes.` Exit 0, **no warnings**. Compiled and linked; **not flashed**. This is what confirmed the Wi-Fi event, `disconnectReasonName`, `persistent`, `SSID` and `onEvent` calls against the real 3.3.12 headers |
| Same with every development switch on (`ASK_ALLOW_PLAIN_HTTP`, `ASK_INSECURE_TLS`, upload only after stop, audio download) | `1144674 bytes (34%)`, globals `57716 bytes (17%)`, exit 0, no warnings |
| Rule tests (C++11, `-Wall -Wextra -Werror -pedantic`) | Shared half 3,815 checks, button-recorder half 61,239 checks (670 new: reason wording for every code 0 to 299, which reasons end a try, name and password rules), 0 failed |
| **Wi-Fi behaviour against simulated hardware** (`api/tests/test_firmware_wifi.py`, 21 tests) | All pass: joins `ESPTEST` with no command and no pairing, with the IP and signal strength logged; `[READY]` appears before a slow network answers; a whole recording is made and queued while the network is still joining, then uploaded; no built-in network (says so, still records, a typed one joins); a typed network replaces the built-in one and survives a restart, `wifi default` goes back; a network saved by an older build is cleared and cannot override; a new build clears a typed network; a bad name or password is refused and changes nothing; pairing needs no `wifi` command; a wrong password reports `AUTH_FAIL` on every try with growing waits up to 60 s; recording still works with the network failing, nothing is reported uploaded, and the queue is sent once the password is put right; network absent then present; a join that never answers gives up after the time allowed; a dropped link is noticed and rejoined; `status` and the maintenance page report the network and reason and never the password; the password is in `config.h` only and no print statement can reach it |
| The existing simulated scenarios (43 + 14) and the 5 real-server tests | Still all pass with the Wi-Fi manager in place |
| **Whole backend suite** | **656 passed, 55 skipped** (635 before), 7 min 39 s. `ruff check`, `ruff format --check` and strict `mypy` (69 files) clean |
| `tests/check_web_upload.ps1`, `tests/check.ps1` | Both pass (the first compiles the shipped and the development variants against the simulated hardware and makes one recording end to end: 3 parts, 3 uploaded, 0 left, 0 bad requests, 0 card-access violations) |
| **Safeguards broken on purpose**, one at a time in a copy of the sketch | **8 of 8 noticed**: a try judged by the stale `WiFi.status()`; the password printed with the "Connecting" line; start-up waiting for the network; a stale saved network overriding the built-in one; a typed network ignored; a back-off that never grows; a lost link not reported; "connected" claimed as soon as a join was asked for |
| Website | Not changed, not re-run |

**Problems on the way** (so the history is not hidden): the first host build failed because the Zig compiler refuses `__DATE__`/`__TIME__` (the build stamp can now be named with `-DASK_BUILD_STAMP`); the two source-scan tests first failed on false positives (the hex digits `0123456789abcdef` contain `12345678`; a header comment mentioned `wifiPassword()`); my own test expected a recording made offline to be sent after a restart, but a recording made before the clock was ever set is correctly kept in `/untimed` after a restart (the test now keeps it in one power-on); and the two findings in the section above. The reason shown by `status` was first cleared at the start of every try, which made it flicker; it now stays until a connection succeeds.


### Not done, so not known to work

* **Anything on a board.** Whether the chip joins a real phone or laptop hotspot named `ESPTEST` with this code; how long it takes (HW-7L); real signal strength; the exact reason codes a real access point gives for a wrong password or a 5 GHz-only network; the manager task's stack (`kNetworkStackBytes`, not measured); the radio staying awake with `setSleep(false)`; how a hidden network behaves. All of this is HW-7L to HW-7S in [HARDWARE_ACCEPTANCE_TESTS.md](HARDWARE_ACCEPTANCE_TESTS.md).
* The simulated radio is a model written from the core's source and the chip's documented reason codes; it is not the chip.
* **A plain-text password in the firmware** is a decision for the owner: see [SECURITY.md](SECURITY.md). It is for a throwaway test hotspot.
