// What the locket remembers across restarts: Wi-Fi, the server's address, its credential, and a boot counter.
// Kept in the chip's non-volatile storage, not on the card, so swapping the card never unpairs the locket.
//
// The credential and the Wi-Fi password are secrets. Nothing here prints them, and nothing else should.

#pragma once

#include <Arduino.h>

namespace settings {

void begin();

String wifiSsid();
String wifiPassword();
void setWifi(const String& ssid, const String& password);

// Base address of the server, no trailing slash, for example https://ask.example.com
String serverUrl();
bool setServerUrl(const String& url, String* problem);

bool isPaired();
String deviceId();
String credential();
void setPairing(const String& deviceId, const String& credential);
void clearPairing();

// Server-chosen limits, refreshed at pairing and with every heartbeat.
uint32_t chunkSeconds();
uint32_t heartbeatSeconds();
void setServerConfig(uint32_t chunkSeconds, uint32_t heartbeatSeconds);

// A number that goes up by one every time the locket starts, and a random one picked at each start. Together
// with a counter they make clip ids that are never reused (see core.h).
uint32_t bootCount();
uint32_t bootSalt();

}  // namespace settings
