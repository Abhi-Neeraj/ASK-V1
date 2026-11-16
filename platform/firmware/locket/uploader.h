// Everything that needs the network: joining Wi-Fi, setting the clock, pairing, heartbeats, and sending clips.
//
// The rule that matters: a clip is deleted from the card ONLY after the server says it is stored (201 or 200).
// Any other outcome keeps the file. What each answer means is decided by core.cpp, which is tested.

#pragma once

#include <Arduino.h>

namespace uploader {

// Starts the upload task.
void begin();

// Redeem a pairing code from the dashboard (Devices, Pair a locket). Blocks while it talks to the server.
// On success the locket stores its credential. `message` says what happened, never including the credential.
bool pair(const String& code, String* message);

struct Status {
  bool wifiConnected;
  bool clockSet;
  bool needsPairing;     // the server rejected this locket's credential (revoked, or the server was reset)
  uint32_t pending;      // clips waiting to be sent
  uint32_t rejected;     // clips the server will never accept, kept on the card
  uint32_t uploaded;     // clips acknowledged since the locket started
  String lastProblem;    // the most recent thing that went wrong, in words; empty if nothing has
};
Status status();

}  // namespace uploader
