# `locket_ble`: the Bluetooth ASK locket

Firmware for the XIAO ESP32-S3 Sense that records meetings and gives them to the ASK phone app over Bluetooth.
Design: `docs/superpowers/specs/2026-11-14-ble-locket-cloud-pipeline-design.md`; plan:
`docs/superpowers/plans/2026-11-15-firmware-locket-ble.md`.

**Never run on a board.** The pure logic (`ble_core.cpp`, `core.cpp`) is tested on a computer
(`tests/check_ble.ps1`). Everything that touches the radio, the card, the microphone or flash has only been
compiled. Expect fixes on first use; the board checklist is in `platform/docs/HARDWARE_ACCEPTANCE_TESTS.md`.

## What it does

1. **Records** exactly like `locket_web_upload`: press the button to start, again to stop. Audio goes microphone
   -> PSRAM ring -> card, as 16 kHz WAV parts in `/pending` (a minute each for the first 30 minutes of a meeting so the audio crosses Bluetooth while people are still talking, then five minutes each). A recording can run for up to 6 hours (`kMaxSessionSeconds`; about 690 MB on the card, so use a card with at least 1 GB free). (`recorder`, `sdwriter`, `storage`,
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

## Test pairing switches (test lockets only)

`config.h` now has `ASK_TEST_PASSKEY -1` (no fixed PIN) and **`ASK_TEST_NO_PASSKEY 1`: the locket pairs with no code
at all and connects directly.** The security review of 2026-11-17 switched the no-code mode off; the owner switched it
back on the same day because the locket should connect directly. While it is on, **anyone within Bluetooth range can
pair with the locket, read its meetings and push it a firmware update.** Use it for a locket on a desk. For any locket
that leaves the desk, set `ASK_TEST_NO_PASSKEY` to `0` (pairing then needs the six-digit code the server emails, which in
development mode only appears in the server's log) and flash again.

In this mode the locket also uses **no encryption and keeps no bonds** (since 2026-11-17 evening): nothing is stored on
the phone or the locket, so the "forget the ASK device in the phone's Bluetooth settings" step is needed at most once,
for a bond an older build made, and never again. The price: audio and commands cross the air unencrypted. The sender
also never has more than `kSendWindowFrames` (96) frames beyond the phone's last confirmation in flight, sends one
frame every 12 ms, and its "the phone went quiet" log line now says what the phone last confirmed, the last frame sent
and how often the Bluetooth stack refused a frame. Not compiled or flashed here.

**Bluetooth buffers and link speed (2026-11-18, from a real log):** a recording showed "the phone went quiet" at frame 33 and
`NimBLE: ble_att_svr_pkt rc=6` (out of memory): the audio frames used up the stack's few small buffers, so the phone's
confirmations could not be received. The locket now hands a frame to the stack only while at least `kMbufReserve` (6)
buffers are free, and asks the phone for a 15 to 30 ms link (`kConnIntervalMin/Max`) because audio needs about 8.2 KB/s and
a slow link carries less. A build that still prints the older "went quiet" line (without "it confirmed ..., last sent ...,
stack refused ...") is NOT this firmware. Not compiled or flashed here.

Every start prints a warning while the no-code mode is on. Pair through the ASK website or app, not the phone's
Bluetooth settings: the server records who the locket belongs to only that way, and it still checks the locket's proof
of the bond. A different mode can be switched on at build time without editing the file, for example
`--build-property "compiler.cpp.extra_flags=-DASK_TEST_NO_PASSKEY=0"` (arduino-cli).

This change (one constant) was **not compiled or flashed here**: there is no C++ toolchain or board on this machine.

## Build

Board package `esp32:esp32` 3.3.10 or 3.3.12 (both compile with no warnings), board **XIAO_ESP32S3**, **PSRAM: OPI PSRAM**.

```text
arduino-cli compile --fqbn "esp32:esp32:XIAO_ESP32S3:PSRAM=opi" --warnings all platform/firmware/locket_ble
```

Keep the board package in a short folder on Windows: a deep path breaks the compiler's own include paths.

## Manufacturing (once per locket)

1. Register the locket on the server: on the website, Lockets, **Register a new locket** (it shows the whole
   `provision` line, once), or `python -m app.cli register-locket --company-id <UUID> --name "..."`.
2. On the locket's USB serial console (115200 baud): `provision <productId> <secretHex>`. While a locket has an
   ID, `provision` is refused. Restart.

### Giving a locket a fresh ID

A locket that already has an ID (see `status`) keeps it until it is wiped. The server never changes or reuses an ID,
so a fresh ID means registering a **new** locket on the server and writing it into this one:

1. Register a new locket on the website (step 1 above) and keep the `provision` line it shows.
2. On the USB serial console, either send the **reset code** (the number set as `ASK_SERIAL_RESET_CODE` in
   `config.h`, sent alone as a line) or type `unprovision <the CURRENT product ID>` (the one `status` shows). Both
   erase the ID, the secret and every paired phone, then restart. The recording parts on the card stay there and go
   to whichever phone pairs next.
3. After the restart: paste the `provision` line from step 1, then `reboot`.
4. **On the phone: forget the locket.** The locket erased its side of the Bluetooth bond, but the phone kept its own.
   In the phone's Bluetooth settings, forget every `ASK-...` device before pairing again, or pairing fails
   (`[BLE] pairing failed: stack status -1` on the serial monitor, or "Some fields are not valid" in the app).

The reset code and `unprovision` were added 2026-11-17 at the owner's request. They work only over the USB cable;
nothing sent over Bluetooth reaches the console. The code is plain text in `config.h` and in the compiled firmware (and
this repository is public), so it protects nothing beyond what holding the cable already allows; set it to `""` to
switch it off. Without it the same result needs the board setting **Erase All
Flash Before Sketch Upload: Enabled** when flashing, which wipes everything the locket stored (Wi-Fi for updates too).
Not compiled or run on a board here (no toolchain or board on this machine).

## Serial console

`help`, `status`, `provision`, `unprovision <current ID>` (erase the ID so a new one can be provisioned), `record` (one
press), `wifi clear`, `forget all` (remove every paired phone), `reboot`. The secret, the pairing code, the bond proof
and the Wi-Fi password are never printed.

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
