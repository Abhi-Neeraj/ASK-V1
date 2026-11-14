#include "core.h"

#include <string.h>

namespace ask {

// ---- WAV ----------------------------------------------------------------------------------------

static void put_u16(uint8_t* p, uint16_t v) {
  p[0] = (uint8_t)(v & 0xff);
  p[1] = (uint8_t)(v >> 8);
}

static void put_u32(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)(v & 0xff);
  p[1] = (uint8_t)((v >> 8) & 0xff);
  p[2] = (uint8_t)((v >> 16) & 0xff);
  p[3] = (uint8_t)((v >> 24) & 0xff);
}

void wav_header(uint8_t out[kWavHeaderBytes], uint32_t data_bytes, uint32_t sample_rate, uint16_t channels,
                uint16_t bits) {
  const uint16_t block_align = (uint16_t)(channels * (bits / 8));
  memcpy(out + 0, "RIFF", 4);
  put_u32(out + 4, 36u + data_bytes);  // everything after these eight bytes
  memcpy(out + 8, "WAVE", 4);
  memcpy(out + 12, "fmt ", 4);
  put_u32(out + 16, 16);  // size of the format chunk for plain PCM
  put_u16(out + 20, 1);   // 1 = uncompressed PCM
  put_u16(out + 22, channels);
  put_u32(out + 24, sample_rate);
  put_u32(out + 28, sample_rate * block_align);  // bytes per second
  put_u16(out + 32, block_align);
  put_u16(out + 34, bits);
  memcpy(out + 36, "data", 4);
  put_u32(out + 40, data_bytes);
}

uint32_t wav_recoverable_data_bytes(uint64_t file_bytes, uint16_t channels, uint16_t bits) {
  const uint32_t frame = (uint32_t)channels * (bits / 8);
  if (frame == 0 || file_bytes <= kWavHeaderBytes) return 0;
  uint64_t audio = file_bytes - kWavHeaderBytes;
  if (audio > 0xFFFFFFFFull - 36u) audio = 0xFFFFFFFFull - 36u;  // a header cannot describe more
  return (uint32_t)(audio - (audio % frame));
}

// ---- SHA-256 ------------------------------------------------------------------------------------

static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};

static uint32_t rotr(uint32_t x, unsigned n) { return (x >> n) | (x << (32 - n)); }

static void sha256_block(Sha256* ctx, const uint8_t* p) {
  uint32_t w[64];
  for (int i = 0; i < 16; i++) {
    w[i] = ((uint32_t)p[i * 4] << 24) | ((uint32_t)p[i * 4 + 1] << 16) | ((uint32_t)p[i * 4 + 2] << 8) |
           (uint32_t)p[i * 4 + 3];
  }
  for (int i = 16; i < 64; i++) {
    const uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
    const uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
    w[i] = w[i - 16] + s0 + w[i - 7] + s1;
  }
  uint32_t a = ctx->state[0], b = ctx->state[1], c = ctx->state[2], d = ctx->state[3];
  uint32_t e = ctx->state[4], f = ctx->state[5], g = ctx->state[6], h = ctx->state[7];
  for (int i = 0; i < 64; i++) {
    const uint32_t s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
    const uint32_t ch = (e & f) ^ (~e & g);
    const uint32_t t1 = h + s1 + ch + K[i] + w[i];
    const uint32_t s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
    const uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
    const uint32_t t2 = s0 + maj;
    h = g;
    g = f;
    f = e;
    e = d + t1;
    d = c;
    c = b;
    b = a;
    a = t1 + t2;
  }
  ctx->state[0] += a;
  ctx->state[1] += b;
  ctx->state[2] += c;
  ctx->state[3] += d;
  ctx->state[4] += e;
  ctx->state[5] += f;
  ctx->state[6] += g;
  ctx->state[7] += h;
}

void sha256_begin(Sha256* ctx) {
  static const uint32_t init[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                   0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
  memcpy(ctx->state, init, sizeof(init));
  ctx->total_bytes = 0;
  ctx->used = 0;
}

void sha256_update(Sha256* ctx, const uint8_t* data, size_t length) {
  ctx->total_bytes += length;
  while (length > 0) {
    size_t take = 64 - ctx->used;
    if (take > length) take = length;
    memcpy(ctx->block + ctx->used, data, take);
    ctx->used += take;
    data += take;
    length -= take;
    if (ctx->used == 64) {
      sha256_block(ctx, ctx->block);
      ctx->used = 0;
    }
  }
}

void sha256_finish(Sha256* ctx, uint8_t out[32]) {
  const uint64_t bit_length = ctx->total_bytes * 8;
  ctx->block[ctx->used++] = 0x80;
  if (ctx->used > 56) {
    memset(ctx->block + ctx->used, 0, 64 - ctx->used);
    sha256_block(ctx, ctx->block);
    ctx->used = 0;
  }
  memset(ctx->block + ctx->used, 0, 56 - ctx->used);
  for (int i = 0; i < 8; i++) ctx->block[56 + i] = (uint8_t)(bit_length >> (56 - 8 * i));
  sha256_block(ctx, ctx->block);
  for (int i = 0; i < 8; i++) {
    out[i * 4] = (uint8_t)(ctx->state[i] >> 24);
    out[i * 4 + 1] = (uint8_t)(ctx->state[i] >> 16);
    out[i * 4 + 2] = (uint8_t)(ctx->state[i] >> 8);
    out[i * 4 + 3] = (uint8_t)ctx->state[i];
  }
}

void hex_encode(const uint8_t* data, size_t length, char* out) {
  static const char digits[] = "0123456789abcdef";
  for (size_t i = 0; i < length; i++) {
    out[i * 2] = digits[data[i] >> 4];
    out[i * 2 + 1] = digits[data[i] & 0x0f];
  }
  out[length * 2] = '\0';
}

// ---- Time ---------------------------------------------------------------------------------------

// 2024-01-01 and 2100-01-01 in seconds since 1970. A clock outside this window was never set, or is wrong.
static const int64_t kEarliestPlausible = 1704067200;
static const int64_t kLatestPlausible = 4102444800;

bool clock_is_plausible(int64_t unix_seconds) {
  return unix_seconds >= kEarliestPlausible && unix_seconds < kLatestPlausible;
}

bool iso8601_utc(int64_t unix_seconds, char out[21]) {
  if (!clock_is_plausible(unix_seconds)) return false;
  // Days since the epoch to a calendar date (Howard Hinnant's civil_from_days).
  int64_t days = unix_seconds / 86400;
  const int64_t rest = unix_seconds % 86400;
  days += 719468;
  const int64_t era = (days >= 0 ? days : days - 146096) / 146097;
  const int64_t day_of_era = days - era * 146097;
  const int64_t year_of_era = (day_of_era - day_of_era / 1460 + day_of_era / 36524 - day_of_era / 146096) / 365;
  int64_t year = year_of_era + era * 400;
  const int64_t day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
  const int64_t mp = (5 * day_of_year + 2) / 153;
  const int64_t day = day_of_year - (153 * mp + 2) / 5 + 1;
  const int64_t month = mp < 10 ? mp + 3 : mp - 9;
  if (month <= 2) year += 1;

  const int hour = (int)(rest / 3600);
  const int minute = (int)((rest % 3600) / 60);
  const int second = (int)(rest % 60);

  const int64_t fields[6] = {year, month, day, hour, minute, second};
  const int widths[6] = {4, 2, 2, 2, 2, 2};
  const char separators[6] = {'-', '-', 'T', ':', ':', 'Z'};
  size_t at = 0;
  for (int f = 0; f < 6; f++) {
    int64_t value = fields[f];
    for (int d = widths[f] - 1; d >= 0; d--) {
      out[at + (size_t)d] = (char)('0' + value % 10);
      value /= 10;
    }
    at += (size_t)widths[f];
    out[at++] = separators[f];
  }
  out[at] = '\0';
  return true;
}

// ---- What to do after an upload ------------------------------------------------------------------

static bool same(const char* a, const char* b) { return a != 0 && b != 0 && strcmp(a, b) == 0; }

Upload classify_upload(int http_status, const char* error_code) {
  if (http_status == 200 || http_status == 201) return Upload::Done;
  if (http_status == 401) return Upload::Repair;

  if (http_status == 409) return same(error_code, "recording_conflict") ? Upload::NewId : Upload::Retry;

  // Audio the server has looked at and will never accept. Retrying the same bytes cannot change that, and
  // a file stuck at the front of the queue would block everything behind it, so it is set aside, not deleted.
  static const char* const never[] = {"invalid_audio",       "truncated_audio", "unsupported_audio_format",
                                      "unsupported_sample_rate", "audio_too_short", "audio_too_long",
                                      "silent_audio",          "upload_too_large", "unsupported_media_type",
                                      "validation_error"};
  for (size_t i = 0; i < sizeof(never) / sizeof(never[0]); i++) {
    if (same(error_code, never[i])) return Upload::Reject;
  }
  if (http_status == 413 || http_status == 415) return Upload::Reject;

  // Everything else, including damaged bytes (422 checksum_mismatch), a slow upload (408), rate limiting
  // (429), server trouble (5xx) and no answer at all (status 0 or below): not acknowledged, so keep the file.
  return Upload::Retry;
}

// ---- Retry timing -------------------------------------------------------------------------------

uint32_t backoff_ms(uint32_t attempt, uint32_t base_ms, uint32_t max_ms, uint32_t random32) {
  if (max_ms == 0) return 0;
  uint64_t ceiling = base_ms;
  for (uint32_t i = 0; i < attempt && ceiling < max_ms; i++) ceiling *= 2;
  if (ceiling > max_ms) ceiling = max_ms;
  if (ceiling == 0) return 0;
  const uint64_t floor_ms = ceiling / 2;
  return (uint32_t)(floor_ms + (random32 % (ceiling - floor_ms + 1)));
}

// ---- Identifiers --------------------------------------------------------------------------------

static void put_hex32(char* out, uint32_t v) {
  static const char digits[] = "0123456789abcdef";
  for (int i = 7; i >= 0; i--) {
    out[i] = digits[v & 0x0f];
    v >>= 4;
  }
}

bool client_id_make(char* out, size_t capacity, uint32_t boot, uint32_t boot_salt, uint32_t sequence) {
  if (capacity < kClientIdBytes) return false;
  out[0] = 'r';
  put_hex32(out + 1, boot);
  put_hex32(out + 9, boot_salt);
  out[17] = '-';
  put_hex32(out + 18, sequence);
  out[26] = '\0';
  return true;
}

bool client_id_valid(const char* id) {
  if (id == 0) return false;
  const size_t length = strlen(id);
  if (length < 8 || length > 64) return false;
  for (size_t i = 0; i < length; i++) {
    const char c = id[i];
    const bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_';
    if (!ok) return false;
  }
  return true;
}

// ---- Battery ------------------------------------------------------------------------------------

int battery_percent(int millivolts) {
  struct Point {
    int mv;
    int percent;
  };
  static const Point curve[] = {{4200, 100}, {4150, 95}, {4110, 90}, {4080, 85}, {4020, 80}, {3980, 75},
                                {3950, 70},  {3910, 65}, {3870, 60}, {3850, 55}, {3840, 50}, {3820, 45},
                                {3800, 40},  {3790, 35}, {3770, 30}, {3750, 25}, {3730, 20}, {3710, 15},
                                {3690, 10},  {3610, 5},  {3270, 0}};
  const size_t count = sizeof(curve) / sizeof(curve[0]);
  if (millivolts < 3000 || millivolts > 4400) return -1;
  if (millivolts >= curve[0].mv) return 100;
  if (millivolts <= curve[count - 1].mv) return 0;
  for (size_t i = 1; i < count; i++) {
    if (millivolts >= curve[i].mv) {
      const int span = curve[i - 1].mv - curve[i].mv;
      const int into = millivolts - curve[i].mv;
      return curve[i].percent + (curve[i - 1].percent - curve[i].percent) * into / span;
    }
  }
  return 0;
}

// ---- Reading the server's replies ---------------------------------------------------------------

// Points just past `"key"` and the colon after it, or null if the key is not there.
static const char* after_key(const char* json, const char* key) {
  if (json == 0 || key == 0) return 0;
  const size_t key_length = strlen(key);
  const char* at = json;
  while ((at = strchr(at, '"')) != 0) {
    if (strncmp(at + 1, key, key_length) == 0 && at[1 + key_length] == '"') {
      const char* p = at + 2 + key_length;
      while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
      if (*p == ':') {
        p++;
        while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
        return p;
      }
    }
    // Skip over this string so a quoted value is not mistaken for a key.
    at++;
    while (*at != '\0' && *at != '"') {
      if (*at == '\\' && at[1] != '\0') at++;
      at++;
    }
    if (*at == '\0') return 0;
    at++;
  }
  return 0;
}

bool json_string(const char* json, const char* key, char* out, size_t capacity) {
  if (capacity == 0) return false;
  const char* p = after_key(json, key);
  if (p == 0 || *p != '"') return false;
  p++;
  size_t n = 0;
  while (*p != '\0' && *p != '"') {
    char c = *p;
    if (c == '\\') {
      p++;
      if (*p == '\0') return false;
      c = *p;
      if (c == 'n') c = '\n';
      else if (c == 't') c = '\t';
      else if (c == 'u') return false;  // the server's replies never need it; refuse rather than guess
    }
    if (n + 1 >= capacity) return false;  // too long for the caller's buffer: fail, never cut short
    out[n++] = c;
    p++;
  }
  if (*p != '"') return false;
  out[n] = '\0';
  return true;
}

bool json_uint(const char* json, const char* key, uint32_t* out) {
  const char* p = after_key(json, key);
  if (p == 0 || *p < '0' || *p > '9') return false;
  uint64_t value = 0;
  while (*p >= '0' && *p <= '9') {
    value = value * 10 + (uint64_t)(*p - '0');
    if (value > 0xFFFFFFFFull) return false;
    p++;
  }
  *out = (uint32_t)value;
  return true;
}

}  // namespace ask
