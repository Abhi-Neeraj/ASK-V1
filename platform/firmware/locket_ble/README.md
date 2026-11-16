# `locket_ble`: the Bluetooth ASK locket

Firmware for the XIAO ESP32-S3 Sense that records meetings and gives them to the ASK phone app over Bluetooth.
Design: `docs/superpowers/specs/2026-11-14-ble-locket-cloud-pipeline-design.md`; plan:
`docs/superpowers/plans/2026-11-15-firmware-locket-ble.md`.

**Never run on a board.** The pure logic (`ble_core.cpp`, `core.cpp`) is tested on a computer
(`tests/check_ble.ps1`). Everything that touches the radio, the card, the microphone or flash has only been
compiled. Expect fixes on first use; the board checklist is in `platform/docs/HARDWARE_ACCEPTANCE_TESTS.md`.

## What it does

1. **Records** exactly like `locket_web_upload`: press the button to start, again to stop. Audio goes microphone
   -> PSRAM ring -> card, as 16 kHz WAV parts of five minutes in `/pending`. (`recorder`, `sdwriter`, `storage`,
   `status`, `core` are copies of that sketch's files; `storage` adds `readAt`.)
2. **Pairs** with the phone: it advertises its product ID (`ASK-XXXXXX`). The phone reads a fresh 16-byte number
   from PAIR_NONCE, the server emails the person a six-digit code made from it, and the person types the code in
   the phone's Bluetooth pairing box: the locket's passkey is that same code. After the bond, BOND_PROOF shows a
   proof the server checks. After 5 wrong codes in 10 minutes pairing is blocked until the window passes.
   The locket never starts pairing on its own: the phone app's read of BOND_PROOF does, after the code was
   emailed. A bonded phone is asked to re-encrypt as soon as it connects. A device that pairs without the code
   ("Just Works") is disconnected and forgotten, and audio goes only to a link made with the code.
3. **Sends** each meeting as one Bluetooth session: START, then AUDIO frames of 16 ms each (IMA ADPCM, 132 bytes),
   numbered straight across the meeting's parts, then END once the meeting is no longer being recorded. The
   phone's `ACK` of END deletes the meeting from the card; `RESEND` goes back to the frame it names, so a dropout
   costs nothing. If the phone goes quiet for 5 seconds with frames unacknowledged, sending goes back to just
   after its last ACK. A meeting recorded before the clock was set waits for the phone's `TIME` and is renamed
   with its real start; one from an earlier start-up that can no longer be dated is kept under `/untimed`, never
   sent with a made-up date.
4. **Updates** over Wi-Fi only when the phone asks: `WIFI` (stored, never printed), then `OTA version size sha256
   url`. The download must be https (certificates checked against the core's built-in list), the size and
   SHA-256 must match, and a new firmware that does not get as far as starting Bluetooth is rolled back.

## Test PIN (test lockets only)

`ASK_TEST_PASSKEY` in `config.h` is **1234** in this build, by the owner's request: when the phone or browser asks
for the Bluetooth pairing code, type 1234 instead of the emailed code. Anyone nearby who knows it can pair, so set it to
`-1` (the emailed code) for every real locket. Still pair through the ASK website or app, not the phone's Bluetooth
settings: the server records who the locket belongs to only that way.

## Build

Board package `esp32:esp32` 3.3.10 or 3.3.12 (both compile with no warnings), board **XIAO_ESP32S3**, **PSRAM: OPI PSRAM**.

```text
arduino-cli compile --fqbn "esp32:esp32:XIAO_ESP32S3:PSRAM=opi" --warnings all platform/firmware/locket_ble
```

Keep the board package in a short folder on Windows: a deep path breaks the compiler's own include paths.

## Manufacturing (once per locket)

1. On the server: `python -m app.cli register-locket --company-id <UUID> --name "..."` prints the product ID and
   the secret, once.
2. On the locket's USB serial console (115200 baud): `provision <productId> <secretHex>`. A second `provision` is
   refused: the product ID can never change. Restart.

## Serial console

`help`, `status`, `provision`, `record` (one press), `wifi clear`, `forget all` (remove every paired phone),
`reboot`. The secret, the pairing code, the bond proof and the Wi-Fi password are never printed.

## Files

| File | What |
| --- | --- |
| `ble_core.*` | Pure logic: HMAC-SHA-256 pairing code and proof, ADPCM, frames and CRC-32, commands, INFO and STATUS text, frame numbering across parts, wrong-code limit, write-once rule, update check |
| `ble_link.*` | The Bluetooth service, pairing, notifications |
| `sender.*` | Streams meetings from the card; ACK, RESEND, delete after END |
| `ota.*` | Firmware updates over Wi-Fi, and the dispatcher for the phone's commands |
| `identity.*` | Write-once product ID and secret in flash |
| `settings.*` | Boot counters, Wi-Fi for updates |
| `console.*` | Serial commands |
| `uploader.h` | Lets the copied recorder call the sender instead of an uploader |
