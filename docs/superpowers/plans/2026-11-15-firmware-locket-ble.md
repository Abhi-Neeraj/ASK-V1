# Firmware: Bluetooth locket (`locket_ble`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new sketch, `platform/firmware/locket_ble`, for the XIAO ESP32-S3 Sense: it records meetings to the card exactly like `locket_web_upload`, and instead of uploading over Wi-Fi it pairs with the ASK phone app over Bluetooth (emailed code), streams the recordings to the phone, and uses Wi-Fi only to install firmware updates.

**Architecture:** Reuse the recording path of `locket_web_upload` unchanged (button and microphone `recorder`, PSRAM ring and card `sdwriter`, card `storage`, `status` state machine, `core` rules). Replace its uploader, Wi-Fi manager and web dashboard with: `ble_core` (pure, host-tested: ADPCM encoder, binary frames with CRC-32, HMAC-SHA-256 pairing code and bond proof, command parser, INFO/STATUS text, session position map), `identity` (write-once product ID and secret in flash), `ble_link` (the GATT service, pairing, notifications), `sender` (streams finished parts of a meeting as one Bluetooth session; ACK, RESEND, delete after END is acknowledged), `ota` (Wi-Fi download with SHA-256 check into the spare app slot) and a smaller `console`.

**Tech Stack:** Arduino-ESP32 core 3.3.12 (`esp32:esp32:XIAO_ESP32S3:PSRAM=opi`), its BLE library, `Update`, `HTTPClient`/`WiFiClientSecure`; host tests with g++ (C++17).

**Spec:** `docs/superpowers/specs/2026-11-14-ble-locket-cloud-pipeline-design.md` (5.1, 5.2, 5.3, 5.5). Must match the server (`platform/api/app/security/locket_codes.py`) and the app (`platform/web/src/domain/locket`, `src/domain/device/framing.ts`).

## Global Constraints

- UUIDs, characteristic access, INFO/STATUS formats, CONTROL commands and frame format exactly as spec 5.3. AUDIO payload: 132 bytes (`predictor` int16 LE, `stepIndex` u8, 0, then 128 bytes of IMA ADPCM nibbles, low first), standard IMA tables.
- Pairing code = `uint32_be(HMAC-SHA256(secret, "ASK-PAIR" + nonce)[0:4]) % 1000000`; bond proof = hex of `HMAC-SHA256(secret, "ASK-BONDED" + nonce)`. Secret = 32 bytes. Nonce = 16 random bytes, new at boot, after every pairing attempt and every 10 minutes. At most 5 wrong passkeys per 10 minutes.
- Product ID and secret are written once with `provision <productId> <secretHex>`; a second `provision` is refused. Never print or log the secret, the pairing code, a Wi-Fi password or the proof.
- One Bluetooth session per meeting (the `sdwriter` session ref, shortened to 16 characters); frame sequence numbers run across all its parts; END is sent when the meeting's last part is on the card; the meeting's files are deleted only when an `ACK` covers the END frame.
- No Wi-Fi network or password in the sketch. The test hotspot exception of `locket_web_upload` does not carry over.
- Firmware update: refused while recording; size and SHA-256 checked before the new slot is activated; plain `http://` only when `ASK_ALLOW_PLAIN_HTTP` is set (development).
- Everything that touches the radio, the card, the microphone or flash has never run on a board here: say so in every file header and doc.

## Review Focus

- The phone disconnects in the middle of a meeting and reconnects: sending must resume from the phone's `RESEND`, not from zero, and the frames must decode to the same audio. → Task 1 test `resend_maps_back_to_the_same_bytes`.
- A meeting longer than one card part: sequence numbers must continue across part files with no gap or repeat. → Task 1 test `session_map_spans_parts`.
- Someone tries passkeys on a locket: after 5 wrong ones in 10 minutes pairing must stop until the window passes. → Task 1 test `passkey_attempts_are_limited`.
- `provision` typed twice, or with a malformed ID or secret: the stored identity must never change. → Task 1 test `identity_is_write_once`.
- A firmware download that is short, too long or has the wrong checksum must never be activated. → Task 1 test `ota_check_refuses_bad_images`.

---

### Task 1: `ble_core` (pure logic) with host tests and shared vectors

**Files:**
- Create: `platform/firmware/locket_ble/ble_core.h`, `ble_core.cpp`; `platform/firmware/tests/test_ble_core.cpp`; `platform/firmware/tests/check_ble.ps1`
- Copy unchanged into `locket_ble/`: `core.h`, `core.cpp` (for `Sha256`, `hex_encode`, WAV helpers)

**Interfaces (namespace `askble`):**
- `void hmac_sha256(const uint8_t* key, size_t key_len, const uint8_t* msg, size_t msg_len, uint8_t out[32])`
- `uint32_t pairing_code(const uint8_t secret[32], const uint8_t nonce[16])` (0..999999) and `void bond_proof_hex(const uint8_t secret[32], const uint8_t nonce[16], char out[65])`
- `struct AdpcmState { int16_t predictor; uint8_t index; }`; `void adpcm_encode_block(const int16_t samples[256], AdpcmState* state, uint8_t out[132])`
- `uint32_t crc32(const uint8_t* data, size_t len)`; `size_t frame_encode(uint8_t type, const char session_id[16], uint32_t seq, uint64_t ts_ms, const uint8_t* payload, size_t len, uint8_t* out, size_t cap)` (types: START 2, AUDIO 3, END 4)
- `bool hex_decode(const char* hex, uint8_t* out, size_t out_len)`, `bool hex_text(const char* hex, char* out, size_t cap)`
- `struct Command { enum Kind { Invalid, RecStart, RecStop, Ack, Resend, Time, Wifi, WifiClear, Ota, Forget } kind; char session[17]; uint32_t seq; uint64_t number; char text1[129]; char text2[129]; char sha256[65]; char version[24]; uint32_t size; }`; `Command parse_command(const char* line)`
- `int info_text(char* out, size_t cap, const char* product_id, const char* version, int battery, bool bonded)`; `int status_text(char* out, size_t cap, const char* state, int battery, const char* session, uint32_t last_seq, int ota_pct)`
- `struct SessionMap { uint32_t parts; uint32_t blocks[64]; }`; `uint32_t session_total_blocks(const SessionMap&)`; `bool session_locate(const SessionMap&, uint32_t seq, uint32_t* part, uint32_t* block)` (seq 1 = part 0 block 0)
- `bool session_id_make(const char* session_ref, char out[17])` (stable 16 characters `[A-Za-z0-9_-]`)
- `struct PasskeyGuard { uint32_t window_start_ms; uint8_t failures; }`; `bool passkey_allowed(PasskeyGuard*, uint32_t now_ms)`; `void passkey_failed(PasskeyGuard*, uint32_t now_ms)` (5 per 600000 ms)
- `bool product_id_valid(const char*)` (`ASK-` + 6 of `ABCDEFGHJKMNPQRSTUVWXYZ23456789`)
- `bool ota_image_ok(uint32_t expected_size, const char expected_sha_hex[65], uint32_t got_size, const uint8_t got_sha[32])`

- [ ] **Step 1: Failing tests** (`tests/test_ble_core.cpp`, plain asserts, prints a summary like `check.ps1` does):
  - `pairing_vectors`: the three server vectors (`platform/api/tests/test_locket_codes.py`): codes 403836, 400246, 778647; proofs `055ce7fe…e21e` and `d1cfced0…85ea`.
  - `adpcm_known_block`: encoding the samples 11, 41, 104, 240, 533, 1164, 2521, 5431 from state {0,0} gives code 7 for each (the app's `decodes_a_known_block` vector); round trip of a 1 kHz sine within 2% after the first block (decoder in the test).
  - `frame_vector`: `frame_encode(AUDIO, "SESSION000000001", 5, 1763114400000, {1,2,3})` equals the bytes the app's `encodeFrame` produces (hex generated once with Node and pasted).
  - `commands_parse`: every command of spec 5.3, hex arguments decoded, malformed lines `Invalid`.
  - `info_and_status_text`, `session_map_spans_parts`, `resend_maps_back_to_the_same_bytes`, `passkey_attempts_are_limited`, `identity_is_write_once` (on `product_id_valid` + a `identity_accept(current_set, id, secret_hex)` helper), `ota_check_refuses_bad_images`.
- [ ] **Step 2:** `.\tests\check_ble.ps1 -Compiler g++` — FAIL (missing symbols). **Step 3:** implement. **Step 4:** PASS. **Step 5:** record.

### Task 2: Sketch skeleton: reused recording path, identity, settings, console

**Files:**
- Create: `locket_ble/locket_ble.ino`, `config.h` (pins and audio constants copied; no Wi-Fi defaults; `ASK_FIRMWARE_VERSION "1.0.0-ble"`; `ASK_ALLOW_PLAIN_HTTP 0`), `identity.h/.cpp` (Preferences namespace `ask-id`: `begin()`, `provisioned()`, `productId()`, `secret(uint8_t[32])`, `provision(id, secretHex, String* problem)`), `settings.h/.cpp` (boot counters, Wi-Fi for OTA, last nonce time), `console.h/.cpp` (`help`, `status`, `provision`, `record`, `wifi clear`, `forget all`)
- Copy from `locket_web_upload` and adapt only includes: `recorder`, `sdwriter`, `storage`, `status`, `core`

- [ ] Steps: write; `arduino-cli compile --fqbn "esp32:esp32:XIAO_ESP32S3:PSRAM=opi" --warnings all platform/firmware/locket_ble` — compiles with no warnings; record size.

### Task 3: Bluetooth service and pairing

**Files:** Create `locket_ble/ble_link.h/.cpp`.

**Interfaces:** `ble_link::begin(productId)`, `ble_link::connected()`, `ble_link::notifyAudio(const uint8_t*, size_t) -> bool`, `ble_link::notifyStatus(const char*)`, `ble_link::setCommandHandler(void(*)(const char*))`, `ble_link::forgetCurrent()`, `ble_link::forgetAll()`, `ble_link::tick()` (rotates the nonce every 10 minutes when no one is pairing).

- Advertise the service UUID and the product ID as the name. INFO and PAIR_NONCE readable without encryption; CONTROL write, BOND_PROOF read, STATUS and AUDIO notify only encrypted and authenticated. Security: LE Secure Connections, IO capability Display Only, static passkey = `pairing_code(secret, nonce)`, bonding on. On every authentication result: rotate the nonce (and the passkey); on failure count it in `PasskeyGuard` and refuse new pairing while blocked; on success keep the nonce it used for BOND_PROOF. MTU 185 requested.
- [ ] Steps: implement against the core's BLE headers (read them first in the installed core), compile with no warnings.

### Task 4: Sending meetings to the phone

**Files:** Create `locket_ble/sender.h/.cpp`; modify `storage` only if a helper is missing (list pending parts of one session, sorted by part).

- A FreeRTOS task: picks the oldest meeting with parts in `pending`; builds its `SessionMap` from the part files; sends START (seq 0), then AUDIO frames encoded from the WAV data (zero-padding the last block of each part), then END once the meeting is no longer the one being recorded. One frame every `kSendIntervalMs` (8 ms, twice real time) while connected; pauses when disconnected. `RESEND s n` seeks with `session_locate`; `ACK s n` covering END deletes the meeting's files. STATUS is notified every 2 seconds and on every state change.
- [ ] Steps: implement; compile with no warnings.

### Task 5: Firmware update over Wi-Fi

**Files:** Create `locket_ble/ota.h/.cpp`.

- `WIFI` stores the network (Preferences, never printed); `OTA v size sha url` (refused while recording or while another update runs): stop sending, join Wi-Fi, HTTPS GET with the core's CA bundle (`http://` only with `ASK_ALLOW_PLAIN_HTTP`), stream into `Update` while hashing, `ota_image_ok` before `Update.end(true)`, report percent on STATUS, restart. After a restart into new firmware, `esp_ota_mark_app_valid_cancel_rollback()` once Bluetooth is up.
- [ ] Steps: implement; compile with no warnings.

### Task 6: Docs and full check

**Files:** `platform/firmware/locket_ble/README.md`, `platform/firmware/README.md`, `platform/docs/HARDWARE_ACCEPTANCE_TESTS.md` (a Bluetooth section: pairing with the emailed code, wrong code, reconnect mid-meeting, long meeting, update), `platform/docs/SECURITY.md` (passkey limit, write-once identity), `CLAUDE.md`.

- [ ] `.\tests\check_ble.ps1 -Compiler g++` passes; `arduino-cli compile ... locket_ble` passes with no warnings (report program and memory use); the existing `check.ps1` and `check_web_upload.ps1` still pass.
