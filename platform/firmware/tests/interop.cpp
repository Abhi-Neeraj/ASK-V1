// A command-line wrapper around locket/core.cpp so the API's own test suite can check the firmware's
// logic against the server's: `pytest platform/api/tests/test_firmware_interop.py`. Not part of the firmware.
//
//   interop wav <path> <seconds> [rate] [channels]   write a clip the way the locket writes one
//   interop torn <path> <seconds>                    write a clip whose header was never finished, then recover it
//   interop sha256 <path>                            hex SHA-256 of a file
//   interop iso <unix seconds>                       the X-Recorded-At text for a time
//   interop clientid <boot> <salt> <sequence>        a clip id
//   interop classify <status> <code>                 what the locket does after that answer

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../locket/core.h"

using namespace ask;

static int write_clip(const char* path, double seconds, uint32_t rate, uint16_t channels, bool finish_header) {
  FILE* f = fopen(path, "wb");
  if (!f) return 2;
  const uint32_t frames = (uint32_t)(seconds * rate);
  const uint32_t data_bytes = frames * channels * 2;
  uint8_t header[kWavHeaderBytes];
  // A real recording writes a header for "nothing yet" first and fixes it when the clip ends.
  wav_header(header, 0, rate, channels, 16);
  fwrite(header, 1, sizeof(header), f);
  for (uint32_t i = 0; i < frames; i++) {
    const int16_t sample = (int16_t)(8000.0 * sin(2.0 * 3.14159265358979 * 440.0 * i / rate));
    for (uint16_t c = 0; c < channels; c++) {
      uint8_t bytes[2] = {(uint8_t)(sample & 0xff), (uint8_t)((sample >> 8) & 0xff)};
      fwrite(bytes, 1, 2, f);
    }
  }
  if (finish_header) {
    wav_header(header, data_bytes, rate, channels, 16);
    fseek(f, 0, SEEK_SET);
    fwrite(header, 1, sizeof(header), f);
  } else {
    // The power went mid-write: the header still says "nothing yet" and the last sample is cut in half.
    fwrite("\x01", 1, 1, f);
    fclose(f);
    // What the device does at the next start: measure the file, and write a header for the whole frames in it.
    // The stray byte stays after the audio; the card library cannot shorten a file, and the server ignores it.
    f = fopen(path, "r+b");
    if (!f) return 2;
    fseek(f, 0, SEEK_END);
    const long size = ftell(f);
    const uint32_t recoverable = wav_recoverable_data_bytes((uint64_t)size, channels, 16);
    wav_header(header, recoverable, rate, channels, 16);
    fseek(f, 0, SEEK_SET);
    fwrite(header, 1, sizeof(header), f);
  }
  fclose(f);
  return 0;
}

int main(int argc, char** argv) {
  if (argc >= 4 && strcmp(argv[1], "wav") == 0) {
    return write_clip(argv[2], atof(argv[3]), argc > 4 ? (uint32_t)atoi(argv[4]) : 16000,
                      argc > 5 ? (uint16_t)atoi(argv[5]) : 1, true);
  }
  if (argc >= 4 && strcmp(argv[1], "torn") == 0) {
    return write_clip(argv[2], atof(argv[3]), 16000, 1, false);
  }
  if (argc >= 3 && strcmp(argv[1], "sha256") == 0) {
    FILE* f = fopen(argv[2], "rb");
    if (!f) return 2;
    Sha256 ctx;
    sha256_begin(&ctx);
    uint8_t buffer[4096];
    size_t n;
    while ((n = fread(buffer, 1, sizeof(buffer), f)) > 0) sha256_update(&ctx, buffer, n);
    fclose(f);
    uint8_t digest[32];
    char hex[65];
    sha256_finish(&ctx, digest);
    hex_encode(digest, 32, hex);
    printf("%s\n", hex);
    return 0;
  }
  if (argc >= 3 && strcmp(argv[1], "iso") == 0) {
    char text[21];
    if (!iso8601_utc(strtoll(argv[2], 0, 10), text)) {
      printf("none\n");
      return 0;
    }
    printf("%s\n", text);
    return 0;
  }
  if (argc >= 5 && strcmp(argv[1], "clientid") == 0) {
    char id[kClientIdBytes];
    if (!client_id_make(id, sizeof(id), (uint32_t)strtoul(argv[2], 0, 10), (uint32_t)strtoul(argv[3], 0, 10),
                        (uint32_t)strtoul(argv[4], 0, 10)))
      return 2;
    printf("%s\n", id);
    return 0;
  }
  if (argc >= 4 && strcmp(argv[1], "classify") == 0) {
    static const char* names[] = {"done", "retry", "newid", "reject", "repair"};
    printf("%s\n", names[(int)classify_upload(atoi(argv[2]), argv[3])]);
    return 0;
  }
  fprintf(stderr, "usage: see the comment at the top of interop.cpp\n");
  return 64;
}
