// The locket's permanent identity: its product ID and 32-byte secret, written ONCE at manufacture with the
// serial command `provision <productId> <secretHex>` (both printed by the server's `register-locket`). A second
// `provision` is refused, so the product ID can never change. The secret is never printed or logged.
#pragma once

#include <Arduino.h>

namespace identity {

void begin();
bool provisioned();
String productId();          // "" until provisioned
bool secret(uint8_t out[32]);  // false until provisioned
// Writes the identity if none is stored and both values are valid. `problem` explains a refusal.
bool provision(const String& productId, const String& secretHex, String* problem);

}  // namespace identity
