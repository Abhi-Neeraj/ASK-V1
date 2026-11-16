#include "wifi_manager.h"

#include <WiFi.h>
#include <esp_random.h>  // esp_random() is declared here, not in Arduino.h or esp_system.h
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "config.h"
#include "core.h"
#include "settings.h"
#include "status.h"

namespace wifi_manager {

static portMUX_TYPE guard = portMUX_INITIALIZER_UNLOCKED;
static volatile State currentState = State::NoNetwork;
static volatile bool linkUp = false;
static volatile bool reconnectRequested = false;
static volatile uint32_t attempts = 0;
static volatile uint32_t retryAt = 0;          // millis() when the next attempt starts, while Waiting
static volatile unsigned lastReasonCode = 0;   // set by the chip's disconnect event; kept until the next success
static volatile bool reasonFresh = false;      // the chip gave a reason during the attempt that is in progress
static volatile bool attemptFailed = false;    // ... and it is one that ends the attempt (no such network, refused)
static volatile uint32_t ownLeaveUntil = 0;    // millis() until which a "leave" (code 8) is our own doing, not news
static ::status::Text reasonText;
static char connectedIp[16] = "";
static volatile int connectedRssi = 0;

bool connected() { return linkUp; }

Status status() {
  Status s;
  memset(&s, 0, sizeof(s));
  s.state = currentState;
  s.connected = linkUp;
  s.builtIn = settings::wifiIsBuiltIn();
  const String ssid = settings::wifiSsid();
  strncpy(s.ssid, ssid.c_str(), sizeof(s.ssid) - 1);
  s.attempts = attempts;
  s.lastReasonCode = lastReasonCode;
  reasonText.get(s.lastReason, sizeof(s.lastReason));
  if (linkUp) {
    portENTER_CRITICAL(&guard);
    strncpy(s.ip, connectedIp, sizeof(s.ip) - 1);
    portEXIT_CRITICAL(&guard);
    s.rssi = connectedRssi;
  }
  if (currentState == State::Waiting) {
    const uint32_t now = millis();
    s.retryInSeconds = (int32_t)(retryAt - now) > 0 ? (retryAt - now + 999) / 1000 : 0;
  }
  return s;
}

void reconnectNow() { reconnectRequested = true; }

bool waitConnected(uint32_t timeoutMs) {
  const uint32_t started = millis();
  while (!linkUp) {
    if (millis() - started >= timeoutMs) return false;
    vTaskDelay(pdMS_TO_TICKS(100));
  }
  return true;
}

// The chip says why a connection failed or dropped. Kept for the message that follows. (WiFi.status() is not used for
// this: the core changes it only on these same events, so after one failure it keeps saying "failed" into the next try.)
static void onDisconnected(arduino_event_id_t, arduino_event_info_t info) {
  const unsigned code = info.wifi_sta_disconnected.reason;
  const bool weLeft = code == 8 && (int32_t)(ownLeaveUntil - millis()) > 0;  // ASSOC_LEAVE after our own disconnect()
  if (weLeft) return;
  lastReasonCode = code;
  char text[96];
  snprintf(text, sizeof(text), "%s (%s, code %u)", ask::wifi_reason_hint(code),
           WiFi.disconnectReasonName((wifi_err_reason_t)code), code);
  reasonText.set(text);
  reasonFresh = true;
  if (ask::wifi_reason_ends_attempt(code)) attemptFailed = true;
}

// Drops whatever link or attempt there is, keeping the radio on. The leave it causes is not a reason to report.
static void leave() {
  ownLeaveUntil = millis() + 1500;
  WiFi.disconnect(false, false);
}

static bool joined(const String& ssid) {
  // Connected, and to the network asked for: a link to the previous network can still read "connected" for a moment.
  return WiFi.status() == WL_CONNECTED && WiFi.SSID() == ssid;
}

// One try at joining. Returns true if the link is up. Never blocks longer than kWifiConnectTimeoutMs.
// The reason for the last failure stays on show (`status`, the local page) until a connection succeeds; `reasonFresh`
// tells whether this attempt produced its own.
static bool tryToJoin(const String& ssid, const String& password) {
  currentState = State::Connecting;
  leave();  // start from a clean state, keeping the radio on
  reasonFresh = false;
  attemptFailed = false;
  WiFi.begin(ssid.c_str(), password.c_str());
  const uint32_t started = millis();
  while (millis() - started < kWifiConnectTimeoutMs) {
    if (joined(ssid)) return true;
    // The chip has said this try cannot succeed (no such network, or it refused us): no need to wait out the clock.
    if (attemptFailed) break;
    if (reconnectRequested) return false;
    vTaskDelay(pdMS_TO_TICKS(250));
  }
  return joined(ssid);
}

static void announceConnected() {
  const String ip = WiFi.localIP().toString();
  portENTER_CRITICAL(&guard);
  strncpy(connectedIp, ip.c_str(), sizeof(connectedIp) - 1);
  connectedIp[sizeof(connectedIp) - 1] = '\0';
  portEXIT_CRITICAL(&guard);
  connectedRssi = WiFi.RSSI();
  Serial.println(F("[WiFi] Connected successfully"));
  Serial.printf("[WiFi] IP address: %s   signal strength (RSSI): %d dBm\n", connectedIp, (int)connectedRssi);
  attempts = 0;
  lastReasonCode = 0;
  reasonText.set("");
  linkUp = true;
  currentState = State::Connected;
}

static void task(void*) {
  bool wasConnected = false;
  bool saidNoNetwork = false;
  for (;;) {
    const String ssid = settings::wifiSsid();
    const String password = settings::wifiPassword();

    if (reconnectRequested) {  // `wifi` was typed: start again on the (new) network
      reconnectRequested = false;
      leave();
      linkUp = false;
      wasConnected = false;
      attempts = 0;
    }

    if (ssid.length() == 0) {
      if (!saidNoNetwork) {
        Serial.println(F("[WiFi] No network is set. Type: wifi <name> <password>"));
        saidNoNetwork = true;
      }
      currentState = State::NoNetwork;
      linkUp = false;
      vTaskDelay(pdMS_TO_TICKS(1000));
      continue;
    }
    saidNoNetwork = false;

    if (joined(ssid)) {
      if (!wasConnected) {
        announceConnected();
        wasConnected = true;
      } else {
        connectedRssi = WiFi.RSSI();
      }
      vTaskDelay(pdMS_TO_TICKS(1000));
      continue;
    }

    // Not connected: either it never was, or the link dropped.
    if (wasConnected) {
      char why[96];
      reasonText.get(why, sizeof(why));
      Serial.printf("[WiFi] Connection lost%s%s\n", why[0] ? ": " : "", why);
      wasConnected = false;
      linkUp = false;
      attempts = 0;
    }

    attempts = attempts + 1;
    if (attempts == 1) Serial.printf("[WiFi] Connecting to %s...\n", ssid.c_str());
    else Serial.printf("[WiFi] Connecting to %s... (attempt %lu)\n", ssid.c_str(), (unsigned long)attempts);

    if (tryToJoin(ssid, password)) continue;  // the loop announces it
    if (reconnectRequested) continue;

    char why[96];
    if (reasonFresh) {
      reasonText.get(why, sizeof(why));
    } else {
      // The chip gave no reason for this attempt: it simply did not answer in time.
      strncpy(why, "no answer within the time allowed", sizeof(why) - 1);
      why[sizeof(why) - 1] = '\0';
      lastReasonCode = 0;
      reasonText.set(why);
    }
    const uint32_t wait = ask::backoff_ms(attempts - 1, kWifiRetryBaseMs, kWifiRetryMaxMs, esp_random());
    retryAt = millis() + wait;
    currentState = State::Waiting;
    Serial.printf("[WiFi] Connection failed: %s. Trying again in %lu s.\n", why, (unsigned long)((wait + 999) / 1000));
    // Wait in short steps so a `wifi` command (or the network coming back) is acted on promptly.
    while ((int32_t)(retryAt - millis()) > 0 && !reconnectRequested) vTaskDelay(pdMS_TO_TICKS(250));
  }
}

void begin() {
  // No copy of the network is kept in the chip's own Wi-Fi storage (settings.cpp is the one place it lives), and the
  // chip does not reconnect on its own: this task does, so there is one owner and one set of messages.
  WiFi.persistent(false);
  WiFi.setAutoReconnect(false);
  WiFi.mode(settings::apEnabled() ? WIFI_AP_STA : WIFI_STA);
  WiFi.setSleep(false);  // keep the radio awake through large uploads
  WiFi.onEvent(onDisconnected, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  xTaskCreatePinnedToCore(task, "wifi", kNetworkStackBytes, nullptr, 1, nullptr, 0);
}

}  // namespace wifi_manager
