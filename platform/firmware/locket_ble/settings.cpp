#include "settings.h"

#include <Preferences.h>
#include <esp_random.h>  // esp_random() is declared here, not in Arduino.h or esp_system.h

#include "config.h"
#include "identity.h"

namespace settings {

static Preferences prefs;
static uint32_t boot = 0;
static uint32_t salt = 0;
static uint32_t sequence = 0;  // each counter is used by one task only
static uint32_t sessionNumber = 0;

void begin() {
  prefs.begin("ask-ble", false);
  boot = prefs.getUInt("boot", 0) + 1;
  prefs.putUInt("boot", boot);
  salt = esp_random();
}

bool isPaired() { return identity::provisioned(); }

uint32_t maxClipSeconds() { return kDefaultMaxClipSeconds; }
uint32_t maxUploadBytes() { return kDefaultMaxUploadBytes; }
uint32_t maxPendingParts() { return kMaxPendingParts; }
uint32_t bootCount() { return boot; }
uint32_t bootSalt() { return salt; }
uint32_t nextSequence() { return sequence++; }
uint32_t nextSession() { return sessionNumber++; }

String wifiSsid() { return prefs.getString("ssid", ""); }
String wifiPassword() { return prefs.getString("wpass", ""); }

void setWifi(const String& ssid, const String& password) {
  prefs.putString("ssid", ssid);
  prefs.putString("wpass", password);
}

void clearWifi() {
  prefs.remove("ssid");
  prefs.remove("wpass");
}

}  // namespace settings
