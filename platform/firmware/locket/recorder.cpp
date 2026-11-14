#include "recorder.h"

#include <ESP_I2S.h>
#include <SD.h>
#include <time.h>

#include "config.h"
#include "core.h"
#include "settings.h"
#include "storage.h"

namespace recorder {

static I2SClass microphone;
static volatile uint32_t written = 0;
// The pointer changes from one task and is read from another; the text it points at never changes.
static const char* volatile currentState = "starting";
static uint32_t sequence = 0;

String state() { return String(currentState); }
uint32_t clipsWritten() { return written; }

// Why recording cannot go ahead right now, or null if it can. The reasons are the things an owner can fix.
static const char* whyNot() {
  if (!settings::isPaired()) return "waiting to be paired";
  if (!storage::ready()) return "no memory card";
  if (!ask::clock_is_plausible((int64_t)time(nullptr))) return "waiting for the time";
  if (storage::freeBytes() < kMinFreeBytes) return "memory card is full";
  return nullptr;
}

// The microphone is quiet, so each gain step doubles the samples, clipping instead of wrapping around.
static void applyGain(int16_t* samples, size_t count) {
  if (kSoftwareGainShift == 0) return;
  for (size_t i = 0; i < count; i++) {
    int32_t v = (int32_t)samples[i] << kSoftwareGainShift;
    if (v > 32767) v = 32767;
    if (v < -32768) v = -32768;
    samples[i] = (int16_t)v;
  }
}

static void recordOneClip() {
  const int64_t startedAt = (int64_t)time(nullptr);
  char id[ask::kClientIdBytes];
  if (!ask::client_id_make(id, sizeof(id), settings::bootCount(), settings::bootSalt(), sequence++)) return;
  const String name = storage::nameFor(id, startedAt);
  const String path = storage::path(kDirRecording, name);

  File file = SD.open(path, FILE_WRITE);
  if (!file) {
    currentState = "cannot write to the memory card";
    vTaskDelay(pdMS_TO_TICKS(2000));
    return;
  }

  // A header that says "no audio yet", so that if the power goes the file is still recognisably a clip.
  uint8_t header[ask::kWavHeaderBytes];
  ask::wav_header(header, 0, kSampleRate, kChannels, kBitsPerSample);
  file.write(header, sizeof(header));

  const uint32_t bytesPerSecond = kSampleRate * kChannels * (kBitsPerSample / 8);
  const uint32_t target = settings::chunkSeconds() * bytesPerSecond;
  uint32_t audioBytes = 0;
  bool failed = false;
  uint32_t emptyReads = 0;
  static uint8_t buffer[kAudioBufferBytes];

  currentState = "recording";
  while (audioBytes < target) {
    const size_t got = microphone.readBytes((char*)buffer, sizeof(buffer));
    if (got == 0) {
      // Nothing arrived within the read timeout. A few are normal; many in a row mean the microphone is dead,
      // and spinning here would never notice or recover, so give up on this clip.
      if (++emptyReads >= 5) {
        failed = true;
        currentState = "microphone is not responding";
        break;
      }
      continue;
    }
    emptyReads = 0;
    applyGain((int16_t*)buffer, got / 2);
    if (file.write(buffer, got) != got) {
      failed = true;  // the card refused the write: stop rather than record into a broken file
      break;
    }
    audioBytes += (uint32_t)got;
    // Stop early if the card is about to fill, or the locket was unpaired, so a clip is always finished cleanly.
    if (!settings::isPaired() || storage::freeBytes() < kMinFreeBytes / 2) break;
  }

  // The header now says how much audio there really is.
  ask::wav_header(header, audioBytes, kSampleRate, kChannels, kBitsPerSample);
  file.seek(0);
  file.write(header, sizeof(header));
  file.close();

  if (failed || audioBytes < kMinClipBytes) {
    SD.remove(path);  // too short for the server to take, or damaged: nothing worth keeping
    if (failed && String(currentState) == "recording") currentState = "memory card write failed";
    if (failed) vTaskDelay(pdMS_TO_TICKS(2000));  // do not spin on a fault
    return;
  }
  if (storage::moveFile(kDirRecording, name, kDirPending)) written = written + 1;
}

static void task(void*) {
  for (;;) {
    const char* reason = whyNot();
    if (reason != nullptr) {
      currentState = reason;
      vTaskDelay(pdMS_TO_TICKS(1000));
      continue;
    }
    recordOneClip();
  }
}

bool begin() {
  microphone.setPinsPdmRx(ASK_PIN_PDM_CLOCK, ASK_PIN_PDM_DATA);
  if (!microphone.begin(I2S_MODE_PDM_RX, kSampleRate, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
    currentState = "microphone failed to start";
    return false;
  }
  xTaskCreatePinnedToCore(task, "recorder", kRecorderStackBytes, nullptr, 3, nullptr, 1);
  return true;
}

}  // namespace recorder
