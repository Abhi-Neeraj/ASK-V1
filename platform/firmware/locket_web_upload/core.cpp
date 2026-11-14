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
  // The ASK server never answers a locket with 403, so this came from something in front of it. Pairing again
  // would not help, and neither would retrying quickly.
  if (http_status == 403) return Upload::Denied;

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

// ---- Button recorder: reading and writing JSON -----------------------------------------------------

static bool is_word_char(char c) {
  return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_';
}

bool json_bool(const char* json, const char* key, bool* out) {
  const char* p = after_key(json, key);
  if (p == 0) return false;
  if (strncmp(p, "true", 4) == 0 && !is_word_char(p[4])) {
    *out = true;
    return true;
  }
  if (strncmp(p, "false", 5) == 0 && !is_word_char(p[5])) {
    *out = false;
    return true;
  }
  return false;
}

bool json_escape(const char* in, char* out, size_t capacity) {
  if (out == 0 || capacity == 0) return false;
  out[0] = '\0';
  if (in == 0) return true;
  static const char digits[] = "0123456789abcdef";
  size_t n = 0;
  for (const unsigned char* p = (const unsigned char*)in; *p != '\0'; p++) {
    char piece[7];
    size_t length = 0;
    if (*p == '"' || *p == '\\') {
      piece[length++] = '\\';
      piece[length++] = (char)*p;
    } else if (*p == '\n') {
      piece[length++] = '\\';
      piece[length++] = 'n';
    } else if (*p == '\r') {
      piece[length++] = '\\';
      piece[length++] = 'r';
    } else if (*p == '\t') {
      piece[length++] = '\\';
      piece[length++] = 't';
    } else if (*p < 0x20) {
      piece[length++] = '\\';
      piece[length++] = 'u';
      piece[length++] = '0';
      piece[length++] = '0';
      piece[length++] = digits[*p >> 4];
      piece[length++] = digits[*p & 0x0f];
    } else {
      piece[length++] = (char)*p;  // includes UTF-8 bytes, which JSON allows as they are
    }
    if (n + length + 1 > capacity) {
      out[0] = '\0';
      return false;
    }
    memcpy(out + n, piece, length);
    n += length;
  }
  out[n] = '\0';
  return true;
}

// ---- Button recorder: checking a finished WAV ------------------------------------------------------

static uint32_t get_u32(const uint8_t* p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint16_t get_u16(const uint8_t* p) { return (uint16_t)(p[0] | (p[1] << 8)); }

WavCheck wav_check(const uint8_t header[kWavHeaderBytes], uint64_t file_bytes, uint32_t sample_rate,
                   uint16_t channels, uint16_t bits, uint32_t* data_bytes) {
  if (file_bytes <= kWavHeaderBytes) return WavCheck::TooSmall;
  if (memcmp(header, "RIFF", 4) != 0 || memcmp(header + 8, "WAVE", 4) != 0 ||
      memcmp(header + 12, "fmt ", 4) != 0 || memcmp(header + 36, "data", 4) != 0) {
    return WavCheck::NotWav;
  }
  const uint16_t block_align = (uint16_t)(channels * (bits / 8));
  if (get_u32(header + 16) != 16 || get_u16(header + 20) != 1 || get_u16(header + 22) != channels ||
      get_u32(header + 24) != sample_rate || get_u32(header + 28) != sample_rate * block_align ||
      get_u16(header + 32) != block_align || get_u16(header + 34) != bits) {
    return WavCheck::WrongFormat;
  }
  const uint32_t data = get_u32(header + 40);
  if (data == 0) return WavCheck::TooSmall;
  if (data > 0xFFFFFFFFu - 36u || get_u32(header + 4) != 36u + data) return WavCheck::SizeMismatch;
  if (kWavHeaderBytes + (uint64_t)data > file_bytes) return WavCheck::SizeMismatch;
  const uint64_t extra = file_bytes - kWavHeaderBytes - data;
  if (block_align != 0 && extra >= block_align) return WavCheck::SizeMismatch;
  if (data_bytes != 0) *data_bytes = data;
  return WavCheck::Ok;
}

// ---- Button recorder: names of saved parts ---------------------------------------------------------

static bool id_text_valid(const char* s, size_t min_length, size_t max_length) {
  if (s == 0) return false;
  const size_t length = strlen(s);
  if (length < min_length || length > max_length) return false;
  for (size_t i = 0; i < length; i++) {
    const char c = s[i];
    const bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_';
    if (!ok) return false;
  }
  return true;
}

bool session_ref_make(char* out, size_t capacity, uint32_t boot, uint32_t boot_salt, uint32_t session_number) {
  if (capacity < kClientIdBytes) return false;
  out[0] = 's';
  put_hex32(out + 1, boot);
  put_hex32(out + 9, boot_salt);
  out[17] = '-';
  put_hex32(out + 18, session_number);
  out[26] = '\0';
  return true;
}

static size_t put_dec(char* out, uint64_t value) {
  char reversed[21];
  size_t count = 0;
  do {
    reversed[count++] = (char)('0' + value % 10);
    value /= 10;
  } while (value != 0);
  for (size_t i = 0; i < count; i++) out[i] = reversed[count - 1 - i];
  return count;
}

bool clip_name_make(char* out, size_t capacity, const ClipName& name) {
  if (!id_text_valid(name.client_id, 8, 64) || !id_text_valid(name.session, 1, 64)) return false;
  if (name.part < 1 || name.part > 999999) return false;
  if (name.when < 0 || name.when > 99999999999LL) return false;
  const size_t id_length = strlen(name.client_id);
  const size_t session_length = strlen(name.session);
  // id . [u] when . part . session .wav
  if (id_length + session_length + 1 + 1 + 11 + 1 + 6 + 1 + 4 + 1 > capacity) return false;
  size_t at = 0;
  memcpy(out + at, name.client_id, id_length);
  at += id_length;
  out[at++] = '.';
  if (!name.timed) out[at++] = 'u';
  at += put_dec(out + at, (uint64_t)name.when);
  out[at++] = '.';
  at += put_dec(out + at, name.part);
  out[at++] = '.';
  memcpy(out + at, name.session, session_length);
  at += session_length;
  memcpy(out + at, ".wav", 4);
  at += 4;
  out[at] = '\0';
  return true;
}

static bool parse_dec(const char* s, size_t length, uint64_t* out) {
  if (length == 0 || length > 12) return false;
  uint64_t value = 0;
  for (size_t i = 0; i < length; i++) {
    if (s[i] < '0' || s[i] > '9') return false;
    value = value * 10 + (uint64_t)(s[i] - '0');
  }
  *out = value;
  return true;
}

bool clip_name_parse(const char* file_name, ClipName* out) {
  if (file_name == 0) return false;
  const size_t length = strlen(file_name);
  if (length < 20 || length >= kClipNameBytes) return false;

  // Exactly five fields separated by dots: id, when, part, session, wav.
  const char* starts[5];
  size_t lengths[5];
  size_t field = 0;
  size_t begin = 0;
  for (size_t i = 0; i <= length; i++) {
    if (i == length || file_name[i] == '.') {
      if (field >= 5) return false;
      starts[field] = file_name + begin;
      lengths[field] = i - begin;
      field++;
      begin = i + 1;
    }
  }
  if (field != 5 || lengths[4] != 3 || memcmp(starts[4], "wav", 3) != 0) return false;

  ClipName parsed;
  memset(&parsed, 0, sizeof(parsed));
  if (lengths[0] >= sizeof(parsed.client_id) || lengths[3] >= sizeof(parsed.session)) return false;
  memcpy(parsed.client_id, starts[0], lengths[0]);
  memcpy(parsed.session, starts[3], lengths[3]);
  if (!client_id_valid(parsed.client_id) || !id_text_valid(parsed.session, 1, 64)) return false;

  const char* when = starts[1];
  size_t when_length = lengths[1];
  parsed.timed = true;
  if (when_length > 0 && when[0] == 'u') {
    parsed.timed = false;
    when++;
    when_length--;
  }
  uint64_t value = 0;
  if (!parse_dec(when, when_length, &value)) return false;
  parsed.when = (int64_t)value;
  if (!parse_dec(starts[2], lengths[2], &value) || value < 1 || value > 999999) return false;
  parsed.part = (uint32_t)value;

  *out = parsed;
  return true;
}

uint32_t client_id_boot(const char* id) {
  if (id == 0 || strlen(id) != 26 || id[0] != 'r' || id[17] != '-') return 0;
  uint32_t value = 0;
  for (int i = 1; i <= 8; i++) {
    const char c = id[i];
    uint32_t digit;
    if (c >= '0' && c <= '9') digit = (uint32_t)(c - '0');
    else if (c >= 'a' && c <= 'f') digit = (uint32_t)(c - 'a' + 10);
    else return 0;
    value = (value << 4) | digit;
  }
  return value;
}

bool resolve_start_time(int64_t now_epoch, uint32_t uptime_now_seconds, int64_t uptime_at_part_start_seconds,
                        int64_t* out_epoch) {
  if (!clock_is_plausible(now_epoch)) return false;
  if (uptime_at_part_start_seconds < 0 || uptime_at_part_start_seconds > (int64_t)uptime_now_seconds) return false;
  const int64_t started = now_epoch - ((int64_t)uptime_now_seconds - uptime_at_part_start_seconds);
  if (!clock_is_plausible(started)) return false;
  *out_epoch = started;
  return true;
}

// ---- Button recorder: planning a recording ---------------------------------------------------------

uint32_t segment_blocks(uint32_t segment_seconds, uint32_t max_upload_bytes, uint32_t block_bytes,
                        uint32_t bytes_per_second) {
  if (block_bytes == 0 || bytes_per_second == 0) return 1;
  uint64_t blocks = (uint64_t)segment_seconds * bytes_per_second / block_bytes;
  if (max_upload_bytes != 0) {  // 0 means the server's limit is not known
    const uint64_t by_size =
        max_upload_bytes > kWavHeaderBytes ? (uint64_t)(max_upload_bytes - kWavHeaderBytes) / block_bytes : 0;
    if (by_size < blocks) blocks = by_size;
  }
  if (blocks < 1) blocks = 1;
  if (blocks > 0xFFFFFFFFull) blocks = 0xFFFFFFFFull;
  return (uint32_t)blocks;
}

uint32_t segment_seconds_for(uint32_t preferred_seconds, uint32_t server_max_clip_seconds) {
  uint32_t seconds = preferred_seconds;
  if (server_max_clip_seconds != 0 && server_max_clip_seconds < seconds) seconds = server_max_clip_seconds;
  return seconds < 1 ? 1 : seconds;
}

bool space_ok(uint64_t free_at_start, uint64_t written, uint64_t reserve) {
  if (written > free_at_start) return false;
  return free_at_start - written >= reserve;
}

bool upload_may_run(bool recording, bool allowed_while_recording, uint32_t ring_used, uint32_t ring_total,
                    uint32_t hold_percent) {
  if (!recording) return true;
  if (!allowed_while_recording || ring_total == 0) return false;
  return (uint64_t)ring_used * 100 <= (uint64_t)ring_total * hold_percent;
}

// ---- Button recorder: confirming an upload --------------------------------------------------------

static bool uuid_shaped(const char* s) {
  if (strlen(s) != 36) return false;
  for (int i = 0; i < 36; i++) {
    const char c = s[i];
    if (i == 8 || i == 13 || i == 18 || i == 23) {
      if (c != '-') return false;
    } else if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'))) {
      return false;
    }
  }
  return true;
}

bool ack_validate(const char* json, const char* client_id, const char* sha256_hex, uint32_t bytes, Ack* out) {
  if (json == 0 || client_id == 0 || sha256_hex == 0) return false;
  char text[80];
  if (!json_string(json, "status", text, sizeof(text)) || strcmp(text, "stored") != 0) return false;
  if (!json_string(json, "clientRecordingId", text, sizeof(text)) || strcmp(text, client_id) != 0) return false;
  char recording_id[40];
  if (!json_string(json, "recordingId", recording_id, sizeof(recording_id)) || !uuid_shaped(recording_id)) {
    return false;
  }
  if (!json_string(json, "sha256", text, sizeof(text)) || strcmp(text, sha256_hex) != 0) return false;
  uint32_t echoed = 0;
  if (!json_uint(json, "bytes", &echoed) || echoed != bytes) return false;
  uint32_t duration = 0;
  if (!json_uint(json, "durationMs", &duration)) return false;  // a receipt for audio the server never decoded
  bool duplicate = false;
  json_bool(json, "duplicate", &duplicate);
  if (out != 0) {
    memcpy(out->recording_id, recording_id, sizeof(out->recording_id));
    out->duplicate = duplicate;
  }
  return true;
}

// ---- Button recorder: the button ---------------------------------------------------------------------

void debounce_begin(Debounce* d, bool level) {
  d->raw = level;
  d->stable = level;
  d->changed_ms = 0;
}

bool debounce_pressed(Debounce* d, bool level, uint32_t now_ms, uint32_t debounce_ms) {
  if (level != d->raw) {
    d->raw = level;
    d->changed_ms = now_ms;
  }
  if ((uint32_t)(now_ms - d->changed_ms) >= debounce_ms && level != d->stable) {
    d->stable = level;
    if (!level) return true;  // pulled to ground: pressed
  }
  return false;
}

// ---- Button recorder: the recording state machine ------------------------------------------------------

RecStep rec_step(RecState state, RecEvent event) {
  RecStep stay = {state, PressAction::Ignore};
  switch (event) {
    case RecEvent::Press:
      // The one place a press is given a meaning. In Error a press tries again, which is how the locket recovers.
      if (state == RecState::Idle || state == RecState::Error) return {state, PressAction::Start};
      if (state == RecState::Recording) return {state, PressAction::Stop};
      return stay;  // Booting or Finalizing: neither starts nor stops anything
    case RecEvent::BootedOk:
      return state == RecState::Booting ? RecStep{RecState::Idle, PressAction::Ignore} : stay;
    case RecEvent::BootFailed:
      return state == RecState::Booting ? RecStep{RecState::Error, PressAction::Ignore} : stay;
    case RecEvent::Started:
      return (state == RecState::Idle || state == RecState::Error) ? RecStep{RecState::Recording, PressAction::Ignore}
                                                                    : stay;
    case RecEvent::StartRefused:
      return (state == RecState::Idle || state == RecState::Error) ? RecStep{RecState::Error, PressAction::Ignore}
                                                                    : stay;
    case RecEvent::CaptureEnded:
      return state == RecState::Recording ? RecStep{RecState::Finalizing, PressAction::Ignore} : stay;
    case RecEvent::Saved:
      return state == RecState::Finalizing ? RecStep{RecState::Idle, PressAction::Ignore} : stay;
    case RecEvent::SavedWithProblem:
      return state == RecState::Finalizing ? RecStep{RecState::Error, PressAction::Ignore} : stay;
  }
  return stay;
}

// ---- Button recorder: joining Wi-Fi ---------------------------------------------------------------------

const char* wifi_reason_hint(unsigned reason) {
  switch (reason) {
    case 201:  // NO_AP_FOUND
    case 210:  // NO_AP_FOUND_W_COMPATIBLE_SECURITY
    case 211:  // NO_AP_FOUND_IN_AUTHMODE_THRESHOLD
    case 212:  // NO_AP_FOUND_IN_RSSI_THRESHOLD
      return "no network with that name was found: is the hotspot on, in range, and 2.4 GHz?";
    case 14:   // MIC_FAILURE
    case 15:   // 4WAY_HANDSHAKE_TIMEOUT
    case 16:   // GROUP_KEY_UPDATE_TIMEOUT
    case 17:   // IE_IN_4WAY_DIFFERS
    case 202:  // AUTH_FAIL
    case 204:  // HANDSHAKE_TIMEOUT
      return "the password was not accepted: is it right for that network?";
    case 203:  // ASSOC_FAIL
    case 205:  // CONNECTION_FAIL
      return "the access point refused the connection";
    case 5:  // ASSOC_TOOMANY
      return "the access point has too many devices connected";
    case 200:  // BEACON_TIMEOUT
      return "lost contact with the access point: out of range?";
    case 3:    // AUTH_LEAVE
    case 4:    // DISASSOC_DUE_TO_INACTIVITY
    case 8:    // ASSOC_LEAVE
    case 46:   // PEER_INITIATED
    case 47:   // AP_INITIATED
      return "the access point disconnected the locket";
    case 2:    // AUTH_EXPIRE
    case 39:   // TIMEOUT
    case 209:  // SA_QUERY_TIMEOUT
      return "the connection timed out";
    case 1:  // UNSPECIFIED
      return "disconnected for an unspecified reason";
    default:
      return "disconnected for another reason (see the code)";
  }
}

bool wifi_reason_ends_attempt(unsigned reason) {
  switch (reason) {
    case 201:  // NO_AP_FOUND
    case 210:  // NO_AP_FOUND_W_COMPATIBLE_SECURITY
    case 211:  // NO_AP_FOUND_IN_AUTHMODE_THRESHOLD
    case 212:  // NO_AP_FOUND_IN_RSSI_THRESHOLD
    case 14:   // MIC_FAILURE
    case 15:   // 4WAY_HANDSHAKE_TIMEOUT
    case 202:  // AUTH_FAIL
    case 204:  // HANDSHAKE_TIMEOUT
    case 203:  // ASSOC_FAIL
    case 205:  // CONNECTION_FAIL
    case 5:    // ASSOC_TOOMANY
      return true;
    default:
      return false;
  }
}

const char* wifi_credentials_problem(const char* ssid, const char* password) {
  if (ssid == 0 || ssid[0] == '\0') return "the network name is empty";
  if (strlen(ssid) > 32) return "the network name is longer than 32 characters";
  for (const char* p = ssid; *p != '\0'; p++) {
    if ((unsigned char)*p < 0x20) return "the network name has a control character in it";
  }
  const size_t length = password == 0 ? 0 : strlen(password);
  if (length != 0 && length < 8) return "the password must be at least 8 characters (or empty for an open network)";
  if (length > 63) return "the password must be at most 63 characters";
  return "";
}

// ---- Button recorder: may a recording start? --------------------------------------------------------------

Blocker start_blocker(const StartChecks& c) {
  if (!c.mic_ok) return Blocker::MicFailed;
  if (!c.paired) return Blocker::NotPaired;
  if (c.needs_pairing) return Blocker::NeedsPairing;
  if (!c.card_ready) return Blocker::NoCard;
  if (!c.ring_ready) return Blocker::NoMemory;
  if (c.free_bytes < c.min_free_bytes) return Blocker::CardFull;
  if (c.max_pending_parts != 0 && c.pending_parts >= c.max_pending_parts) return Blocker::TooManyWaiting;
  return Blocker::None;
}

const char* blocker_text(Blocker blocker) {
  switch (blocker) {
    case Blocker::None: return "";
    case Blocker::MicFailed: return "the microphone did not start";
    case Blocker::NotPaired: return "not paired with the server yet";
    case Blocker::NeedsPairing: return "the server no longer accepts this locket: pair it again";
    case Blocker::NoCard: return "no usable memory card";
    case Blocker::NoMemory: return "not enough memory for the audio buffers";
    case Blocker::CardFull: return "the memory card is full";
    case Blocker::TooManyWaiting: return "too many recordings are waiting to upload: connect to the network first";
  }
  return "cannot start";
}

}  // namespace ask
