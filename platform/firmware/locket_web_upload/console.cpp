#include "console.h"

#include <Arduino.h>

#include "config.h"
#include "core.h"
#include "wifi_manager.h"
#include "recorder.h"
#include "sdwriter.h"
#include "settings.h"
#include "status.h"
#include "storage.h"
#include "uploader.h"

namespace console {

static String line;
static bool liveLines = true;
static const uint32_t kLiveLineEveryMs = 10000;

void printHelp() {
  Serial.println(F("Commands:"));
  Serial.println(F("  status"));
  Serial.println(F("  wifi <name> <password>     (quote a name with spaces; replaces the built-in network)"));
  Serial.println(F("  wifi default               (go back to the built-in network)"));
  Serial.println(F("  server <https://address>"));
  Serial.println(F("  pair <CODE>                (from the dashboard: Devices, Pair a locket)"));
  Serial.println(F("  unpair                     (forget the credential; recordings on the card are kept)"));
  Serial.println(F("  record                     (the same as one press of the button: starts, then stops)"));
  Serial.println(F("  button                     (shows whether the button reads up or down: for checking the wiring)"));
  Serial.println(F("  retry                      (try uploading again now)"));
  Serial.println(F("  live [on|off]              (the status line printed every 10 seconds)"));
  Serial.println(F("  files                      (lists the recordings on the card, and which ones can be sent)"));
  Serial.println(F("  ap [on|off|password <12-63 characters>]   (the local maintenance Wi-Fi; on/off restart the locket)"));
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
  char error[96];
  status::error(error, sizeof(error));
  const recorder::Mic mic = recorder::microphone();
  Serial.printf("firmware      %s\n", ASK_FIRMWARE_VERSION);
  Serial.printf("state         %s\n", status::stateName(status::state()));
  if (error[0]) Serial.printf("last error    %s\n", error);
  Serial.printf("paired        %s%s\n", s.paired ? "yes" : "no", s.needsPairing ? " (the server refused the credential: pair again)" : "");
  Serial.printf("microphone    %s\n", mic == recorder::Mic::Ok ? "ok" : (mic == recorder::Mic::Failed ? "FAILED" : "not tested"));
  Serial.printf("memory card   %s, %lu MB free\n", storage::ready() ? "ready" : "NOT READY",
                (unsigned long)(storage::freeBytes() / (1024 * 1024)));
  const wifi_manager::Status net = wifi_manager::status();
  if (net.state == wifi_manager::State::NoNetwork) {
    Serial.println(F("wifi          no network is set (type: wifi <name> <password>)"));
  } else if (net.connected) {
    Serial.printf("wifi          connected to %s (%s), IP %s, %d dBm\n", net.ssid,
                  net.builtIn ? "built-in network" : "network you typed", net.ip, net.rssi);
  } else {
    Serial.printf("wifi          NOT connected to %s (%s)%s%s\n", net.ssid,
                  net.builtIn ? "built-in network" : "network you typed", net.lastReason[0] ? ": " : "", net.lastReason);
  }
  Serial.printf("server        %s%s\n", settings::serverUrl().length() ? settings::serverUrl().c_str() : "not set",
                s.serverReachable ? " (online)" : "");
  Serial.printf("clock         %s\n", s.clockSet ? "set" : "not set yet");
  Serial.printf("recordings    %lu started, %lu parts saved since start\n", (unsigned long)status::counters.sessions,
                (unsigned long)status::counters.partsSaved);
  Serial.printf("queue         %lu waiting to send, %lu uploaded since start, %lu set aside, %lu untimed, %lu unreadable\n",
                (unsigned long)storage::pendingCount(), (unsigned long)s.uploaded, (unsigned long)storage::rejectedCount(),
                (unsigned long)storage::untimedCount(), (unsigned long)storage::unreadableCount());
  Serial.printf("card writer   %lu retries, %lu remounts, queue high-water %lu/%lu\n",
                (unsigned long)status::counters.sdWriteRetries, (unsigned long)status::counters.sdRemounts,
                (unsigned long)status::counters.sdQueueHighWater, (unsigned long)kRingBlockCount);
  if (s.lastProblem[0]) Serial.printf("last problem  %s\n", s.lastProblem);
}

// One line per file on the card, for the `files` command. A name this firmware did not make cannot be sent: it is
// ignored on purpose, never uploaded under a guessed id (that is what a recording from the other sketch looks like).
static void showFile(const char* name, uint32_t size) {
  ask::ClipName clip;
  const bool known = ask::clip_name_parse(name, &clip);
  Serial.printf("      %s  %lu bytes%s\n", name, (unsigned long)size,
                !known ? "   <- NOT a name this firmware makes: it is ignored and never sent"
                : !clip.timed ? "   <- no start time yet" : "");
}

static void listFolder(const char* dir, const char* meaning) {
  storage::recount();
  const uint32_t count = strcmp(dir, kDirPending) == 0    ? storage::pendingCount()
                         : strcmp(dir, kDirUntimed) == 0  ? storage::untimedCount()
                         : strcmp(dir, kDirRejected) == 0 ? storage::rejectedCount()
                                                          : 0;
  if (strcmp(dir, kDirRecording) == 0) Serial.printf("  %s: (%s)\n", dir, meaning);
  else if (strcmp(dir, kDirPending) == 0)
    Serial.printf("  %s: %lu file(s) %s, and %lu it cannot send (names it does not recognise)\n", dir, (unsigned long)count,
                  meaning, (unsigned long)storage::unreadableCount());
  else Serial.printf("  %s: %lu file(s), %s\n", dir, (unsigned long)count, meaning);
  storage::forEachFile(dir, showFile);
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
      Serial.println(F("usage: wifi <name> <password>   or   wifi default"));
      return;
    }
    if (w[1] == "default" && n == 2) {
      settings::useBuiltInWifi();
      wifi_manager::reconnectNow();
      if (settings::wifiSsid().length() > 0) Serial.println(F("Back to the built-in network. Joining it now."));
      else Serial.println(F("Forgotten. There is no built-in network, so none is set."));
      return;
    }
    const char* why = ask::wifi_credentials_problem(w[1].c_str(), n >= 3 ? w[2].c_str() : "");
    if (why[0] != '\0') {
      Serial.printf("Not saved: %s\n", why);  // the password is never echoed, even here
      return;
    }
    settings::setWifi(w[1], n >= 3 ? w[2] : String(""));
    wifi_manager::reconnectNow();
    Serial.printf("Wi-Fi saved: joining %s now. It replaces the built-in network until you type: wifi default\n",
                  w[1].c_str());
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
    Serial.println(F("Forgotten. Recordings already on the card are kept and are sent after the next pairing."));
  } else if (w[0] == "record") {
    recorder::requestPress();  // exactly one press of the button: starts when ready, stops when recording
  } else if (w[0] == "button") {
    // For checking the wiring: a button pin reads HIGH when it is up (internal pull-up) and LOW when it is down. Either
    // the D1 button (GPIO2) or the board's own BOOT button (GPIO0, if enabled in config.h) counts.
    Serial.printf("buttons (D1 = GPIO%d, BOOT = GPIO%d): %s\n", (int)ASK_PIN_BUTTON, (int)ASK_PIN_BUTTON_ALT,
                  recorder::buttonIsDown() ? "one is DOWN (reads LOW)" : "both up (read HIGH)");
  } else if (w[0] == "files") {
    if (status::busyRecording()) {
      Serial.println(F("Not while recording: try again once it is saved."));
      return;
    }
    listFolder(kDirPending, "waiting to be sent");  // (the heading also counts the ones it cannot send)
    listFolder(kDirUntimed, "recorded before the clock was set: kept, never sent by themselves");
    listFolder(kDirRejected, "refused by the server or damaged: kept, not sent again");
    listFolder(kDirRecording, "being written, or cut short by a power loss");
  } else if (w[0] == "live") {
    if (n >= 2 && (w[1] == "on" || w[1] == "off")) {
      liveLines = w[1] == "on";
      Serial.println(liveLines ? F("Status line every 10 seconds: on.") : F("Status line every 10 seconds: off."));
    } else {
      Serial.printf("The status line is %s. Use: live on, or live off\n", liveLines ? "on" : "off");
    }
  } else if (w[0] == "retry") {
    uploader::retryNow();
    Serial.println(F("Trying again."));
  } else if (w[0] == "ap") {
    if (n == 1) {
      Serial.printf("maintenance Wi-Fi  %s\n", settings::apEnabled() ? "on" : "off");
      Serial.printf("  name      %s\n", settings::apSsid().c_str());
      Serial.printf("  password  %s\n", settings::apPassword().c_str());  // serial only: never on the page or the network
      Serial.println(F("  address   http://192.168.4.1/ once joined"));
    } else if (w[1] == "on" || w[1] == "off") {
      if (status::busyRecording()) {
        Serial.println(F("Not while recording: this restarts the locket."));
        return;
      }
      settings::setApEnabled(w[1] == "on");
      Serial.println(F("Saved. Restarting."));
      delay(200);
      ESP.restart();
    } else if (w[1] == "password" && n >= 3) {
      String why;
      if (settings::setApPassword(w[2], &why)) Serial.println(F("Password saved. It applies after the next restart."));
      else Serial.println("Not saved: " + why);
    } else {
      Serial.println(F("usage: ap [on|off|password <12-63 characters>]"));
    }
  } else if (w[0] == "reboot") {
    if (status::busyRecording()) {
      Serial.println(F("Not while recording: stop the recording first (it would be cut short)."));
      return;
    }
    Serial.println(F("Restarting."));
    delay(200);
    ESP.restart();
  } else {
    Serial.println(F("Unknown command. Type help."));
  }
}

void tick() {
  static uint32_t lastPrinted = 0;
  const uint32_t now = millis();
  if (!liveLines || now - lastPrinted < kLiveLineEveryMs) return;
  lastPrinted = now;

  const wifi_manager::Status net = wifi_manager::status();
  const uploader::Status up = uploader::status();

  char doing[48] = "";  // while recording: for how long, and which part
  if (status::state() == status::State::Recording) {
    snprintf(doing, sizeof(doing), " %lu s (part %lu)", (unsigned long)status::counters.sessionSeconds,
             (unsigned long)status::counters.sessionPart);
  }
  char wifi[72];
  if (net.state == wifi_manager::State::NoNetwork) snprintf(wifi, sizeof(wifi), "Wi-Fi: none set");
  else if (net.connected) snprintf(wifi, sizeof(wifi), "Wi-Fi: %s %d dBm", net.ssid, net.rssi);
  else snprintf(wifi, sizeof(wifi), "Wi-Fi: NOT connected (%s)", net.state == wifi_manager::State::Waiting ? "retrying" : "joining");
  const char* server = !up.paired        ? "not paired"
                       : up.needsPairing ? "REFUSED, pair again"
                       : up.serverReachable ? "online"
                                            : "not reachable";
  char unreadable[48] = "";  // only when there are some: recordings this firmware cannot send
  if (storage::unreadableCount() > 0) {
    snprintf(unreadable, sizeof(unreadable), " | UNREADABLE %lu (type files)", (unsigned long)storage::unreadableCount());
  }
  Serial.printf("[STATUS] %s%s | %s | server: %s | button: %s | to send %lu | untimed %lu%s | uploaded %lu | upload: %s\n",
                status::stateName(status::state()), doing, wifi, server, recorder::buttonIsDown() ? "DOWN" : "up",
                (unsigned long)storage::pendingCount(), (unsigned long)storage::untimedCount(), unreadable,
                (unsigned long)up.uploaded, up.uploading ? "SENDING a part now" : "idle");
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
