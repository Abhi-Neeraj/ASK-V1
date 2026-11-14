// Settings that survive a restart, kept in the chip's own storage (not on the memory card, so swapping the card
// never unpairs the locket). Uses the same storage keys as the continuous-clip firmware, so a locket already
// paired by that firmware stays paired.

#pragma once

#include <Arduino.h>

namespace settings {

void begin();  // opens the storage and counts this start-up

// The network to join. This is the built-in one from config.h (ASK_DEFAULT_WIFI_SSID) unless a network was typed
// with `wifi <name> <password>` since this firmware was flashed. A network saved by an older firmware never counts
// when a built-in one exists. With no built-in network, the saved one is used, as before.
String wifiSsid();
String wifiPassword();  // never print or log this
bool wifiIsBuiltIn();   // is the network in use the one from config.h?
void setWifi(const String& ssid, const String& password);  // a network the owner typed: replaces the built-in one
void useBuiltInWifi();                                      // forget the typed network and go back to config.h's

String serverUrl();
// Accepts https:// (and http:// only when the firmware was built to allow it). Trailing slashes are dropped.
bool setServerUrl(const String& raw, String* problem);

bool isPaired();
String deviceId();
String credential();
void setPairing(const String& id, const String& secret);
void clearPairing();

// What the server asked for. A value it did not send (0) leaves the stored one alone; nonsense is ignored.
uint32_t heartbeatSeconds();
uint32_t maxClipSeconds();
uint32_t maxUploadBytes();
// How many parts may wait for upload before a new recording is refused (normally kMaxPendingParts; stored only
// so a test can lower it).
uint32_t maxPendingParts();
void setServerConfig(uint32_t heartbeatSeconds, uint32_t maxClipSeconds, uint32_t maxUploadBytes);

// Counts start-ups, and a random number picked at each one. With a counter inside a start-up they make recording
// ids that are never reused, even after a power cut or a swapped card.
uint32_t bootCount();
uint32_t bootSalt();
uint32_t nextSequence();  // 0, 1, 2 ... for each part saved in this start-up
uint32_t nextSession();   // the same for each recording

// The local maintenance Wi-Fi. Off unless switched on. Its password is chosen by the locket on first use and is
// shown only on the USB serial connection.
bool apEnabled();
void setApEnabled(bool on);
String apSsid();
String apPassword();
bool setApPassword(const String& password, String* problem);

}  // namespace settings
