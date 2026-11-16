#include "recorder.h"

#include <ESP_I2S.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <time.h>

#include "config.h"
#include "core.h"
#include "sdwriter.h"
#include "settings.h"
#include "status.h"
#include "storage.h"
#include "ota.h"
#include "uploader.h"

namespace recorder {

static I2SClass mic;
static volatile Mic micHealth = Mic::Unknown;
static volatile bool pressInjected = false;  // set by requestPress(); consumed by pressed()
static ask::Debounce button;
static uint8_t audio[kAudioBufferBytes];

Mic microphone() { return micHealth; }
void requestPress() { pressInjected = true; }

static bool micStart() {
  mic.setPinsPdmRx(ASK_PIN_PDM_CLOCK, ASK_PIN_PDM_DATA);
  return mic.begin(I2S_MODE_PDM_RX, kSampleRate, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
}

// GPIO2 (D1) has the internal pull-up on and the other leg of the button to GND: released reads HIGH, pressed LOW.
// Either button counts: the one wired to D1, and (unless switched off in config.h) the board's own BOOT button.
static bool buttonLevelHigh() {
  if (digitalRead(ASK_PIN_BUTTON) != HIGH) return false;
#if ASK_PIN_BUTTON_ALT >= 0
  if (digitalRead(ASK_PIN_BUTTON_ALT) != HIGH) return false;
#endif
  return true;
}
bool buttonIsDown() { return !buttonLevelHigh(); }

// Says out loud when the board sees a button go down or come up, whatever the recording logic then does with it. If
// pressing a button prints nothing here, the board is not seeing that button at all: a wiring problem, not software.
static void watchButtons() {
  struct Watch {
    int pin;
    const char* name;
    bool released;  // what was last reported
    uint8_t same;   // how many reads in a row have disagreed with that
  };
  static Watch watches[2] = {{ASK_PIN_BUTTON, "D1 button", true, 0}, {ASK_PIN_BUTTON_ALT, "BOOT button", true, 0}};
  for (Watch& w : watches) {
    if (w.pin < 0) continue;
    const bool high = digitalRead(w.pin) == HIGH;
    if (high == w.released) {
      w.same = 0;
      continue;
    }
    if (++w.same < 3) continue;  // three reads in a row (30 ms): contact bounce is not reported
    w.same = 0;
    w.released = high;
    Serial.printf("[BUTTON] %s (GPIO%d): %s\n", w.name, w.pin, high ? "released" : "DOWN");
  }
}

// True once per press. A press is the button settling LOW after having settled HIGH: not a bounce, not a release,
// and not the same press seen again while it is held. A press from the serial console counts the same.
static bool pressed() {
  if (pressInjected) {
    pressInjected = false;
    return true;
  }
  return ask::debounce_pressed(&button, buttonLevelHigh(), millis(), ASK_BUTTON_DEBOUNCE_MS);
}

// Check every precondition, doing the cheap remedies on the way (mounting a card that was not there at start-up,
// measuring free space that may be stale). Returns null if a recording can start, else the reason in words.
static const char* whyNot() {
  // locket_ble: never start while a firmware update is being installed (it ends in a restart).
  if (ota::running()) return "a firmware update is being installed; try again when it has finished";
  ask::StartChecks checks;
  checks.mic_ok = true;
  if (micHealth == Mic::Failed) {  // it failed before: see whether it works now, and leave it stopped
    checks.mic_ok = micStart();
    if (checks.mic_ok) mic.end();
  }
  checks.paired = settings::isPaired();
  checks.needs_pairing = uploader::needsPairing();
  checks.card_ready = storage::ready() || storage::begin();
  checks.ring_ready = sdwriter::allocated() || sdwriter::allocate();
  checks.free_bytes = storage::freeBytes();
  checks.min_free_bytes = kMinFreeBytes;
  if (checks.card_ready && checks.free_bytes < kMinFreeBytes) {
    storage::refreshFree();  // the stored figure may be old; measure once before refusing
    checks.free_bytes = storage::freeBytes();
  }
  checks.pending_parts = storage::pendingCount();
  checks.max_pending_parts = settings::maxPendingParts();
  const ask::Blocker blocker = ask::start_blocker(checks);
  return blocker == ask::Blocker::None ? nullptr : ask::blocker_text(blocker);
}

// Each gain step scales the samples, clipping instead of wrapping around, and tracks the loudest one.
static void processSamples(int16_t* samples, size_t count) {
  uint32_t peak = status::counters.micPeak;
  for (size_t i = 0; i < count; i++) {
    float v = (float)samples[i] * kAudioGain;
    if (v > 32767.0f) v = 32767.0f;
    if (v < -32768.0f) v = -32768.0f;
    const int16_t out = (int16_t)v;
    samples[i] = out;
    int32_t magnitude = out;
    if (magnitude < 0) magnitude = -magnitude;
    if ((uint32_t)magnitude > peak) peak = (uint32_t)magnitude;
  }
  status::counters.micPeak = peak;
}

// A start that could not go ahead. Nothing is recording, and the next press tries again.
static void refuseStart(const char* reason) {
  status::setError(reason);
  status::send(ask::RecEvent::StartRefused);
}

static void runSession() {
  status::clearError();

  const char* reason = whyNot();
  if (reason != nullptr) {
    refuseStart(reason);
    return;
  }

  // The time is taken at the press. If the clock is not set yet, uptime is recorded instead and the true time is
  // worked out later, once the network has set the clock (see uploader.cpp).
  sdwriter::Session session;
  memset(&session, 0, sizeof(session));
  ask::session_ref_make(session.ref, sizeof(session.ref), settings::bootCount(), settings::bootSalt(),
                        settings::nextSession());
  const int64_t now = (int64_t)time(nullptr);
  session.timed = ask::clock_is_plausible(now);
  session.startEpoch = session.timed ? now : 0;
  session.startUptimeSeconds = millis() / 1000;
  const uint32_t partSeconds = ask::segment_seconds_for(kDefaultSegmentSeconds, settings::maxClipSeconds());
  session.partBlocks =
      ask::segment_blocks(partSeconds, settings::maxUploadBytes(), kRingBlockBytes, kBytesPerSecond);

  // The button needs no waiting for: the press that started this is already consumed, the button counts as held
  // until it is released, and a release is never a press. Recording starts at once, even with the button held down.
  if (!sdwriter::begin(session)) {
    char why[96];
    sdwriter::lastError(why, sizeof(why));
    refuseStart(why[0] != '\0' ? why : "the memory card cannot be written");
    return;
  }
  if (!micStart()) {
    micHealth = Mic::Failed;
    sdwriter::finish();
    sdwriter::waitDone(10000);
    refuseStart("the microphone did not start");
    return;
  }
  micHealth = Mic::Ok;

  status::counters.sessions = status::counters.sessions + 1;
  status::counters.micPeak = 0;
  status::counters.sessionSeconds = 0;
  status::counters.sessionPart = 1;
  status::send(ask::RecEvent::Started);  // logs "[STATE] RECORDING": from here the locket really is recording
  Serial.printf("[RECORD] Press the button again to stop. Parts of up to %lu blocks (about %lu sec); the clock %s\n",
                (unsigned long)session.partBlocks, (unsigned long)(session.partBlocks * kRingBlockBytes / kBytesPerSecond),
                session.timed ? "is set" : "is NOT set yet (the start time will be worked out later)");

  const uint64_t freeAtStart = storage::freeBytes();
  const uint64_t maxBytes = (uint64_t)kMaxSessionSeconds * kBytesPerSecond;

  int32_t block = -1;
  uint8_t* blockData = nullptr;  // the ring block being filled, or null between blocks
  size_t fill = 0;
  uint32_t blocksInPart = 0;
  uint64_t captured = 0;
  uint32_t lastData = millis();
  uint32_t lastSecond = 0;
  const char* problem = nullptr;  // why the recording ended early, if it did

  while (true) {
    watchButtons();
    if (pressed() && status::send(ask::RecEvent::Press) == ask::PressAction::Stop) {
      Serial.println(F("[BUTTON] press: stop"));
      break;
    }
    if (sdwriter::failed()) {
      problem = "the memory card stopped taking writes";
      break;
    }
    if (captured >= maxBytes) {
      Serial.println(F("[SAFETY] Maximum recording length reached; stopping"));
      break;
    }

    size_t got = mic.readBytes((char*)audio, kAudioBufferBytes);
    if (got == 0) {
      if (millis() - lastData > kMicSilenceTimeoutMs) {
        micHealth = Mic::Failed;
        problem = "the microphone stopped sending audio";
        break;
      }
      vTaskDelay(1);
      continue;
    }
    lastData = millis();
    got &= ~(size_t)1;  // whole samples only
    if (got > maxBytes - captured) got = (size_t)(maxBytes - captured);
    if (got == 0) break;
    processSamples((int16_t*)audio, got / sizeof(int16_t));

    size_t offset = 0;
    while (offset < got) {
      if (blockData == nullptr) {
        blockData = sdwriter::acquire(&block);
        if (blockData == nullptr) {
          problem = "the memory card fell too far behind the microphone";
          break;
        }
      }
      size_t take = got - offset;
      if (take > kRingBlockBytes - fill) take = kRingBlockBytes - fill;
      memcpy(blockData + fill, audio + offset, take);
      fill += take;
      offset += take;
      if (fill == kRingBlockBytes) {
        blocksInPart++;
        const bool endsPart = blocksInPart >= session.partBlocks;
        sdwriter::submit(block, fill, endsPart);
        if (endsPart) blocksInPart = 0;
        blockData = nullptr;
        fill = 0;
      }
    }
    if (problem != nullptr) break;
    captured += got;

    const uint32_t seconds = (uint32_t)(captured / kBytesPerSecond);
    if (seconds != lastSecond) {
      lastSecond = seconds;
      status::counters.sessionSeconds = seconds;
      if (!ask::space_ok(freeAtStart, captured, kMinFreeBytes)) {
        problem = "the memory card is nearly full";
        break;
      }
      if (seconds % 10 == 0) {
        Serial.printf("[RECORD] %lu sec | part %lu | card queue %lu/%lu | remounts %lu\n", (unsigned long)seconds,
                      (unsigned long)status::counters.sessionPart, (unsigned long)sdwriter::queued(),
                      (unsigned long)kRingBlockCount, (unsigned long)status::counters.sdRemounts);
      }
    }
  }

  // Hand the last, partly filled block to the writer so no captured audio is left behind.
  if (blockData != nullptr) {
    if (fill > 0) sdwriter::submit(block, fill, false);
    else sdwriter::release(block);
  }
  mic.end();
  status::counters.sessionSeconds = (uint32_t)(captured / kBytesPerSecond);

  status::send(ask::RecEvent::CaptureEnded);  // -> FINALIZING: presses are ignored until the part is saved
  // Whole numbers only: this is the one line that used to print a decimal number, and a decimal format needs far
  // more stack than the rest of the log lines. Hundredths of a second, so the text is the same ("12.31").
  const unsigned long hundredths = (unsigned long)((captured * 100) / kBytesPerSecond);
  Serial.printf("[EVENT] RECORDING_STOPPED seconds=%lu.%02lu bytes=%lu\n", hundredths / 100, hundredths % 100,
                (unsigned long)captured);
  // How much of this task's stack was never used (bytes). If it ever nears zero, kCaptureStackBytes is too small.
  Serial.printf("[RECORD] capture task stack never used: %lu bytes of %lu\n",
                (unsigned long)uxTaskGetStackHighWaterMark(nullptr), (unsigned long)kCaptureStackBytes);
  Serial.println(F("[RECORD] Saving the last part to the card"));
  sdwriter::finish();

  // Wait for the card writer to finish, but keep watching the button, so that a press now is seen, logged and
  // thrown away here instead of being found later and mistaken for a new request. A note every 5 seconds says it
  // is still waiting, so a slow card shows as a slow card and not as a locket that has hung.
  const uint32_t finalizeStarted = millis();
  uint32_t lastNote = finalizeStarted;
  bool drained = false;
  while (!(drained = sdwriter::waitDone(10))) {
    watchButtons();
    if (pressed()) status::send(ask::RecEvent::Press);
    if (millis() - lastNote >= 5000) {
      lastNote = millis();
      Serial.printf("[RECORD] still saving to the card (%lu s so far)\n",
                    (unsigned long)((millis() - finalizeStarted) / 1000));
    }
    if (millis() - finalizeStarted > kWriterDrainTimeoutMs) break;
  }
  Serial.printf("[RECORD] the card %s after %lu ms\n", drained ? "finished" : "did NOT finish",
                (unsigned long)(millis() - finalizeStarted));
  pressInjected = false;

  // The first thing that went wrong is the one worth showing.
  char why[96] = "";
  if (problem != nullptr) {
    strncpy(why, problem, sizeof(why) - 1);
  } else if (!drained) {
    strncpy(why, "the memory card did not finish writing in time", sizeof(why) - 1);
  } else if (sdwriter::failed()) {
    sdwriter::lastError(why, sizeof(why));
  } else if (sdwriter::bytesWritten() != captured) {
    snprintf(why, sizeof(why), "audio byte count mismatch: wrote %lu of %lu", (unsigned long)sdwriter::bytesWritten(),
             (unsigned long)captured);
  }

  Serial.printf("[RECORD] %lu part(s) saved; %lu waiting to upload\n", (unsigned long)sdwriter::partsSaved(),
                (unsigned long)storage::pendingCount());
  uploader::retryNow();  // start uploading now rather than at the next scheduled look at the queue
  if (why[0] != '\0') {
    status::setError(why);
    status::send(ask::RecEvent::SavedWithProblem);
  } else {
    status::send(ask::RecEvent::Saved);  // -> IDLE
    Serial.println(F("[READY] Press the button to start a new recording"));
  }
}

static void task(void*) {
  // Whatever the button is doing now is its settled state. If it is held down at power-up, that is NOT a press:
  // it has to be released and pressed again.
  ask::debounce_begin(&button, buttonLevelHigh());
  if (!buttonLevelHigh()) Serial.println(F("[BUTTON] held down at power-up: ignored until it is released"));
  for (;;) {
    if (pressed()) {
      // Every press the locket notices is said out loud, so "nothing happened" can be told apart from "it did not
      // notice the button". (A press that is not noticed prints nothing: check the wiring with the `button` command.)
      const char* where = status::stateName(status::state());
      const ask::PressAction action = status::send(ask::RecEvent::Press);
      if (action == ask::PressAction::Start) {
        Serial.printf("[BUTTON] press noticed (%s): starting a recording\n", where);
        runSession();
      } else {
        Serial.printf("[BUTTON] press noticed (%s): ignored, it cannot start a recording now\n", where);
      }
    }
    watchButtons();
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

bool begin() {
  pinMode(ASK_PIN_BUTTON, INPUT_PULLUP);
#if ASK_PIN_BUTTON_ALT >= 0
  pinMode(ASK_PIN_BUTTON_ALT, INPUT_PULLUP);
#endif
  // Test the microphone once now, so a dead one shows on the dashboard before anyone presses the button.
  if (micStart()) {
    micHealth = Mic::Ok;
    mic.end();
  } else {
    micHealth = Mic::Failed;
  }
  return xTaskCreatePinnedToCore(task, "capture", kCaptureStackBytes, nullptr, 3, nullptr, 1) == pdPASS;
}

}  // namespace recorder
