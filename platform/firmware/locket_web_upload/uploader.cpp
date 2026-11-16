#include "uploader.h"

#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_random.h>  // esp_random() is declared here, not in Arduino.h or esp_system.h
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>
#include <time.h>

#include "config.h"
#include "core.h"
#include "wifi_manager.h"
#include "sdwriter.h"
#include "settings.h"
#include "status.h"
#include "storage.h"

namespace uploader {

static SemaphoreHandle_t netLock = nullptr;  // one network conversation at a time: the uploader's, or a pairing
static volatile bool repairNeeded = false;
static volatile bool reachable = false;
static volatile bool uploadingNow = false;
static volatile bool retryRequested = false;
static volatile uint32_t uploadedCount = 0;
static volatile uint32_t lastUploadMs = 0;
static volatile uint32_t lastBeatMs = 0;
static volatile bool anyUpload = false;
static volatile bool anyBeat = false;
static volatile uint32_t unconfirmed = 0;
static status::Text problemText;
static status::Text lastIdText;
static bool ntpStarted = false;
// A confirmed part that could not be removed from the card yet. Nothing else is sent until it is.
static char pendingDelete[ask::kClipNameBytes] = "";

static void problem(const char* text) { problemText.set(text); }
static void problem(const String& text) { problemText.set(text.c_str()); }

bool needsPairing() { return repairNeeded; }
void retryNow() { retryRequested = true; }

// ---- Connection -------------------------------------------------------------------------------------------

// TLS checks the certificate's dates, and a part's start time cannot be corrected later, so nothing goes to the
// server until the clock has been set from the network.
static bool clockSet() {
  if (!ntpStarted) {
    configTime(0, 0, "pool.ntp.org", "time.google.com");
    ntpStarted = true;
  }
  const uint32_t started = millis();
  while (!ask::clock_is_plausible((int64_t)time(nullptr)) && millis() - started < kClockWaitMs) {
    vTaskDelay(pdMS_TO_TICKS(250));
  }
  return ask::clock_is_plausible((int64_t)time(nullptr));
}

// One HTTP request's worth of connection objects. The certificate text must outlive the request, so it lives here.
struct Connection {
  WiFiClientSecure secure;
  WiFiClient plain;
  HTTPClient http;
  String ca;

  bool open(const String& url) {
    if (!url.startsWith("https://")) {
      if (!ASK_ALLOW_PLAIN_HTTP) {
        problem("the server address is not https://");
        return false;
      }
      return http.begin(plain, url);
    }
    if (ASK_INSECURE_TLS) {
      secure.setInsecure();  // development only: the server is not verified
    } else {
      ca = storage::readSmallFile(kCaFile, 16384);
      if (ca.length() == 0) {
        problem("no certificate authority file (/ca.pem) on the memory card");
        return false;
      }
      secure.setCACert(ca.c_str());
    }
    return http.begin(secure, url);
  }
};

// A short reply read whole. False if it is missing, chunked (size unknown) or larger than the buffer.
static bool readReply(HTTPClient& http, char* out, size_t capacity) {
  out[0] = '\0';
  const int size = http.getSize();
  if (size < 0 || (size_t)size >= capacity || size > (int)kMaxReplyBytes) return false;
  const String body = http.getString();
  if (body.length() >= capacity) return false;
  strcpy(out, body.c_str());
  return true;
}

static void applyServerConfig(const char* json) {
  uint32_t beat = 0, maxClip = 0, maxUpload = 0;
  ask::json_uint(json, "heartbeatIntervalSeconds", &beat);
  ask::json_uint(json, "maxClipSeconds", &maxClip);  // newer servers only; older ones leave the stored value
  ask::json_uint(json, "maxUploadBytes", &maxUpload);
  settings::setServerConfig(beat, maxClip, maxUpload);
}

// ---- Pairing and heartbeats -------------------------------------------------------------------------------

bool pair(const String& code, String* message) {
  const String server = settings::serverUrl();
  if (server.length() == 0) {
    *message = "no server address is set (use: server https://your-address)";
    return false;
  }
  if (netLock == nullptr || xSemaphoreTake(netLock, pdMS_TO_TICKS(30000)) != pdTRUE) {
    *message = "the locket is busy with the network; try again in a minute";
    return false;
  }
  struct Release {
    ~Release() { xSemaphoreGive(netLock); }
  } release;

  if (!wifi_manager::waitConnected(kWifiConnectTimeoutMs)) {
    // The network task is already trying; say what it knows rather than starting a second attempt.
    const wifi_manager::Status net = wifi_manager::status();
    *message = net.state == wifi_manager::State::NoNetwork
                   ? String("no Wi-Fi network is set (use: wifi <name> <password>)")
                   : String("the locket is not on Wi-Fi yet") + (net.lastReason[0] ? String(": ") + net.lastReason : String(""));
    return false;
  }
  Connection c;
  if (!c.open(server + "/api/device/v1/pair")) {
    char why[96];
    problemText.get(why, sizeof(why));
    *message = why[0] ? String(why) : String("could not reach the server");
    return false;
  }
  c.http.setTimeout(kHttpTimeoutMs);
  c.http.addHeader("Content-Type", "application/json");
  const String body = String("{\"code\":\"") + code + "\",\"firmwareVersion\":\"" + ASK_FIRMWARE_VERSION + "\"}";
  const int httpStatus = c.http.POST(body);
  char reply[kMaxReplyBytes + 1];
  const bool haveReply = httpStatus > 0 && readReply(c.http, reply, sizeof(reply));
  c.http.end();
  if (httpStatus != 200) {
    char errorCode[40] = "";
    if (haveReply) ask::json_string(reply, "code", errorCode, sizeof(errorCode));
    if (httpStatus <= 0) *message = "could not reach the server";
    else if (errorCode[0]) *message = String("the server did not accept that code (") + errorCode + ")";
    else *message = "the server answered " + String(httpStatus);
    return false;
  }
  char deviceId[48], credential[160];
  if (!haveReply || !ask::json_string(reply, "deviceId", deviceId, sizeof(deviceId)) ||
      !ask::json_string(reply, "credential", credential, sizeof(credential))) {
    *message = "the server's answer was not understood";
    return false;
  }
  settings::setPairing(deviceId, credential);
  applyServerConfig(reply);
  memset(credential, 0, sizeof(credential));  // do not leave the secret lying in memory
  memset(reply, 0, sizeof(reply));
  repairNeeded = false;
  *message = String("paired as ") + deviceId;
  return true;
}

static int batteryPercent() {
#if ASK_PIN_BATTERY_ADC >= 0
  const int millivolts = (int)(analogReadMilliVolts(ASK_PIN_BATTERY_ADC) * ASK_BATTERY_DIVIDER_RATIO);
  return ask::battery_percent(millivolts);
#else
  return -1;
#endif
}

static void heartbeat() {
  Connection c;
  if (!c.open(settings::serverUrl() + "/api/device/v1/heartbeat")) {
    reachable = false;
    return;
  }
  c.http.setTimeout(kHttpTimeoutMs);
  c.http.addHeader("Content-Type", "application/json");
  c.http.addHeader("Authorization", "Bearer " + settings::credential());
  String body = String("{\"firmwareVersion\":\"") + ASK_FIRMWARE_VERSION + "\",\"uploadQueueDepth\":" +
                String((unsigned long)storage::pendingCount());
  const int battery = batteryPercent();
  if (battery >= 0) body += ",\"batteryPercent\":" + String(battery);
  body += "}";

  const int httpStatus = c.http.POST(body);
  char reply[kMaxReplyBytes + 1];
  const bool haveReply = httpStatus > 0 && readReply(c.http, reply, sizeof(reply));
  c.http.end();

  const ask::Upload meaning = ask::classify_upload(httpStatus, "");
  if (httpStatus == 200) {
    if (haveReply) applyServerConfig(reply);
    repairNeeded = false;
    reachable = true;
    lastBeatMs = millis();
    anyBeat = true;
  } else if (meaning == ask::Upload::Repair) {
    repairNeeded = true;
    reachable = false;
    problem("the server no longer accepts this locket's credential: pair it again");
  } else {
    reachable = false;
    problem(httpStatus > 0 ? "heartbeat answered " + String(httpStatus) : String("heartbeat could not reach the server"));
  }
}

// ---- Uploading parts ------------------------------------------------------------------------------------

// Feeds a part to the HTTP client from the card, a slice at a time, through the card's Reader so that it steps
// aside for the writer and notices a remount. The client asks for bytes with available() and readBytes().
class ClipStream : public Stream {
 public:
  ClipStream(storage::Reader& reader, size_t total) : reader_(reader), total_(total) {}

  int available() override {
    if (failed_) return -1;
    // While recording, give way as soon as the card's queue grows: the writer has priority over any upload.
    if (!ask::upload_may_run(status::busyRecording(), ASK_UPLOAD_WHILE_RECORDING, sdwriter::queued(), kRingBlockCount,
                             kUploadHoldRingPercent)) {
      failed_ = true;
      held_ = true;
      return -1;
    }
    return (int)(total_ - delivered_);
  }
  int read() override {
    uint8_t one;
    return readBytes((char*)&one, 1) == 1 ? one : -1;
  }
  int peek() override { return -1; }
  size_t write(uint8_t) override { return 0; }
  using Stream::readBytes;
  size_t readBytes(char* buffer, size_t length) override {
    size_t out = 0;
    while (out < length && delivered_ < total_) {
      if (used_ == filled_) {
        const size_t want = total_ - fetched_ < sizeof(slice_) ? total_ - fetched_ : sizeof(slice_);
        const size_t got = want == 0 ? 0 : reader_.read(slice_, want);
        if (got == 0) {
          failed_ = true;
          break;
        }
        fetched_ += got;
        filled_ = got;
        used_ = 0;
      }
      size_t take = length - out;
      if (take > filled_ - used_) take = filled_ - used_;
      memcpy(buffer + out, slice_ + used_, take);
      used_ += take;
      out += take;
      delivered_ += take;
    }
    return out;
  }
  bool failed() const { return failed_; }
  bool held() const { return held_; }

 private:
  storage::Reader& reader_;
  size_t total_;
  size_t fetched_ = 0;
  size_t delivered_ = 0;
  size_t filled_ = 0;
  size_t used_ = 0;
  bool failed_ = false;
  bool held_ = false;
  uint8_t slice_[4096];
};

enum class Hashed { Ok, ReadFailed, NotAValidWav };

// Reads the whole part once for its SHA-256 (the server checks it), and checks the WAV header on the way past.
static Hashed hashClip(storage::Reader& reader, char hex[65]) {
  static uint8_t slice[4096];
  ask::Sha256 hash;
  ask::sha256_begin(&hash);
  uint32_t slices = 0;
  while (reader.position() < reader.size()) {
    const size_t got = reader.read(slice, sizeof(slice));
    if (got == 0) return Hashed::ReadFailed;
    if (slices == 0) {
      if (got < ask::kWavHeaderBytes ||
          ask::wav_check(slice, reader.size(), kSampleRate, kChannels, kBitsPerSample, nullptr) != ask::WavCheck::Ok) {
        return Hashed::NotAValidWav;
      }
    }
    ask::sha256_update(&hash, slice, got);
    if ((++slices & 15) == 0) vTaskDelay(1);  // let the idle task run during a long read
  }
  if (reader.size() == 0) return Hashed::NotAValidWav;
  uint8_t digest[32];
  ask::sha256_finish(&hash, digest);
  ask::hex_encode(digest, 32, hex);
  return Hashed::Ok;
}

struct Attempt {
  ask::Upload outcome = ask::Upload::Retry;
  bool held = false;           // not tried, or cut short, so as not to compete with a recording
  uint32_t retryAfter = 0;     // seconds the server asked us to leave it alone
};

static void setAside(const char* name, const char* dir, const char* why) {
  storage::moveFile(kDirPending, name, dir);
  problem(why);
  Serial.printf("[UPLOAD] %s: %s\n", name, why);
}

// Sends one timed part. Applies the outcome to the card. (Use sendOne(), which also tells everyone it is busy.)
static Attempt sendOnePart(const char* name, const ask::ClipName& clip) {
  Attempt attempt;
  char stamp[21];
  if (!ask::iso8601_utc(clip.when, stamp)) {
    setAside(name, kDirRejected, "a recording has an impossible start time and was set aside");
    attempt.outcome = ask::Upload::Reject;
    return attempt;
  }

  storage::Reader reader;
  if (!reader.open(kDirPending, name)) {
    problem("could not open a recording on the memory card");
    return attempt;
  }
  if (reader.size() > settings::maxUploadBytes()) {
    reader.close();
    setAside(name, kDirRejected, "a recording is larger than the server accepts and was set aside");
    attempt.outcome = ask::Upload::Reject;
    return attempt;
  }
  const size_t size = (size_t)reader.size();

  char hex[65];
  const Hashed hashed = hashClip(reader, hex);
  if (hashed == Hashed::NotAValidWav) {
    reader.close();
    setAside(name, kDirRejected, "a recording is not a valid WAV file and was set aside");
    attempt.outcome = ask::Upload::Reject;
    return attempt;
  }
  if (hashed != Hashed::Ok) {
    reader.close();
    problem("could not read a recording from the memory card");
    return attempt;
  }
  reader.close();
  if (!reader.open(kDirPending, name)) {
    problem("could not open a recording on the memory card");
    return attempt;
  }

  Connection c;
  if (!c.open(settings::serverUrl() + "/api/device/v1/recordings/" + clip.client_id)) {
    reader.close();
    return attempt;
  }
  c.http.setTimeout(kHttpTimeoutMs);
  const char* wanted[] = {"Retry-After"};
  c.http.collectHeaders(wanted, 1);
  c.http.addHeader("Authorization", "Bearer " + settings::credential());
  c.http.addHeader("Content-Type", "audio/wav");
  c.http.addHeader("X-Content-SHA256", hex);
  c.http.addHeader("X-Recorded-At", stamp);
  c.http.addHeader("X-Session-Ref", clip.session);
  c.http.addHeader("X-Sequence", String((unsigned long)clip.part));

  ClipStream stream(reader, size);
  const int httpStatus = c.http.sendRequest("PUT", &stream, size);
  reader.close();

  if (stream.failed()) {
    // The card was remounted, was unreadable, or a recording needed it: whatever the server said, this was not a
    // clean upload of the whole part. Keep the file; the server stored nothing it could confirm.
    c.http.end();
    attempt.held = stream.held();
    problem(stream.held() ? "upload paused while recording" : "the memory card could not be read during an upload");
    return attempt;
  }

  char reply[kMaxReplyBytes + 1];
  const bool haveReply = httpStatus > 0 && readReply(c.http, reply, sizeof(reply));
  const String wait = httpStatus > 0 ? c.http.header("Retry-After") : String("");
  c.http.end();
  if (wait.length() > 0) attempt.retryAfter = (uint32_t)wait.toInt();

  char errorCode[40] = "";
  if (haveReply && httpStatus != 200 && httpStatus != 201) ask::json_string(reply, "code", errorCode, sizeof(errorCode));
  attempt.outcome = ask::classify_upload(httpStatus, errorCode);

  switch (attempt.outcome) {
    case ask::Upload::Done: {
      // A 200 or 201 is not enough: the receipt must name this part and repeat the hash and size that were sent.
      ask::Ack ack;
      if (!haveReply || !ask::ack_validate(reply, clip.client_id, hex, (uint32_t)size, &ack)) {
        attempt.outcome = ask::Upload::Retry;
        unconfirmed = unconfirmed + 1;
        problem("the server answered but its receipt could not be confirmed; the recording was kept");
        Serial.printf("[UPLOAD] %s: HTTP %d without a valid receipt (%lu in a row); kept\n", name, httpStatus,
                      (unsigned long)unconfirmed);
        break;
      }
      unconfirmed = 0;
      lastIdText.set(ack.recording_id);
      uploadedCount = uploadedCount + 1;
      lastUploadMs = millis();
      anyUpload = true;
      problem("");
      Serial.printf("[EVENT] UPLOADED file=%s recording=%s duplicate=%d (stored by the server)\n", name, ack.recording_id,
                    ack.duplicate ? 1 : 0);
      // The only place a part is deleted.
      if (!storage::removeFile(kDirPending, name)) {
        strncpy(pendingDelete, name, sizeof(pendingDelete) - 1);
        problem("a confirmed recording could not be removed from the card yet");
      }
      break;
    }
    case ask::Upload::Reject:
      setAside(name, kDirRejected, errorCode[0] ? "the server refused a recording; it was set aside, not deleted"
                                                : "the server refused a recording (too large or unsupported); it was set aside");
      break;
    case ask::Upload::NewId: {
      // The server already holds a different recording under this id. Give this one a fresh id and try again.
      ask::ClipName renamed = clip;
      char renamedFile[ask::kClipNameBytes];
      static uint32_t renumber = 0x80000000u;
      if (ask::client_id_make(renamed.client_id, sizeof(renamed.client_id), settings::bootCount(),
                              settings::bootSalt(), renumber++) &&
          ask::clip_name_make(renamedFile, sizeof(renamedFile), renamed)) {
        storage::renameFile(kDirPending, name, renamedFile);
      }
      break;
    }
    case ask::Upload::Repair:
      repairNeeded = true;
      problem("the server no longer accepts this locket's credential: pair it again");
      break;
    case ask::Upload::Denied:
      problem("the server's gateway refused the upload (403); trying again slowly");
      break;
    case ask::Upload::Retry:
      problem(httpStatus > 0 ? String("upload answered ") + httpStatus + (errorCode[0] ? String(" ") + errorCode : String(""))
                             : String("upload could not reach the server"));
      break;
  }
  return attempt;
}

static Attempt sendOne(const char* name, const ask::ClipName& clip) {
  uploadingNow = true;
  Serial.printf("[EVENT] UPLOADING file=%s\n", name);
  const Attempt attempt = sendOnePart(name, clip);
  uploadingNow = false;
  return attempt;
}

// A part recorded before the clock was set carries seconds-since-start instead of a time. If the clock is set now
// and it is the same start-up, the real time can be worked out exactly. From an earlier start-up it cannot, so
// the part is set aside under /untimed (kept, never guessed at).
static void settleUntimed(const char* name, const ask::ClipName& clip) {
  const int64_t now = (int64_t)time(nullptr);
  int64_t started = 0;
  if (ask::client_id_boot(clip.client_id) == settings::bootCount() &&
      ask::resolve_start_time(now, millis() / 1000, clip.when, &started)) {
    ask::ClipName timed = clip;
    timed.timed = true;
    timed.when = started;
    char renamed[ask::kClipNameBytes];
    if (ask::clip_name_make(renamed, sizeof(renamed), timed) && storage::renameFile(kDirPending, name, renamed)) {
      Serial.printf("[UPLOAD] %s: start time worked out now that the clock is set\n", name);
      return;
    }
    return;  // try again next time round
  }
  setAside(name, kDirUntimed, "a recording from before the clock was ever set could not be timed and was set aside");
}

// Everything the uploader remembers between rounds.
struct Loop {
  uint32_t failures = 0;
  uint32_t beatAt = 0;
  uint32_t freeCheckAt = 0;
  bool beatDone = false;
};

// Holds the network lock for one round of work.
struct NetGuard {
  NetGuard() { xSemaphoreTake(netLock, portMAX_DELAY); }
  ~NetGuard() { xSemaphoreGive(netLock); }
};

// One round of work. Returns how many milliseconds to wait before the next.
static uint32_t step(Loop& loop) {
  if (!settings::isPaired() || settings::serverUrl().length() == 0) return 1000;

  // Joining Wi-Fi is the network task's job (wifi_manager.cpp), which retries on its own: all that is needed here is to
  // wait until it has.
  if (!wifi_manager::connected()) {
    reachable = false;
    return 1000;
  }
  if (!clockSet()) {
    reachable = false;
    return ask::backoff_ms(loop.failures++, kRetryBaseMs, kRetryMaxMs, esp_random());
  }

  // Measuring free space walks the card's allocation table and can take seconds, so it happens here, never in
  // the capture task, and not while recording.
  if (!status::busyRecording() && millis() - loop.freeCheckAt > 60000) {
    storage::refreshFree();
    loop.freeCheckAt = millis();
  }

  // Once the server has said the credential is bad, ask again only every five minutes.
  const uint32_t beatGap = repairNeeded ? 300000 : settings::heartbeatSeconds() * 1000;
  if (!loop.beatDone || millis() - loop.beatAt >= beatGap) {
    heartbeat();
    loop.beatAt = millis();
    loop.beatDone = true;
  }
  if (repairNeeded) return 5000;

  if (pendingDelete[0] != '\0') {
    if (storage::removeFile(kDirPending, pendingDelete)) pendingDelete[0] = '\0';
    return 2000;
  }

  // A recording has first call on the card.
  if (!ask::upload_may_run(status::busyRecording(), ASK_UPLOAD_WHILE_RECORDING, sdwriter::queued(), kRingBlockCount,
                           kUploadHoldRingPercent)) {
    return 500;
  }

  char name[ask::kClipNameBytes];
  ask::ClipName clip;
  if (!storage::oldest(kDirPending, name, sizeof(name)) || !ask::clip_name_parse(name, &clip)) {
    // Files are waiting but none has a name this firmware makes (a recording from the other sketch, say): they are
    // ignored on purpose and nothing is sent. Say so once, after a few looks so a busy card does not cause it.
    static uint8_t looks = 0;
    static bool said = false;
    if (storage::ready() && storage::unreadableCount() > 0 && ++looks >= 5 && !said) {
      said = true;
      Serial.printf("[UPLOAD] %lu file(s) in /pending have names this firmware does not recognise, so they cannot be "
                    "sent (a recording from the other sketch, say). Type: files\n", (unsigned long)storage::unreadableCount());
      problem("files in /pending have names this firmware does not recognise (type: files)");
    }
    return 1000;
  }
  if (!clip.timed) {
    settleUntimed(name, clip);
    return 100;
  }

  const Attempt attempt = sendOne(name, clip);
  if (attempt.outcome == ask::Upload::Done) {
    loop.failures = 0;
    return 0;  // straight on to the next part
  }
  if (attempt.held) return 2000;
  if (attempt.outcome == ask::Upload::Retry || attempt.outcome == ask::Upload::Denied) {
    uint32_t wait = ask::backoff_ms(loop.failures++, kRetryBaseMs, kRetryMaxMs, esp_random());
    if (attempt.outcome == ask::Upload::Denied) wait = kRetryMaxMs;
    // The server said how long to leave it alone (rate limiting). Believe it, within reason.
    uint32_t asked = attempt.retryAfter > 3600 ? 3600 : attempt.retryAfter;
    asked *= 1000;
    if (asked > wait) wait = asked < kRetryMaxMs ? asked : kRetryMaxMs;
    char why[96];
    problemText.get(why, sizeof(why));
    Serial.printf("[UPLOAD] %s was not sent: %s. Trying again in %lu s (type retry to try now).\n", name,
                  why[0] != '\0' ? why : "no answer", (unsigned long)((wait + 999) / 1000));
    return wait;
  }
  return 1000;  // set aside, given a new id, or the credential was refused: nothing to wait for, but never spin
}

static void task(void*) {
  Loop loop;
  for (;;) {
    if (retryRequested) {
      retryRequested = false;
      loop.failures = 0;
    }
    uint32_t wait;
    {
      NetGuard guard;
      wait = step(loop);
    }
    // Sleep in short steps, so `retry` is noticed and the idle task is fed.
    if (wait == 0) vTaskDelay(1);
    for (uint32_t waited = 0; waited < wait && !retryRequested; waited += 250) vTaskDelay(pdMS_TO_TICKS(250));
  }
}

void begin() {
  netLock = xSemaphoreCreateMutex();
  xTaskCreatePinnedToCore(task, "uploader", kUploaderStackBytes, nullptr, 1, nullptr, 0);
}

Status status() {
  Status s;
  memset(&s, 0, sizeof(s));
  const wifi_manager::Status net = wifi_manager::status();
  s.wifiConnected = net.connected;
  s.clockSet = ask::clock_is_plausible((int64_t)time(nullptr));
  s.paired = settings::isPaired();
  s.needsPairing = repairNeeded;
  s.serverReachable = reachable;
  s.uploading = uploadingNow;
  s.uploaded = uploadedCount;
  s.lastUploadAgoSeconds = anyUpload ? (int32_t)((millis() - lastUploadMs) / 1000) : -1;
  s.lastHeartbeatAgoSeconds = anyBeat ? (int32_t)((millis() - lastBeatMs) / 1000) : -1;
  s.rssi = net.rssi;
  problemText.get(s.lastProblem, sizeof(s.lastProblem));
  lastIdText.get(s.lastRecordingId, sizeof(s.lastRecordingId));
  return s;
}

}  // namespace uploader
