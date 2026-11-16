// Bluetooth locket (locket_ble): settings that belong in code rather than in the device's stored configuration.
//
// Press the button once to start recording, again to stop. Audio goes microphone -> PSRAM ring -> writer task ->
// WAV parts on the memory card (exactly as in locket_web_upload). Finished parts are streamed to the paired ASK
// phone app over Bluetooth; the phone uploads them to the ASK server. Wi-Fi is used only to install firmware
// updates the phone asks for.
//
// NEVER RUN ON A DEVICE where this was written (no board). Pins and library calls follow the board's published
// documentation and must be checked against YOUR board revision. Spec:
// docs/superpowers/specs/2026-11-14-ble-locket-cloud-pipeline-design.md

#pragma once

#include <Arduino.h>

// Shown on Bluetooth (INFO) and compared with the server's latest release by the phone. MAJOR.MINOR.PATCH.
#define ASK_FIRMWARE_VERSION "1.0.0"

// ---- TEST ONLY, by the owner's request (2026-11-15): a fixed Bluetooth pairing PIN --------------------------------
// With a number here (0 to 999999), the locket's pairing code is always this PIN instead of the six-digit code the
// server emails, so testers can type 1234 when the phone or browser asks. ANYONE NEARBY WHO KNOWS IT CAN PAIR AND
// RECEIVE MEETINGS, so it is for test lockets only: set -1 (the emailed code) for every real locket. The wrong-code
// limit still applies. Every start prints a warning while it is on. Pair through the ASK app or website (not the
// phone's Bluetooth settings), so the server records who the locket belongs to.
#ifndef ASK_TEST_PASSKEY
#define ASK_TEST_PASSKEY 1234
#endif

// ---- TEST ONLY, by the owner's request (2026-11-15): pair with NO code at all ("Just Works") --------------------
// With 1, the phone or browser pairs without asking for any code, so a test locket pairs in one step. ANYONE NEARBY
// CAN THEN PAIR WITH THE LOCKET AND RECEIVE ITS MEETINGS (the link is encrypted but nobody is verified), so this is
// for test lockets on a desk only. Set 0 for any locket that leaves the desk (that restores the passkey above, or the
// emailed code if ASK_TEST_PASSKEY is -1). Every start prints a warning while it is on. The server still checks the
// locket's proof of the bond before it records who the locket belongs to.
#ifndef ASK_TEST_NO_PASSKEY
#define ASK_TEST_NO_PASSKEY 1
#endif

// ---- Pins: Seeed Studio XIAO ESP32S3 Sense (the same as locket_web_upload) -------------------------------
#define ASK_PIN_BUTTON 2
#define ASK_PIN_BUTTON_ALT 0
#define ASK_BUTTON_DEBOUNCE_MS 50
#define ASK_PIN_PDM_CLOCK 42
#define ASK_PIN_PDM_DATA 41
#define ASK_PIN_SD_CS 21
#define ASK_PIN_SD_SCK 7
#define ASK_PIN_SD_MISO 8
#define ASK_PIN_SD_MOSI 9
#define ASK_SD_SPI_FREQUENCY 4000000UL
#define ASK_PIN_BATTERY_ADC -1
#define ASK_BATTERY_DIVIDER_RATIO 2.0f

// ---- Safety switches (the shipped values are the safe ones; anything switched on is announced at start) ------
// Plain http:// for firmware downloads: development only. With 0 a download must be https:// and the server's
// certificate is checked against the certificate authorities built into the ESP32 core.
#ifndef ASK_ALLOW_PLAIN_HTTP
#define ASK_ALLOW_PLAIN_HTTP 0
#endif

// ---- Audio (the server takes 16-bit PCM; the phone decodes the Bluetooth audio back to this) -------------
constexpr uint32_t kSampleRate = 16000;
constexpr uint16_t kChannels = 1;
constexpr uint16_t kBitsPerSample = 16;
constexpr uint32_t kBytesPerSecond = kSampleRate * kChannels * (kBitsPerSample / 8);
constexpr float kAudioGain = 1.0f;
constexpr size_t kAudioBufferBytes = 1024;
constexpr uint32_t kMaxSessionSeconds = 7000;
constexpr uint32_t kMicSilenceTimeoutMs = 5000;

// ---- The memory-card pipeline (as in locket_web_upload) -----------------------------------------------------
constexpr size_t kRingBlockBytes = 32 * 1024;
constexpr uint32_t kRingBlockCount = 32;
constexpr uint32_t kWriterStackBytes = 12 * 1024;
constexpr int kZeroWriteRetries = 3;
constexpr int kRemountRetries = 3;
constexpr uint32_t kFlushEveryBlocks = 8;
constexpr uint32_t kRemountWaitMs = 3000;
constexpr uint32_t kStorageLockMs = 15000;
constexpr uint32_t kWriterDrainTimeoutMs = 120000;

// ---- Recordings on the card -----------------------------------------------------------------------------------
// Parts of five minutes; a meeting is many parts, all sent to the phone as one Bluetooth session.
constexpr uint32_t kDefaultSegmentSeconds = 300;
constexpr uint32_t kDefaultMaxClipSeconds = 600;
constexpr uint32_t kDefaultMaxUploadBytes = 25u * 1024 * 1024;
constexpr const char* kDirRecording = "/recording";  // a part being written
constexpr const char* kDirPending = "/pending";      // finished parts waiting for the phone
constexpr const char* kDirRejected = "/rejected";    // parts that cannot be read back, kept for diagnosis
constexpr const char* kDirUntimed = "/untimed";      // parts recorded before the clock was set
constexpr uint64_t kMinFreeBytes = 8ull * 1024 * 1024;
// The phone may be away for days: the card's free space is the real limit.
constexpr uint32_t kMaxPendingParts = 5000;
constexpr uint32_t kMinClipBytes = kBytesPerSecond / 2;

// ---- Bluetooth ------------------------------------------------------------------------------------------------
// One AUDIO frame (16 ms of audio) every this many ms while connected: twice real time, to catch up a backlog.
constexpr uint32_t kSendIntervalMs = 8;
constexpr uint32_t kStatusEveryMs = 2000;
// A new pairing number (and so a new code) at least this often, and after every pairing attempt.
constexpr uint32_t kNonceRotateMs = 600000;
constexpr uint32_t kUnpairedLinkMs = 300000;  // a device connected this long without pairing is dropped
constexpr uint16_t kRequestedMtu = 185;

// ---- Firmware updates over Wi-Fi ------------------------------------------------------------------------------
constexpr uint32_t kWifiConnectTimeoutMs = 20000;
constexpr uint32_t kOtaHttpTimeoutMs = 30000;
constexpr uint32_t kOtaMaxBytes = 4u * 1024 * 1024;

// ---- Tasks ----------------------------------------------------------------------------------------------------
constexpr uint32_t kCaptureStackBytes = 8192;
constexpr uint32_t kSenderStackBytes = 8192;
constexpr uint32_t kOtaStackBytes = 16384;  // TLS needs a deep stack
