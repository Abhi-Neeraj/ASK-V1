// Locket firmware settings that belong in code rather than in the device's stored configuration.
//
// COMPILED with the real ESP32 toolchain, but NEVER RUN ON A DEVICE (no board where this was written). Pins and
// library calls below follow the board's published documentation and must be checked against YOUR board revision.

#pragma once

#include <Arduino.h>

// Sent to the server at pairing and with every heartbeat. Letters, digits and . _ + - only, up to 40 characters.
#define ASK_FIRMWARE_VERSION "0.1.0"

// ---- Pins: Seeed Studio XIAO ESP32S3 Sense ---------------------------------------------------------------
// Check these against the board's schematic. The microphone is a PDM part on two fixed pins; the card slot's
// chip-select is a pin of its own on the expansion board.
#define ASK_PIN_PDM_CLOCK 42
#define ASK_PIN_PDM_DATA 41
#define ASK_PIN_SD_CS 21
// The board has no battery gauge. Wire a divider from the cell to an analogue pin and put its number here to
// report a battery level; -1 reports none (the dashboard then shows no level). The divider ratio is below.
#define ASK_PIN_BATTERY_ADC -1
#define ASK_BATTERY_DIVIDER_RATIO 2.0f

// ---- Network safety ------------------------------------------------------------------------------------
// Plain http:// sends the locket's credential and the audio in the clear. Leave this 0 outside a trusted
// development network. With 0, a server address must start with https://.
#define ASK_ALLOW_PLAIN_HTTP 0
// 1 skips checking the server's certificate, which defeats the point of TLS. Development only, never a
// deployed locket. With 0 the locket checks the server against the certificate authority in /ca.pem on the card.
#define ASK_INSECURE_TLS 0

// ---- Audio: must be a format the server accepts (16-bit PCM at an allowed rate; 16000 Hz by default) -----
constexpr uint32_t kSampleRate = 16000;
constexpr uint16_t kChannels = 1;
constexpr uint16_t kBitsPerSample = 16;
constexpr size_t kAudioBufferBytes = 4096;
// The PDM microphone is quiet. Each step doubles the level (with clipping). Tune by listening to a clip.
constexpr uint8_t kSoftwareGainShift = 0;

// ---- Storage ------------------------------------------------------------------------------------------
constexpr const char* kDirRecording = "/recording";  // a clip being written (may be cut short by a power loss)
constexpr const char* kDirPending = "/pending";      // finished clips waiting to be uploaded
constexpr const char* kDirRejected = "/rejected";    // clips the server will never accept, kept for diagnosis
constexpr const char* kCaFile = "/ca.pem";
// Stop recording when the card has less free space than this, rather than fail halfway through a clip.
constexpr uint64_t kMinFreeBytes = 8ull * 1024 * 1024;
// A clip shorter than the server's minimum (0.5 s) would only be rejected.
constexpr uint32_t kMinClipBytes = kSampleRate * kChannels * (kBitsPerSample / 8) / 2;

// ---- Timing. The server's configuration, sent at pairing and with each heartbeat, replaces the first two --
constexpr uint32_t kDefaultChunkSeconds = 10;
constexpr uint32_t kDefaultHeartbeatSeconds = 30;
constexpr uint32_t kRetryBaseMs = 2000;
constexpr uint32_t kRetryMaxMs = 300000;
constexpr uint32_t kHttpTimeoutMs = 30000;
constexpr uint32_t kWifiConnectTimeoutMs = 20000;
constexpr uint32_t kClockWaitMs = 20000;
constexpr size_t kMaxErrorBodyBytes = 512;

// The recorder and the uploader run as separate tasks. TLS needs a deep stack.
constexpr uint32_t kRecorderStackBytes = 8192;
constexpr uint32_t kUploaderStackBytes = 16384;
