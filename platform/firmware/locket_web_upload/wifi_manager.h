// The Wi-Fi connection: joins the network at start-up, by itself, and keeps it joined.
//
// It runs as its own task, so joining (which can take many seconds, or fail) never holds up the button, the
// microphone or the card. It needs no serial command and does not wait for the locket to be paired: pairing itself
// needs the network. The network comes from settings::wifiSsid() / wifiPassword() (the built-in one in config.h,
// unless one was typed with `wifi`). The password is never printed or logged.
//
// On a failure it says why, using the reason the chip reports, and tries again after a wait that grows to a minute
// and stays there. A connection that drops is noticed and rejoined the same way.

#pragma once

#include <Arduino.h>

namespace wifi_manager {

enum class State : uint8_t {
  NoNetwork,   // there is no network name to join (no built-in one, and none typed)
  Connecting,  // an attempt is in progress
  Connected,
  Waiting,     // the last attempt failed; the next one is a few seconds away
};

struct Status {
  State state;
  bool connected;
  bool builtIn;               // the network in use is the one from config.h
  char ssid[33];
  char ip[16];                // "" unless connected
  int rssi;                   // dBm, 0 unless connected
  uint32_t attempts;          // tries since the last time it was connected
  uint32_t retryInSeconds;    // while Waiting
  unsigned lastReasonCode;    // the chip's reason for the last failure or drop; 0 if none
  char lastReason[96];        // that reason in words, "" if none
};

// Sets the radio up (station mode, no saved copy of the network in flash, no automatic reconnect of its own) and
// starts the task. Call once, early in setup().
void begin();

bool connected();
Status status();

// The network to join has changed (`wifi` was typed): drop the current connection and try the new one now.
void reconnectNow();

// Waits up to `timeoutMs` for the connection. Only for something that has to have the network now (pairing).
bool waitConnected(uint32_t timeoutMs);

}  // namespace wifi_manager
