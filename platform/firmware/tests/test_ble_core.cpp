// Tests for the Bluetooth locket's pure logic (locket_ble/ble_core.cpp). Self-checking: prints one line per
// failure and exits non-zero if anything failed. The pairing values come from the server's tests
// (platform/api/tests/test_locket_codes.py) and the frame bytes from the app's (platform/web/src/domain/device/
// frameVector.test.ts): if any side changes, one of the three suites fails.

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "ble_core.h"

static int failures = 0;
static int checks = 0;

#define CHECK(condition)                                                          \
  do {                                                                            \
    checks++;                                                                     \
    if (!(condition)) {                                                           \
      failures++;                                                                 \
      printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #condition);                 \
    }                                                                             \
  } while (0)

#define CHECK_STR(actual, expected)                                                                \
  do {                                                                                             \
    checks++;                                                                                      \
    if (strcmp((actual), (expected)) != 0) {                                                       \
      failures++;                                                                                  \
      printf("FAIL %s:%d  got \"%s\" expected \"%s\"\n", __FILE__, __LINE__, (actual), (expected)); \
    }                                                                                              \
  } while (0)

using namespace askble;

static void from_hex(const char* hex, uint8_t* out, size_t n) { CHECK(hex_decode(hex, out, n)); }

// --- reference IMA ADPCM decoder (the app's algorithm), to check the encoder ---------------------------

static const int16_t kSteps[89] = {
    7,     8,     9,     10,    11,    12,    13,    14,    16,    17,    19,    21,    23,    25,    28,
    31,    34,    37,    41,    45,    50,    55,    60,    66,    73,    80,    88,    97,    107,   118,
    130,   143,   157,   173,   190,   209,   230,   253,   279,   307,   337,   371,   408,   449,   494,
    544,   598,   658,   724,   796,   876,   963,   1060,  1166,  1282,  1411,  1552,  1707,  1878,  2066,
    2272,  2499,  2749,  3024,  3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,  7132,  7845,  8630,
    9493,  10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767};
static const int8_t kIndexChange[16] = {-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8};

static int16_t step(int* predictor, int* index, int code) {
  const int size = kSteps[*index];
  int delta = size >> 3;
  if (code & 4) delta += size;
  if (code & 2) delta += size >> 1;
  if (code & 1) delta += size >> 2;
  int p = (code & 8) ? *predictor - delta : *predictor + delta;
  if (p > 32767) p = 32767;
  if (p < -32768) p = -32768;
  *predictor = p;
  int i = *index + kIndexChange[code];
  *index = i < 0 ? 0 : (i > 88 ? 88 : i);
  return (int16_t)p;
}

static void decode(const uint8_t block[kAdpcmBlockBytes], int16_t out[kAdpcmSamples]) {
  int predictor = (int16_t)(block[0] | (block[1] << 8));
  int index = block[2] > 88 ? 88 : block[2];
  for (int i = 0; i < kAdpcmSamples / 2; i++) {
    out[2 * i] = step(&predictor, &index, block[4 + i] & 0x0f);
    out[2 * i + 1] = step(&predictor, &index, block[4 + i] >> 4);
  }
}

// --- tests ---------------------------------------------------------------------------------------------

static void test_pairing_vectors() {
  const char* master = "test-master-key-0123456789abcdef0123456789";
  uint8_t secret[32];
  hmac_sha256((const uint8_t*)master, strlen(master), (const uint8_t*)"ASK-7KQ2MX", 10, secret);
  char hex[65];
  ask::hex_encode(secret, 32, hex);
  CHECK_STR(hex, "89fbcaf408d79a1e3879d5f3559f7b38d63d1b45c74450ae3cefea6b2b798b04");

  uint8_t nonce[16];
  from_hex("00112233445566778899aabbccddeeff", nonce, 16);
  CHECK(pairing_code(secret, nonce) == 403836u);
  from_hex("0f1e2d3c4b5a69788796a5b4c3d2e1f0", nonce, 16);
  CHECK(pairing_code(secret, nonce) == 778647u);

  uint8_t secret2[32];
  from_hex("1f2bbb094260a90004e01c966a9c063fd7f81c44354820394347fc68c5fad0fa", secret2, 32);
  from_hex("ffffffffffffffffffffffffffffffff", nonce, 16);
  CHECK(pairing_code(secret2, nonce) == 400246u);

  char proof[65];
  from_hex("00112233445566778899aabbccddeeff", nonce, 16);
  bond_proof_hex(secret, nonce, proof);
  CHECK_STR(proof, "055ce7fed48fcd43c8693f2c210191a87603d1bc1ffe0afcb40772aa9ba6e21e");
  from_hex("ffffffffffffffffffffffffffffffff", nonce, 16);
  bond_proof_hex(secret2, nonce, proof);
  CHECK_STR(proof, "d1cfced0427579ea3f74a396c731eaf581aab4f4dfe2720c154ab1dc431385ea");
}

static void test_adpcm_known_block() {
  // The app's decodes_a_known_block: from state {0, 0}, code 7 again and again decodes to these samples.
  int16_t samples[kAdpcmSamples] = {11, 41, 104, 240, 533, 1164, 2521, 5431};
  for (int i = 8; i < kAdpcmSamples; i++) samples[i] = 5431;
  AdpcmState state = {0, 0};
  uint8_t block[kAdpcmBlockBytes];
  adpcm_encode_block(samples, &state, block);
  CHECK(block[0] == 0 && block[1] == 0 && block[2] == 0 && block[3] == 0);
  CHECK(block[4] == 0x77 && block[5] == 0x77 && block[6] == 0x77 && block[7] == 0x77);
  int16_t back[kAdpcmSamples];
  decode(block, back);
  CHECK(back[0] == 11 && back[7] == 5431);
}

static void sine_block(int16_t out[kAdpcmSamples], int block, double amplitude) {
  for (int i = 0; i < kAdpcmSamples; i++) {
    out[i] = (int16_t)lround(amplitude * sin(2.0 * M_PI * 1000.0 * (block * kAdpcmSamples + i) / 16000.0));
  }
}

static void test_adpcm_round_trip_and_self_contained_blocks() {
  int worst = 0;
  uint8_t first[kAdpcmBlockBytes], again[kAdpcmBlockBytes];
  for (int b = 0; b < 8; b++) {
    int16_t pcm[kAdpcmSamples], back[kAdpcmSamples];
    sine_block(pcm, b, 12000);
    adpcm_encode_block_auto(pcm, first);
    adpcm_encode_block_auto(pcm, again);
    CHECK(memcmp(first, again, kAdpcmBlockBytes) == 0);  // a resend is byte for byte the original
    decode(first, back);
    for (int i = 0; i < kAdpcmSamples; i++) {
      const int error = back[i] > pcm[i] ? back[i] - pcm[i] : pcm[i] - back[i];
      if (error > worst) worst = error;
    }
  }
  CHECK(worst < (int)(0.02 * 32768));
  int16_t silence[kAdpcmSamples] = {0};
  adpcm_encode_block_auto(silence, first);
  int16_t back[kAdpcmSamples];
  decode(first, back);
  CHECK(back[kAdpcmSamples - 1] == 0 || (back[kAdpcmSamples - 1] < 16 && back[kAdpcmSamples - 1] > -16));
}

static void test_frame_vector() {
  const uint8_t payload[3] = {1, 2, 3};
  uint8_t out[64];
  const size_t n = frame_encode(kFrameAudio, "SESSION000000001", 5, 1763114400000ULL, payload, 3, out, sizeof(out));
  CHECK(n == 45);
  char hex[2 * 64 + 1];
  ask::hex_encode(out, n, hex);
  CHECK_STR(hex, "41530103002a53455353494f4e303030303030303031000000050000019a81ceb9000000000301630d2c010203");
  CHECK(frame_encode(kFrameAudio, "SESSION000000001", 5, 0, payload, 3, out, 10) == 0);  // too small
  CHECK(crc32((const uint8_t*)"123456789", 9) == 0xCBF43926u);
}

static void test_commands_parse() {
  Command c = parse_command("REC START");
  CHECK(c.kind == Command::RecStart);
  CHECK(parse_command("REC STOP").kind == Command::RecStop);
  c = parse_command("ACK SESSION000000001 32");
  CHECK(c.kind == Command::Ack && c.seq == 32);
  CHECK_STR(c.session, "SESSION000000001");
  c = parse_command("RESEND SESSION000000001 33");
  CHECK(c.kind == Command::Resend && c.seq == 33);
  c = parse_command("TIME 1763114400000");
  CHECK(c.kind == Command::Time && c.number == 1763114400000ULL);
  c = parse_command("WIFI 486f6d65204e6574 704073732077c3b67264");
  CHECK(c.kind == Command::Wifi);
  CHECK_STR(c.text1, "Home Net");
  CHECK_STR(c.text2, "p@ss w\xc3\xb6rd");
  CHECK(parse_command("WIFI CLEAR").kind == Command::WifiClear);
  c = parse_command(
      "OTA 1.2.0 1074604 abababababababababababababababababababababababababababababababab "
      "68747470733a2f2f61736b2e6578616d706c652f6677");
  CHECK(c.kind == Command::Ota && c.size == 1074604);
  CHECK_STR(c.version, "1.2.0");
  CHECK_STR(c.text1, "https://ask.example/fw");
  CHECK(parse_command("FORGET").kind == Command::Forget);

  const char* bad[] = {"", "REC", "ACK SESSION000000001", "ACK SESSION000000001 x", "ACK TOOLONGSESSIONID00 1",
                       "WIFI zz 00", "WIFI 616", "OTA 1.2.0 abc ab 00", "OTA 1.2.0 10 short 6161", "NOPE",
                       "TIME -5", "RESEND bad id! 1"};
  for (const char* line : bad) {
    if (parse_command(line).kind != Command::Invalid) {
      failures++;
      printf("FAIL parse accepted \"%s\"\n", line);
    }
    checks++;
  }
}

static void test_info_and_status_text() {
  char out[96];
  info_text(out, sizeof(out), "ASK-7KQ2MX", "1.0.0-ble", 87, true);
  CHECK_STR(out, "ASKINFO/1 ASK-7KQ2MX 1.0.0-ble 87 1");
  info_text(out, sizeof(out), "ASK-7KQ2MX", "1.0.0-ble", -1, false);
  CHECK_STR(out, "ASKINFO/1 ASK-7KQ2MX 1.0.0-ble 0 0");
  status_text(out, sizeof(out), "recording", 87, "SESSION000000001", 120, 0);
  CHECK_STR(out, "ASKSTAT/1 recording 87 SESSION000000001 120 0");
  status_text(out, sizeof(out), "idle", 50, nullptr, 0, 0);
  CHECK_STR(out, "ASKSTAT/1 idle 50 - 0 0");
}

static void test_session_map_spans_parts() {
  SessionMap map = {};
  map.parts = 3;
  map.blocks[0] = 3;
  map.blocks[1] = 2;
  map.blocks[2] = 5;
  CHECK(session_total_blocks(map) == 10);
  uint32_t part = 99, block = 99;
  CHECK(session_locate(map, 1, &part, &block) && part == 0 && block == 0);
  CHECK(session_locate(map, 3, &part, &block) && part == 0 && block == 2);
  CHECK(session_locate(map, 4, &part, &block) && part == 1 && block == 0);
  CHECK(session_locate(map, 10, &part, &block) && part == 2 && block == 4);
  CHECK(!session_locate(map, 11, &part, &block));
  CHECK(!session_locate(map, 0, &part, &block));
  CHECK(blocks_for_samples(0) == 0 && blocks_for_samples(1) == 1 && blocks_for_samples(256) == 1 &&
        blocks_for_samples(257) == 2);
}

static void test_resend_maps_back_to_the_same_bytes() {
  // Two parts of a meeting as PCM; frame N encoded on the first pass equals frame N encoded after a RESEND.
  SessionMap map = {};
  map.parts = 2;
  map.blocks[0] = 4;
  map.blocks[1] = 3;
  uint8_t first[8][kAdpcmBlockBytes];
  for (uint32_t seq = 1; seq <= session_total_blocks(map); seq++) {
    uint32_t part, block;
    CHECK(session_locate(map, seq, &part, &block));
    int16_t pcm[kAdpcmSamples];
    sine_block(pcm, (int)(part * 10 + block), 9000);
    adpcm_encode_block_auto(pcm, first[seq]);
  }
  uint32_t part, block;
  CHECK(session_locate(map, 6, &part, &block) && part == 1 && block == 1);
  int16_t pcm[kAdpcmSamples];
  sine_block(pcm, (int)(part * 10 + block), 9000);
  uint8_t resent[kAdpcmBlockBytes];
  adpcm_encode_block_auto(pcm, resent);
  CHECK(memcmp(resent, first[6], kAdpcmBlockBytes) == 0);
}

static void test_session_ids() {
  char a[17], b[17], a2[17];
  CHECK(session_id_make("b0001-5f3a9c-s0007", a));
  CHECK(session_id_make("b0001-5f3a9c-s0008", b));
  CHECK(session_id_make("b0001-5f3a9c-s0007", a2));
  CHECK(strlen(a) == 16 && strcmp(a, a2) == 0 && strcmp(a, b) != 0);
  for (int i = 0; i < 16; i++) {
    const char ch = a[i];
    CHECK((ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9'));
  }
  CHECK(!session_id_make("", a));
}

static void test_passkey_attempts_are_limited() {
  PasskeyGuard g = {0, 0};
  CHECK(passkey_allowed(&g, 1000));
  for (int i = 0; i < 4; i++) passkey_failed(&g, 1000 + i);
  CHECK(passkey_allowed(&g, 2000));
  passkey_failed(&g, 2000);
  CHECK(!passkey_allowed(&g, 2001));
  CHECK(!passkey_allowed(&g, 1000 + 599999));
  CHECK(passkey_allowed(&g, 1000 + 600001));  // the window passed
  CHECK(g.failures == 0);
}

static void test_identity_is_write_once() {
  const char* secret = "89fbcaf408d79a1e3879d5f3559f7b38d63d1b45c74450ae3cefea6b2b798b04";
  CHECK(identity_accept(false, "ASK-7KQ2MX", secret) == Identity::Accepted);
  CHECK(identity_accept(true, "ASK-7KQ2MX", secret) == Identity::AlreadySet);
  CHECK(identity_accept(false, "ASK-7KQ2M", secret) == Identity::BadId);
  CHECK(identity_accept(false, "ask-7kq2mx", secret) == Identity::BadId);
  CHECK(identity_accept(false, "ASK-7KQ2MI", secret) == Identity::BadId);  // I is not in the alphabet
  CHECK(identity_accept(false, "ASK-7KQ2MX", "89fb") == Identity::BadSecret);
  CHECK(identity_accept(false, "ASK-7KQ2MX", "zz") == Identity::BadSecret);
  CHECK(product_id_valid("ASK-ABCDEF") && !product_id_valid("ASK-ABCDE0"));
}

static void test_ota_check_refuses_bad_images() {
  uint8_t sha[32];
  ask::Sha256 ctx;
  ask::sha256_begin(&ctx);
  ask::sha256_update(&ctx, (const uint8_t*)"firmware", 8);
  ask::sha256_finish(&ctx, sha);
  char hex[65];
  ask::hex_encode(sha, 32, hex);
  CHECK(ota_image_ok(8, hex, 8, sha));
  char upper[65];
  for (int i = 0; i < 65; i++) upper[i] = (hex[i] >= 'a' && hex[i] <= 'f') ? (char)(hex[i] - 32) : hex[i];
  CHECK(ota_image_ok(8, upper, 8, sha));
  CHECK(!ota_image_ok(9, hex, 8, sha));   // short
  CHECK(!ota_image_ok(8, hex, 9, sha));   // too long
  uint8_t other[32];
  memcpy(other, sha, 32);
  other[0] ^= 1;
  CHECK(!ota_image_ok(8, hex, 8, other));  // wrong checksum
  CHECK(!ota_image_ok(0, hex, 0, sha));    // empty
}

// Final review C2/I7: what a finished security procedure means.
static void test_auth_outcomes() {
  // A bond made just now with the passkey this locket handed out.
  CHECK(auth_outcome(true, 0, true, true) == AuthOutcome::NewBond);
  // A bonded phone coming back: trusted, but no new proof and no new number.
  CHECK(auth_outcome(false, 0, true, true) == AuthOutcome::Trusted);
  // Encrypted without the passkey ("Just Works"): never trusted, whatever was shown.
  CHECK(auth_outcome(false, 0, true, false) == AuthOutcome::Reject);
  CHECK(auth_outcome(true, 0, true, false) == AuthOutcome::Reject);
  // A wrong passkey counts against the limit...
  CHECK(auth_outcome(true, 13, false, false) == AuthOutcome::Failed);
  CHECK(auth_outcome(true, 0, false, false) == AuthOutcome::Failed);
  // ...but a phone that forgot its bond failing to re-encrypt does not.
  CHECK(auth_outcome(false, 13, false, false) == AuthOutcome::Ignore);
}

// Final review I1: a listing that failed never declares a meeting complete.
static void test_meeting_complete_needs_a_full_listing() {
  CHECK(meeting_complete(true, false, false, false));         // nothing recording
  CHECK(!meeting_complete(false, false, false, false));       // the card could not be listed
  CHECK(!meeting_complete(true, true, false, true));          // its part is being written
  CHECK(!meeting_complete(true, false, false, true));         // between two of its parts
  CHECK(meeting_complete(true, false, true, true));           // a newer meeting is recording
  CHECK(!meeting_complete(false, false, true, true));
}

// Final review I3: a lost RESEND must not stall the meeting.
static void test_stall_goes_back_to_after_the_last_ack() {
  uint32_t to = 0;
  CHECK(!stall_rewind(100, 99, 0, 60000, &to));                 // everything sent was acknowledged
  CHECK(!stall_rewind(100, 31, 1000, 1000 + kStallRewindMs - 1, &to));  // not silent long enough
  CHECK(stall_rewind(100, 31, 1000, 1000 + kStallRewindMs, &to));
  CHECK(to == 32);
  CHECK(stall_rewind(5, 0, 0, kStallRewindMs, &to));            // nothing acknowledged: from START
  CHECK(to == 0);
}

// Final review C4: a meeting recorded before the clock was set.
static void test_untimed_meetings_wait_for_the_clock() {
  const int64_t now = 1763114400;  // 2025-11-14, plausible
  int64_t at = 0;
  CHECK(meeting_start_time(true, 7, 7, 0, 0, now - 50, &at) == StartTime::Known && at == now - 50);
  // Same start-up, clock not set yet: wait (do not send a 1970 date the server refuses).
  CHECK(meeting_start_time(false, 7, 7, 0, 300, 100, &at) == StartTime::Wait);
  // Same start-up, clock set now: worked out exactly.
  CHECK(meeting_start_time(false, 7, 7, now, 300, 100, &at) == StartTime::Resolved && at == now - 200);
  // An earlier start-up: cannot be known.
  CHECK(meeting_start_time(false, 6, 7, now, 300, 100, &at) == StartTime::Unknowable);
  // Impossible numbers: cannot be known.
  CHECK(meeting_start_time(false, 7, 7, now, 300, 400, &at) == StartTime::Unknowable);
}

// Final review I4: the server's real download links fit.
static void test_long_ota_url() {
  char url[201];
  strcpy(url, "https://");
  for (size_t i = strlen(url); i < 200; i++) url[i] = 'a';
  url[200] = '\0';
  char line[520];
  int n = snprintf(line, sizeof(line), "OTA 10.20.30 1074604 %s ", "abababababababababababababababababababababababababababababababab");
  for (size_t i = 0; i < 200; i++) n += snprintf(line + n, sizeof(line) - n, "%02x", (unsigned char)url[i]);
  Command c = parse_command(line);
  CHECK(c.kind == Command::Ota);
  CHECK_STR(c.text1, url);
}

// The `provision` line as people really type or paste it.
static void test_provision_line_is_forgiving() {
  const char* hex = "7f3a9c1e5b2d4f6a8c0e1b3d5f7a9c2e4b6d8f0a1c3e5b7d9f2a4c6e8b0d1f3a";
  char id[11];
  char secret[65];
  ProvisionArgs got;
  // Exactly as the website shows it.
  char line1[160];
  snprintf(line1, sizeof(line1), "ASK-7K2M9Q %s", hex);
  got = provision_args(line1, id, secret);
  CHECK(got.ok);
  CHECK_STR(id, "ASK-7K2M9Q");
  CHECK_STR(secret, hex);
  // Extra spaces and tabs, the usage example's <brackets>, quotes, upper-case hex, a lower-case ID.
  char line2[200];
  snprintf(line2, sizeof(line2), "  <ask-7k2m9q>   \t \"%s\"  ", "7F3A9C1E5B2D4F6A8C0E1B3D5F7A9C2E4B6D8F0A1C3E5B7D9F2A4C6E8B0D1F3A");
  got = provision_args(line2, id, secret);
  CHECK(got.ok);
  CHECK_STR(id, "ASK-7K2M9Q");
  CHECK_STR(secret, hex);
  // The secret pasted split in two (a line wrap turned into a space).
  char line3[200];
  snprintf(line3, sizeof(line3), "ASK-7K2M9Q %.32s %s", hex, hex + 32);
  got = provision_args(line3, id, secret);
  CHECK(got.ok);
  CHECK_STR(secret, hex);
  // What is wrong is said: its length (never the secret), or that it has other characters.
  got = provision_args("ASK-7K2M9Q 7f3a9c", id, secret);
  CHECK(!got.ok && got.problem == ProvisionProblem::SecretLength && got.secret_length == 6);
  char line4[200];
  snprintf(line4, sizeof(line4), "ASK-7K2M9Q %.63sz", hex);
  got = provision_args(line4, id, secret);
  CHECK(!got.ok && got.problem == ProvisionProblem::SecretNotHex);
  CHECK(provision_args("", id, secret).problem == ProvisionProblem::Missing);
  CHECK(provision_args("ASK-7K2M9Q", id, secret).problem == ProvisionProblem::Missing);
  char line5[200];
  snprintf(line5, sizeof(line5), "ASK-7K2M9 %s", hex);
  CHECK(provision_args(line5, id, secret).problem == ProvisionProblem::BadId);
}

// The owner's test switch (ASK_TEST_PASSKEY): a fixed PIN instead of the emailed code, still under the
// wrong-code limit.
static void test_passkey_choice() {
  // Normal: the emailed code.
  CHECK(choose_passkey(false, -1, 482913, 777) == 482913);
  // Test build: the fixed PIN.
  CHECK(choose_passkey(false, 1234, 482913, 777) == 1234);
  // Too many wrong codes: a random value nobody was told, even in a test build.
  CHECK(choose_passkey(true, 1234, 482913, 777) == 777);
  CHECK(choose_passkey(true, -1, 482913, 777) == 777);
  // A PIN that cannot be a Bluetooth passkey (over six digits) is ignored: the emailed code is used.
  CHECK(choose_passkey(false, 1000000, 482913, 777) == 482913);
}

int main() {
  test_pairing_vectors();
  test_adpcm_known_block();
  test_adpcm_round_trip_and_self_contained_blocks();
  test_frame_vector();
  test_commands_parse();
  test_info_and_status_text();
  test_session_map_spans_parts();
  test_resend_maps_back_to_the_same_bytes();
  test_session_ids();
  test_passkey_attempts_are_limited();
  test_identity_is_write_once();
  test_ota_check_refuses_bad_images();
  test_auth_outcomes();
  test_meeting_complete_needs_a_full_listing();
  test_stall_goes_back_to_after_the_last_ack();
  test_untimed_meetings_wait_for_the_clock();
  test_long_ota_url();
  test_provision_line_is_forgiving();
  test_passkey_choice();
  printf("%d checks, %d failed\n", checks, failures);
  return failures == 0 ? 0 : 1;
}
