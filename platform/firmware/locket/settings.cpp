#include "settings.h"

#include <Preferences.h>
#include <esp_random.h>  // esp_random() is declared here, not in Arduino.h or esp_system.h

#include "config.h"

namespace settings {

static Preferences prefs;
static uint32_t boot = 0;
static uint32_t salt = 0;

void begin() {
  prefs.begin("ask", false);
  boot = prefs.getUInt("boot", 0) + 1;
  prefs.putUInt("boot", boot);
  salt = esp_random();
}

String wifiSsid() { return prefs.getString("ssid", ""); }
String wifiPassword() { return prefs.getString("wpass", ""); }
void setWifi(const String& ssid, const String& password) {
  prefs.putString("ssid", ssid);
  prefs.putString("wpass", password);
}

String serverUrl() { return prefs.getString("server", ""); }

bool setServerUrl(const String& raw, String* problem) {
  String url = raw;
  url.trim();
  while (url.endsWith("/")) url.remove(url.length() - 1);
  const bool secure = url.startsWith("https://");
  const bool plain = url.startsWith("http://");
  if (!secure && !plain) {
    *problem = "The address must start with https://";
    return false;
  }
  if (plain && !ASK_ALLOW_PLAIN_HTTP) {
    *problem = "Plain http:// is switched off in this firmware (ASK_ALLOW_PLAIN_HTTP). Use https://";
    return false;
  }
  if (url.length() < 12 || url.length() > 95) {
    *problem = "That address is the wrong length";
    return false;
  }
  prefs.putString("server", url);
  return true;
}

bool isPaired() { return deviceId().length() > 0 && credential().length() > 0; }
String deviceId() { return prefs.getString("devid", ""); }
String credential() { return prefs.getString("cred", ""); }

void setPairing(const String& id, const String& secret) {
  prefs.putString("devid", id);
  prefs.putString("cred", secret);
}

void clearPairing() {
  prefs.remove("devid");
  prefs.remove("cred");
}

uint32_t chunkSeconds() { return prefs.getUInt("chunk", kDefaultChunkSeconds); }
uint32_t heartbeatSeconds() { return prefs.getUInt("beat", kDefaultHeartbeatSeconds); }

void setServerConfig(uint32_t chunk, uint32_t beat) {
  // Only remember sensible values, so a bad reply can never stop the locket recording or flood the server.
  if (chunk >= 1 && chunk <= 600) prefs.putUInt("chunk", chunk);
  if (beat >= 10 && beat <= 3600) prefs.putUInt("beat", beat);
}

uint32_t bootCount() { return boot; }
uint32_t bootSalt() { return salt; }

}  // namespace settings
