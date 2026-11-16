// Locket firmware: the parts that are pure logic.
//
// Nothing in this file or core.cpp touches the Arduino framework, the clock, the network or storage.
// That is deliberate. It means these rules can be compiled and tested on a laptop (see ../tests), even
// though the rest of the firmware can only be tested on the device. Every rule here mirrors something the
// server enforces: platform/docs/API.md ("Locket API") and platform/api/app/services/audio.py.

#pragma once

#include <stddef.h>
#include <stdint.h>

namespace ask {

// ---- WAV ----------------------------------------------------------------------------------------

// A canonical 44-byte header for plain PCM audio. The server accepts only 16-bit PCM, mono or stereo,
// at a sample rate it allows (16000 Hz by default), and requires the sizes in the header to match the bytes
// that follow. A header that lies about its length is rejected as a truncated clip.
const size_t kWavHeaderBytes = 44;
void wav_header(uint8_t out[kWavHeaderBytes], uint32_t data_bytes, uint32_t sample_rate, uint16_t channels,
                uint16_t bits);

// A clip whose header was never finished (the power went) has a placeholder header and real audio after it.
// This is how much of it is whole audio: everything after the header, cut back to a whole number of frames.
// Zero means there is nothing usable.
uint32_t wav_recoverable_data_bytes(uint64_t file_bytes, uint16_t channels, uint16_t bits);

// ---- SHA-256 ------------------------------------------------------------------------------------

// The server checks a SHA-256 of every upload (header X-Content-SHA256). Written out here, instead of using
// the board library's, so the same code runs on the device and in the tests against known answers.
struct Sha256 {
  uint32_t state[8];
  uint64_t total_bytes;
  uint8_t block[64];
  size_t used;
};
void sha256_begin(Sha256* ctx);
void sha256_update(Sha256* ctx, const uint8_t* data, size_t length);
void sha256_finish(Sha256* ctx, uint8_t out[32]);
// Lowercase hex of `length` bytes into `out`, which must hold 2 * length + 1 characters.
void hex_encode(const uint8_t* data, size_t length, char* out);

// ---- Time ---------------------------------------------------------------------------------------

// "2026-10-26T09:30:00Z" for seconds since 1970-01-01 UTC. The server needs a timestamp with a time zone
// (X-Recorded-At). `out` holds 21 characters. False, writing nothing, for a time that cannot be right.
bool iso8601_utc(int64_t unix_seconds, char out[21]);
// Before the clock has been set from the network the device thinks it is 1970. A recording stamped then
// would be wrong forever, so recording waits until this is true.
bool clock_is_plausible(int64_t unix_seconds);

// ---- What to do after an upload ------------------------------------------------------------------

enum class Upload {
  Done,    // stored (201) or already stored (200): delete the local copy
  Retry,   // not acknowledged: keep the file, try again later
  NewId,   // same id, different content (409): the file needs a fresh id, then retry
  Reject,  // the server will never accept this file: set it aside, keep it, do not retry
  Repair,  // the credential is no longer valid (401): stop, the locket must be paired again
};

// `error_code` is the "code" from the server's error body, or "" when there is none. The status alone is not
// enough: a 422 can mean damaged bytes (retry) or audio the server will never accept (do not retry).
Upload classify_upload(int http_status, const char* error_code);

// ---- Retry timing -------------------------------------------------------------------------------

// Exponential back-off: `base_ms` doubling with each failed attempt (0 is the first), never more than
// `max_ms`, then spread over the upper half of that range by `random32` so lockets that fail together do
// not all come back together.
uint32_t backoff_ms(uint32_t attempt, uint32_t base_ms, uint32_t max_ms, uint32_t random32);

// ---- Identifiers --------------------------------------------------------------------------------

// The server wants 8 to 64 letters, digits, '-' or '_', unique per locket, chosen by the locket.
// Built from a counter that increases on every boot, a random number picked at boot, and a sequence number
// within the boot, so an id is never reused even after a power cut or a swapped memory card.
// `out` needs room for kClientIdBytes.
const size_t kClientIdBytes = 27;
bool client_id_make(char* out, size_t capacity, uint32_t boot, uint32_t boot_salt, uint32_t sequence);
bool client_id_valid(const char* id);

// ---- Battery ------------------------------------------------------------------------------------

// Rough charge of a single lithium cell from its voltage. The curve is a typical one, not measured for any
// particular cell. -1 for a reading that cannot be a single cell (nothing connected, or a wiring fault).
int battery_percent(int millivolts);

// ---- Reading the server's replies ---------------------------------------------------------------

// The server's own replies are small, flat-ish JSON with unique key names ("credential", "chunkSeconds"),
// so a tiny reader is enough and the device does not need a JSON library. These are NOT general JSON
// parsers: do not use them on anything that was not written by the ASK server.
bool json_string(const char* json, const char* key, char* out, size_t capacity);
bool json_uint(const char* json, const char* key, uint32_t* out);

}  // namespace ask
