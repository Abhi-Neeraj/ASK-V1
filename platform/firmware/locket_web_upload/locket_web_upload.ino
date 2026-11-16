// ASK voice locket: button-controlled recorder that uploads to the ASK server.
//
// Press the button once to start recording, again to stop. Audio goes microphone -> PSRAM ring -> writer task ->
// WAV files on the memory card. Each finished file is queued on the card and sent to the ASK server over HTTPS,
// which stores it and does the transcription and speaker work. The locket deletes a file only after the server's
// receipt for it has been checked, and keeps and retries everything else.
//
// The original recorder sent audio to speech-to-text, summary and chat-message services from the device. None of
// that is here: the device only records, stores and uploads.
//
// COMPILED with the real ESP32 toolchain, but NEVER RUN ON A DEVICE where this was written. The rules in core.cpp
// are tested on a computer, and the rest was run there against simulated hardware; everything that touches the
// board has never run and may need fixes on first use. See ../README.md and ../../docs/HARDWARE_ACCEPTANCE_TESTS.md.
//
// Board settings: XIAO_ESP32S3 with PSRAM set to "OPI PSRAM" (the audio ring lives in PSRAM).

#include "config.h"
#include "console.h"
#include "dashboard.h"
#include "wifi_manager.h"
#include "recorder.h"
#include "sdwriter.h"
#include "settings.h"
#include "status.h"
#include "storage.h"
#include "uploader.h"

void setup() {
  Serial.begin(115200);
  delay(1500);  // let a serial monitor attach so the first lines are not lost
  Serial.println();
  Serial.println(F("================================================"));
  Serial.printf("ASK voice locket firmware %s\n", ASK_FIRMWARE_VERSION);
  Serial.println(F("PRESS ONCE = START | PRESS AGAIN = STOP | recordings are uploaded to the ASK server"));
  Serial.println(F("================================================"));

  settings::begin();
  // Join Wi-Fi in the background, at once, with no serial command: the recording state machine never waits for it.
  wifi_manager::begin();
  Serial.printf("[PSRAM] Total: %lu  Free: %lu\n", (unsigned long)ESP.getPsramSize(), (unsigned long)ESP.getFreePsram());

  if (ESP.getPsramSize() == 0) {
    status::setError("PSRAM is not enabled: choose PSRAM: OPI PSRAM in the board settings");
  } else if (!sdwriter::allocate()) {
    status::setError("not enough memory for the audio buffers");
  }

  if (!storage::begin()) {
    status::setError("no usable memory card");
    Serial.println(F("Nothing can be recorded until a card is inserted and the locket restarted."));
  } else {
    // A power loss can leave a part half written. Finish it now, so what was recorded is not lost.
    storage::recoverInterrupted();
    Serial.printf("[SD] Ready. %lu recording(s) waiting to upload\n", (unsigned long)storage::pendingCount());
  }

  if (!recorder::begin()) status::setError("could not start the capture task");
  uploader::begin();
  dashboard::begin();

  if (ASK_INSECURE_TLS) Serial.println(F("WARNING: certificate checking is OFF (ASK_INSECURE_TLS). Development only."));
  if (ASK_ALLOW_PLAIN_HTTP) Serial.println(F("WARNING: plain http is allowed (ASK_ALLOW_PLAIN_HTTP). Development only."));
  if (!settings::isPaired()) Serial.println(F("Not paired. Type help, then server and pair (Wi-Fi joins by itself)."));

  // The machine starts in Booting, where a press does nothing. Nothing records on its own, and a button that is
  // already held down is not a press: it has to be released and pressed again.
  char problem[96];
  status::error(problem, sizeof(problem));
  status::send(problem[0] ? ask::RecEvent::BootFailed : ask::RecEvent::BootedOk);
  if (!problem[0]) Serial.println(F("[READY] Press the button to start recording"));
}

void loop() {
  console::poll();
  console::tick();  // the status line, every 10 seconds
  delay(20);
}
