// Firmware updates over Wi-Fi (spec 5.5), and the dispatcher for every CONTROL command from the phone.
//
// `OTA <version> <size> <sha256> <url>`: refused while recording or while another update runs. Joins the Wi-Fi
// network the phone gave (`WIFI`), downloads over HTTPS (the server's certificate is checked against the core's
// built-in authorities; plain http only with ASK_ALLOW_PLAIN_HTTP), writes into the spare app slot while hashing,
// and activates the new firmware only if the size and SHA-256 match. A new firmware marks itself good once
// Bluetooth is up again (confirmRunningImage); if it crashes before that, the bootloader goes back to the old one
// (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is on in core 3.3.12).
//
// NEVER RUN ON A DEVICE where this was written: compiled against the core's headers only.

#include "ota.h"

#include <HTTPClient.h>
#include <NetworkClientSecure.h>
#include <Update.h>
#include <WiFi.h>
#include <esp_ota_ops.h>

#include "ble_core.h"
#include "ble_link.h"
#include "config.h"
#include "core.h"
#include "recorder.h"
#include "sender.h"
#include "settings.h"
#include "status.h"

namespace ota {

static volatile bool busy = false;
static volatile int progress = 0;
static askble::Command job;  // copied before the task starts; only the task reads it afterwards

bool running() { return busy; }
int percent() { return progress; }

static void say(const char* text) { Serial.printf("[OTA] %s\n", text); }

static bool joinWifi() {
  const String ssid = settings::wifiSsid();
  if (ssid.length() == 0) {
    say("no Wi-Fi network: the phone must send one first");
    return false;
  }
  WiFi.persistent(false);  // the password stays only in this firmware's own settings, which WIFI CLEAR empties
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid.c_str(), settings::wifiPassword().c_str());
  const uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < kWifiConnectTimeoutMs) delay(250);
  if (WiFi.status() != WL_CONNECTED) {
    say("could not join the Wi-Fi network");
    return false;
  }
  return true;
}

// The core's built-in list of certificate authorities. Board package 3.3.12 has useBuiltinCACertBundle() for this;
// 3.3.10 does not, so it is done here the same way, which works on both.
class BundleClient : public NetworkClientSecure {
 public:
  void useBuiltInAuthorities() {
    attach_ssl_certificate_bundle(sslclient.get(), true);
    _use_ca_bundle = true;
  }
};

static bool download() {
  const String url = job.text1;
  const bool https = url.startsWith("https://");
  if (!https && !(ASK_ALLOW_PLAIN_HTTP && url.startsWith("http://"))) {
    say("refused: the download address must be https://");
    return false;
  }
  if (job.size == 0 || job.size > kOtaMaxBytes) {
    say("refused: the firmware size is not plausible");
    return false;
  }
  BundleClient secure;
  NetworkClient plain;
  if (https) secure.useBuiltInAuthorities();
  HTTPClient http;
  http.setTimeout(kOtaHttpTimeoutMs);
  if (!(https ? http.begin(secure, url) : http.begin(plain, url))) {
    say("could not start the download");
    return false;
  }
  const int code = http.GET();
  if (code != 200) {
    Serial.printf("[OTA] the server answered %d\n", code);
    http.end();
    return false;
  }
  const int length = http.getSize();
  if (length >= 0 && (uint32_t)length != job.size) {
    say("refused: the file is not the size the phone announced");
    http.end();
    return false;
  }
  if (!Update.begin(job.size)) {
    Serial.printf("[OTA] cannot prepare the update slot: %s\n", Update.errorString());
    http.end();
    return false;
  }
  ask::Sha256 sha;
  ask::sha256_begin(&sha);
  NetworkClient* stream = http.getStreamPtr();
  static uint8_t buffer[4096];
  uint32_t got = 0;
  uint32_t lastData = millis();
  while (got < job.size && millis() - lastData < kOtaHttpTimeoutMs) {
    const size_t want = min((size_t)(job.size - got), sizeof(buffer));
    const int n = stream->readBytes(buffer, want);
    if (n <= 0) {
      delay(10);
      continue;
    }
    lastData = millis();
    ask::sha256_update(&sha, buffer, (size_t)n);
    if (Update.write(buffer, (size_t)n) != (size_t)n) {
      Serial.printf("[OTA] writing failed: %s\n", Update.errorString());
      Update.abort();
      http.end();
      return false;
    }
    got += (uint32_t)n;
    progress = (int)((uint64_t)got * 100 / job.size);
  }
  http.end();
  uint8_t digest[32];
  ask::sha256_finish(&sha, digest);
  if (!askble::ota_image_ok(job.size, job.sha256, got, digest)) {
    say("refused: the download is short, too long or its checksum does not match. Nothing was changed.");
    Update.abort();
    return false;
  }
  if (!Update.end(true)) {
    Serial.printf("[OTA] the new firmware was not accepted: %s\n", Update.errorString());
    return false;
  }
  return true;
}

static void run(void*) {
  Serial.printf("[OTA] updating to %s\n", job.version);
  const bool ok = joinWifi() && download();
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  if (ok) {
    // Recording cannot start while an update runs (recorder and REC START check ota::running()); this is a last
    // guard so a restart never cuts a recording short.
    while (status::busyRecording()) delay(1000);
    say("installed; restarting into the new firmware");
    delay(500);
    ESP.restart();
  }
  progress = 0;
  busy = false;
  sender::wake();
  vTaskDelete(nullptr);
}

static void startUpdate(const askble::Command& c) {
  if (busy) return;
  if (status::busyRecording()) {
    say("refused while recording; stop the recording first");
    return;
  }
  job = c;
  busy = true;
  progress = 0;
  if (xTaskCreatePinnedToCore(run, "ask-ota", kOtaStackBytes, nullptr, 1, nullptr, 1) != pdPASS) busy = false;
}

void onCommandOrForward(const char* line) {
  const askble::Command c = askble::parse_command(line);
  switch (c.kind) {
    case askble::Command::Wifi:
      settings::setWifi(c.text1, c.text2);  // never printed
      say("Wi-Fi network saved for updates");
      break;
    case askble::Command::WifiClear:
      settings::clearWifi();
      break;
    case askble::Command::Ota:
      startUpdate(c);
      break;
    case askble::Command::RecStart:
      if (busy) {
        say("recording refused: a firmware update is being installed");
      } else if (status::state() == status::State::Idle) {
        recorder::requestPress();
      }
      break;
    case askble::Command::RecStop:
      if (status::state() == status::State::Recording) recorder::requestPress();
      break;
    case askble::Command::Forget:
      ble_link::forgetCurrent();
      break;
    case askble::Command::Ack:
    case askble::Command::Resend:
    case askble::Command::Time:
      sender::onCommand(c);
      break;
    case askble::Command::Invalid:
      break;
  }
}

void confirmRunningImage() {
  const esp_partition_t* running = esp_ota_get_running_partition();
  esp_ota_img_states_t state;
  if (running && esp_ota_get_state_partition(running, &state) == ESP_OK && state == ESP_OTA_IMG_PENDING_VERIFY) {
    esp_ota_mark_app_valid_cancel_rollback();
    say("the new firmware started and is kept");
  }
}

}  // namespace ota
