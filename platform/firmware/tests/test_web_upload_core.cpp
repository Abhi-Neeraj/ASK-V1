// Tests for the logic added for the button recorder (locket_web_upload/core.cpp): the parts of a recording, their
// file names, checking a finished WAV, confirming the server's receipt, planning, the button, and JSON helpers.
// The shared half of core.cpp is covered by test_core.cpp, which is run against this copy as well.
//
// Self-checking: prints one line per failure and exits non-zero if anything failed. No framework.

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "core.h"

static int failures = 0;
static int checks = 0;

#define CHECK(condition)                                         \
  do {                                                           \
    checks++;                                                    \
    if (!(condition)) {                                          \
      failures++;                                                \
      printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #condition); \
    }                                                            \
  } while (0)

#define CHECK_STR(actual, expected)                                                            \
  do {                                                                                         \
    checks++;                                                                                  \
    if (strcmp((actual), (expected)) != 0) {                                                   \
      failures++;                                                                              \
      printf("FAIL %s:%d  got \"%s\" expected \"%s\"\n", __FILE__, __LINE__, (actual), (expected)); \
    }                                                                                          \
  } while (0)

using namespace ask;

static const char* kSha = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

static void test_classify_denied() {
  CHECK(classify_upload(403, "") == Upload::Denied);
  CHECK(classify_upload(403, "forbidden") == Upload::Denied);
  CHECK(classify_upload(401, "") == Upload::Repair);  // unchanged: only 401 means "pair again"
  CHECK(classify_upload(200, "") == Upload::Done);
}

static void test_json_helpers() {
  bool b = false;
  CHECK(json_bool("{\"duplicate\":true}", "duplicate", &b) && b);
  CHECK(json_bool("{\"duplicate\": false }", "duplicate", &b) && !b);
  b = true;
  CHECK(!json_bool("{\"duplicate\":\"true\"}", "duplicate", &b));  // a string is not a boolean
  CHECK(b);                                                          // left alone on failure
  CHECK(!json_bool("{\"duplicate\":truex}", "duplicate", &b));
  CHECK(!json_bool("{}", "duplicate", &b));
  CHECK(!json_bool(0, "duplicate", &b));

  char out[64];
  CHECK(json_escape("plain", out, sizeof(out)));
  CHECK_STR(out, "plain");
  CHECK(json_escape("a\"b\\c\nd\te\rf", out, sizeof(out)));
  CHECK_STR(out, "a\\\"b\\\\c\\nd\\te\\rf");
  CHECK(json_escape("bell\x07", out, sizeof(out)));
  CHECK_STR(out, "bell\\u0007");
  CHECK(json_escape("caf\xc3\xa9", out, sizeof(out)));  // UTF-8 passes through
  CHECK_STR(out, "caf\xc3\xa9");
  CHECK(json_escape("", out, sizeof(out)));
  CHECK_STR(out, "");
  CHECK(json_escape(0, out, sizeof(out)));
  CHECK_STR(out, "");
  // Exactly fits: 3 characters need 4 bytes. One short fails and leaves nothing cut.
  CHECK(json_escape("abc", out, 4));
  CHECK_STR(out, "abc");
  CHECK(!json_escape("abc", out, 3));
  CHECK_STR(out, "");
  CHECK(!json_escape("\"\"", out, 4));  // each quote doubles: 4 characters + terminator need 5
  CHECK(json_escape("\"\"", out, 5));
  CHECK(!json_escape("a", out, 0));
}

static void test_wav_check() {
  uint8_t header[kWavHeaderBytes];
  uint32_t data = 0;
  wav_header(header, 32000, 16000, 1, 16);
  CHECK(wav_check(header, 44 + 32000, 16000, 1, 16, &data) == WavCheck::Ok);
  CHECK(data == 32000);
  CHECK(wav_check(header, 44 + 32000 + 1, 16000, 1, 16, &data) == WavCheck::Ok);  // a torn last sample is tolerated
  CHECK(wav_check(header, 44 + 32000 + 2, 16000, 1, 16, &data) == WavCheck::SizeMismatch);  // a whole frame of junk is not
  CHECK(wav_check(header, 44 + 31999, 16000, 1, 16, &data) == WavCheck::SizeMismatch);  // shorter than it says
  CHECK(wav_check(header, 44, 16000, 1, 16, &data) == WavCheck::TooSmall);
  CHECK(wav_check(header, 10, 16000, 1, 16, &data) == WavCheck::TooSmall);
  CHECK(wav_check(header, 44 + 32000, 8000, 1, 16, &data) == WavCheck::WrongFormat);
  CHECK(wav_check(header, 44 + 32000, 16000, 2, 16, &data) == WavCheck::WrongFormat);
  CHECK(wav_check(header, 44 + 32000, 16000, 1, 8, &data) == WavCheck::WrongFormat);
  CHECK(wav_check(header, 44 + 32000, 16000, 1, 16, 0) == WavCheck::Ok);  // the length is optional

  uint8_t bad[kWavHeaderBytes];
  memcpy(bad, header, sizeof(bad));
  bad[0] = 'X';
  CHECK(wav_check(bad, 44 + 32000, 16000, 1, 16, &data) == WavCheck::NotWav);
  memcpy(bad, header, sizeof(bad));
  memcpy(bad + 8, "WAVX", 4);
  CHECK(wav_check(bad, 44 + 32000, 16000, 1, 16, &data) == WavCheck::NotWav);
  memcpy(bad, header, sizeof(bad));
  memcpy(bad + 36, "dat!", 4);
  CHECK(wav_check(bad, 44 + 32000, 16000, 1, 16, &data) == WavCheck::NotWav);
  memcpy(bad, header, sizeof(bad));
  bad[4] ^= 0x01;  // the RIFF size no longer matches the data size
  CHECK(wav_check(bad, 44 + 32000, 16000, 1, 16, &data) == WavCheck::SizeMismatch);

  wav_header(header, 0, 16000, 1, 16);  // header for "nothing yet"
  CHECK(wav_check(header, 44 + 100, 16000, 1, 16, &data) == WavCheck::TooSmall);

  // A header that claims more audio than the largest a header can describe.
  wav_header(header, 0xFFFFFFF0u, 16000, 1, 16);
  CHECK(wav_check(header, 44 + 1000, 16000, 1, 16, &data) != WavCheck::Ok);
}

static void test_names() {
  char session[kClientIdBytes];
  CHECK(session_ref_make(session, sizeof(session), 3, 0xdeadbeef, 7));
  CHECK_STR(session, "s00000003deadbeef-00000007");
  char tiny[10];
  CHECK(!session_ref_make(tiny, sizeof(tiny), 1, 2, 3));

  ClipName name;
  memset(&name, 0, sizeof(name));
  CHECK(client_id_make(name.client_id, sizeof(name.client_id), 3, 0xdeadbeef, 12));
  strcpy(name.session, session);
  name.timed = true;
  name.when = 1767225600;
  name.part = 4;
  char text[kClipNameBytes];
  CHECK(clip_name_make(text, sizeof(text), name));
  CHECK_STR(text, "r00000003deadbeef-0000000c.1767225600.4.s00000003deadbeef-00000007.wav");
  CHECK(strlen(text) < kClipNameBytes);

  ClipName back;
  CHECK(clip_name_parse(text, &back));
  CHECK_STR(back.client_id, name.client_id);
  CHECK_STR(back.session, name.session);
  CHECK(back.timed && back.when == 1767225600 && back.part == 4);

  // A part recorded before the clock was set carries seconds since start, marked with a 'u'.
  name.timed = false;
  name.when = 1234;
  name.part = 1;
  CHECK(clip_name_make(text, sizeof(text), name));
  CHECK_STR(text, "r00000003deadbeef-0000000c.u1234.1.s00000003deadbeef-00000007.wav");
  CHECK(clip_name_parse(text, &back));
  CHECK(!back.timed && back.when == 1234 && back.part == 1);

  // Names that are not ours are refused, never uploaded under a guessed id.
  CHECK(!clip_name_parse("", &back));
  CHECK(!clip_name_parse("record.WAV", &back));
  CHECK(!clip_name_parse("r00000003deadbeef-0000000c.1767225600.wav", &back));  // the continuous-clip format
  CHECK(!clip_name_parse("r00000003deadbeef-0000000c.1767225600.4.s00000003deadbeef-00000007.mp3", &back));
  CHECK(!clip_name_parse("r00000003deadbeef-0000000c.1767225600.0.s00000003deadbeef-00000007.wav", &back));  // part 0
  CHECK(!clip_name_parse("r00000003deadbeef-0000000c.17672256x0.4.s00000003deadbeef-00000007.wav", &back));
  CHECK(!clip_name_parse("r00000003deadbeef-0000000c.1767225600.4.s00000003deadbeef-00000007.wav.tmp", &back));
  CHECK(!clip_name_parse("r0000.1767225600.4.s00000003deadbeef-00000007.wav", &back));  // id too short for the server
  CHECK(!clip_name_parse("r00000003deadbeef-0000000c.1767225600.4.s0 0.wav", &back));
  CHECK(!clip_name_parse("../r00000003deadbeef-0000000c.1767225600.4.s1234567890.wav", &back));  // a path is not a name
  CHECK(!clip_name_parse("r00000003deadbeef-0000000c.u.4.s00000003deadbeef-00000007.wav", &back));  // 'u' with no number

  // Cannot be made from bad pieces.
  ClipName bad = name;
  bad.part = 0;
  CHECK(!clip_name_make(text, sizeof(text), bad));
  bad = name;
  bad.part = 1000000;
  CHECK(!clip_name_make(text, sizeof(text), bad));
  bad = name;
  bad.when = -1;
  CHECK(!clip_name_make(text, sizeof(text), bad));
  bad = name;
  strcpy(bad.client_id, "bad.id");
  CHECK(!clip_name_make(text, sizeof(text), bad));
  CHECK(!clip_name_make(text, 20, name));  // does not fit

  // Names sort in the order they were recorded within one start-up: the part number is not what decides it, the
  // id (a counter) is, and ids are issued in order.
  char a[kClipNameBytes], b[kClipNameBytes];
  name.timed = true;
  name.when = 1767225600;
  client_id_make(name.client_id, sizeof(name.client_id), 3, 0xdeadbeef, 1);
  clip_name_make(a, sizeof(a), name);
  client_id_make(name.client_id, sizeof(name.client_id), 3, 0xdeadbeef, 2);
  clip_name_make(b, sizeof(b), name);
  CHECK(strcmp(a, b) < 0);
  client_id_make(name.client_id, sizeof(name.client_id), 4, 0x00000001, 0);  // a later start-up sorts after, whatever its salt
  clip_name_make(b, sizeof(b), name);
  CHECK(strcmp(a, b) < 0);

  CHECK(client_id_boot("r00000003deadbeef-0000000c") == 3);
  CHECK(client_id_boot("r000000ffdeadbeef-0000000c") == 255);
  CHECK(client_id_boot("x00000003deadbeef-0000000c") == 0);
  CHECK(client_id_boot("r0000000zdeadbeef-0000000c") == 0);
  CHECK(client_id_boot("short") == 0);
  CHECK(client_id_boot(0) == 0);
}

static void test_resolve_start_time() {
  int64_t out = 0;
  // The part started 100 s after the locket did; the clock was set when it had been up for 130 s, reading
  // 1767225730: so the part started 30 s earlier, at 1767225700.
  CHECK(resolve_start_time(1767225730, 130, 100, &out));
  CHECK(out == 1767225700);
  CHECK(resolve_start_time(1767225730, 100, 100, &out) && out == 1767225730);
  CHECK(!resolve_start_time(1767225730, 99, 100, &out));   // cannot have started in the future
  CHECK(!resolve_start_time(1767225730, 130, -1, &out));
  CHECK(!resolve_start_time(5, 130, 100, &out));           // the clock is still not plausible
  CHECK(!resolve_start_time(1704067200 + 10, 4000, 1, &out));  // the answer would fall before 2024
}

static void test_planning() {
  // 300 seconds is 9,600,000 bytes = 292 whole 32 KB blocks.
  CHECK(segment_blocks(300, 25 * 1024 * 1024, 32768, 32000) == 292);
  // The block count never gives more audio than asked for, so it never exceeds the server's length limit.
  for (uint32_t seconds = 1; seconds <= 600; seconds += 7) {
    const uint32_t blocks = segment_blocks(seconds, 25 * 1024 * 1024, 32768, 32000);
    CHECK((uint64_t)blocks * 32768 <= (uint64_t)seconds * 32000 || blocks == 1);
    CHECK(blocks >= 1);
  }
  // The server's upload size limit lowers it: 1 MiB allows (1048576 - 44) / 32768 = 31 blocks.
  CHECK(segment_blocks(300, 1024 * 1024, 32768, 32000) == 31);
  CHECK(segment_blocks(300, 0, 32768, 32000) == 292);  // size unknown: time decides
  CHECK(segment_blocks(0, 25 * 1024 * 1024, 32768, 32000) == 1);  // never zero
  CHECK(segment_blocks(300, 10, 32768, 32000) == 1);
  CHECK(segment_blocks(300, 1000, 0, 32000) == 1);

  CHECK(segment_seconds_for(300, 600) == 300);
  CHECK(segment_seconds_for(300, 120) == 120);
  CHECK(segment_seconds_for(300, 0) == 300);  // not told
  CHECK(segment_seconds_for(0, 600) == 1);

  CHECK(space_ok(100, 0, 10));
  CHECK(space_ok(100, 90, 10));
  CHECK(!space_ok(100, 91, 10));
  CHECK(!space_ok(100, 101, 0));
  CHECK(space_ok(100, 100, 0));
  CHECK(!space_ok(0, 0, 1));

  CHECK(upload_may_run(false, false, 99, 32, 50));  // idle: always
  CHECK(!upload_may_run(true, false, 0, 32, 50));   // recording and not allowed
  CHECK(upload_may_run(true, true, 0, 32, 50));
  CHECK(upload_may_run(true, true, 16, 32, 50));    // exactly half: still fine
  CHECK(!upload_may_run(true, true, 17, 32, 50));   // the card is falling behind: stand down
  CHECK(!upload_may_run(true, true, 0, 0, 50));
}

static void test_ack() {
  const char* client = "r00000003deadbeef-0000000c";
  char json[600];
  snprintf(json, sizeof(json),
           "{\"recordingId\":\"3f2a9c4e-1b7d-4e8a-9c3f-0a1b2c3d4e5f\",\"clientRecordingId\":\"%s\",\"status\":\"stored\","
           "\"sha256\":\"%s\",\"bytes\":9568300,\"durationMs\":299000,\"duplicate\":false,\"startedAt\":\"2026-01-01T00:00:00Z\"}",
           client, kSha);
  Ack ack;
  CHECK(ack_validate(json, client, kSha, 9568300, &ack));
  CHECK_STR(ack.recording_id, "3f2a9c4e-1b7d-4e8a-9c3f-0a1b2c3d4e5f");
  CHECK(!ack.duplicate);
  CHECK(ack_validate(json, client, kSha, 9568300, 0));  // the receipt itself is optional to keep

  // Anything that does not match is NOT a receipt, so the file would be kept.
  CHECK(!ack_validate(json, "r00000003deadbeef-0000000d", kSha, 9568300, &ack));  // a receipt for another clip
  CHECK(!ack_validate(json, client, "f123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", 9568300, &ack));
  CHECK(!ack_validate(json, client, kSha, 9568301, &ack));
  CHECK(!ack_validate(json, client, kSha, 0, &ack));

  char variant[600];
  auto without = [&](const char* from, const char* to) {
    strcpy(variant, json);
    char* at = strstr(variant, from);
    if (at) memcpy(at, to, strlen(to));  // same length replacement keeps the rest intact
    return variant;
  };
  CHECK(!ack_validate(without("\"stored\"", "\"queued\""), client, kSha, 9568300, &ack));
  CHECK(!ack_validate(without("\"status\"", "\"statuz\""), client, kSha, 9568300, &ack));
  CHECK(!ack_validate(without("3f2a9c4e-1b7d", "3f2a9c4e_1b7d"), client, kSha, 9568300, &ack));  // not a UUID
  CHECK(!ack_validate(without("\"durationMs\"", "\"durationMz\""), client, kSha, 9568300, &ack));
  CHECK(!ack_validate(without("\"recordingId\"", "\"recordingIz\""), client, kSha, 9568300, &ack));

  CHECK(!ack_validate("", client, kSha, 9568300, &ack));
  CHECK(!ack_validate("{}", client, kSha, 9568300, &ack));
  CHECK(!ack_validate("<html><body>Sign in to the guest Wi-Fi</body></html>", client, kSha, 9568300, &ack));
  CHECK(!ack_validate("OK", client, kSha, 9568300, &ack));
  CHECK(!ack_validate("{\"status\":\"stored\"}", client, kSha, 9568300, &ack));  // too little
  CHECK(!ack_validate(0, client, kSha, 9568300, &ack));
  CHECK(!ack_validate(json, 0, kSha, 9568300, &ack));
  CHECK(!ack_validate(json, client, 0, 9568300, &ack));

  // A repeat upload is acknowledged too, and says so.
  strcpy(variant, json);
  char* dup = strstr(variant, "\"duplicate\":false");
  memcpy(dup, "\"duplicate\":true ", 17);
  CHECK(ack_validate(variant, client, kSha, 9568300, &ack));
  CHECK(ack.duplicate);
}

static void test_debounce() {
  Debounce d;
  debounce_begin(&d, true);
  // Nothing happens while the button is up.
  for (uint32_t t = 1000; t < 1200; t += 10) CHECK(!debounce_pressed(&d, true, t, 50));

  // A clean press: down at 2000. Only reported once it has been down for 50 ms.
  int presses = 0;
  for (uint32_t t = 2000; t < 2400; t += 10) presses += debounce_pressed(&d, false, t, 50) ? 1 : 0;
  CHECK(presses == 1);  // once, not once per poll
  // Held down, then released: no second event on release.
  for (uint32_t t = 2400; t < 2600; t += 10) CHECK(!debounce_pressed(&d, true, t, 50));

  // Contact bounce: down, up, down, up, down within 30 ms, then held. One press.
  presses = 0;
  const bool bounce[] = {false, true, false, true, false, false, false, false, false, false, false, false};
  uint32_t t = 3000;
  for (bool level : bounce) {
    presses += debounce_pressed(&d, level, t, 50) ? 1 : 0;
    t += 10;
  }
  for (; t < 3400; t += 10) presses += debounce_pressed(&d, false, t, 50) ? 1 : 0;
  CHECK(presses == 1);

  // A glitch shorter than the debounce time is not a press.
  debounce_begin(&d, true);
  presses = 0;
  presses += debounce_pressed(&d, false, 5000, 50) ? 1 : 0;
  presses += debounce_pressed(&d, false, 5020, 50) ? 1 : 0;
  presses += debounce_pressed(&d, true, 5030, 50) ? 1 : 0;
  for (uint32_t u = 5040; u < 5300; u += 10) presses += debounce_pressed(&d, true, u, 50) ? 1 : 0;
  CHECK(presses == 0);

  // Two presses separated by a release are two events (start, then stop).
  debounce_begin(&d, true);
  presses = 0;
  for (uint32_t u = 6000; u < 6200; u += 10) presses += debounce_pressed(&d, false, u, 50) ? 1 : 0;
  for (uint32_t u = 6200; u < 6400; u += 10) presses += debounce_pressed(&d, true, u, 50) ? 1 : 0;
  for (uint32_t u = 6400; u < 6600; u += 10) presses += debounce_pressed(&d, false, u, 50) ? 1 : 0;
  CHECK(presses == 2);

  // The millisecond counter wrapping around does not fake or hide a press.
  debounce_begin(&d, true);
  presses = 0;
  for (uint32_t u = 0xFFFFFF00u; u != 0x00000100u; u += 10) presses += debounce_pressed(&d, false, u, 50) ? 1 : 0;
  CHECK(presses == 1);
}

static const char* state_name(RecState s) {
  switch (s) {
    case RecState::Booting: return "Booting";
    case RecState::Idle: return "Idle";
    case RecState::Recording: return "Recording";
    case RecState::Finalizing: return "Finalizing";
    case RecState::Error: return "Error";
  }
  return "?";
}

// The whole machine, written out a second time in a different shape, and compared with rec_step for every one of
// the 5 states x 8 events.
static void test_state_machine_table() {
  const RecState B = RecState::Booting, I = RecState::Idle, R = RecState::Recording, F = RecState::Finalizing,
                 E = RecState::Error;
  const PressAction N = PressAction::Ignore, S = PressAction::Start, P = PressAction::Stop;
  struct Row {
    RecState from;
    RecEvent event;
    RecState to;
    PressAction action;
  };
  static const Row rows[] = {
      // A press means start in Idle, stop in Recording, try-again in Error, and nothing otherwise.
      {B, RecEvent::Press, B, N}, {I, RecEvent::Press, I, S}, {R, RecEvent::Press, R, P},
      {F, RecEvent::Press, F, N}, {E, RecEvent::Press, E, S},
      // Start-up finishing.
      {B, RecEvent::BootedOk, I, N}, {I, RecEvent::BootedOk, I, N}, {R, RecEvent::BootedOk, R, N},
      {F, RecEvent::BootedOk, F, N}, {E, RecEvent::BootedOk, E, N},
      {B, RecEvent::BootFailed, E, N}, {I, RecEvent::BootFailed, I, N}, {R, RecEvent::BootFailed, R, N},
      {F, RecEvent::BootFailed, F, N}, {E, RecEvent::BootFailed, E, N},
      // A recording really starting, or being refused.
      {B, RecEvent::Started, B, N}, {I, RecEvent::Started, R, N}, {R, RecEvent::Started, R, N},
      {F, RecEvent::Started, F, N}, {E, RecEvent::Started, R, N},
      {B, RecEvent::StartRefused, B, N}, {I, RecEvent::StartRefused, E, N}, {R, RecEvent::StartRefused, R, N},
      {F, RecEvent::StartRefused, F, N}, {E, RecEvent::StartRefused, E, N},
      // The microphone stopping, and the last part being saved.
      {B, RecEvent::CaptureEnded, B, N}, {I, RecEvent::CaptureEnded, I, N}, {R, RecEvent::CaptureEnded, F, N},
      {F, RecEvent::CaptureEnded, F, N}, {E, RecEvent::CaptureEnded, E, N},
      {B, RecEvent::Saved, B, N}, {I, RecEvent::Saved, I, N}, {R, RecEvent::Saved, R, N},
      {F, RecEvent::Saved, I, N}, {E, RecEvent::Saved, E, N},
      {B, RecEvent::SavedWithProblem, B, N}, {I, RecEvent::SavedWithProblem, I, N},
      {R, RecEvent::SavedWithProblem, R, N}, {F, RecEvent::SavedWithProblem, E, N},
      {E, RecEvent::SavedWithProblem, E, N},
  };
  CHECK(sizeof(rows) / sizeof(rows[0]) == 5 * 8);  // every combination is listed exactly once
  for (size_t i = 0; i < sizeof(rows) / sizeof(rows[0]); i++) {
    const RecStep step = rec_step(rows[i].from, rows[i].event);
    if (step.next != rows[i].to || step.action != rows[i].action) {
      printf("  rec_step(%s, event %d) gave %s/%d, expected %s/%d\n", state_name(rows[i].from), (int)rows[i].event,
             state_name(step.next), (int)step.action, state_name(rows[i].to), (int)rows[i].action);
    }
    CHECK(step.next == rows[i].to);
    CHECK(step.action == rows[i].action);
  }
}

// The cycle the owner asked for, and the things that must not happen inside it.
static void test_state_machine_cycle() {
  RecState s = RecState::Booting;
  auto send = [&](RecEvent e) {
    const RecStep step = rec_step(s, e);
    s = step.next;
    return step.action;
  };
  CHECK(send(RecEvent::Press) == PressAction::Ignore);  // a press during start-up does nothing
  CHECK(s == RecState::Booting);
  send(RecEvent::BootedOk);
  CHECK(s == RecState::Idle);

  for (int cycle = 0; cycle < 3; cycle++) {
    CHECK(send(RecEvent::Press) == PressAction::Start);  // first press: start...
    CHECK(s == RecState::Idle);                            // ...but it is not recording until it really started
    send(RecEvent::Started);
    CHECK(s == RecState::Recording);
    CHECK(send(RecEvent::Press) == PressAction::Stop);   // second press: stop
    send(RecEvent::CaptureEnded);
    CHECK(s == RecState::Finalizing);
    for (int i = 0; i < 20; i++) {  // pressing like mad while the last part is saved
      CHECK(send(RecEvent::Press) == PressAction::Ignore);
      CHECK(s == RecState::Finalizing);
    }
    send(RecEvent::Saved);
    CHECK(s == RecState::Idle);  // back to ready, and it stays there until the next press
  }

  // A start that is refused is recoverable: the next press tries again.
  CHECK(send(RecEvent::Press) == PressAction::Start);
  send(RecEvent::StartRefused);
  CHECK(s == RecState::Error);
  CHECK(send(RecEvent::Press) == PressAction::Start);
  send(RecEvent::Started);
  CHECK(s == RecState::Recording);
  // A recording that ended badly is also recoverable.
  send(RecEvent::CaptureEnded);
  send(RecEvent::SavedWithProblem);
  CHECK(s == RecState::Error);
  CHECK(send(RecEvent::Press) == PressAction::Start);
}

// Whatever order events come in, a recording can only begin by Started, only end by CaptureEnded, and a press
// starts only from Idle or Error and stops only from Recording.
static void test_state_machine_cannot_be_confused() {
  uint32_t seed = 12345;
  RecState s = RecState::Booting;
  for (int i = 0; i < 200000; i++) {
    seed = seed * 1664525u + 1013904223u;
    const RecEvent e = (RecEvent)((seed >> 16) % 8);
    const RecState before = s;
    const RecStep step = rec_step(s, e);
    s = step.next;
    if (step.action == PressAction::Start) {
      CHECK(e == RecEvent::Press && (before == RecState::Idle || before == RecState::Error));
      CHECK(s == before);  // a press alone never changes the state; the start has to succeed first
    }
    if (step.action == PressAction::Stop) CHECK(e == RecEvent::Press && before == RecState::Recording);
    if (s == RecState::Recording && before != RecState::Recording) CHECK(e == RecEvent::Started);
    if (s == RecState::Finalizing && before != RecState::Finalizing) {
      CHECK(e == RecEvent::CaptureEnded && before == RecState::Recording);
    }
    if (before == RecState::Booting && s != RecState::Booting) {
      CHECK(e == RecEvent::BootedOk || e == RecEvent::BootFailed);
    }
    if (before == RecState::Finalizing && s != RecState::Finalizing) {
      CHECK(e == RecEvent::Saved || e == RecEvent::SavedWithProblem);
    }
  }
}

static StartChecks all_good() {
  StartChecks c;
  c.mic_ok = true;
  c.paired = true;
  c.needs_pairing = false;
  c.card_ready = true;
  c.ring_ready = true;
  c.free_bytes = 100u * 1024 * 1024;
  c.min_free_bytes = 8u * 1024 * 1024;
  c.pending_parts = 3;
  c.max_pending_parts = 1000;
  return c;
}

static void test_start_blockers() {
  StartChecks c = all_good();
  CHECK(start_blocker(c) == Blocker::None);
  CHECK_STR(blocker_text(Blocker::None), "");

  c = all_good();
  c.mic_ok = false;
  CHECK(start_blocker(c) == Blocker::MicFailed);
  c = all_good();
  c.paired = false;
  CHECK(start_blocker(c) == Blocker::NotPaired);
  c = all_good();
  c.needs_pairing = true;
  CHECK(start_blocker(c) == Blocker::NeedsPairing);
  c = all_good();
  c.card_ready = false;
  CHECK(start_blocker(c) == Blocker::NoCard);
  c = all_good();
  c.ring_ready = false;
  CHECK(start_blocker(c) == Blocker::NoMemory);
  c = all_good();
  c.free_bytes = c.min_free_bytes - 1;
  CHECK(start_blocker(c) == Blocker::CardFull);
  c.free_bytes = c.min_free_bytes;  // exactly the reserve is still enough
  CHECK(start_blocker(c) == Blocker::None);
  c = all_good();
  c.pending_parts = 1000;
  CHECK(start_blocker(c) == Blocker::TooManyWaiting);
  c.pending_parts = 999;
  CHECK(start_blocker(c) == Blocker::None);
  c.max_pending_parts = 0;  // 0 means "no limit"
  c.pending_parts = 100000;
  CHECK(start_blocker(c) == Blocker::None);

  // With several things wrong, the first one an owner would fix is reported.
  c = all_good();
  c.paired = false;
  c.card_ready = false;
  c.free_bytes = 0;
  CHECK(start_blocker(c) == Blocker::NotPaired);
  c.paired = true;
  CHECK(start_blocker(c) == Blocker::NoCard);

  // Every reason has words for the owner, and they are all different.
  const Blocker all[] = {Blocker::MicFailed, Blocker::NotPaired, Blocker::NeedsPairing, Blocker::NoCard,
                         Blocker::NoMemory,  Blocker::CardFull,  Blocker::TooManyWaiting};
  for (size_t i = 0; i < sizeof(all) / sizeof(all[0]); i++) {
    CHECK(strlen(blocker_text(all[i])) > 10);
    for (size_t j = i + 1; j < sizeof(all) / sizeof(all[0]); j++) CHECK(strcmp(blocker_text(all[i]), blocker_text(all[j])) != 0);
  }
}

// ---- Wi-Fi: what a failure is called, and which network names and passwords can be used -------------------------

static void test_wifi_reasons() {
  // The reasons the chip gives when a try fails or a link drops, in the owner's words.
  CHECK(strstr(wifi_reason_hint(201), "no network with that name") != 0);  // NO_AP_FOUND
  CHECK(strstr(wifi_reason_hint(201), "2.4 GHz") != 0);                     // the usual cause for a phone hotspot
  CHECK_STR(wifi_reason_hint(210), wifi_reason_hint(201));
  CHECK_STR(wifi_reason_hint(211), wifi_reason_hint(201));
  CHECK_STR(wifi_reason_hint(212), wifi_reason_hint(201));
  CHECK(strstr(wifi_reason_hint(202), "password was not accepted") != 0);  // AUTH_FAIL
  CHECK_STR(wifi_reason_hint(15), wifi_reason_hint(202));                    // 4WAY_HANDSHAKE_TIMEOUT
  CHECK_STR(wifi_reason_hint(204), wifi_reason_hint(202));                   // HANDSHAKE_TIMEOUT
  CHECK_STR(wifi_reason_hint(14), wifi_reason_hint(202));                    // MIC_FAILURE
  CHECK(strstr(wifi_reason_hint(203), "refused") != 0);                      // ASSOC_FAIL
  CHECK_STR(wifi_reason_hint(205), wifi_reason_hint(203));
  CHECK(strstr(wifi_reason_hint(200), "lost contact") != 0);  // BEACON_TIMEOUT
  CHECK(strstr(wifi_reason_hint(5), "too many") != 0);        // ASSOC_TOOMANY
  CHECK(strstr(wifi_reason_hint(8), "disconnected the locket") != 0);
  CHECK(strstr(wifi_reason_hint(2), "timed out") != 0);
  CHECK(strstr(wifi_reason_hint(1), "unspecified") != 0);
  // A code nobody planned for still gets words, never a null or empty string.
  CHECK(strlen(wifi_reason_hint(0)) > 10);
  CHECK(strlen(wifi_reason_hint(65535)) > 10);
  CHECK(strstr(wifi_reason_hint(777), "see the code") != 0);
  // A password is something a reason never contains: the hint is fixed text, whatever the code.
  for (unsigned code = 0; code < 300; code++) {
    CHECK(wifi_reason_hint(code) != 0);
    CHECK(strlen(wifi_reason_hint(code)) < 100);
  }
}

static void test_wifi_reasons_that_end_an_attempt() {
  // A try is over for good when there is no such network, or it turned us away or refused the password.
  const unsigned final_reasons[] = {201, 210, 211, 212, 202, 204, 15, 14, 203, 205, 5};
  for (size_t i = 0; i < sizeof(final_reasons) / sizeof(final_reasons[0]); i++) CHECK(wifi_reason_ends_attempt(final_reasons[i]));
  // Not these: a leave the locket asked for (8, sent when it drops a connection to start another), a timeout that
  // can pass, a lost link, and codes nobody planned for. Waiting out the limit is the safe thing for all of them.
  const unsigned open_reasons[] = {0, 1, 2, 3, 4, 8, 39, 46, 47, 200, 209, 777, 65535};
  for (size_t i = 0; i < sizeof(open_reasons) / sizeof(open_reasons[0]); i++) CHECK(!wifi_reason_ends_attempt(open_reasons[i]));
  // Every reason that ends an attempt has words of its own, never the "another reason" fallback.
  for (size_t i = 0; i < sizeof(final_reasons) / sizeof(final_reasons[0]); i++) {
    CHECK(strstr(wifi_reason_hint(final_reasons[i]), "see the code") == 0);
  }
}

static void test_wifi_credentials() {
  // The built-in network from config.h passes the same rules as one typed in.
  CHECK_STR(wifi_credentials_problem("ESPTEST", "12345678"), "");
  CHECK_STR(wifi_credentials_problem("home wifi", "12345678"), "");  // spaces are allowed in a name
  CHECK_STR(wifi_credentials_problem("open-cafe", ""), "");           // an open network has no password
  CHECK_STR(wifi_credentials_problem("open-cafe", 0), "");

  // Name: 1 to 32 characters, no control characters.
  CHECK(wifi_credentials_problem("", "12345678")[0] != '\0');
  CHECK(wifi_credentials_problem(0, "12345678")[0] != '\0');
  CHECK_STR(wifi_credentials_problem("12345678901234567890123456789012", "12345678"), "");   // 32 is the most
  CHECK(wifi_credentials_problem("123456789012345678901234567890123", "12345678")[0] != '\0');  // 33 is too many
  CHECK(wifi_credentials_problem("bad\nname", "12345678")[0] != '\0');
  CHECK(wifi_credentials_problem("bad\tname", "12345678")[0] != '\0');

  // Password: empty, or 8 to 63 characters (the WPA rule).
  CHECK(wifi_credentials_problem("ESPTEST", "1234567")[0] != '\0');
  CHECK_STR(wifi_credentials_problem("ESPTEST", "12345678"), "");
  char sixtythree[64], sixtyfour[65];
  memset(sixtythree, 'a', 63);
  sixtythree[63] = '\0';
  memset(sixtyfour, 'a', 64);
  sixtyfour[64] = '\0';
  CHECK_STR(wifi_credentials_problem("ESPTEST", sixtythree), "");
  CHECK(wifi_credentials_problem("ESPTEST", sixtyfour)[0] != '\0');

  // The words never repeat what was passed in, so a refused password cannot end up in a log through them.
  CHECK(strstr(wifi_credentials_problem("ESPTEST", "1234567"), "1234567") == 0);
  CHECK(strstr(wifi_credentials_problem("bad\nname", "12345678"), "bad") == 0);
}

int main() {
  test_wifi_reasons();
  test_wifi_reasons_that_end_an_attempt();
  test_wifi_credentials();
  test_state_machine_table();
  test_state_machine_cycle();
  test_state_machine_cannot_be_confused();
  test_start_blockers();
  test_classify_denied();
  test_json_helpers();
  test_wav_check();
  test_names();
  test_resolve_start_time();
  test_planning();
  test_ack();
  test_debounce();
  printf("%d checks, %d failed\n", checks, failures);
  return failures == 0 ? 0 : 1;
}
