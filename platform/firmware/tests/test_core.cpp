// Tests for the firmware's pure logic (locket/core.cpp). Self-checking: prints one line per failure and
// exits non-zero if anything failed. No framework, so it builds with any C++11 compiler (see run.ps1).

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "core.h"

static int failures = 0;
static int checks = 0;

#define CHECK(condition)                                                              \
  do {                                                                                \
    checks++;                                                                         \
    if (!(condition)) {                                                               \
      failures++;                                                                     \
      printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #condition);                     \
    }                                                                                 \
  } while (0)

#define CHECK_STR(actual, expected)                                                   \
  do {                                                                                \
    checks++;                                                                         \
    if (strcmp((actual), (expected)) != 0) {                                          \
      failures++;                                                                     \
      printf("FAIL %s:%d  got \"%s\" expected \"%s\"\n", __FILE__, __LINE__, (actual), (expected)); \
    }                                                                                 \
  } while (0)

using namespace ask;

static uint32_t u32(const uint8_t* p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static uint16_t u16(const uint8_t* p) { return (uint16_t)(p[0] | (p[1] << 8)); }

static void sha_hex(const uint8_t* data, size_t length, char out[65]) {
  Sha256 ctx;
  uint8_t digest[32];
  sha256_begin(&ctx);
  sha256_update(&ctx, data, length);
  sha256_finish(&ctx, digest);
  hex_encode(digest, 32, out);
}

static void test_sha256() {
  char hex[65];
  sha_hex((const uint8_t*)"", 0, hex);
  CHECK_STR(hex, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  sha_hex((const uint8_t*)"abc", 3, hex);
  CHECK_STR(hex, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  const char* two_blocks = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
  sha_hex((const uint8_t*)two_blocks, strlen(two_blocks), hex);
  CHECK_STR(hex, "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");

  // One million 'a', fed in awkward pieces, as an upload read from a card in buffers would be.
  Sha256 ctx;
  uint8_t digest[32];
  uint8_t chunk[977];
  memset(chunk, 'a', sizeof(chunk));
  sha256_begin(&ctx);
  size_t left = 1000000;
  while (left > 0) {
    const size_t n = left < sizeof(chunk) ? left : sizeof(chunk);
    sha256_update(&ctx, chunk, n);
    left -= n;
  }
  sha256_finish(&ctx, digest);
  hex_encode(digest, 32, hex);
  CHECK_STR(hex, "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");

  // The padding rule changes at 55, 56 and 64 bytes: a one-shot hash and a byte-at-a-time hash must agree.
  uint8_t data[130];
  for (size_t i = 0; i < sizeof(data); i++) data[i] = (uint8_t)(i * 7 + 3);
  for (size_t length = 0; length <= sizeof(data); length++) {
    char whole[65], pieces[65];
    sha_hex(data, length, whole);
    Sha256 split;
    sha256_begin(&split);
    for (size_t i = 0; i < length; i++) sha256_update(&split, data + i, 1);
    sha256_finish(&split, digest);
    hex_encode(digest, 32, pieces);
    CHECK_STR(whole, pieces);
  }
}

static void test_wav_header() {
  uint8_t h[kWavHeaderBytes];
  wav_header(h, 32000, 16000, 1, 16);  // one second of 16 kHz mono
  CHECK(memcmp(h, "RIFF", 4) == 0);
  CHECK(u32(h + 4) == 32036);  // 36 + data: everything after the first eight bytes
  CHECK(memcmp(h + 8, "WAVEfmt ", 8) == 0);
  CHECK(u32(h + 16) == 16);
  CHECK(u16(h + 20) == 1);  // PCM
  CHECK(u16(h + 22) == 1);
  CHECK(u32(h + 24) == 16000);
  CHECK(u32(h + 28) == 32000);  // bytes per second
  CHECK(u16(h + 32) == 2);      // bytes per frame
  CHECK(u16(h + 34) == 16);
  CHECK(memcmp(h + 36, "data", 4) == 0);
  CHECK(u32(h + 40) == 32000);

  wav_header(h, 64000, 16000, 2, 16);  // stereo
  CHECK(u16(h + 32) == 4);
  CHECK(u32(h + 28) == 64000);

  wav_header(h, 0, 16000, 1, 16);  // a header for nothing yet, written when recording starts
  CHECK(u32(h + 4) == 36);
  CHECK(u32(h + 40) == 0);
}

static void test_wav_recovery() {
  CHECK(wav_recoverable_data_bytes(0, 1, 16) == 0);
  CHECK(wav_recoverable_data_bytes(44, 1, 16) == 0);  // header only
  CHECK(wav_recoverable_data_bytes(45, 1, 16) == 0);  // half a sample
  CHECK(wav_recoverable_data_bytes(46, 1, 16) == 2);
  CHECK(wav_recoverable_data_bytes(1000, 1, 16) == 956);
  CHECK(wav_recoverable_data_bytes(1001, 1, 16) == 956);  // a torn last sample is dropped
  CHECK(wav_recoverable_data_bytes(1003, 2, 16) == 956);  // stereo frames are 4 bytes
  CHECK(wav_recoverable_data_bytes(100, 0, 16) == 0);     // nonsense format never divides by zero
  CHECK(wav_recoverable_data_bytes(0xFFFFFFFFFFull, 1, 16) <= 0xFFFFFFFFu - 36u);  // a header cannot describe more
}

static void test_time() {
  char t[21];
  CHECK(iso8601_utc(1704067200, t));
  CHECK_STR(t, "2024-01-01T00:00:00Z");
  CHECK(iso8601_utc(1709210096, t));  // a leap day
  CHECK_STR(t, "2024-02-29T12:34:56Z");
  CHECK(iso8601_utc(1767225600, t));
  CHECK_STR(t, "2026-01-01T00:00:00Z");
  CHECK(iso8601_utc(1793006846, t));
  CHECK_STR(t, "2026-10-26T09:27:26Z");  // checked against Python's datetime, not worked out by hand
  CHECK(iso8601_utc(4102444799, t));
  CHECK_STR(t, "2099-12-31T23:59:59Z");

  // A clock that was never set must not produce a timestamp that would be stored as the truth.
  strcpy(t, "untouched");
  CHECK(!iso8601_utc(0, t));
  CHECK(!iso8601_utc(1704067199, t));
  CHECK(!iso8601_utc(4102444800, t));
  CHECK(!iso8601_utc(-5, t));
  CHECK_STR(t, "untouched");
  CHECK(!clock_is_plausible(0));
  CHECK(!clock_is_plausible(86400));
  CHECK(clock_is_plausible(1704067200));
  CHECK(clock_is_plausible(1793006846));
  CHECK(!clock_is_plausible(4102444800));
}

static void test_upload_outcomes() {
  // Stored, or stored before.
  CHECK(classify_upload(201, "") == Upload::Done);
  CHECK(classify_upload(200, "") == Upload::Done);
  // The credential is no longer good: stop, do not hammer the server.
  CHECK(classify_upload(401, "invalid_device_credential") == Upload::Repair);
  CHECK(classify_upload(401, "") == Upload::Repair);
  // Same id, different content: only that kind of 409 needs a new id.
  CHECK(classify_upload(409, "recording_conflict") == Upload::NewId);
  CHECK(classify_upload(409, "something_else") == Upload::Retry);
  CHECK(classify_upload(409, "") == Upload::Retry);
  // Damaged bytes: send again.
  CHECK(classify_upload(422, "checksum_mismatch") == Upload::Retry);
  // Audio the server has judged and will never take.
  const char* never[] = {"invalid_audio", "truncated_audio", "unsupported_audio_format", "unsupported_sample_rate",
                         "audio_too_short", "audio_too_long", "silent_audio", "validation_error"};
  for (size_t i = 0; i < sizeof(never) / sizeof(never[0]); i++) {
    CHECK(classify_upload(422, never[i]) == Upload::Reject);
    CHECK(classify_upload(413, never[i]) == Upload::Reject);
  }
  CHECK(classify_upload(413, "") == Upload::Reject);
  CHECK(classify_upload(413, "upload_too_large") == Upload::Reject);
  CHECK(classify_upload(415, "unsupported_media_type") == Upload::Reject);
  // Not acknowledged for any other reason: keep the file and try later.
  CHECK(classify_upload(408, "upload_timeout") == Upload::Retry);
  CHECK(classify_upload(429, "rate_limited") == Upload::Retry);
  CHECK(classify_upload(500, "internal_error") == Upload::Retry);
  CHECK(classify_upload(502, "") == Upload::Retry);
  CHECK(classify_upload(503, "database_unavailable") == Upload::Retry);
  CHECK(classify_upload(0, "") == Upload::Retry);    // no answer at all
  CHECK(classify_upload(-1, "") == Upload::Retry);   // connection refused or timed out
  CHECK(classify_upload(404, "not_found") == Upload::Retry);
  CHECK(classify_upload(200, 0) == Upload::Done);    // a missing code is safe
  CHECK(classify_upload(422, 0) == Upload::Retry);
}

static void test_backoff() {
  // The first retry waits between half of the base and the base.
  CHECK(backoff_ms(0, 1000, 60000, 0) == 500);
  CHECK(backoff_ms(0, 1000, 60000, 0xFFFFFFFFu) >= 500 && backoff_ms(0, 1000, 60000, 0xFFFFFFFFu) <= 1000);
  // Each failed attempt doubles the ceiling.
  CHECK(backoff_ms(3, 1000, 60000, 0) == 4000);
  uint32_t top = 0;
  for (uint32_t r = 0; r < 5000; r += 7) {
    const uint32_t v = backoff_ms(3, 1000, 60000, r * 2654435761u);
    CHECK(v >= 4000 && v <= 8000);
    if (v > top) top = v;
  }
  CHECK(top > 7000);  // the spread really covers the upper half
  // Capped, and no overflow however many attempts there have been.
  CHECK(backoff_ms(6, 1000, 60000, 0) == 30000);
  CHECK(backoff_ms(100, 1000, 60000, 0xFFFFFFFFu) <= 60000);
  CHECK(backoff_ms(1000000, 1000, 60000, 12345) >= 30000);
  CHECK(backoff_ms(4000000000u, 4000000000u, 4000000000u, 7) <= 4000000000u);
  // Never waits longer than a later attempt could.
  uint32_t previous = 0;
  for (uint32_t attempt = 0; attempt < 12; attempt++) {
    const uint32_t floor_now = backoff_ms(attempt, 1000, 60000, 0);
    CHECK(floor_now >= previous);
    previous = floor_now;
  }
  CHECK(backoff_ms(5, 1000, 0, 5) == 0);
  CHECK(backoff_ms(5, 0, 1000, 5) == 0);
}

static void test_client_ids() {
  char id[kClientIdBytes];
  CHECK(client_id_make(id, sizeof(id), 0x1, 0xabcdef12, 0x2a));
  CHECK_STR(id, "r00000001abcdef12-0000002a");
  CHECK(strlen(id) == 26);
  CHECK(client_id_valid(id));
  CHECK(client_id_make(id, sizeof(id), 0xFFFFFFFFu, 0xFFFFFFFFu, 0xFFFFFFFFu));
  CHECK(client_id_valid(id));
  char small[26];
  CHECK(!client_id_make(small, sizeof(small), 1, 2, 3));  // never writes a cut-off id

  // Different boots or sequences never collide.
  char a[kClientIdBytes], b[kClientIdBytes], c[kClientIdBytes];
  client_id_make(a, sizeof(a), 5, 100, 1);
  client_id_make(b, sizeof(b), 5, 100, 2);
  client_id_make(c, sizeof(c), 6, 100, 1);
  CHECK(strcmp(a, b) != 0 && strcmp(a, c) != 0 && strcmp(b, c) != 0);

  // What the server accepts: 8 to 64 of letters, digits, '-', '_'.
  CHECK(client_id_valid("abcdefgh"));
  CHECK(client_id_valid("A-b_9-Zz"));
  CHECK(!client_id_valid("abcdefg"));  // 7
  CHECK(!client_id_valid(""));
  CHECK(!client_id_valid(0));
  CHECK(!client_id_valid("has space1"));
  CHECK(!client_id_valid("slash/inside"));
  CHECK(!client_id_valid("dots.inside1"));
  CHECK(!client_id_valid("../../etc/passwd"));
  CHECK(client_id_valid("0123456789012345678901234567890123456789012345678901234567890123"));    // 64
  CHECK(!client_id_valid("01234567890123456789012345678901234567890123456789012345678901234"));  // 65
}

static void test_battery() {
  CHECK(battery_percent(4200) == 100);
  CHECK(battery_percent(4350) == 100);  // a charger can push a cell a little over
  CHECK(battery_percent(3270) == 0);
  CHECK(battery_percent(3100) == 0);
  CHECK(battery_percent(3800) == 40);
  CHECK(battery_percent(3815) == 43);
  CHECK(battery_percent(3000) == 0);
  // Not a single cell: report unknown rather than a made-up number.
  CHECK(battery_percent(0) == -1);
  CHECK(battery_percent(2999) == -1);
  CHECK(battery_percent(4401) == -1);
  CHECK(battery_percent(-300) == -1);
  int previous = -1;
  for (int mv = 3000; mv <= 4400; mv++) {
    const int p = battery_percent(mv);
    CHECK(p >= 0 && p <= 100);
    CHECK(p >= previous);  // more volts is never less charge
    previous = p;
  }
}

static void test_json() {
  const char* pair =
      "{\"deviceId\":\"ASK-A7F2\",\"credential\":\"ASK-A7F2.s3cr3t_-tok\","
      "\"config\":{\"chunkSeconds\":10,\"heartbeatIntervalSeconds\":30,\"sampleRates\":[16000],"
      "\"maxUploadBytes\":26214400}}";
  char text[64];
  uint32_t n = 0;
  CHECK(json_string(pair, "deviceId", text, sizeof(text)));
  CHECK_STR(text, "ASK-A7F2");
  CHECK(json_string(pair, "credential", text, sizeof(text)));
  CHECK_STR(text, "ASK-A7F2.s3cr3t_-tok");
  CHECK(json_uint(pair, "chunkSeconds", &n) && n == 10);
  CHECK(json_uint(pair, "heartbeatIntervalSeconds", &n) && n == 30);
  CHECK(json_uint(pair, "maxUploadBytes", &n) && n == 26214400u);

  // Missing, wrong kind, and a value that is not the thing asked for.
  CHECK(!json_string(pair, "nothing", text, sizeof(text)));
  CHECK(!json_uint(pair, "nothing", &n));
  CHECK(!json_string(pair, "chunkSeconds", text, sizeof(text)));  // a number, not a string
  CHECK(!json_uint(pair, "credential", &n));                      // a string, not a number

  // The error body: {"error":{"code":"...","message":"..."}}
  const char* error = "{\"error\":{\"code\":\"recording_conflict\",\"message\":\"Same id, different bytes.\"}}";
  CHECK(json_string(error, "code", text, sizeof(text)));
  CHECK_STR(text, "recording_conflict");

  // Whitespace is allowed, and a quoted value that merely looks like a key is not mistaken for one.
  const char* spaced = "{ \"message\" : \"say \\\"chunkSeconds\\\": 5\" ,\n \"chunkSeconds\" :  7 }";
  CHECK(json_uint(spaced, "chunkSeconds", &n) && n == 7);
  const char* decoy = "{\"note\":\"credential\",\"credential\":\"real\"}";
  CHECK(json_string(decoy, "credential", text, sizeof(text)));
  CHECK_STR(text, "real");

  // Escapes: a quote and a backslash decode; anything fancier is refused rather than guessed.
  CHECK(json_string("{\"k\":\"a\\\"b\\\\c\"}", "k", text, sizeof(text)));
  CHECK_STR(text, "a\"b\\c");
  CHECK(!json_string("{\"k\":\"caf\\u00e9\"}", "k", text, sizeof(text)));

  // Never overruns the caller's buffer, never returns half a value.
  char tiny[8];
  CHECK(!json_string(pair, "credential", tiny, sizeof(tiny)));
  CHECK(!json_string(pair, "deviceId", tiny, 0));
  CHECK(json_string(pair, "deviceId", tiny, 9));
  CHECK(!json_string(pair, "deviceId", tiny, 8));  // 8 characters need 9 bytes with the terminator
  CHECK(!json_string("{\"k\":\"unterminated", "k", text, sizeof(text)));
  CHECK(!json_string("", "k", text, sizeof(text)));
  CHECK(!json_string(0, "k", text, sizeof(text)));
  CHECK(!json_uint("{\"k\":99999999999}", "k", &n));  // does not fit in 32 bits
  CHECK(json_uint("{\"k\":4294967295}", "k", &n) && n == 4294967295u);
  CHECK(!json_uint("{\"k\":-5}", "k", &n));
}

int main() {
  test_sha256();
  test_wav_header();
  test_wav_recovery();
  test_time();
  test_upload_outcomes();
  test_backoff();
  test_client_ids();
  test_battery();
  test_json();
  printf("%d checks, %d failed\n", checks, failures);
  return failures == 0 ? 0 : 1;
}
