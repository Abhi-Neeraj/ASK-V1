#include "console.h"

#include <Arduino.h>

#include "config.h"
#include "recorder.h"
#include "settings.h"
#include "uploader.h"

namespace console {

static String line;

void printHelp() {
  Serial.println(F("Commands:"));
  Serial.println(F("  status"));
  Serial.println(F("  wifi <name> <password>     (quote a name with spaces)"));
  Serial.println(F("  server <https://address>"));
  Serial.println(F("  pair <CODE>                (from the dashboard: Devices, Pair a locket)"));
  Serial.println(F("  unpair"));
  Serial.println(F("  reboot"));
}

// Splits `text` into words, honouring double quotes. Returns how many words were found (at most `max`).
static size_t words(const String& text, String* out, size_t max) {
  size_t count = 0;
  size_t i = 0;
  while (i < text.length() && count < max) {
    while (i < text.length() && text[i] == ' ') i++;
    if (i >= text.length()) break;
    String word;
    if (text[i] == '"') {
      i++;
      while (i < text.length() && text[i] != '"') word += text[i++];
      i++;
    } else {
      while (i < text.length() && text[i] != ' ') word += text[i++];
    }
    out[count++] = word;
  }
  return count;
}

static void printStatus() {
  const uploader::Status s = uploader::status();
  Serial.printf("firmware      %s\n", ASK_FIRMWARE_VERSION);
  Serial.printf("paired        %s%s\n", settings::isPaired() ? "yes" : "no",
                s.needsPairing ? " (the server refused the credential: pair again)" : "");
  Serial.printf("wifi          %s (%s)\n", s.wifiConnected ? "connected" : "not connected",
                settings::wifiSsid().length() ? settings::wifiSsid().c_str() : "none set");
  Serial.printf("server        %s\n", settings::serverUrl().length() ? settings::serverUrl().c_str() : "not set");
  Serial.printf("clock         %s\n", s.clockSet ? "set" : "not set yet");
  Serial.printf("recording     %s\n", recorder::state().c_str());
  Serial.printf("clips         %lu recorded, %lu uploaded, %lu waiting, %lu set aside\n",
                (unsigned long)recorder::clipsWritten(), (unsigned long)s.uploaded, (unsigned long)s.pending,
                (unsigned long)s.rejected);
  if (s.lastProblem.length()) Serial.printf("last problem  %s\n", s.lastProblem.c_str());
}

static void run(const String& text) {
  String w[3];
  const size_t n = words(text, w, 3);
  if (n == 0) return;

  if (w[0] == "help") {
    printHelp();
  } else if (w[0] == "status") {
    printStatus();
  } else if (w[0] == "wifi") {
    if (n < 2) {
      Serial.println(F("usage: wifi <name> <password>"));
      return;
    }
    settings::setWifi(w[1], n >= 3 ? w[2] : String(""));
    Serial.println(F("Wi-Fi saved. It is used the next time the locket connects."));  // never echo the password
  } else if (w[0] == "server") {
    String why;
    if (n < 2) Serial.println(F("usage: server https://your-address"));
    else if (settings::setServerUrl(w[1], &why)) Serial.println(F("Server address saved."));
    else Serial.println("Not saved: " + why);
  } else if (w[0] == "pair") {
    if (n < 2) {
      Serial.println(F("usage: pair <CODE>"));
      return;
    }
    Serial.println(F("Pairing..."));
    String message;
    const bool ok = uploader::pair(w[1], &message);
    Serial.println((ok ? "OK: " : "Failed: ") + message);
  } else if (w[0] == "unpair") {
    settings::clearPairing();
    Serial.println(F("Forgotten. Clips already on the card are kept and are sent after the next pairing."));
  } else if (w[0] == "reboot") {
    Serial.println(F("Restarting."));
    delay(200);
    ESP.restart();
  } else {
    Serial.println(F("Unknown command. Type help."));
  }
}

void poll() {
  while (Serial.available() > 0) {
    const char c = (char)Serial.read();
    if (c == '\r' || c == '\n') {
      if (line.length() > 0) run(line);
      line = "";
    } else if (line.length() < 200) {
      line += c;
    }
  }
}

}  // namespace console
