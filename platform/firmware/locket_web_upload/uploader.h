// Sends saved recordings to the ASK server, and tells the server the locket is alive.
//
// Runs as its own task, so the network can be slow, down or hung without the microphone noticing. A part is
// deleted from the card in exactly one place: after the server's receipt for it has been checked (core.cpp,
// ack_validate). Anything else keeps the file.

#pragma once

#include <Arduino.h>

namespace uploader {

// Starts the uploader task.
void begin();

// Redeems a one-time pairing code (from the dashboard: Devices, Pair a locket) for this locket's credential.
bool pair(const String& code, String* message);

// True once the server has refused the stored credential. Recording of new sessions pauses until the locket is
// paired again; nothing already on the card is touched.
bool needsPairing();

// Skip the current wait and try again now.
void retryNow();

struct Status {
  bool wifiConnected;
  bool clockSet;
  bool paired;
  bool needsPairing;
  bool serverReachable;           // the last heartbeat was accepted
  bool uploading;                 // a part is being sent right now (independent of the recording state)
  uint32_t uploaded;              // parts the server has confirmed since the locket started
  int32_t lastUploadAgoSeconds;   // -1 if none yet
  int32_t lastHeartbeatAgoSeconds;
  int rssi;
  char lastProblem[96];
  char lastRecordingId[40];       // the server's id for the last part it confirmed
};
Status status();

}  // namespace uploader
