// Firmware updates over Wi-Fi (spec 5.5), and the dispatcher for commands the phone writes to CONTROL.
#pragma once

#include <Arduino.h>

namespace ota {

// Every CONTROL command arrives here: WIFI, WIFI CLEAR and OTA are handled by the updater, REC and FORGET by the
// locket, ACK, RESEND and TIME by the sender.
void onCommandOrForward(const char* line);
bool running();       // an update is downloading or installing
int percent();        // 0..100 while running
// Called once Bluetooth is up after a start: a freshly installed firmware marks itself good.
void confirmRunningImage();

}  // namespace ota
