// The Bluetooth locket's pure logic: no Arduino, no radio, no card. Tested on a computer by
// tests/test_ble_core.cpp against the same values the server and the app are tested with.
//
// Spec: docs/superpowers/specs/2026-11-14-ble-locket-cloud-pipeline-design.md (5.2 pairing, 5.3 Bluetooth).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "core.h"

namespace askble {

// --- pairing ------------------------------------------------------------------------------------------

void hmac_sha256(const uint8_t* key, size_t key_len, const uint8_t* msg, size_t msg_len, uint8_t out[32]);

// Six digits (0..999999) from the locket's secret and the nonce it showed: the Bluetooth passkey, and the code
// the server emails. Same formula as platform/api/app/security/locket_codes.py.
uint32_t pairing_code(const uint8_t secret[32], const uint8_t nonce[16]);

// HMAC-SHA256(secret, "ASK-BONDED" + nonce) as 64 lowercase hex characters: shown on BOND_PROOF after a bond.
void bond_proof_hex(const uint8_t secret[32], const uint8_t nonce[16], char out[65]);

// At most 5 wrong passkeys in 10 minutes; then pairing waits until the window has passed.
struct PasskeyGuard {
  uint32_t window_start_ms;
  uint8_t failures;
};
const uint8_t kMaxPasskeyFailures = 5;
const uint32_t kPasskeyWindowMs = 600000;
bool passkey_allowed(PasskeyGuard* guard, uint32_t now_ms);
void passkey_failed(PasskeyGuard* guard, uint32_t now_ms);
// The passkey to use now: a random value while locked out; otherwise the fixed test PIN if the build has one
// (0..999999, ASK_TEST_PASSKEY; -1 = none), else the emailed code.
uint32_t choose_passkey(bool locked, int32_t test_passkey, uint32_t emailed_code, uint32_t random_value);

// What a finished security procedure means. `passkey_shown`: this locket handed out its passkey for this attempt
// (a new pairing, not a bonded phone re-encrypting). `status` 0 is success.
//   NewBond  : the person typed the right code: show the proof, use a new number.
//   Trusted  : a bonded phone came back (its stored key was made with the passkey).
//   Reject   : encrypted without the passkey ("Just Works"): disconnect and forget it.
//   Failed   : a wrong code: counts towards the limit, new number.
//   Ignore   : a bonded phone failed to re-encrypt: not a wrong code.
enum class AuthOutcome : uint8_t { NewBond, Trusted, Reject, Failed, Ignore };
AuthOutcome auth_outcome(bool passkey_shown, int status, bool encrypted, bool authenticated);

// --- identity -----------------------------------------------------------------------------------------

// "ASK-" and six characters of ABCDEFGHJKMNPQRSTUVWXYZ23456789 (the server's alphabet).
bool product_id_valid(const char* id);

enum class Identity : uint8_t { Accepted, AlreadySet, BadId, BadSecret };
// The rule for `provision`: only once, only a valid product ID and a 64-hex-character secret.
Identity identity_accept(bool already_set, const char* product_id, const char* secret_hex);

// The `provision` line after the word itself, as people type or paste it: any spaces or tabs between the parts,
// the usage example's <angle brackets> or quotes left in, upper- or lower-case, and the secret split in two by a
// line wrap. On success `id` is the product ID in capitals and `secret` 64 lower-case hex characters. On failure
// it says what is wrong, and for a secret only its length (the secret itself is never repeated).
enum class ProvisionProblem : uint8_t { None, Missing, BadId, SecretLength, SecretNotHex };
struct ProvisionArgs {
  bool ok;
  ProvisionProblem problem;
  size_t secret_length;  // characters found where the secret should be
};
ProvisionArgs provision_args(const char* rest, char id[11], char secret[65]);

// --- audio --------------------------------------------------------------------------------------------

const int kAdpcmSamples = 256;
const size_t kAdpcmBlockBytes = 132;

struct AdpcmState {
  int16_t predictor;
  uint8_t index;
};

// One block from the given starting state (written into the block header); advances `state`.
void adpcm_encode_block(const int16_t samples[kAdpcmSamples], AdpcmState* state, uint8_t out[kAdpcmBlockBytes]);

// One block whose starting state comes only from its own samples, so encoding the same audio again (a resend)
// gives exactly the same bytes, and every block decodes on its own.
void adpcm_encode_block_auto(const int16_t samples[kAdpcmSamples], uint8_t out[kAdpcmBlockBytes]);

// --- frames (platform/web/src/domain/device/framing.ts) ------------------------------------------------

const uint8_t kFrameStart = 0x02;
const uint8_t kFrameAudio = 0x03;
const uint8_t kFrameEnd = 0x04;
const size_t kFrameHeaderBytes = 42;

uint32_t crc32(const uint8_t* data, size_t length);

// Header + payload into `out`. Returns the frame length, or 0 if `capacity` is too small or the session ID is
// not 16 characters.
size_t frame_encode(uint8_t type, const char* session_id, uint32_t seq, uint64_t timestamp_ms,
                    const uint8_t* payload, size_t payload_len, uint8_t* out, size_t capacity);

// A stable 16-character ID ([A-Z0-9]) for a meeting, from the recorder's session reference.
bool session_id_make(const char* session_ref, char out[17]);

// --- meetings as frame numbers ------------------------------------------------------------------------

// Parts of one meeting the sender can track. 120 (it was 64): the first 30 parts are a minute long, the rest five
// minutes (config.h), so a 6-hour recording (kMaxSessionSeconds) is 96 parts; every part beyond this limit would be
// ignored by the sender.
const uint32_t kMaxParts = 120;
struct SessionMap {
  uint32_t parts;
  uint32_t blocks[kMaxParts];  // AUDIO frames in each part (its last block padded with silence)
};
uint32_t blocks_for_samples(uint32_t samples);
uint32_t session_total_blocks(const SessionMap& map);
// AUDIO frame `seq` (1 = the first) -> part and block within it. False outside the meeting.
bool session_locate(const SessionMap& map, uint32_t seq, uint32_t* part, uint32_t* block);

// Whether every part of a meeting is on the card. `listing_ok`: both folders were listed in full under one card
// lock (a listing that failed proves nothing). `part_being_written`: /recording holds a part of this meeting;
// `other_meeting_recording`: it holds a part of a newer one.
bool meeting_complete(bool listing_ok, bool part_being_written, bool other_meeting_recording, bool busy_recording);

// The phone acknowledges progress every 32 frames; a RESEND it sent can be lost. If nothing new was acknowledged
// for kStallRewindMs while frames are outstanding, go back to the frame after the last acknowledged one
// (`acked_through` 0 = nothing yet: START again). True and `*rewind_to` set when that is due.
const uint32_t kStallRewindMs = 5000;
bool stall_rewind(uint32_t next, uint32_t acked_through, uint32_t last_progress_ms, uint32_t now_ms,
                  uint32_t* rewind_to);

// A meeting's start time. Parts recorded before the clock was set carry seconds since start-up (`when`), which
// can be turned into a time only in the same start-up, once the clock is set. The server refuses dates before
// 2020, so a meeting is sent only once its time is Known or Resolved; Unknowable ones are set aside, never guessed.
enum class StartTime : uint8_t { Known, Resolved, Wait, Unknowable };
StartTime meeting_start_time(bool timed, uint32_t part_boot, uint32_t this_boot, int64_t now_epoch,
                             uint32_t uptime_now_seconds, int64_t when, int64_t* out_epoch);

// --- commands (CONTROL) and texts (INFO, STATUS) --------------------------------------------------------

struct Command {
  enum Kind : uint8_t { Invalid, RecStart, RecStop, Ack, Resend, Time, Wifi, WifiClear, Ota, Forget } kind;
  char session[17];
  uint32_t seq;
  uint64_t number;
  char text1[201];  // WIFI: network name; OTA: download URL (the server's links are base + version + 105)
  char text2[129];  // WIFI: password
  char sha256[65];
  char version[24];
  uint32_t size;
};
// At most kMaxCommandBytes (a Bluetooth attribute holds 512).
const size_t kMaxCommandBytes = 510;
Command parse_command(const char* line);

bool hex_decode(const char* hex, uint8_t* out, size_t out_len);  // exactly 2*out_len hex characters
bool hex_text(const char* hex, char* out, size_t capacity);      // hex of UTF-8 text -> text (no NUL bytes)

int info_text(char* out, size_t capacity, const char* product_id, const char* version, int battery_percent,
              bool bonded);
int status_text(char* out, size_t capacity, const char* state, int battery_percent, const char* session_id,
                uint32_t last_seq, int ota_percent);

// --- firmware updates ---------------------------------------------------------------------------------

bool ota_image_ok(uint32_t expected_size, const char* expected_sha_hex, uint32_t got_size, const uint8_t got_sha[32]);

}  // namespace askble
