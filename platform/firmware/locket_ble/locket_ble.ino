// ASK voice locket, Bluetooth version.
//
// Press the button once to start recording, again to stop. Audio goes microphone -> PSRAM ring -> writer task ->
// WAV parts on the memory card. Finished meetings are streamed to the paired ASK phone app over Bluetooth; the
// phone uploads them to the ASK server, which transcribes and summarises. Wi-Fi is used only to install a
// firmware update the phone asks for.
//
// Pairing: the phone shows this locket as its product ID; the person types the six-digit code the server emails
// them into the phone's Bluetooth pairing box. Manufacturing: `provision <productId> <secretHex>` on this console.
//
// NEVER RUN ON A DEVICE where this was written. The pure logic (ble_core.cpp, core.cpp) is tested on a computer;
// everything that touches the radio, the card, the microphone or flash has never run and may need fixes on first
// use. Board settings: XIAO_ESP32S3 with PSRAM set to "OPI PSRAM". Spec:
// docs/superpowers/specs/2026-11-14-ble-locket-cloud-pipeline-design.md
#include "ble_core.h"
#include "ble_link.h"
#include "config.h"
#include "console.h"
#include "identity.h"
#include "ota.h"
#include "recorder.h"
#include "sdwriter.h"
#include "sender.h"
#include "settings.h"
#include "status.h"
#include "storage.h"

// The core marks a new firmware good before setup() unless this says otherwise. Returning true leaves it pending
// until ota::confirmRunningImage() (after Bluetooth is up), so a build that crashes before that is rolled back.
bool verifyRollbackLater() { return true; }

void setup() {
  Serial.begin(115200);
  delay(1500);  // let a serial monitor attach so the first lines are not lost
  Serial.println();
  Serial.println(F("================================================"));
  Serial.printf("ASK voice locket (Bluetooth) firmware %s\n", ASK_FIRMWARE_VERSION);
  Serial.println(F("PRESS ONCE = START | PRESS AGAIN = STOP | meetings go to the ASK phone app over Bluetooth"));
  Serial.println(F("================================================"));

  identity::begin();
  settings::begin();
  if (ASK_ALLOW_PLAIN_HTTP) Serial.println(F("WARNING: plain http firmware downloads are allowed. Development only."));
  if (ASK_TEST_PASSKEY >= 0) {
    Serial.printf("WARNING: TEST BUILD: the Bluetooth pairing code is fixed (ASK_TEST_PASSKEY). "
                  "Build without -DASK_TEST_PASSKEY for a real locket.\n");
  }

  if (ESP.getPsramSize() == 0) {
    status::setError("PSRAM is not enabled: choose PSRAM: OPI PSRAM in the board settings");
  } else if (!sdwriter::allocate()) {
    status::setError("not enough memory for the audio buffers");
  }
  if (!storage::begin()) {
    status::setError("no usable memory card");
  } else {
    storage::recoverInterrupted();  // finish a part a power loss cut short
    Serial.printf("[SD] Ready. %lu part(s) waiting for the phone\n", (unsigned long)storage::pendingCount());
  }
  if (!recorder::begin()) status::setError("could not start the capture task");

  if (!identity::provisioned()) {
    Serial.println(F("Not provisioned: type provision <ASK-XXXXXX> <secret> (from the server's register-locket)."));
  } else if (!ble_link::begin(identity::productId())) {
    status::setError("Bluetooth did not start");
  } else {
    ble_link::setCommandHandler(ota::onCommandOrForward);
    sender::begin();
    ota::confirmRunningImage();  // a new firmware that got this far keeps itself (no roll-back)
  }

  char problem[96];
  status::error(problem, sizeof(problem));
  status::send(problem[0] ? ask::RecEvent::BootFailed : ask::RecEvent::BootedOk);
  if (!problem[0]) Serial.println(F("[READY] Press the button to start recording"));
}

void loop() {
  static uint32_t lastStatus = 0;
  console::poll();
  ble_link::tick();
  if (millis() - lastStatus >= kStatusEveryMs) {
    lastStatus = millis();
    char text[96];
    askble::status_text(text, sizeof(text), sender::state(), -1, sender::sessionId(), sender::lastSeq(),
                        ota::percent());
    ble_link::notifyStatus(text);
  }
  delay(20);
}
