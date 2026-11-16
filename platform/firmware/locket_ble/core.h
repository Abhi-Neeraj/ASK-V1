// Locket firmware: the parts that are pure logic.
//
// Nothing in this file or core.cpp touches the Arduino framework, the clock, the network or storage.
// That is deliberate. It means these rules can be compiled and tested on a laptop (see ../tests), even
// though the rest of the firmware can only be tested on the device. Every rule here mirrors something the
// server enforces: platform/docs/API.md ("Locket API") and platform/api/app/services/audio.py.
//
// The first half of this file is shared with the continuous-clip firmware in ../locket (same functions, same
// behaviour; ../tests/test_core.cpp is run against both copies). The sections marked "button recorder" are
// new in this sketch.

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
  Denied,  // 403, for example from a gateway in front of the server: keep the file and retry only slowly
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

// ---- Button recorder: reading and writing JSON -----------------------------------------------------

// true / false for a JSON boolean. False for anything else, leaving *out alone.
bool json_bool(const char* json, const char* key, bool* out);
// Text safe to put between double quotes in JSON. False, with `out` left empty, if it does not fit in `capacity`
// (a cut-short copy is never returned).
bool json_escape(const char* in, char* out, size_t capacity);

// ---- Button recorder: checking a finished WAV ------------------------------------------------------

enum class WavCheck {
  Ok,
  TooSmall,       // shorter than a header, or a header with no audio
  NotWav,         // the RIFF / WAVE / fmt / data markers are not where a plain WAV puts them
  WrongFormat,    // not 16-bit PCM at the expected rate and channel count
  SizeMismatch,   // the header's sizes do not match each other or the file
};

// Checks the 44 header bytes against the size of the whole file. A few stray bytes after the audio (less than
// one frame, left by recovering a half-written clip) are allowed because the server ignores them. On Ok,
// *data_bytes is the audio length the header declares.
WavCheck wav_check(const uint8_t header[kWavHeaderBytes], uint64_t file_bytes, uint32_t sample_rate,
                   uint16_t channels, uint16_t bits, uint32_t* data_bytes);

// ---- Button recorder: names of saved parts ---------------------------------------------------------

// One recording (a button press to the next) is saved as one or more numbered parts, each a WAV the server
// takes as a separate upload; they share a session reference and carry a part number, which become the
// X-Session-Ref and X-Sequence headers. The card holds no index: everything needed to upload a part again
// after a reboot is in its file name, <clientId>.<when>.<part>.<session>.wav
//   when  1730000000   seconds since 1970, the clock was set when the part started
//         u1234        the clock was NOT set; 1234 is seconds since the locket started
struct ClipName {
  char client_id[kClientIdBytes];
  bool timed;
  int64_t when;
  uint32_t part;  // 1, 2, 3 ...
  char session[kClientIdBytes];
};
const size_t kClipNameBytes = 96;

bool session_ref_make(char* out, size_t capacity, uint32_t boot, uint32_t boot_salt, uint32_t session_number);
bool clip_name_make(char* out, size_t capacity, const ClipName& name);
bool clip_name_parse(const char* file_name, ClipName* out);
// The start-up count inside an id made by client_id_make, or 0 for anything else.
uint32_t client_id_boot(const char* id);

// The wall-clock time a part started, learned after the fact: the clock was not set when it was recorded, but
// it is now. Only valid in the same start-up the part was recorded in, because the clock the offset was
// measured against restarts at zero on every boot. False if the clock is not set or the numbers cannot be right.
bool resolve_start_time(int64_t now_epoch, uint32_t uptime_now_seconds, int64_t uptime_at_part_start_seconds,
                        int64_t* out_epoch);

// ---- Button recorder: planning a recording ---------------------------------------------------------

// How many whole ring blocks make one part. Never zero. Honours both the length and the upload size the server
// allows; each part is the 44-byte header plus this many blocks (the last part of a recording is shorter).
uint32_t segment_blocks(uint32_t segment_seconds, uint32_t max_upload_bytes, uint32_t block_bytes,
                        uint32_t bytes_per_second);
// The part length to aim for: the firmware's preference, cut down to what the server accepts. 0 for the server's
// limit means "not told", and the preference stands.
uint32_t segment_seconds_for(uint32_t preferred_seconds, uint32_t server_max_clip_seconds);
// True while there is still room on the card: what was free when recording began, less what has been written,
// must stay above `reserve`.
bool space_ok(uint64_t free_at_start, uint64_t written, uint64_t reserve);
// Whether the uploader may use the network and card now. While recording it must not compete with the writer:
// uploads are held back unless allowed and the ring has room to spare.
bool upload_may_run(bool recording, bool allowed_while_recording, uint32_t ring_used, uint32_t ring_total,
                    uint32_t hold_percent);

// ---- Button recorder: confirming an upload --------------------------------------------------------

struct Ack {
  char recording_id[40];
  bool duplicate;
};

// The server answers a stored clip with a small JSON receipt (platform/docs/API.md, "Upload a clip"). A 200 or
// 201 status is not enough to delete the local copy, because a gateway or captive portal can answer 200 to
// anything. This accepts the receipt only if it says "stored", names this clip, carries a recording id, and
// repeats the SHA-256 and byte count the locket sent.
bool ack_validate(const char* json, const char* client_id, const char* sha256_hex, uint32_t bytes, Ack* out);

// ---- Button recorder: the button ---------------------------------------------------------------------

struct Debounce {
  bool raw;
  bool stable;
  uint32_t changed_ms;
};
// Start watching. Whatever the button is doing now counts as its settled state: a button that is already held
// down (at power-up, or because the press that started a recording is still down) is NOT a new press, and only
// a release followed by a fresh press is.
void debounce_begin(Debounce* d, bool level);
// Feed the pin level each time round. True once per press (the level settling LOW), never for a bounce, never
// for a release, and never repeatedly while the button is held.
bool debounce_pressed(Debounce* d, bool level, uint32_t now_ms, uint32_t debounce_ms);

// ---- Button recorder: the recording state machine ------------------------------------------------------
//
//   Booting --BootedOk--> Idle --Press--> (start) --Started--> Recording --Press--> (stop) --CaptureEnded-->
//   Finalizing --Saved--> Idle              (and, from Idle or Error, a refused start goes to Error; Error also
//                                            accepts a Press, which tries again)
//
// Uploading is deliberately NOT a state of this machine: a finished part is queued on the card and the
// uploader sends it on its own task, so the locket is ready for the next recording at once.
enum class RecState : uint8_t { Booting, Idle, Recording, Finalizing, Error };

enum class RecEvent : uint8_t {
  Press,             // one debounced press of the button (or `record` typed on the serial console)
  BootedOk,          // start-up finished and everything needed is there
  BootFailed,        // start-up finished but something needed is missing (the reason is shown)
  Started,           // the card writer and the microphone are really running
  StartRefused,      // a prerequisite was missing; nothing was started
  CaptureEnded,      // the microphone has been stopped (by a press, a safety limit or a fault)
  Saved,             // the last part is closed, checked and queued, and nothing went wrong
  SavedWithProblem,  // the last part is closed and queued, but something went wrong (the reason is shown)
};

// What a press should do. Only a Press ever produces anything other than Ignore.
enum class PressAction : uint8_t { Ignore, Start, Stop };

struct RecStep {
  RecState next;
  PressAction action;
};
// The whole machine, as a table. Any event that does not apply in the current state changes nothing and does
// nothing: a press while finalizing can neither start nor stop a recording.
RecStep rec_step(RecState state, RecEvent event);

// ---- Button recorder: joining Wi-Fi ---------------------------------------------------------------------

// What a Wi-Fi disconnect reason code (ESP-IDF's wifi_err_reason_t, as the chip reports it) means to an owner.
// Never empty. The code itself is printed alongside, so nothing is hidden by the wording.
const char* wifi_reason_hint(unsigned reason);
// True if the chip reporting this reason, while a join is being tried, means this try has failed for good (no such
// network, the password refused, the access point turned us away): there is no point waiting out the time limit.
// False for anything that can pass (a timeout, a leave we asked for) and for codes nobody planned for.
bool wifi_reason_ends_attempt(unsigned reason);
// Why this network name and password cannot be used, or "" if they can. An empty password means an open network.
// A name is 1 to 32 characters; a password is 8 to 63 (what WPA2 allows).
const char* wifi_credentials_problem(const char* ssid, const char* password);

// ---- Button recorder: may a recording start? --------------------------------------------------------------

struct StartChecks {
  bool mic_ok;
  bool paired;
  bool needs_pairing;  // the server has refused the locket's credential
  bool card_ready;
  bool ring_ready;     // the PSRAM audio buffers exist
  uint64_t free_bytes;
  uint64_t min_free_bytes;
  uint32_t pending_parts;      // recordings already waiting on the card to be uploaded
  uint32_t max_pending_parts;
};
enum class Blocker : uint8_t {
  None,
  MicFailed,
  NotPaired,
  NeedsPairing,
  NoCard,
  NoMemory,
  CardFull,
  TooManyWaiting,
};
// The first reason a recording cannot start, in the order an owner would fix them, or None.
Blocker start_blocker(const StartChecks& checks);
// A sentence for the owner. Never empty, and None gives "".
const char* blocker_text(Blocker blocker);

}  // namespace ask
