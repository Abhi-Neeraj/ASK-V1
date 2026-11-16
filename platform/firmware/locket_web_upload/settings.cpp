#include "settings.h"

#include <Preferences.h>
#include <esp_random.h>  // esp_random() is declared here, not in Arduino.h or esp_system.h

#include "config.h"
#include "core.h"

namespace settings {

static Preferences prefs;
static uint32_t boot = 0;
static uint32_t salt = 0;
static uint32_t sequence = 0;       // each counter is used by one task only
static uint32_t sessionNumber = 0;

// Where the saved network came from: "wsrc" is 1 only when `wifi <name> <password>` was typed since this build was
// flashed. "wbid" remembers which build the settings belong to: it changes with every compile and whenever the
// built-in network is edited.
static uint32_t fnv(uint32_t hash, const char* text) {
  for (; *text != '\0'; text++) {
    hash ^= (uint8_t)*text;
    hash *= 16777619u;
  }
  return hash;
}
// Every compile of the sketch has its own stamp (its date and time). Tests on a computer name one themselves, as a bare
// word (-DASK_BUILD_STAMP=buildA), because some compilers refuse __DATE__ and __TIME__ to keep builds reproducible.
#ifdef ASK_BUILD_STAMP
#define ASK_STAMP_TEXT2(x) #x
#define ASK_STAMP_TEXT(x) ASK_STAMP_TEXT2(x)
static const char* const kBuildStamp = ASK_STAMP_TEXT(ASK_BUILD_STAMP);
#else
static const char* const kBuildStamp = __DATE__ " " __TIME__;
#endif
static uint32_t buildId() {
  uint32_t hash = fnv(2166136261u, kBuildStamp);
  hash = fnv(hash, ASK_DEFAULT_WIFI_SSID);
  return fnv(hash, ASK_DEFAULT_WIFI_PASSWORD);
}
static bool haveBuiltIn() { return ASK_DEFAULT_WIFI_SSID[0] != '\0'; }
static bool typedNetwork() { return prefs.getUInt("wsrc", 0) == 1 && prefs.getString("ssid", "").length() > 0; }

void begin() {
  prefs.begin("ask", false);
  boot = prefs.getUInt("boot", 0) + 1;
  prefs.putUInt("boot", boot);
  salt = esp_random();

  // A new build of the firmware starts from its built-in network. Whatever was saved before (by an older firmware,
  // or typed in with `wifi` on an earlier build) is cleared, so that old test settings can never override it. A
  // network typed on THIS build is kept across restarts until `wifi default` is typed.
  if (haveBuiltIn() && prefs.getUInt("wbid", 0) != buildId()) {
    if (prefs.getString("ssid", "").length() > 0) {
      Serial.println(F("[WiFi] A network saved before this firmware was flashed was cleared; using the built-in network."));
    }
    useBuiltInWifi();
    prefs.putUInt("wbid", buildId());
  }
  const char* problem = ask::wifi_credentials_problem(ASK_DEFAULT_WIFI_SSID, ASK_DEFAULT_WIFI_PASSWORD);
  if (haveBuiltIn() && problem[0] != '\0') Serial.printf("[WiFi] WARNING: the built-in network cannot be used: %s\n", problem);
}

String wifiSsid() {
  if (typedNetwork()) return prefs.getString("ssid", "");
  if (haveBuiltIn()) return String(ASK_DEFAULT_WIFI_SSID);
  return prefs.getString("ssid", "");  // no built-in network: whatever was saved, as before
}
String wifiPassword() {
  if (typedNetwork()) return prefs.getString("wpass", "");
  if (haveBuiltIn()) return String(ASK_DEFAULT_WIFI_PASSWORD);
  return prefs.getString("wpass", "");
}
bool wifiIsBuiltIn() { return !typedNetwork() && haveBuiltIn(); }
void setWifi(const String& ssid, const String& password) {
  prefs.putString("ssid", ssid);
  prefs.putString("wpass", password);
  prefs.putUInt("wsrc", 1);
}
void useBuiltInWifi() {
  prefs.putUInt("wsrc", 0);
  prefs.remove("ssid");
  prefs.remove("wpass");
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

uint32_t heartbeatSeconds() { return prefs.getUInt("beat", kDefaultHeartbeatSeconds); }
uint32_t maxClipSeconds() { return prefs.getUInt("maxclip", kDefaultMaxClipSeconds); }
uint32_t maxUploadBytes() { return prefs.getUInt("maxup", kDefaultMaxUploadBytes); }
uint32_t maxPendingParts() { return prefs.getUInt("maxpend", kMaxPendingParts); }

void setServerConfig(uint32_t beat, uint32_t maxClip, uint32_t maxUpload) {
  // Only remember sensible values, so a bad reply can never stop the locket recording or flood the server.
  if (beat >= 10 && beat <= 3600) prefs.putUInt("beat", beat);
  if (maxClip >= 2 && maxClip <= 86400) prefs.putUInt("maxclip", maxClip);
  if (maxUpload >= 1024 * 1024 && maxUpload <= 1024u * 1024 * 1024) prefs.putUInt("maxup", maxUpload);
}

uint32_t bootCount() { return boot; }
uint32_t bootSalt() { return salt; }
uint32_t nextSequence() { return sequence++; }
uint32_t nextSession() { return sessionNumber++; }

bool apEnabled() { return prefs.getUInt("apon", 0) != 0; }
void setApEnabled(bool on) { prefs.putUInt("apon", on ? 1 : 0); }

String apSsid() {
  String name = prefs.getString("apssid", "");
  if (name.length() > 0) return name;
  // The last four hex digits of the chip's own address tell lockets apart without any setting.
  char suffix[8];
  snprintf(suffix, sizeof(suffix), "%04X", (unsigned)(ESP.getEfuseMac() & 0xFFFF));
  return String("ASK-LOCKET-") + suffix;
}

String apPassword() {
  String password = prefs.getString("appass", "");
  if (password.length() >= 12) return password;
  // No look-alike letters or digits, so it can be read off a screen and typed.
  static const char alphabet[] = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  char generated[17];
  for (size_t i = 0; i < 16; i++) generated[i] = alphabet[esp_random() % (sizeof(alphabet) - 1)];
  generated[16] = '\0';
  password = generated;
  prefs.putString("appass", password);
  return password;
}

bool setApPassword(const String& password, String* problem) {
  if (password.length() < 12 || password.length() > 63) {
    *problem = "The password must be 12 to 63 characters";
    return false;
  }
  prefs.putString("appass", password);
  return true;
}

}  // namespace settings
