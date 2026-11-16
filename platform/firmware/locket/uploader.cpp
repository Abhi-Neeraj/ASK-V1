#include "uploader.h"

#include <HTTPClient.h>
#include <SD.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_random.h>  // esp_random() is declared here, not in Arduino.h or esp_system.h
#include <time.h>

#include "config.h"
#include "core.h"
#include "settings.h"
#include "storage.h"

namespace uploader {

static volatile bool needsPairing = false;
static volatile uint32_t uploadedCount = 0;
static String lastProblem;  // written by the uploader task, read for `status`; a torn read shows odd text, nothing worse
static bool ntpStarted = false;

static void problem(const String& text) { lastProblem = text; }

// ---- Connection -----------------------------------------------------------------------------------------

static bool wifiUp() {
  if (WiFi.status() == WL_CONNECTED) return true;
  const String ssid = settings::wifiSsid();
  if (ssid.length() == 0) {
    problem("no Wi-Fi network is set (use: wifi <name> <password>)");
    return false;
  }
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid.c_str(), settings::wifiPassword().c_str());
  const uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < kWifiConnectTimeoutMs) delay(250);
  if (WiFi.status() != WL_CONNECTED) {
    problem("could not join Wi-Fi");
    return false;
  }
  return true;
}

// Recording stamps each clip with the time it started, and a wrong time cannot be fixed later, so nothing is
// recorded until the clock has been set from the network.
static bool clockSet() {
  if (!ntpStarted) {
    configTime(0, 0, "pool.ntp.org", "time.google.com");
    ntpStarted = true;
  }
  const uint32_t started = millis();
  while (!ask::clock_is_plausible((int64_t)time(nullptr)) && millis() - started < kClockWaitMs) delay(250);
  return ask::clock_is_plausible((int64_t)time(nullptr));
}

// One HTTP request's worth of connection objects. The certificate text must outlive the request, so it lives here.
struct Connection {
  WiFiClientSecure secure;
  WiFiClient plain;
  HTTPClient http;
  String ca;
  bool tls = false;

  bool open(const String& url) {
    tls = url.startsWith("https://");
    if (!tls) {
      if (!ASK_ALLOW_PLAIN_HTTP) {
        problem("the server address is not https://");
        return false;
      }
      return http.begin(plain, url);
    }
    if (ASK_INSECURE_TLS) {
      secure.setInsecure();  // development only: the server is not verified
    } else {
      File f = SD.open(kCaFile, FILE_READ);
      if (!f) {
        problem("no certificate authority file (/ca.pem) on the memory card");
        return false;
      }
      ca = f.readString();
      f.close();
      secure.setCACert(ca.c_str());
    }
    return http.begin(secure, url);
  }
};

// Reads a short error body and pulls out the server's error code. Never keeps or prints anything else.
static String errorCode(HTTPClient& http) {
  const int size = http.getSize();
  if (size < 0 || size > (int)kMaxErrorBodyBytes) return "";
  const String body = http.getString();
  char code[40];
  return ask::json_string(body.c_str(), "code", code, sizeof(code)) ? String(code) : String("");
}

// ---- Pairing and heartbeats -----------------------------------------------------------------------------

bool pair(const String& code, String* message) {
  const String server = settings::serverUrl();
  if (server.length() == 0) {
    *message = "no server address is set (use: server https://your-address)";
    return false;
  }
  if (!wifiUp()) {
    *message = lastProblem;
    return false;
  }
  Connection c;
  if (!c.open(server + "/api/device/v1/pair")) {
    *message = lastProblem.length() ? lastProblem : String("could not reach the server");
    return false;
  }
  c.http.setTimeout(kHttpTimeoutMs);
  c.http.addHeader("Content-Type", "application/json");
  String body = String("{\"code\":\"") + code + "\",\"firmwareVersion\":\"" + ASK_FIRMWARE_VERSION + "\"}";
  const int status = c.http.POST(body);
  if (status != 200) {
    // The body can only be read once, so read it once.
    const String code = status > 0 ? errorCode(c.http) : String("");
    if (status <= 0) *message = "could not reach the server";
    else if (code.length() > 0) *message = "the server did not accept that code (" + code + ")";
    else *message = "the server answered " + String(status);
    c.http.end();
    return false;
  }
  const String reply = c.http.getString();
  c.http.end();

  char deviceId[48], credential[160];
  uint32_t chunk = 0, beat = 0;
  if (!ask::json_string(reply.c_str(), "deviceId", deviceId, sizeof(deviceId)) ||
      !ask::json_string(reply.c_str(), "credential", credential, sizeof(credential))) {
    *message = "the server's answer was not understood";
    return false;
  }
  settings::setPairing(deviceId, credential);
  if (ask::json_uint(reply.c_str(), "chunkSeconds", &chunk) &&
      ask::json_uint(reply.c_str(), "heartbeatIntervalSeconds", &beat)) {
    settings::setServerConfig(chunk, beat);
  }
  needsPairing = false;
  memset(credential, 0, sizeof(credential));  // do not leave the secret lying in memory
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
  if (!c.open(settings::serverUrl() + "/api/device/v1/heartbeat")) return;
  c.http.setTimeout(kHttpTimeoutMs);
  c.http.addHeader("Content-Type", "application/json");
  c.http.addHeader("Authorization", "Bearer " + settings::credential());
  String body = String("{\"firmwareVersion\":\"") + ASK_FIRMWARE_VERSION + "\",\"uploadQueueDepth\":" +
                String(storage::countIn(kDirPending));
  const int battery = batteryPercent();
  if (battery >= 0) body += ",\"batteryPercent\":" + String(battery);
  body += "}";

  const int status = c.http.POST(body);
  if (status == 200) {
    const String reply = c.http.getString();
    uint32_t chunk = 0, beat = 0;
    if (ask::json_uint(reply.c_str(), "chunkSeconds", &chunk) &&
        ask::json_uint(reply.c_str(), "heartbeatIntervalSeconds", &beat)) {
      settings::setServerConfig(chunk, beat);
    }
    needsPairing = false;
  } else if (ask::classify_upload(status, "") == ask::Upload::Repair) {
    needsPairing = true;
    problem("the server no longer accepts this locket's credential: pair it again");
  } else {
    problem(status > 0 ? "heartbeat answered " + String(status) : String("heartbeat could not reach the server"));
  }
  c.http.end();
}

// ---- Uploading clips ------------------------------------------------------------------------------------

// Sends one clip. Returns what to do next; applies it to the card.
static ask::Upload sendOne(const String& name, uint32_t* retryAfterSeconds) {
  *retryAfterSeconds = 0;
  String clientId;
  int64_t startedAt = 0;
  if (!storage::parseName(name, &clientId, &startedAt)) return ask::Upload::Reject;

  char stamp[21];
  if (!ask::iso8601_utc(startedAt, stamp)) {
    // The clip carries a time that cannot be right: the server needs a real one, so it cannot be sent. It is set
    // aside here, because leaving it at the front of the queue would make the uploader retry it for ever.
    storage::moveFile(kDirPending, name, kDirRejected);
    problem("a clip has an impossible start time and was set aside");
    return ask::Upload::Reject;
  }

  const String path = storage::path(kDirPending, name);
  File file = SD.open(path, FILE_READ);
  if (!file) return ask::Upload::Retry;
  const size_t size = file.size();

  // The server checks this hash, and a damaged copy is better caught here than stored.
  ask::Sha256 hash;
  ask::sha256_begin(&hash);
  static uint8_t buffer[4096];
  size_t left = size;
  while (left > 0) {
    const size_t n = file.read(buffer, left < sizeof(buffer) ? left : sizeof(buffer));
    if (n == 0) {
      file.close();
      problem("could not read a clip from the memory card");
      return ask::Upload::Retry;
    }
    ask::sha256_update(&hash, buffer, n);
    left -= n;
  }
  uint8_t digest[32];
  char hex[65];
  ask::sha256_finish(&hash, digest);
  ask::hex_encode(digest, 32, hex);
  file.seek(0);

  Connection c;
  if (!c.open(settings::serverUrl() + "/api/device/v1/recordings/" + clientId)) {
    file.close();
    return ask::Upload::Retry;
  }
  c.http.setTimeout(kHttpTimeoutMs);
  const char* wanted[] = {"Retry-After"};
  c.http.collectHeaders(wanted, 1);
  c.http.addHeader("Authorization", "Bearer " + settings::credential());
  c.http.addHeader("Content-Type", "audio/wav");
  c.http.addHeader("X-Content-SHA256", hex);
  c.http.addHeader("X-Recorded-At", stamp);

  const int status = c.http.sendRequest("PUT", &file, size);
  file.close();
  const String code = status > 0 ? errorCode(c.http) : String("");
  const String wait = status > 0 ? c.http.header("Retry-After") : String("");
  c.http.end();
  if (wait.length() > 0) *retryAfterSeconds = (uint32_t)wait.toInt();

  const ask::Upload outcome = ask::classify_upload(status, code.c_str());
  switch (outcome) {
    case ask::Upload::Done:
      storage::removeFile(kDirPending, name);  // the ONLY place a clip is deleted
      uploadedCount = uploadedCount + 1;
      break;
    case ask::Upload::Reject:
      storage::moveFile(kDirPending, name, kDirRejected);
      problem("the server refused a clip (" + code + "); it was set aside, not deleted");
      break;
    case ask::Upload::NewId: {
      // The server already holds a different clip under this id. Give this one a fresh id and try again.
      char fresh[ask::kClientIdBytes];
      static uint32_t renumber = 0x80000000u;
      if (ask::client_id_make(fresh, sizeof(fresh), settings::bootCount(), settings::bootSalt(), renumber++)) {
        storage::renameFile(kDirPending, name, storage::nameFor(fresh, startedAt));
      }
      break;
    }
    case ask::Upload::Repair:
      needsPairing = true;
      problem("the server no longer accepts this locket's credential: pair it again");
      break;
    case ask::Upload::Retry:
      problem(status > 0 ? "upload answered " + String(status) + " " + code : String("upload could not reach the server"));
      break;
  }
  return outcome;
}

static void task(void*) {
  uint32_t failures = 0;
  uint32_t lastBeat = 0;
  uint32_t lastFreeCheck = 0;
  bool beatDone = false;

  for (;;) {
    if (!settings::isPaired() || settings::serverUrl().length() == 0) {
      vTaskDelay(pdMS_TO_TICKS(1000));
      continue;
    }
    if (!wifiUp() || !clockSet()) {
      vTaskDelay(pdMS_TO_TICKS(ask::backoff_ms(failures++, kRetryBaseMs, kRetryMaxMs, esp_random())));
      continue;
    }
    // Measuring free space walks the card's allocation table and can take seconds, so it happens here, never in
    // the recorder, and only once a minute.
    if (millis() - lastFreeCheck > 60000) {
      storage::refreshFree();
      lastFreeCheck = millis();
    }
    const uint32_t beatEvery = settings::heartbeatSeconds() * 1000;
    // Once the server has said the credential is bad, ask again only every five minutes.
    const uint32_t beatGap = needsPairing ? 300000 : beatEvery;
    if (!beatDone || millis() - lastBeat >= beatGap) {
      heartbeat();
      lastBeat = millis();
      beatDone = true;
    }
    if (needsPairing) {
      vTaskDelay(pdMS_TO_TICKS(5000));
      continue;
    }

    String name;
    if (!storage::oldestPending(&name)) {
      vTaskDelay(pdMS_TO_TICKS(1000));
      continue;
    }
    uint32_t retryAfter = 0;
    const ask::Upload outcome = sendOne(name, &retryAfter);
    if (outcome == ask::Upload::Retry) {
      uint32_t wait = ask::backoff_ms(failures++, kRetryBaseMs, kRetryMaxMs, esp_random());
      // The server said how long to leave it alone (rate limiting). Believe it, within reason.
      if (retryAfter > 3600) retryAfter = 3600;
      const uint32_t asked = retryAfter * 1000;
      if (asked > wait) wait = asked < kRetryMaxMs ? asked : kRetryMaxMs;
      vTaskDelay(pdMS_TO_TICKS(wait));
    } else if (outcome == ask::Upload::Done) {
      failures = 0;
    } else {
      // Set aside, given a new id, or the credential was refused: nothing to wait for, but never spin.
      vTaskDelay(pdMS_TO_TICKS(1000));
    }
  }
}

void begin() { xTaskCreatePinnedToCore(task, "uploader", kUploaderStackBytes, nullptr, 2, nullptr, 0); }

Status status() {
  Status s;
  s.wifiConnected = WiFi.status() == WL_CONNECTED;
  s.clockSet = ask::clock_is_plausible((int64_t)time(nullptr));
  s.needsPairing = needsPairing;
  s.pending = storage::countIn(kDirPending);
  s.rejected = storage::countIn(kDirRejected);
  s.uploaded = uploadedCount;
  s.lastProblem = lastProblem;
  return s;
}

}  // namespace uploader
