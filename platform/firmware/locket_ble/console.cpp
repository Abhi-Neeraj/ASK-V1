#include "console.h"

#include <Arduino.h>

#include "ble_core.h"
#include "ble_link.h"
#include "config.h"
#include "identity.h"
#include "recorder.h"
#include "sender.h"
#include "settings.h"
#include "status.h"
#include "storage.h"

namespace console {

static String line;

void printHelp() {
  Serial.println(F("Commands:"));
  Serial.println(F("  status"));
  Serial.println(F("  provision ASK-XXXXXX <secret>   (once; paste the line the website shows when you register the locket)"));
  Serial.println(F("  unprovision ASK-XXXXXX     (type the CURRENT product ID: erases the ID, secret and every pairing, then restarts)"));
  Serial.println(F("  record                     (the same as one press of the button: starts, then stops)"));
  Serial.println(F("  wifi clear                 (forget the Wi-Fi network the phone gave for updates)"));
  Serial.println(F("  forget all                 (remove every phone paired with this locket)"));
  Serial.println(F("  reboot"));
}

static void printStatus() {
  const String id = identity::productId();
  Serial.printf("Product ID: %s\n", id.length() ? id.c_str() : "(not provisioned)");
  Serial.printf("Firmware:   %s\n", ASK_FIRMWARE_VERSION);
  Serial.printf("State:      %s\n", status::stateName(status::state()));
  Serial.printf("Sending:    %s %s frame %lu\n", sender::state(), sender::sessionId(), (unsigned long)sender::lastSeq());
  Serial.printf("Card:       %s, %lu part(s) waiting for the phone\n", storage::ready() ? "ready" : "not ready",
                (unsigned long)storage::pendingCount());
  Serial.printf("Wi-Fi for updates: %s\n", settings::wifiSsid().length() ? "set" : "not set");
}

// After the identity was erased: forget every phone, say what stays, and restart as a fresh locket.
static void finishReset() {
  ble_link::forgetAll();
  Serial.println(F("Product ID erased and every paired phone removed."));
  Serial.printf("The %lu recording part(s) on the card stay there and will go to whichever phone pairs next.\n",
                (unsigned long)storage::pendingCount());
  Serial.println(F("Also forget this locket in your phone's Bluetooth settings (the ASK-... device), or the phone's old "
                   "bond will make pairing with the new ID fail."));
  Serial.println(F("Restarting. Then type: provision ASK-XXXXXX <secret> with the NEW ID from the website."));
  Serial.flush();
  delay(300);
  ESP.restart();
}

// The serial reset code (config.h): the whole line equals it, nothing else on the line.
static bool isResetCode(const String& text) {
  const String code = String(ASK_SERIAL_RESET_CODE);
  return code.length() > 0 && text == code;
}

static void handle(const String& raw) {
  String text = raw;
  text.trim();
  if (text.length() == 0) return;

  if (isResetCode(text)) {
    String problem;
    if (!identity::provisioned()) {
      Serial.println(F("Already fresh: this locket has no product ID. Type provision ASK-XXXXXX <secret> with the "
                       "NEW ID from the website."));
    } else if (identity::erase(&problem)) {
      finishReset();
    } else {
      Serial.printf("Refused: %s\n", problem.c_str());
    }
    return;
  }
  const int space = text.indexOf(' ');
  const String verb = space < 0 ? text : text.substring(0, space);
  const String rest = space < 0 ? String("") : text.substring(space + 1);

  if (verb == "help") {
    printHelp();
  } else if (verb == "status") {
    printStatus();
  } else if (verb == "provision") {
    // Forgiving about spaces, <brackets>, quotes, capitals and a secret split by a line wrap; never echoes it.
    char id[11];
    char secret[65];
    const askble::ProvisionArgs args = askble::provision_args(rest.c_str(), id, secret);
    String problem;
    switch (args.problem) {
      case askble::ProvisionProblem::Missing:
        Serial.println(F("Usage: provision ASK-XXXXXX <secret>. Copy the whole line the website shows under Lockets, "
                         "Register a new locket (or the server's register-locket), and paste it here."));
        break;
      case askble::ProvisionProblem::BadId:
        Serial.println(F("Refused: the product ID must be ASK- and six characters, as the website showed it."));
        break;
      case askble::ProvisionProblem::SecretLength:
        Serial.printf("Refused: the secret has %u characters; it needs exactly 64. Copy it again in one piece.\n",
                      (unsigned)args.secret_length);
        break;
      case askble::ProvisionProblem::SecretNotHex:
        Serial.println(F("Refused: the secret can only contain 0-9 and a-f. Copy it again from the website."));
        break;
      case askble::ProvisionProblem::None:
        if (identity::provision(String(id), String(secret), &problem)) {
          Serial.printf("Provisioned as %s. Type reboot to start advertising it.\n", id);
        } else {
          Serial.printf("Refused: %s\n", problem.c_str());
        }
        break;
    }
    memset(secret, 0, sizeof(secret));
  } else if (verb == "unprovision") {
    // Only here, over the USB cable: nothing sent over Bluetooth reaches this console. It needs the current ID
    // typed out, so a stray line cannot wipe a locket.
    String problem;
    if (rest.length() == 0) {
      Serial.println(F("Usage: unprovision <this locket's current product ID> (status shows it). It erases the ID, the "
                       "secret and every paired phone, restarts, and lets you provision a NEW ID."));
    } else if (identity::unprovision(rest, &problem)) {
      finishReset();
    } else {
      Serial.printf("Refused: %s\n", problem.c_str());
    }
  } else if (verb == "record") {
    recorder::requestPress();
  } else if (verb == "wifi" && rest == "clear") {
    settings::clearWifi();
    Serial.println(F("Wi-Fi network forgotten."));
  } else if (verb == "forget" && rest == "all") {
    ble_link::forgetAll();
    Serial.println(F("Every paired phone was removed. Pair again from the ASK app."));
  } else if (verb == "reboot") {
    ESP.restart();
  } else {
    Serial.println(F("Unknown command. Type help."));
  }
}

void poll() {
  while (Serial.available() > 0) {
    const char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      handle(line);
      line = "";
    } else if (line.length() < 200) {
      line += c;
    }
  }
}

}  // namespace console
