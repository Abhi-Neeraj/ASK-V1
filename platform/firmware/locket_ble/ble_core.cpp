#include "ble_core.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace askble {

// --- pairing ------------------------------------------------------------------------------------------

void hmac_sha256(const uint8_t* key, size_t key_len, const uint8_t* msg, size_t msg_len, uint8_t out[32]) {
  uint8_t block[64] = {0};
  if (key_len > 64) {
    ask::Sha256 k;
    ask::sha256_begin(&k);
    ask::sha256_update(&k, key, key_len);
    ask::sha256_finish(&k, block);
  } else {
    memcpy(block, key, key_len);
  }
  uint8_t pad[64];
  for (int i = 0; i < 64; i++) pad[i] = block[i] ^ 0x36;
  uint8_t inner[32];
  ask::Sha256 ctx;
  ask::sha256_begin(&ctx);
  ask::sha256_update(&ctx, pad, 64);
  ask::sha256_update(&ctx, msg, msg_len);
  ask::sha256_finish(&ctx, inner);
  for (int i = 0; i < 64; i++) pad[i] = block[i] ^ 0x5c;
  ask::sha256_begin(&ctx);
  ask::sha256_update(&ctx, pad, 64);
  ask::sha256_update(&ctx, inner, 32);
  ask::sha256_finish(&ctx, out);
}

static void labelled(const uint8_t secret[32], const char* label, const uint8_t nonce[16], uint8_t out[32]) {
  uint8_t message[32];
  const size_t n = strlen(label);
  memcpy(message, label, n);
  memcpy(message + n, nonce, 16);
  hmac_sha256(secret, 32, message, n + 16, out);
}

uint32_t pairing_code(const uint8_t secret[32], const uint8_t nonce[16]) {
  uint8_t digest[32];
  labelled(secret, "ASK-PAIR", nonce, digest);
  const uint32_t first = ((uint32_t)digest[0] << 24) | ((uint32_t)digest[1] << 16) | ((uint32_t)digest[2] << 8) |
                         (uint32_t)digest[3];
  return first % 1000000u;
}

void bond_proof_hex(const uint8_t secret[32], const uint8_t nonce[16], char out[65]) {
  uint8_t digest[32];
  labelled(secret, "ASK-BONDED", nonce, digest);
  ask::hex_encode(digest, 32, out);
}

bool passkey_allowed(PasskeyGuard* guard, uint32_t now_ms) {
  if (guard->failures > 0 && now_ms - guard->window_start_ms > kPasskeyWindowMs) {
    guard->failures = 0;
  }
  return guard->failures < kMaxPasskeyFailures;
}

void passkey_failed(PasskeyGuard* guard, uint32_t now_ms) {
  if (guard->failures == 0 || now_ms - guard->window_start_ms > kPasskeyWindowMs) {
    guard->window_start_ms = now_ms;
    guard->failures = 0;
  }
  if (guard->failures < 255) guard->failures++;
}

// --- identity -----------------------------------------------------------------------------------------

static const char kIdAlphabet[] = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";

bool product_id_valid(const char* id) {
  if (id == nullptr || strlen(id) != 10 || strncmp(id, "ASK-", 4) != 0) return false;
  for (int i = 4; i < 10; i++) {
    if (strchr(kIdAlphabet, id[i]) == nullptr || id[i] == '\0') return false;
  }
  return true;
}

Identity identity_accept(bool already_set, const char* product_id, const char* secret_hex) {
  if (already_set) return Identity::AlreadySet;
  if (!product_id_valid(product_id)) return Identity::BadId;
  uint8_t secret[32];
  if (secret_hex == nullptr || strlen(secret_hex) != 64 || !hex_decode(secret_hex, secret, 32)) {
    return Identity::BadSecret;
  }
  return Identity::Accepted;
}

// --- audio --------------------------------------------------------------------------------------------

static const int16_t kSteps[89] = {
    7,     8,     9,     10,    11,    12,    13,    14,    16,    17,    19,    21,    23,    25,    28,
    31,    34,    37,    41,    45,    50,    55,    60,    66,    73,    80,    88,    97,    107,   118,
    130,   143,   157,   173,   190,   209,   230,   253,   279,   307,   337,   371,   408,   449,   494,
    544,   598,   658,   724,   796,   876,   963,   1060,  1166,  1282,  1411,  1552,  1707,  1878,  2066,
    2272,  2499,  2749,  3024,  3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,  7132,  7845,  8630,
    9493,  10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767};
static const int8_t kIndexChange[16] = {-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8};

static void advance(AdpcmState* s, int code) {
  const int size = kSteps[s->index];
  int delta = size >> 3;
  if (code & 4) delta += size;
  if (code & 2) delta += size >> 1;
  if (code & 1) delta += size >> 2;
  int p = (code & 8) ? s->predictor - delta : s->predictor + delta;
  if (p > 32767) p = 32767;
  if (p < -32768) p = -32768;
  s->predictor = (int16_t)p;
  int i = s->index + kIndexChange[code];
  s->index = (uint8_t)(i < 0 ? 0 : (i > 88 ? 88 : i));
}

static int encode_one(AdpcmState* s, int sample) {
  const int size = kSteps[s->index];
  int diff = sample - s->predictor;
  int code = 0;
  if (diff < 0) {
    code = 8;
    diff = -diff;
  }
  if (diff >= size) {
    code |= 4;
    diff -= size;
  }
  if (diff >= (size >> 1)) {
    code |= 2;
    diff -= size >> 1;
  }
  if (diff >= (size >> 2)) code |= 1;
  advance(s, code);  // exactly as the decoder will
  return code;
}

void adpcm_encode_block(const int16_t samples[kAdpcmSamples], AdpcmState* state, uint8_t out[kAdpcmBlockBytes]) {
  out[0] = (uint8_t)(state->predictor & 0xff);
  out[1] = (uint8_t)(((uint16_t)state->predictor >> 8) & 0xff);
  out[2] = state->index;
  out[3] = 0;
  for (int i = 0; i < kAdpcmSamples / 2; i++) {
    const int low = encode_one(state, samples[2 * i]);
    const int high = encode_one(state, samples[2 * i + 1]);
    out[4 + i] = (uint8_t)(low | (high << 4));
  }
}

void adpcm_encode_block_auto(const int16_t samples[kAdpcmSamples], uint8_t out[kAdpcmBlockBytes]) {
  // Start at the first sample, with the step that fits how fast the first samples move.
  int total = 0;
  for (int i = 0; i < 16; i++) {
    const int d = samples[i + 1] - samples[i];
    total += d < 0 ? -d : d;
  }
  const int mean = total / 16;
  uint8_t index = 0;
  while (index < 88 && kSteps[index] < mean) index++;
  AdpcmState state = {samples[0], index};
  adpcm_encode_block(samples, &state, out);
}

// --- frames -------------------------------------------------------------------------------------------

uint32_t crc32(const uint8_t* data, size_t length) {
  uint32_t crc = 0xFFFFFFFFu;
  for (size_t i = 0; i < length; i++) {
    crc ^= data[i];
    for (int b = 0; b < 8; b++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}

static void put32(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)(v >> 24);
  p[1] = (uint8_t)(v >> 16);
  p[2] = (uint8_t)(v >> 8);
  p[3] = (uint8_t)v;
}

size_t frame_encode(uint8_t type, const char* session_id, uint32_t seq, uint64_t timestamp_ms,
                    const uint8_t* payload, size_t payload_len, uint8_t* out, size_t capacity) {
  const size_t total = kFrameHeaderBytes + payload_len;
  if (session_id == nullptr || strlen(session_id) != 16 || capacity < total) return 0;
  out[0] = 'A';
  out[1] = 'S';
  out[2] = 1;
  out[3] = type;
  out[4] = 0;
  out[5] = (uint8_t)kFrameHeaderBytes;
  memcpy(out + 6, session_id, 16);
  put32(out + 22, seq);
  put32(out + 26, (uint32_t)(timestamp_ms >> 32));
  put32(out + 30, (uint32_t)(timestamp_ms & 0xFFFFFFFFu));
  put32(out + 34, (uint32_t)payload_len);
  if (payload_len) memcpy(out + kFrameHeaderBytes, payload, payload_len);
  // CRC-32 over header bytes 0..37 and the payload (the CRC field itself is left out).
  uint32_t crc = 0xFFFFFFFFu;
  const uint8_t* parts[2] = {out, out + kFrameHeaderBytes};
  const size_t lengths[2] = {38, payload_len};
  for (int p = 0; p < 2; p++) {
    for (size_t i = 0; i < lengths[p]; i++) {
      crc ^= parts[p][i];
      for (int b = 0; b < 8; b++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
  }
  put32(out + 38, ~crc);
  return total;
}

bool session_id_make(const char* session_ref, char out[17]) {
  if (session_ref == nullptr || session_ref[0] == '\0') return false;
  static const char kAlphabet[] = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";  // 31 letters, no look-alikes
  uint8_t digest[32];
  ask::Sha256 ctx;
  ask::sha256_begin(&ctx);
  ask::sha256_update(&ctx, (const uint8_t*)session_ref, strlen(session_ref));
  ask::sha256_finish(&ctx, digest);
  for (int i = 0; i < 16; i++) out[i] = kAlphabet[digest[i] % 31];
  out[16] = '\0';
  return true;
}

// --- meetings as frame numbers ------------------------------------------------------------------------

uint32_t blocks_for_samples(uint32_t samples) { return (samples + kAdpcmSamples - 1) / kAdpcmSamples; }

uint32_t session_total_blocks(const SessionMap& map) {
  uint32_t total = 0;
  for (uint32_t i = 0; i < map.parts && i < kMaxParts; i++) total += map.blocks[i];
  return total;
}

bool session_locate(const SessionMap& map, uint32_t seq, uint32_t* part, uint32_t* block) {
  if (seq == 0) return false;
  uint32_t remaining = seq - 1;
  for (uint32_t i = 0; i < map.parts && i < kMaxParts; i++) {
    if (remaining < map.blocks[i]) {
      *part = i;
      *block = remaining;
      return true;
    }
    remaining -= map.blocks[i];
  }
  return false;
}

// --- commands and texts -------------------------------------------------------------------------------

static int hex_value(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

bool hex_decode(const char* hex, uint8_t* out, size_t out_len) {
  if (hex == nullptr || strlen(hex) != out_len * 2) return false;
  for (size_t i = 0; i < out_len; i++) {
    const int hi = hex_value(hex[2 * i]);
    const int lo = hex_value(hex[2 * i + 1]);
    if (hi < 0 || lo < 0) return false;
    out[i] = (uint8_t)((hi << 4) | lo);
  }
  return true;
}

bool hex_text(const char* hex, char* out, size_t capacity) {
  const size_t n = hex ? strlen(hex) : 0;
  if (n == 0 || n % 2 != 0 || n / 2 + 1 > capacity) return false;
  if (!hex_decode(hex, (uint8_t*)out, n / 2)) return false;
  out[n / 2] = '\0';
  return strlen(out) == n / 2;  // no NUL bytes hidden inside
}

static bool word_ok(const char* w, size_t max) { return w && w[0] && strlen(w) <= max; }

static bool parse_u64(const char* text, uint64_t* out) {
  if (!text || !text[0] || strlen(text) > 19) return false;
  uint64_t v = 0;
  for (const char* p = text; *p; p++) {
    if (*p < '0' || *p > '9') return false;
    v = v * 10 + (uint64_t)(*p - '0');
  }
  *out = v;
  return true;
}

static bool session_ok(const char* s) {
  if (!s || strlen(s) != 16) return false;
  for (const char* p = s; *p; p++) {
    const char c = *p;
    if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-')) {
      return false;
    }
  }
  return true;
}

Command parse_command(const char* line) {
  Command c;
  memset(&c, 0, sizeof(c));
  c.kind = Command::Invalid;
  if (line == nullptr || strlen(line) > kMaxCommandBytes) return c;
  char buffer[kMaxCommandBytes + 1];
  strcpy(buffer, line);
  // Split on single spaces without strtok (not safe when two tasks parse at once).
  char* words[6] = {nullptr};
  int count = 0;
  char* p = buffer;
  while (*p) {
    while (*p == ' ') *p++ = '\0';
    if (!*p) break;
    if (count == 6) return c;  // too many words
    words[count++] = p;
    while (*p && *p != ' ') p++;
  }
  if (count == 0) return c;
  const char* verb = words[0];
  uint64_t number = 0;

  if (strcmp(verb, "REC") == 0 && count == 2) {
    if (strcmp(words[1], "START") == 0) c.kind = Command::RecStart;
    if (strcmp(words[1], "STOP") == 0) c.kind = Command::RecStop;
  } else if ((strcmp(verb, "ACK") == 0 || strcmp(verb, "RESEND") == 0) && count == 3) {
    if (session_ok(words[1]) && parse_u64(words[2], &number) && number <= 0xFFFFFFFFull) {
      strcpy(c.session, words[1]);
      c.seq = (uint32_t)number;
      c.kind = verb[0] == 'A' ? Command::Ack : Command::Resend;
    }
  } else if (strcmp(verb, "TIME") == 0 && count == 2) {
    if (parse_u64(words[1], &number)) {
      c.number = number;
      c.kind = Command::Time;
    }
  } else if (strcmp(verb, "WIFI") == 0 && count == 2 && strcmp(words[1], "CLEAR") == 0) {
    c.kind = Command::WifiClear;
  } else if (strcmp(verb, "WIFI") == 0 && count == 3) {
    if (hex_text(words[1], c.text1, 33) && hex_text(words[2], c.text2, 65)) c.kind = Command::Wifi;
  } else if (strcmp(verb, "OTA") == 0 && count == 5) {
    uint8_t sha[32];
    if (word_ok(words[1], sizeof(c.version) - 1) && parse_u64(words[2], &number) && number > 0 &&
        number <= 0xFFFFFFFFull && hex_decode(words[3], sha, 32) && hex_text(words[4], c.text1, sizeof(c.text1))) {
      strcpy(c.version, words[1]);
      c.size = (uint32_t)number;
      strcpy(c.sha256, words[3]);
      c.kind = Command::Ota;
    }
  } else if (strcmp(verb, "FORGET") == 0 && count == 1) {
    c.kind = Command::Forget;
  }
  return c;
}

int info_text(char* out, size_t capacity, const char* product_id, const char* version, int battery_percent,
              bool bonded) {
  const int battery = battery_percent < 0 ? 0 : (battery_percent > 100 ? 100 : battery_percent);
  return snprintf(out, capacity, "ASKINFO/1 %s %s %d %d", product_id, version, battery, bonded ? 1 : 0);
}

int status_text(char* out, size_t capacity, const char* state, int battery_percent, const char* session_id,
                uint32_t last_seq, int ota_percent) {
  const int battery = battery_percent < 0 ? 0 : (battery_percent > 100 ? 100 : battery_percent);
  const char* session = (session_id && session_id[0]) ? session_id : "-";
  return snprintf(out, capacity, "ASKSTAT/1 %s %d %s %lu %d", state, battery, session, (unsigned long)last_seq,
                  ota_percent);
}

// --- firmware updates ---------------------------------------------------------------------------------

bool ota_image_ok(uint32_t expected_size, const char* expected_sha_hex, uint32_t got_size, const uint8_t got_sha[32]) {
  if (expected_size == 0 || got_size != expected_size) return false;
  uint8_t expected[32];
  if (!hex_decode(expected_sha_hex, expected, 32)) return false;
  uint8_t diff = 0;
  for (int i = 0; i < 32; i++) diff |= (uint8_t)(expected[i] ^ got_sha[i]);
  return diff == 0;
}

// --- decisions added after the final review ----------------------------------------------------------

AuthOutcome auth_outcome(bool passkey_shown, int status, bool encrypted, bool authenticated) {
  if (status == 0 && encrypted) {
    if (!authenticated) return AuthOutcome::Reject;
    return passkey_shown ? AuthOutcome::NewBond : AuthOutcome::Trusted;
  }
  return passkey_shown ? AuthOutcome::Failed : AuthOutcome::Ignore;
}

bool meeting_complete(bool listing_ok, bool part_being_written, bool other_meeting_recording, bool busy_recording) {
  if (!listing_ok || part_being_written) return false;
  return !busy_recording || other_meeting_recording;
}

bool stall_rewind(uint32_t next, uint32_t acked_through, uint32_t last_progress_ms, uint32_t now_ms,
                  uint32_t* rewind_to) {
  const uint32_t from = acked_through ? acked_through + 1 : 0;
  if (next <= from || now_ms - last_progress_ms < kStallRewindMs) return false;
  *rewind_to = from;
  return true;
}

StartTime meeting_start_time(bool timed, uint32_t part_boot, uint32_t this_boot, int64_t now_epoch,
                             uint32_t uptime_now_seconds, int64_t when, int64_t* out_epoch) {
  if (timed) {
    *out_epoch = when;
    return StartTime::Known;
  }
  if (part_boot == 0 || part_boot != this_boot) return StartTime::Unknowable;
  if (!ask::clock_is_plausible(now_epoch)) return StartTime::Wait;
  return ask::resolve_start_time(now_epoch, uptime_now_seconds, when, out_epoch) ? StartTime::Resolved
                                                                                : StartTime::Unknowable;
}

// A character people leave around the parts by accident: the usage example's <brackets>, and quotes.
static bool wrapper_char(char c) { return c == '<' || c == '>' || c == '"' || c == '\''; }
static bool gap_char(char c) { return c == ' ' || c == '\t'; }

ProvisionArgs provision_args(const char* rest, char id[11], char secret[65]) {
  id[0] = 0;
  secret[0] = 0;
  ProvisionArgs out = {false, ProvisionProblem::Missing, 0};
  if (rest == nullptr) return out;
  size_t words = 0;
  size_t id_length = 0;
  size_t secret_length = 0;  // every character found, even past 64
  bool all_hex = true;
  const char* p = rest;
  while (*p) {
    while (*p && gap_char(*p)) p++;
    if (!*p) break;
    const char* start = p;
    while (*p && !gap_char(*p)) p++;
    const char* end = p;
    while (start < end && wrapper_char(*start)) start++;
    while (end > start && wrapper_char(end[-1])) end--;
    if (start == end) continue;
    words++;
    for (const char* q = start; q < end; q++) {
      char c = *q;
      if (words == 1) {
        if (c >= 'a' && c <= 'z') c = (char)(c - 'a' + 'A');
        if (id_length < 10) id[id_length] = c;
        id_length++;
      } else {
        if (c >= 'A' && c <= 'F') c = (char)(c - 'A' + 'a');
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) all_hex = false;
        if (secret_length < 64) secret[secret_length] = c;
        secret_length++;
      }
    }
  }
  id[id_length < 10 ? id_length : 10] = 0;
  secret[secret_length < 64 ? secret_length : 64] = 0;
  if (words < 2) return out;
  if (id_length != 10 || !product_id_valid(id)) {
    out.problem = ProvisionProblem::BadId;
    return out;
  }
  out.secret_length = secret_length;
  if (!all_hex) {
    out.problem = ProvisionProblem::SecretNotHex;
    secret[0] = 0;
    return out;
  }
  if (secret_length != 64) {
    out.problem = ProvisionProblem::SecretLength;
    secret[0] = 0;
    return out;
  }
  out.ok = true;
  out.problem = ProvisionProblem::None;
  return out;
}

uint32_t choose_passkey(bool locked, int32_t test_passkey, uint32_t emailed_code, uint32_t random_value) {
  if (locked) return random_value % 1000000u;
  if (test_passkey >= 0 && test_passkey <= 999999) return (uint32_t)test_passkey;
  return emailed_code;
}

}  // namespace askble
