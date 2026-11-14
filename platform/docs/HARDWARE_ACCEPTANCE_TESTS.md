# Locket hardware acceptance tests

**Status: the board checks in this document have not been done. No locket, no microphone, no memory card and no
board was available when the platform was built, so the firmware has never run on a device.** The one check that
does not need a board, **HW-00 (the sketch compiles)**, was done and is recorded below. This is the checklist for
the day you have a board. A test is only "passed" when you have run it on the board and written down what you saw.
Until every test in sections 1 to 6 has passed, do not describe the locket as working.

What *has* been checked without a board is listed in [firmware/README.md](../firmware/README.md#what-was-and-was-not-checked)
and again at the end of this page. The **button recorder** (`firmware/locket_web_upload`) has its own checks in
[section 7](#7-the-button-recorder-locket_web_upload).

## What you need

| Item | Notes |
| --- | --- |
| Seeed Studio **XIAO ESP32S3 Sense**, with the Sense expansion board attached | The microphone and the card slot are on the expansion board. Seat it firmly: a loose one gives "microphone is not responding" or "no memory card". |
| USB-C data cable | A charge-only cable shows no serial port. |
| microSD card, **FAT32**, 32 GB or smaller | A new, empty one. Format it on a computer first. |
| A 2.4 GHz Wi-Fi network | The board cannot join 5 GHz. |
| A computer running the platform, reachable from the locket | See "Make the server reachable" below. Never expose a development server to the internet. |
| Arduino CLI (or the Arduino IDE) with the **esp32 board package by Espressif, 3.3.x** | The firmware was compared with the 3.3.12 library headers (ESP_I2S, SD, FS, HTTPClient, NetworkClientSecure, Preferences). |
| Optional: a multimeter, a stopwatch, a USB power meter | For the battery and timing tests. |

Wiring assumptions (set in `locket/config.h`, check them against your board revision's schematic):

| Signal | Pin |
| --- | --- |
| PDM microphone clock | GPIO 42 |
| PDM microphone data | GPIO 41 |
| microSD chip select | GPIO 21 (the card shares the default SPI bus: SCK GPIO 7, MISO GPIO 8, MOSI GPIO 9) |
| Battery sense (optional) | none by default (`ASK_PIN_BATTERY_ADC = -1`); wire a divider and set the pin and ratio to report a level |

## Make the server reachable

Pick one. Both keep the server on your own network.

**A. HTTPS with your own certificate authority (the safe default).** Put the server behind HTTPS (the Compose
stack's proxy does this; see [RUNBOOK.md](RUNBOOK.md)), copy the authority's certificate in PEM form to the card
as `/ca.pem`, and leave `ASK_ALLOW_PLAIN_HTTP` and `ASK_INSECURE_TLS` at `0`.

**B. Plain HTTP on a trusted test network (development only).** In `locket/config.h` set `#define ASK_ALLOW_PLAIN_HTTP 1`,
rebuild, and start the API listening on the network (this also sends the credential and audio unencrypted, so use
a network you control and switch it back before any real use):

```powershell
cd platform\api
.\.venv\Scripts\python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

Allow it through the firewall for the private network only. Use the computer's address (for example
`http://192.168.1.20:8000`) as the locket's server address. The dashboard still runs at `http://localhost:3000`.

## 0. Build and flash

```powershell
arduino-cli core update-index
arduino-cli core install esp32:esp32
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 platform\firmware\locket
arduino-cli board list                                   # find the port, for example COM5
arduino-cli upload  --fqbn esp32:esp32:XIAO_ESP32S3 -p COM5 platform\firmware\locket
arduino-cli monitor -p COM5 -c baudrate=115200
```

If the compile fails after you change something, write the error down, fix the source, and re-run
`firmware/tests/check.ps1` before building again. (One mistake of this kind, a missing `#include <esp_random.h>`,
was found by comparing the sources with the real library headers and is fixed.)

| ID | Test | Expected | Result |
| --- | --- | --- | --- |
| HW-00 | The sketch compiles for `esp32:esp32:XIAO_ESP32S3` with no errors | `Sketch uses ... bytes`; write down the size and free RAM | **Done, passed (no board needed).** Arduino CLI 1.5.2-rc.1, `esp32:esp32` core 3.3.12, `--warnings all`: `Sketch uses 1074604 bytes (32%) of program storage space. Maximum is 3342336 bytes. Global variables use 55112 bytes (16%) of dynamic memory, leaving 272568 bytes for local variables. Maximum is 327680 bytes.` No warnings. Re-run it after any change to the sources. Static figures only: run-time stack and heap use is unmeasured. |
| HW-01 | Flash succeeds and the monitor shows the banner | `ASK locket firmware 0.1.0`, then lines about the card and microphone | |

## 1. Hardware: microphone, card, power

Open the serial monitor at **115200 baud**. Type `help` for the commands. `status` prints, among other lines,
`recording` (what the recorder is doing) and `last problem`.

| ID | Test | How | Expected | Result |
| --- | --- | --- | --- | --- |
| HW-10 | Card is found | Boot with a FAT32 card in | No "No usable memory card" line. `status` does not say "no memory card" | |
| HW-11 | No card is reported, not crashed | Boot with no card | `No usable memory card...`; `status` says `recording  no memory card`; the board keeps running | |
| HW-12 | Folders are made | Remove the card after HW-10 and look on a computer | `/recording`, `/pending` and `/rejected` exist | |
| HW-13 | Microphone starts | Boot (paired, with a card, Wi-Fi set; see section 2) | No "The microphone did not start." line | |
| HW-14 | **A clip is a valid WAV** | Let a clip finish; move the card to a computer; run `python platform\api\tools\inspect_clip.py <folder with the .wav files>` | `1 ch 16-bit 16000 Hz`, about 10.0 s, `OK` | |
| HW-15 | **The microphone hears speech** | Speak normally at arm's length for a clip, then inspect it | Peak above -30 dBFS, not clipped, and **you can understand it when you play it**. If it is too quiet, raise `kSoftwareGainShift` (each step doubles the level) and repeat | |
| HW-16 | Silence is silent | Record in a quiet room without speaking | Average level well below the speech clip; no loud clicks | |
| HW-17 | Clip names are unique across restarts | Record three clips, reboot, record three more | Six different names of the form `<id>.<unix time>.wav`; none overwritten | |
| HW-18 | **A full card stops recording cleanly** | Fill the card to within 8 MB with another file | `recording  memory card is full`; no half-written clip is left in `/pending`; recording resumes after you free space | |
| HW-19 | **Power cut mid-clip** | While `recording  recording`, pull the USB cable. Power up again | The half-written clip is finished at boot (a correct header for the audio in it) and moved to `/pending`; it plays on a computer and uploads | |
| HW-1A | A card pulled while recording | Pull the card mid-clip, put it back, reboot | No crash; the partial clip is recovered or removed; recording resumes | |

## 2. Wi-Fi, clock, pairing

(This section is for the continuous-clip `locket` sketch, which needs the `wifi` command. The button recorder `locket_web_upload` joins its built-in network by itself: see HW-7L to HW-7S.)

Type these in the monitor (the Wi-Fi password is never echoed):

```text
wifi "My Network" my-password
server https://ask.example.com          (or http://192.168.1.20:8000 for option B)
pair ABCDE-FGHIJ                        (a code from the dashboard: Devices, Pair a locket; it works once and expires after 10 minutes)
status
```

| ID | Test | Expected | Result |
| --- | --- | --- | --- |
| HW-20 | Joins Wi-Fi | `status`: `wifi  connected (<name>)` | |
| HW-21 | Wrong password / network out of range | `last problem  could not join Wi-Fi`; the board keeps retrying with back-off; recording continues if already paired | |
| HW-22 | Clock is set from the network | `status`: `clock  set`. Recording starts only after this | |
| HW-23 | **Pairing works once** | `pair <code>` prints `OK: paired as ASK-...`. The dashboard shows the locket | |
| HW-24 | A used or wrong code is refused | Repeat `pair` with the same code, and with a wrong one | `Failed: the server did not accept that code (...)`; nothing is stored | |
| HW-25 | The credential is not shown | Read the whole serial log of the session | The Wi-Fi password and the locket's credential appear **nowhere** | |
| HW-26 | `unpair` keeps clips | `unpair`; look at `/pending` | Clips are still on the card; recording stops (`waiting to be paired`) | |
| HW-27 | A revoked locket stops, keeps its clips | Revoke it in the dashboard (Devices, Revoke) | `status`: `paired yes (the server refused the credential: pair again)`; uploads stop; files stay in `/pending` | |

## 3. Heartbeat and live status

| ID | Test | Expected | Result |
| --- | --- | --- | --- |
| HW-30 | **Online within one heartbeat** | The dashboard shows **Online** within about 30 s of pairing, and a connection prompt appears for an authorised user | |
| HW-31 | Firmware version and queue depth are reported | Devices page shows `0.1.0` and the waiting count | |
| HW-32 | **Offline when it stops** | Unplug the locket. The dashboard shows **Offline** after about 90 s (no reload) | |
| HW-33 | Reconnects after a network break | Turn the Wi-Fi router off for two minutes, then on | The locket returns to **Online** by itself; no re-pairing | |
| HW-34 | Battery (only if you wired a divider) | Compare the dashboard percentage with a multimeter reading | Within a few percent; otherwise the curve in `core.cpp` needs adjusting for your cell | |

## 4. Upload, acknowledgement and retry

| ID | Test | Expected | Result |
| --- | --- | --- | --- |
| HW-40 | **Clip → server → deleted from the card** | A clip is uploaded; after the server answers 201 the file leaves `/pending`. The recording appears in the dashboard with the right device and wearer | |
| HW-41 | Audio on the server is the audio recorded | Play the dashboard recording and a copy of the clip from the card | Same speech; same length | |
| HW-42 | **Server unreachable: clips are kept** | Stop the API for two minutes while recording | Clips pile up in `/pending`; `status` shows `waiting` rising and a `last problem` about the server; nothing is deleted | |
| HW-43 | **Back-off, then catch-up** | Start the API again | Uploads resume oldest first, with growing waits (about 2 s up to 5 minutes) between failures; every clip arrives **once** | |
| HW-44 | **Wi-Fi lost mid-upload** | Turn Wi-Fi off during an upload | The file is still on the card afterwards; the retry stores one recording, not two | |
| HW-45 | Duplicate protection | Reboot the locket immediately after an upload succeeds but before it deleted the file (hard to time: a power cut during the upload does it) | One recording on the server; the locket removes its copy after the server answers 200 (already stored) | |
| HW-46 | A damaged upload is caught | (Hard on hardware. Covered on a computer by `tests/test_firmware_interop.py`.) Skip unless you can corrupt a file | `422 checksum_mismatch`: the clip is kept and retried | |
| HW-47 | A clip the server will never take | Copy a silent WAV with a valid name into `/pending` | It is moved to `/rejected` (kept, not deleted) and the others carry on | |
| HW-48 | Rate limiting is honoured | (Optional) | A `429` with `Retry-After` makes the locket wait that long | |

## 5. Security checks

| ID | Test | Expected | Result |
| --- | --- | --- | --- |
| HW-50 | **The certificate is checked** | With `/ca.pem` for the right authority, uploads work. Replace it with a different authority's certificate | Uploads **fail** with `last problem ... could not reach the server` or a TLS error; no audio is sent | |
| HW-51 | No `/ca.pem` and HTTPS | `last problem  no certificate authority file (/ca.pem) on the memory card`; nothing is sent | |
| HW-52 | Plain HTTP is refused by default | Set a server address that starts with `http://` in a build with `ASK_ALLOW_PLAIN_HTTP 0` | `Plain http:// is switched off ...`; address not saved | |
| HW-53 | The warnings print when the unsafe switches are on | Build with `ASK_INSECURE_TLS 1` or `ASK_ALLOW_PLAIN_HTTP 1` | A WARNING line at every boot | |
| HW-54 | Serial access is the same trust as holding the locket | Note that anyone with the cable can read the stored Wi-Fi password and credential (they are not encrypted in flash; see the firmware README). Decide whether that is acceptable for your use | Decision recorded | |

## 6. End to end, and how long it takes

| ID | Test | Expected | Result |
| --- | --- | --- | --- |
| HW-60 | **Speak, then read** | Say a 10-second sentence into the locket. Start a stopwatch when you stop. Reload the recording in the dashboard when the clip appears | The transcript contains what you said. Write down the time from the end of the clip to the text appearing, **ten times**, and compute the median and worst case. Compare with the 60-second target in [FINAL_COMPLETION_REPORT.md](FINAL_COMPLETION_REPORT.md) | |
| HW-61 | The wearer rule | Assign the locket to person A, record, reassign to B, record | A sees the first clip only; B the second; a manager sees both | |
| HW-62 | A 24-hour soak | Leave it recording and uploading for 24 hours on mains power | Still online; no growth in `/pending` beyond the upload backlog; no reboots (`status` boot count in the log); write down the free heap if you add a print for it | |

## 7. The button recorder (`locket_web_upload`)

The checks above are for the continuous-clip firmware in `firmware/locket`. The **button-controlled recorder**
([firmware/locket_web_upload](../firmware/locket_web_upload/README.md)) needs the same board, card, Wi-Fi and
server, plus a push button between **D1 (GPIO2)** and GND. Build it with **PSRAM: OPI PSRAM**:

```powershell
arduino-cli compile --fqbn "esp32:esp32:XIAO_ESP32S3:PSRAM=opi" --warnings all platform\firmware\locket_web_upload
arduino-cli upload  --fqbn "esp32:esp32:XIAO_ESP32S3:PSRAM=opi" -p COM5 platform\firmware\locket_web_upload
```

Everything below is **not done**. Its logic has been run on a computer against simulated hardware and against the
real server (see the sketch's README); none of these has run on a board. `status` and `help` work as in section 1,
and `ap` shows the maintenance Wi-Fi's name and password.

| ID | Test | How | Expected | Result |
| --- | --- | --- | --- | --- |
| HW-7F | The sketch compiles for `esp32:esp32:XIAO_ESP32S3:PSRAM=opi` | `Sketch uses ... bytes`, no warnings | **Done, passed (no board needed).** Arduino CLI 1.5.2-rc.1, `esp32:esp32` 3.3.12, `--warnings all`: `Sketch uses 1135146 bytes (33%) of program storage space. Maximum is 3342336 bytes. Global variables use 55508 bytes (16%) of dynamic memory, leaving 272172 bytes for local variables. Maximum is 327680 bytes.` No warnings. With every development switch on: 1,135,986 bytes (33%), 57,556 bytes of globals (17%), no warnings. Static figures only: run-time stack and heap use is unmeasured. Re-run after any change |
| HW-70 | Boots with PSRAM and the ring | Flash; open the monitor at 115200 | `ASK voice locket firmware 0.2.0-web-upload`; `[PSRAM] Total:` about 8,000,000; `[SD-RING] 32 blocks`; `[READY]`. With PSRAM off in the board settings: `PSRAM is not enabled` | |
| HW-71 | **One press starts, the next stops, nothing else does** | Press once, wait 5 s, press once more. Repeat pressing hard and slowly | `[BUTTON] RECORDING STARTED`, then `[BUTTON] STOP`, once each. A long or bouncy press is one event. No start/stop without a press | |
| HW-72 | **A long recording is parts of valid audio** | Record 12 minutes of speech with a tick every 10 s, then check the card on a computer (`inspect_clip.py`) | Three parts of about 299, 299 and 120 s, each `OK`, 16 kHz mono; **no gap, repeat or click where the parts join** (listen at each boundary) | |
| HW-73 | **Uploading does not disturb recording** | Record for 30 minutes with `ASK_UPLOAD_WHILE_RECORDING` on and the server reachable | Parts reach the server during the recording; `status` shows `queue high-water` well under 32; no audio gaps; no `fell too far behind` | |
| HW-74 | The card keeps up | Record 60 minutes on each of three different cards | `card writer` shows few retries, **0 `LOST`/`bytesLost`**. Write down retries, remounts and the high-water mark per card. A slow card (high-water near 32) means the 4 MHz setting needs attention | |
| HW-75 | **A card pulled while recording** | Pull it mid-recording, then put it back and press reset | Parts already finished are in `/pending` and upload; the partial is recovered at start-up (`[RECOVER] Saved an interrupted recording`); the error was shown, not a crash | |
| HW-76 | **Power cut while recording** | Pull the USB cable mid-recording; power up | At most about 8 seconds are lost (flush interval); the rest is recovered, valid, and uploads | |
| HW-77 | **Recording continues without Wi-Fi** | Switch the router off, record for 3 minutes, switch it on | Recording unaffected; `queue` grows; uploads resume with back-off; every part reaches the server once; nothing is deleted before its receipt | |
| HW-78 | Pressing the button before the clock is set | Boot with Wi-Fi off, record 1 minute, switch Wi-Fi on in the same start-up | `the clock is NOT set yet`; names with `u…`; after the network sets the clock the parts upload and their start times on the website match the wall clock within 2 s | |
| HW-79 | **TLS with a real certificate, under load** | Put the server behind HTTPS with `/ca.pem` on the card. Upload a 5-minute part (9.6 MB) while recording. Add a print of free heap and stack high-water if you can | Upload succeeds; **no reboot**, no `abort()`; heap and the uploader's stack (`kUploaderStackBytes`) have headroom. With a wrong `/ca.pem`: nothing is sent, files stay, `last problem` says so | |
| HW-7A | A revoked locket | Revoke it in the dashboard | Uploads stop with `pair again`; every file stays on the card; a new press shows the error and does not record; re-pairing resumes the upload of the old files | |
| HW-7B | The maintenance page | `ap on` (restarts). Join `ASK-LOCKET-XXXX` with the password from `ap`, open `http://192.168.4.1/` | The state, card, microphone, queue and last problem update. It does not show the credential or any password. From another device on the home network, the locket's page answers `403` | |
| HW-7C | **Level and quality** | Record the same speech with the original recorder and with this one | Same level (gain 1x), no new clipping, no new noise | |
| HW-7D | An 8-hour soak | Start and stop recordings every few minutes with uploads on, mains power | No reboot, no growth of `/pending` beyond the upload backlog, `bytesLost` stays 0, memory does not shrink | |
| HW-7G | **The button's wiring (D1 to GND)** | In the serial monitor type `button` with the button up, then hold it and type `button` again | `up (reads HIGH)`, then `DOWN (reads LOW)`. If it says DOWN with nothing pressed, or never DOWN, check the D1 and GND legs; no resistor should be needed | |
| HW-7H | **Two presses, one recording** | Press once, count to ten, press once more | `[STATE] RECORDING`, then `[BUTTON] press: stop`, `[STATE] FINALIZING`, `RECORDING_STOPPED`, `UPLOAD_QUEUED`, `[STATE] IDLE`. The first press never stops it; letting go does nothing. Nothing records at power-up | |
| HW-7I | **Hold and mash** | Start a recording; hold the button for five seconds. Later, press it five times quickly right after a stop | The hold is one press: it stops once and does not restart. The quick presses give `press ignored while FINALIZING` or one clean new start; never two recordings, never a half-saved file | |
| HW-7J | **Ready again while an upload is pending** | Switch the network off, record, switch it on, and start a new recording while the first is still uploading | The new recording starts at once; both reach the server once each, with different ids; neither has a gap | |
| HW-7K | Clear errors | Boot with no card; with a full card; unpaired | Each says what is wrong (`no usable memory card`, `the memory card is full`, `not paired`); `[STATE] RECORDING` never appears; a press after fixing it works | |
| HW-7E | A button held at power-up | Hold the button while plugging in | Nothing records until it has been released and pressed again | |
| HW-7L | **Joins the built-in network by itself** | Switch on a 2.4 GHz hotspot named `ESPTEST`, WPA2 password `12345678`. Power the locket. No `wifi` command has ever been typed on this firmware, and it is not paired | Within about 20 s: `[WiFi] Connecting to ESPTEST...`, `[WiFi] Connected successfully`, `[WiFi] IP address: …   signal strength (RSSI): … dBm` (write down the RSSI and the seconds taken). `status`: `connected to ESPTEST (built-in network)`. The password is nowhere in the log | |
| HW-7M | **Start-up and recording do not wait for Wi-Fi** | Hotspot off. Power up and press the button as soon as `[READY]` appears | `[READY]` appears without waiting. Recording starts and stops normally, `UPLOAD_QUEUED`, nothing says uploaded. Meanwhile `[WiFi] Connection failed: no network with that name was found … (NO_AP_FOUND, code 201). Trying again in N s.` repeats, with waits that grow to a minute | |
| HW-7N | **A refusal says why** | Change the hotspot's password, power-cycle; then switch the hotspot off | `the password was not accepted … (AUTH_FAIL, code 202)` (a handshake timeout is also right), then `no network with that name was found …`. Never a silent hang, never the password | |
| HW-7O | Joins when the hotspot appears | Continue from HW-7M and switch the hotspot on | `Connected successfully` within a minute (the longest wait). The queued recordings upload and the website shows each once | |
| HW-7P | A lost link is noticed and rejoined | Connected, record, then switch the hotspot off for a minute and on again | `[WiFi] Connection lost: …` within seconds; the recording is not affected; it rejoins after the hotspot is back | |
| HW-7Q | **A saved network cannot override the built-in one** | Type `wifi OtherNet otherpass12`; reset: it still uses OtherNet. Then flash the firmware again (even unchanged) and reset | After the new flash: `A network saved before this firmware was flashed was cleared; using the built-in network.` and it joins `ESPTEST`. `wifi default` also returns to `ESPTEST` | |
| HW-7R | Pairing and certificate checking are unchanged | On the built-in network: `server …`, `pair <code>`, upload with the right `/ca.pem`; then with a wrong `/ca.pem` | Pairing needs a valid code. With the wrong CA nothing is sent and the files stay. Nothing was made less strict by the Wi-Fi change | |
| HW-7S | How long a real join takes, and what breaks it | Power on with a phone hotspot (5 GHz-capable phone set to 2.4 GHz); try 5 GHz only, then a hidden name | Write down seconds to `Connected`. 5 GHz only: `no network with that name was found` | |

## If something fails

| Symptom | Likely cause | What to try |
| --- | --- | --- |
| No serial port appears | Charge-only cable, or the board needs the boot button held while plugging in | Try another cable; hold BOOT, plug in, release |
| `recording  microphone failed to start` / `microphone is not responding` | Expansion board loose, or the pins differ on your revision | Reseat the board; check `ASK_PIN_PDM_CLOCK` and `ASK_PIN_PDM_DATA` against the schematic |
| Clips play back silent or as hiss | Gain or data pin wrong | Run `inspect_clip.py`; raise `kSoftwareGainShift`; check the pins |
| Clips play back too fast, slow or chipmunk | Sample-rate mismatch | The firmware writes 16 kHz; confirm `kSampleRate` is unchanged and the server's `ASK_ALLOWED_SAMPLE_RATES` includes it |
| `no memory card` | Not FAT32, over 32 GB, bad contact, wrong chip select | Reformat as FAT32; try another card; check `ASK_PIN_SD_CS` |
| `could not join Wi-Fi` | 5 GHz network, wrong password, weak signal | Use 2.4 GHz; retype with `wifi "name" password` |
| `waiting for the time` | NTP blocked on the network | Allow outbound UDP 123, or use a network that does |
| Upload `401` | Revoked, or paired with another server | Pair again |
| TLS fails | Wrong or missing `/ca.pem`; the server name does not match its certificate; board clock wrong | Check the certificate chain and `status` clock |
| Crash or reboot loop during upload | Task stack too small for TLS | Raise `kUploaderStackBytes`; report the backtrace |

## What was verified without a board (so you know what is *not* on the list above)

* The decision logic in `core.cpp` (WAV header, SHA-256, timestamps, clip ids, retry timing, what to do after each
  server answer) is compiled with strict warnings and passes 3,800+ checks on a computer, and agrees with the real
  server: `firmware/tests/check.ps1` and `platform/api/tests/test_firmware_interop.py`.
* Every source file compiles against *stand-in* Arduino headers, which now deliberately omit what the real core
  omits (the `esp_random()` mistake above was caught this way after the real headers were read).
* The library calls the sketch makes were compared by hand, signature by signature, with the real
  arduino-esp32 3.3.12 headers for ESP_I2S, SD/FS, HTTPClient, NetworkClientSecure and Preferences.

* The sketch was compiled and linked with the **real toolchain** (HW-00 above): no warnings, 32% of program
  storage, 16% of globals. Compiled is not flashed, and nothing was flashed.

Not verified: everything on the list above (HW-01 onwards), and the sketch's run-time memory use.
