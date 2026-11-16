#include "identity.h"

#include <Preferences.h>

#include "ble_core.h"

namespace identity {

static Preferences prefs;  // namespace "ask-id": only this file touches it

void begin() { prefs.begin("ask-id", false); }

bool provisioned() { return prefs.isKey("product") && prefs.isKey("secret") && prefs.getBytesLength("secret") == 32; }

String productId() { return provisioned() ? prefs.getString("product", "") : String(""); }

bool secret(uint8_t out[32]) {
  if (!provisioned()) return false;
  return prefs.getBytes("secret", out, 32) == 32;
}

bool provision(const String& productId, const String& secretHex, String* problem) {
  switch (askble::identity_accept(provisioned(), productId.c_str(), secretHex.c_str())) {
    case askble::Identity::AlreadySet:
      *problem = "this locket already has its product ID; it can never be changed";
      return false;
    case askble::Identity::BadId:
      *problem = "the product ID must be ASK- and six characters, as the server printed it";
      return false;
    case askble::Identity::BadSecret:
      *problem = "the secret must be the 64 hexadecimal characters the server printed";
      return false;
    case askble::Identity::Accepted:
      break;
  }
  uint8_t bytes[32];
  askble::hex_decode(secretHex.c_str(), bytes, 32);
  const bool ok = prefs.putBytes("secret", bytes, 32) == 32 && prefs.putString("product", productId) > 0;
  memset(bytes, 0, sizeof(bytes));
  if (!ok) *problem = "the flash refused the write";
  return ok;
}

}  // namespace identity
