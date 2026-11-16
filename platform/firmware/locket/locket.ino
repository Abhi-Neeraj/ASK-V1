// ASK voice locket firmware.
//
// What it does: records speech from the microphone into short clips on the memory card, and sends each clip to
// the ASK server over Wi-Fi, deleting a clip only once the server has confirmed it is stored.
//
// COMPILED with the real ESP32 toolchain, but NEVER RUN ON A DEVICE where this was written. The rules in core.cpp
// are also tested on a computer; everything that touches the board (this file and recorder, storage, uploader,
// settings, console) has never run and may need fixes on first use. See ../README.md for what has and has not
// been checked.

#include "config.h"
#include "console.h"
#include "recorder.h"
#include "settings.h"
#include "storage.h"
#include "uploader.h"

void setup() {
  Serial.begin(115200);
  delay(300);  // let a serial monitor attach so the first lines are not lost
  Serial.println();
  Serial.printf("ASK locket firmware %s\n", ASK_FIRMWARE_VERSION);

  settings::begin();

  if (!storage::begin()) {
    Serial.println(F("No usable memory card. Nothing can be recorded until one is inserted and the locket restarted."));
  } else {
    // A power loss can leave a clip half written. Finish it now, so what was recorded is not lost.
    storage::recoverInterrupted();
  }

  if (!recorder::begin()) Serial.println(F("The microphone did not start."));
  uploader::begin();

  if (ASK_INSECURE_TLS) Serial.println(F("WARNING: certificate checking is OFF (ASK_INSECURE_TLS). Development only."));
  if (ASK_ALLOW_PLAIN_HTTP) Serial.println(F("WARNING: plain http is allowed (ASK_ALLOW_PLAIN_HTTP). Development only."));
  if (!settings::isPaired()) Serial.println(F("Not paired. Type help, then wifi, server and pair."));
}

void loop() {
  console::poll();
  delay(20);
}
