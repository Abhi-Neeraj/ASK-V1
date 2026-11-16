// The locket's Bluetooth side (spec 5.2 and 5.3): the ASK service, pairing with the emailed code, and
// notifications to the phone. Built on the ESP32 core's BLE library. NEVER RUN ON A DEVICE where this was written.
#pragma once

#include <Arduino.h>

namespace ble_link {

// Starts advertising the ASK service under the product ID. False if Bluetooth could not start.
bool begin(const String& productId);
bool connected();      // a phone is connected AND the link is encrypted with a key made with the passkey
uint16_t mtu();        // the negotiated packet size
// Sends one frame on AUDIO. False if no phone can receive it now (not connected, or the link is busy).
bool notifyAudio(const uint8_t* frame, size_t length);
void notifyStatus(const char* text);
// Called from the Bluetooth task for every CONTROL write, one command per call.
void setCommandHandler(void (*handler)(const char* line));
void forgetCurrent();  // FORGET: drop the pairing of the phone that asked
void forgetAll();      // console: drop every pairing
void tick();           // from loop(): new pairing number every 10 minutes, STATUS every 2 seconds

}  // namespace ble_link
