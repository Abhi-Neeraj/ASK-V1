// Stored settings of the Bluetooth locket (flash, namespace "ask-ble"). The functions the recording path uses
// (recorder.cpp, sdwriter.cpp, copied from locket_web_upload) keep the same names and meaning.
#pragma once

#include <Arduino.h>

namespace settings {

void begin();  // opens the storage and counts this start-up

// The recording path asks "may we record?"; for this locket that means "does it know its product ID and secret".
bool isPaired();

uint32_t maxClipSeconds();
uint32_t maxUploadBytes();
uint32_t maxPendingParts();
uint32_t bootCount();
uint32_t bootSalt();
uint32_t nextSequence();  // 0, 1, 2 ... for each part saved in this start-up
uint32_t nextSession();   // the same for each recording

// The Wi-Fi network the phone gave for firmware updates. Never print or log the password.
String wifiSsid();
String wifiPassword();
void setWifi(const String& ssid, const String& password);
void clearWifi();

}  // namespace settings
