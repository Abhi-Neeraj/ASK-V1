// The locket's identity: its product ID and 32-byte secret, written with the serial command
// `provision <productId> <secretHex>` (both shown by the website's Lockets screen or the server's
// `register-locket`). While an identity is stored, `provision` is refused. The only way to a different ID is the
// serial command `unprovision <current product ID>`: it needs the USB cable and the current ID, erases the identity
// and every pairing, and nothing sent over Bluetooth can reach it. The secret is never printed or logged.
#pragma once

#include <Arduino.h>

namespace identity {

void begin();
bool provisioned();
String productId();          // "" until provisioned
bool secret(uint8_t out[32]);  // false until provisioned
// Writes the identity if none is stored and both values are valid. `problem` explains a refusal.
bool provision(const String& productId, const String& secretHex, String* problem);
// Erases the stored identity so the locket can be provisioned again with a NEW product ID. Only when `productId`
// is the ID stored now (capitals do not matter). `problem` explains a refusal.
bool unprovision(const String& productId, String* problem);
// Erases the stored identity without asking for the ID (used by the serial reset code, config.h
// ASK_SERIAL_RESET_CODE). `problem` explains a failure.
bool erase(String* problem);

}  // namespace identity
