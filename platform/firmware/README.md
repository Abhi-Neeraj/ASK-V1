# ASK locket firmware

Three sketches live here:

- `locket`: continuous 10-second clips uploaded to the ASK server over Wi-Fi.
- `locket_web_upload`: the button recorder, uploading to the ASK server over Wi-Fi.
- **`locket_ble`: the Bluetooth locket** (2026-11-15). It records like `locket_web_upload`, pairs with the ASK
  phone app using a code the server emails, streams meetings to the phone, and uses Wi-Fi only for firmware
  updates. See `locket_ble/README.md`. Compiled with no warnings; never run on a board.

> **Two sketches live here.** `locket/` (this page) records continuously in 10-second clips and has no button. **[`locket_web_upload/`](locket_web_upload/README.md) is the button-controlled recorder**: press to start, press again to stop, the recording is saved as parts and uploaded to the ASK server. It is derived from the original single-file recorder (PSRAM ring, writer task, remount recovery) and has none of that recorder's speech-to-text, summary or chat-message code. Both speak the same device API. Use `locket_web_upload` if you want the button.

Firmware for the voice locket: a Seeed Studio **XIAO ESP32S3 Sense** (microphone and memory-card slot on the expansion board). It records speech into short clips on a memory card and uploads each one to the ASK server over Wi-Fi.

> **Status: compiles with the real ESP32 toolchain; never flashed, never run on a device.** There was no board where this was written. The sketch builds for `esp32:esp32:XIAO_ESP32S3` with the Arduino CLI and arduino-esp32 3.3.12 (no warnings with `--warnings all`; 1,074,604 bytes of program storage, 32%, and 55,112 bytes of globals, 16%). That shows the real compiler and headers accept the code and it fits; it does not show it works. The decision rules in `locket/core.cpp` are also compiled and tested on a computer, including against the real server. Everything that touches the board (microphone, card, Wi-Fi, TLS) has never run. [What was and was not checked](#what-was-and-was-not-checked) is spelled out below, and [../docs/HARDWARE_ACCEPTANCE_TESTS.md](../docs/HARDWARE_ACCEPTANCE_TESTS.md) is the checklist for the day a board is available.

Developer documentation: it names parts and libraries. The dashboard never does.

## What it does

1. **Records.** The microphone is read at 16 kHz, 16-bit, mono. Every `chunkSeconds` (the server decides, 10 by default) a clip is finished and filed in `/pending` on the card. Nothing records until the locket is **paired**, has a **card with room**, and has set its **clock** from the network, because a clip's start time can't be corrected afterwards.
2. **Uploads.** Clips are sent oldest first with `PUT /api/device/v1/recordings/{clipId}`, with the SHA-256 the server checks. A clip is **deleted only after the server answers 201 or 200**. Any other answer keeps the file; see the table below.
3. **Reports in.** A heartbeat goes to the server every `heartbeatIntervalSeconds` (30 by default) with the firmware version, how many clips are waiting and, if a battery divider is wired, the battery level. That is the only thing the dashboard uses to say a locket is online.
4. **Survives power loss.** A clip being written has a placeholder header. At the next start, any half-written clip is given a correct header for the whole audio in it and queued. Names on the card carry the clip id and start time, so there is no index to lose and nothing to rebuild.

### What the locket does with each server answer

Decided in `core.cpp` (`classify_upload`), tested against the server.

| Server answer | The locket |
| --- | --- |
| `201` stored, `200` already stored | Deletes its copy |
| `409 recording_conflict` | Gives the clip a new id and sends it again |
| `422 checksum_mismatch` (bytes arrived damaged) | Keeps the file, retries |
| `422` or `413` for audio the server will never take (`invalid_audio`, `truncated_audio`, `unsupported_audio_format`, `unsupported_sample_rate`, `audio_too_short`, `audio_too_long`, `silent_audio`), `415`, `validation_error` | Moves it to `/rejected` and carries on. It is **kept, not deleted**, and not retried |
| `401` | Stops uploading and keeps every file. The locket must be paired again. It asks again every five minutes |
| `408`, `429`, `5xx`, no answer | Keeps the file, waits (exponential back-off with jitter, up to 5 minutes; a `Retry-After` is honoured up to an hour), retries |

## Hardware

| Part | Notes |
| --- | --- |
| XIAO ESP32S3 Sense | The PDM microphone is on GPIO 42 (clock) and 41 (data); the card slot's chip select is GPIO 21. **Check these against your board revision's schematic**; they are set in `locket/config.h`. |
| microSD card, FAT32 | Needs room: about 2 MB per minute of audio waiting to be sent. Recording pauses below 8 MB free. |
| Battery (optional) | The board has no battery gauge. Wire a divider to an analogue pin and set `ASK_PIN_BATTERY_ADC` and `ASK_BATTERY_DIVIDER_RATIO`; otherwise no level is reported. The percentage curve is a typical lithium-cell one, not measured for your cell. |

## Build and flash

Not done by the author. The sketch is plain Arduino and should build with either route.

1. Install the **esp32** board package by Espressif in the Arduino IDE (version 3.x; the sketch uses the `ESP_I2S` library that ships with it), and select **XIAO_ESP32S3**. In Tools set **PSRAM: OPI PSRAM** only if you need it (this firmware does not).
2. Open `locket/locket.ino` and upload. With `arduino-cli`:

   ```text
   arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 platform/firmware/locket
   arduino-cli upload  --fqbn esp32:esp32:XIAO_ESP32S3 -p <port> platform/firmware/locket
   ```

3. Put a card in. For a server on `https://` with a certificate that the board doesn't already trust, copy the certificate authority's certificate (PEM) to the card as `/ca.pem`.

## Set up a locket (over the USB cable)

Open a serial monitor at **115200 baud** and type:

```text
wifi "My Network" my-password
server https://ask.example.com
pair ABCDE-FGHIJ
status
```

The pairing code comes from the dashboard (**Devices → Pair a locket**) and works once. `status` shows what it's doing and never prints the Wi-Fi password or the credential. `unpair` forgets the credential but keeps the clips on the card; `reboot` restarts. The credential is stored in the chip, not on the card, so swapping the card never unpairs it. Anyone holding the cable can use these commands: that is the same trust as holding the locket.

## Safety settings in `config.h`

| Setting | Default | Why |
| --- | --- | --- |
| `ASK_ALLOW_PLAIN_HTTP` | `0` | Plain `http://` sends the credential and the audio in the clear. Turn on only on a trusted development network. |
| `ASK_INSECURE_TLS` | `0` | `1` skips checking the server's certificate. Development only. With `0` and no `/ca.pem`, the locket refuses to connect rather than guess. |
| `kSoftwareGainShift` | `0` | The microphone is quiet. Each step doubles the level. Listen to a clip and adjust. |

Both warnings print at every start if switched on.

## What was and was not checked

**Checked on a computer** (`tests\check.ps1`, or `pytest platform/api/tests/test_firmware_interop.py` with a compiler):

- `core.cpp`, the pure logic, builds with strict warnings (`-Wall -Wextra -Werror -pedantic`, C++11) and passes 3,800+ checks, including with undefined-behaviour checking on: the WAV header, SHA-256 (published test vectors, and agreement with Python's `hashlib` at the sizes where padding changes), timestamps (against Python's `datetime`), clip ids, retry timing, battery curve, the small JSON reader, and the table of server answers above.
- **Against the real server code:** the clips it writes pass the API's own audio validation, a clip recovered after a simulated power cut is accepted, and a real upload through the device endpoint with the locket-computed hash returns `201`, a repeat returns `200`, and a wrong hash returns `422 checksum_mismatch`.
- Every source file compiles with `-Wall -Wextra` against **stand-in** Arduino headers (`tests/stubs`). This catches mistakes between the sketch's own files. The stand-ins were written from memory, so they prove little about the real libraries; they were tightened after the comparison below (they no longer declare `esp_random()` in `Arduino.h`, because the real one does not).
- **Compared by hand with the real headers.** The arduino-esp32 3.3.12 headers for `ESP_I2S`, `SD`/`FS`, `HTTPClient`, `NetworkClientSecure`/`WiFiClientSecure`, `Preferences` and `Arduino.h` (`configTime`) were read and every call the sketch makes was matched to a signature: `I2SClass::setPinsPdmRx` and `begin(I2S_MODE_PDM_RX, ...)`, `readBytes`, `SD.begin(cs)`, `File::name()` (the bare name in 3.x), `seek`, `write`, `openNextFile`, `totalBytes`/`usedBytes`, `HTTPClient::begin(client, url)`, `sendRequest("PUT", Stream*, size)`, `collectHeaders`/`header`, `setCACert`/`setInsecure`, and the `Preferences` getters and setters. One mistake was found and fixed: `esp_random()` is declared in `esp_random.h`, which neither `Arduino.h` nor `esp_system.h` includes (ESP-IDF 5.5), so `settings.cpp` and `uploader.cpp` would not have built. A comparison on paper is not a build, so the next item was done too.
- **Built with the real toolchain.** Arduino CLI 1.5.2-rc.1 with the `esp32:esp32` core 3.3.12 compiled and linked the whole sketch for `esp32:esp32:XIAO_ESP32S3` (`arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 --warnings all locket`): it succeeded with no warnings, using 1,074,604 bytes of the 3,342,336 available for the program (32%) and 55,112 of 327,680 bytes for global variables (16%, leaving 272,568). A copy with the battery pin on pin 2 and plain HTTP and insecure TLS switched on (the development setup) also built without warnings. This was done once the owner allowed the Arduino CLI to run from a scratch folder; nothing was flashed.

**Not checked, so not known to work:**

- Memory **at run time**: the build reports static use only. Stack sizes of the tasks, heap use during a TLS upload and fragmentation after hours of running are unmeasured; those are the likeliest first-run problems, along with anything that behaves differently from its header.
- Anything running **on the board**: the microphone's real signal level and gain, the card's speed and reliability, the pin assignments, TLS with real certificates and its memory use, Wi-Fi behaviour, task stack sizes, power use and battery life.
- Behaviour across a real power loss. The recovery logic is tested; the card's behaviour when power is cut mid-write is not.
- Over-the-air updates, deep sleep and a hardware watchdog: none are implemented.
- A hardware **button**, LED or other indicator: none are implemented, so the only feedback is the serial monitor.

## Known limits

- **The clock** is set over the network each boot (the board's clock does not survive power-off), so a locket powered up away from its Wi-Fi records nothing until it can reach the network once.
- A half-written final sample can remain after a clip's audio; the card library cannot shorten a file and the server ignores it.
- Rejected clips (`/rejected`) pile up on the card if something is wrong. Nothing prunes them, on purpose, so they can be examined.
- The Wi-Fi password and credential are stored unencrypted in the chip's storage. Anyone with the device and a cable can read them. Enabling the board's flash encryption is the way to change that and is not done here.
- Voice and consent: a locket records whatever the microphone hears. Making sure the people recorded know and agree is the company's responsibility, and the dashboard records consent per person.
