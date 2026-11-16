// Locket firmware settings that belong in code rather than in the device's stored configuration.
//
// This is the button-controlled recorder: press once to start recording, press again to stop. Every finished
// recording is stored on the memory card and uploaded to the ASK server. It keeps the recording pipeline of the
// original recorder (microphone -> PSRAM ring -> writer task -> memory card) and replaces its cloud
// speech-to-text, summary and chat-message steps with an upload to the ASK server, which does the processing.
//
// COMPILED with the real ESP32 toolchain, but NEVER RUN ON A DEVICE (no board where this was written). Pins and
// library calls follow the board's published documentation and must be checked against YOUR board revision.

#pragma once

#include <Arduino.h>

// Sent to the server at pairing and with every heartbeat. Letters, digits and . _ + - only, up to 40 characters.
#define ASK_FIRMWARE_VERSION "0.2.0-web-upload"

// ---- Pins: Seeed Studio XIAO ESP32S3 Sense (the same as the original recorder) ---------------------------
// Button: D1 = GPIO2. Wire a momentary push button between D1 and GND (the pull-up is switched on in software).
#define ASK_PIN_BUTTON 2
// The board's own BOOT button (GPIO0) also counts as the record button, so recording works with no button wired at
// all. Set to -1 to switch this off. (Holding BOOT while the board powers up or resets puts it into firmware-loading
// mode instead: that is the board, not this firmware.)
#define ASK_PIN_BUTTON_ALT 0
#define ASK_BUTTON_DEBOUNCE_MS 50
// Microphone: the board's PDM part on two fixed pins.
#define ASK_PIN_PDM_CLOCK 42
#define ASK_PIN_PDM_DATA 41
// Memory card on the expansion board. GPIO21 is also the board's user LED, so do not drive that LED.
#define ASK_PIN_SD_CS 21
#define ASK_PIN_SD_SCK 7
#define ASK_PIN_SD_MISO 8
#define ASK_PIN_SD_MOSI 9
// Reliability first: the original recorder used 4 MHz for this prototype's wiring.
#define ASK_SD_SPI_FREQUENCY 4000000UL
// The board has no battery gauge. Wire a divider from the cell to an analogue pin and put its number here to
// report a battery level; -1 reports none. Never GPIO2 (the button). The divider ratio is below.
#define ASK_PIN_BATTERY_ADC -1
#define ASK_BATTERY_DIVIDER_RATIO 2.0f

// ---- Safety switches. Each can be overridden at build time with -D, but the shipped values are the safe ----
// ---- ones. Anything switched on prints a warning at every start. ---------------------------------------------
// Plain http:// sends the locket's credential and the audio in the clear. Development on a trusted network only.
// With 0, a server address must start with https://.
#ifndef ASK_ALLOW_PLAIN_HTTP
#define ASK_ALLOW_PLAIN_HTTP 1
#endif
// 1 skips checking the server's certificate, which defeats the point of TLS. Development only, never a deployed
// locket. With 0 the locket checks the server against the certificate authority in /ca.pem on the card.
#ifndef ASK_INSECURE_TLS
#define ASK_INSECURE_TLS 0
#endif
// 1 lets finished parts of a long recording upload while the next part is still being recorded. They are held
// back automatically if the card falls behind (see kUploadHoldRingPercent). 0 uploads only when recording stops.
#ifndef ASK_UPLOAD_WHILE_RECORDING
#define ASK_UPLOAD_WHILE_RECORDING 1
#endif
// 1 lets someone on the local maintenance Wi-Fi download queued recordings from the device. Audio is personal data,
// so it is off unless you need it for support.
#ifndef ASK_DASHBOARD_AUDIO_DOWNLOAD
#define ASK_DASHBOARD_AUDIO_DOWNLOAD 0
#endif

// ---- The Wi-Fi network the locket joins by itself ---------------------------------------------------------------
// At every start-up the locket joins this network, with no serial command needed, and keeps trying (with a longer
// wait between tries, up to a minute) if it cannot. This is a PLAIN-TEXT password in the source and in the flashed
// program: acceptable for a throwaway test hotspot, not for a network you care about. For a real deployment set both
// to "" and use the serial `wifi` command instead. The password is never printed or logged.
//
// What wins: a network typed with `wifi <name> <password>` replaces this one, across restarts, until `wifi default`
// is typed or a NEW BUILD of the firmware is flashed. A new build clears whatever was saved before it (including by
// an older firmware) and starts from what is written here, so old test settings can never override it.
// Rules (checked at start-up): a name of 1 to 32 characters; a password of 8 to 63 characters, or empty for an
// open network. 2.4 GHz only.
#ifndef ASK_DEFAULT_WIFI_SSID
#define ASK_DEFAULT_WIFI_SSID "ESPTEST"
#endif
#ifndef ASK_DEFAULT_WIFI_PASSWORD
#define ASK_DEFAULT_WIFI_PASSWORD "12345678"
#endif

// ---- Audio: must be a format the server accepts (16-bit PCM at an allowed rate; 16000 Hz by default) -------
constexpr uint32_t kSampleRate = 16000;
constexpr uint16_t kChannels = 1;
constexpr uint16_t kBitsPerSample = 16;
constexpr uint32_t kBytesPerSecond = kSampleRate * kChannels * (kBitsPerSample / 8);
// 1x, because the original recorder's log showed clipping at 4x.
constexpr float kAudioGain = 1.0f;
// Small real-time read buffer. Audio is copied to PSRAM at once; the capture task never waits on the card.
constexpr size_t kAudioBufferBytes = 1024;
// A safety limit so an accidental endless recording cannot fill the card. The original recorder used 7000 s.
constexpr uint32_t kMaxSessionSeconds = 7000;
// Seconds without any microphone data before the recording is ended with an error.
constexpr uint32_t kMicSilenceTimeoutMs = 5000;

// ---- The memory-card pipeline (as in the original recorder) -------------------------------------------------
// About 1 MB of PSRAM sits between the microphone and the card: 32 blocks of 32 KB, 1.024 s of audio each.
constexpr size_t kRingBlockBytes = 32 * 1024;
constexpr uint32_t kRingBlockCount = 32;
constexpr uint32_t kWriterStackBytes = 12 * 1024;
constexpr int kZeroWriteRetries = 3;
constexpr int kRemountRetries = 3;
constexpr uint32_t kFlushEveryBlocks = 8;
// How long to wait for readers of the card to step aside before the card is remounted.
constexpr uint32_t kRemountWaitMs = 3000;
// How long one task may wait for the card's lock.
constexpr uint32_t kStorageLockMs = 15000;
// How long to wait for the writer to drain its queue and close the file after STOP.
constexpr uint32_t kWriterDrainTimeoutMs = 120000;
// Uploads that overlap a recording are held back while the ring is more than this full.
constexpr uint32_t kUploadHoldRingPercent = 50;

// ---- Recordings and the upload queue ------------------------------------------------------------------------
// A long recording is saved as parts, so each part is small enough to upload and for the server to accept. The
// server's own limits (sent at pairing and with each heartbeat) lower this further if they have to.
constexpr uint32_t kDefaultSegmentSeconds = 300;
constexpr uint32_t kDefaultMaxClipSeconds = 600;           // used until the server says otherwise
constexpr uint32_t kDefaultMaxUploadBytes = 25u * 1024 * 1024;
constexpr const char* kDirRecording = "/recording";  // a part being written (may be cut short by a power loss)
constexpr const char* kDirPending = "/pending";      // finished parts waiting to be uploaded
constexpr const char* kDirRejected = "/rejected";    // parts the server will never accept, kept for diagnosis
constexpr const char* kDirUntimed = "/untimed";      // parts recorded before the clock was set and never timed
constexpr const char* kCaFile = "/ca.pem";
// Stop recording when the card has less free space than this, rather than fail halfway through a part.
constexpr uint64_t kMinFreeBytes = 8ull * 1024 * 1024;
// Do not START a recording while this many parts are already waiting to be uploaded. The card's free space is the
// real limit; this keeps a long offline spell of very short recordings from filling the folder with files.
constexpr uint32_t kMaxPendingParts = 1000;
// A part shorter than the server's minimum (0.5 s) would only be rejected, so a tail that short is not kept.
constexpr uint32_t kMinClipBytes = kBytesPerSecond / 2;

// ---- Timing. The server's configuration, sent at pairing and with each heartbeat, replaces some of these ------
constexpr uint32_t kDefaultHeartbeatSeconds = 30;
constexpr uint32_t kRetryBaseMs = 2000;
constexpr uint32_t kRetryMaxMs = 300000;
constexpr uint32_t kHttpTimeoutMs = 30000;
constexpr uint32_t kWifiConnectTimeoutMs = 20000;  // how long one attempt to join the network may take
constexpr uint32_t kWifiRetryBaseMs = 5000;        // then wait about this long, doubling each time...
constexpr uint32_t kWifiRetryMaxMs = 60000;        // ...up to about a minute, and keep trying for ever
constexpr uint32_t kClockWaitMs = 20000;
constexpr size_t kMaxReplyBytes = 1024;
// After this many answers in a row that said "stored" but could not be confirmed, say so loudly (the file is
// always kept).
constexpr uint32_t kUnconfirmedWarnAfter = 5;

// ---- Tasks. The capture task owns the microphone and the button; TLS needs a deep stack --------------------
constexpr uint32_t kCaptureStackBytes = 8192;
constexpr uint32_t kUploaderStackBytes = 20480;  // TLS plus a 4 KB read slice and the reply buffers
constexpr uint32_t kNetworkStackBytes = 8192;    // the Wi-Fi connection manager (stack use not measured on a board)
constexpr uint32_t kDashboardStackBytes = 8192;
