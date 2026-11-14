#include "sdwriter.h"

#include <SD.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

#include "config.h"
#include "settings.h"
#include "status.h"
#include "storage.h"

namespace sdwriter {

struct Job {
  int32_t block;
  uint32_t bytes;
  bool endsPart;
};

static uint8_t* blocks[kRingBlockCount] = {nullptr};
static QueueHandle_t freeQueue = nullptr;
static QueueHandle_t jobQueue = nullptr;
static TaskHandle_t taskHandle = nullptr;

static Session session;
static volatile bool finishedFlag = false;  // the capture task will queue no more blocks
static volatile bool doneFlag = true;       // the writer task has ended (true when none is running)
static volatile bool openedFlag = false;    // the first part exists on the card
static volatile bool failedFlag = false;
static volatile uint64_t totalWritten = 0;
static volatile uint32_t saved = 0;
static status::Text errorText;

// The part being written. Only the writer task touches these (begin() waits for it to open the first part).
static File wav;
static bool partOpen = false;
static uint32_t partNumber = 0;
static uint64_t partBytes = 0;     // audio bytes written to this part
static uint64_t priorBytes = 0;    // audio bytes in the parts before it
static char partName[ask::kClipNameBytes] = "";

static void fail(const char* reason) {
  errorText.set(reason);
  failedFlag = true;
  Serial.print(F("[SD-WRITER] "));
  Serial.println(reason);
}

bool allocate() {
  if (allocated()) return true;
  if (freeQueue == nullptr) freeQueue = xQueueCreate(kRingBlockCount, sizeof(int32_t));
  if (jobQueue == nullptr) jobQueue = xQueueCreate(kRingBlockCount, sizeof(Job));
  if (freeQueue == nullptr || jobQueue == nullptr) return false;
  for (uint32_t i = 0; i < kRingBlockCount; i++) {
    if (blocks[i] != nullptr) continue;
    blocks[i] = (uint8_t*)ps_malloc(kRingBlockBytes);
    if (blocks[i] == nullptr) return false;
  }
  Serial.printf("[SD-RING] %lu blocks x %lu bytes (~%lu sec cushion). Free PSRAM: %lu\n", (unsigned long)kRingBlockCount,
                (unsigned long)kRingBlockBytes, (unsigned long)(kRingBlockCount * kRingBlockBytes / kBytesPerSecond),
                (unsigned long)ESP.getFreePsram());
  return true;
}

bool allocated() {
  for (uint32_t i = 0; i < kRingBlockCount; i++) {
    if (blocks[i] == nullptr) return false;
  }
  return freeQueue != nullptr && jobQueue != nullptr;
}

// ---- Parts -----------------------------------------------------------------------------------------------

// Creates the next part on the card with a header that says "no audio yet" and leaves it open for appending.
static bool openPart() {
  partNumber++;
  priorBytes += partBytes;
  partBytes = 0;
  status::counters.sessionPart = partNumber;

  ask::ClipName name;
  memset(&name, 0, sizeof(name));
  name.timed = session.timed;
  const uint64_t offsetSeconds = priorBytes / kBytesPerSecond;
  name.when = session.timed ? session.startEpoch + (int64_t)offsetSeconds
                            : (int64_t)session.startUptimeSeconds + (int64_t)offsetSeconds;
  name.part = partNumber;
  memcpy(name.session, session.ref, sizeof(name.session));

  storage::Lock lock;
  if (!lock.held() || !storage::ready()) {
    fail("the memory card is not available");
    return false;
  }
  // A new id for every part, and a check that the name is free: a recording is never written over another.
  for (int attempt = 0; attempt < 8; attempt++) {
    if (!ask::client_id_make(name.client_id, sizeof(name.client_id), settings::bootCount(), settings::bootSalt(),
                             settings::nextSequence()) ||
        !ask::clip_name_make(partName, sizeof(partName), name)) {
      fail("could not name the recording");
      return false;
    }
    if (!SD.exists(storage::path(kDirRecording, partName)) && !SD.exists(storage::path(kDirPending, partName))) break;
    partName[0] = '\0';
  }
  if (partName[0] == '\0') {
    fail("could not find an unused recording name");
    return false;
  }

  wav = SD.open(storage::path(kDirRecording, partName), FILE_WRITE);
  if (!wav) {
    fail("cannot create the recording file");
    return false;
  }
  uint8_t header[ask::kWavHeaderBytes];
  ask::wav_header(header, 0, kSampleRate, kChannels, kBitsPerSample);
  if (wav.write(header, sizeof(header)) != sizeof(header)) {
    wav.close();
    fail("cannot write the recording header");
    return false;
  }
  wav.flush();
  partOpen = true;
  Serial.printf("[SD-WRITER] Part %lu opened: %s\n", (unsigned long)partNumber, partName);
  return true;
}

// Writes the true header, closes the file, checks it, and queues it for upload.
static bool closePart() {
  if (!partOpen) return true;
  partOpen = false;
  bool ok = true;

  storage::Lock lock;
  if (!lock.held()) {
    fail("the memory card is busy; the recording was left to be recovered");
    return false;
  }
  uint8_t header[ask::kWavHeaderBytes];
  ask::wav_header(header, (uint32_t)partBytes, kSampleRate, kChannels, kBitsPerSample);
  if (!wav.seek(0) || wav.write(header, sizeof(header)) != sizeof(header)) {
    wav.close();
    fail("cannot write the final recording header; it will be recovered at the next start");
    return false;
  }
  wav.flush();
  wav.close();

  if (partBytes < kMinClipBytes) {
    // Under half a second: the server refuses it as too short. This can only be the tail of a long recording
    // (or a press-and-release), so it is logged and not kept.
    SD.remove(storage::path(kDirRecording, partName));
    status::counters.tailsDropped = status::counters.tailsDropped + 1;
    Serial.printf("[SD-WRITER] Part %lu was %lu bytes (under half a second); not kept\n", (unsigned long)partNumber,
                  (unsigned long)partBytes);
    return true;
  }

  // Read it back: the header must be a valid WAV of exactly the audio that was written.
  File check = SD.open(storage::path(kDirRecording, partName), FILE_READ);
  uint8_t back[ask::kWavHeaderBytes];
  uint32_t declared = 0;
  if (!check) {
    ok = false;
  } else {
    const uint64_t onCard = check.size();
    const size_t got = check.read(back, sizeof(back));
    check.close();
    ok = got == sizeof(back) &&
         ask::wav_check(back, onCard, kSampleRate, kChannels, kBitsPerSample, &declared) == ask::WavCheck::Ok &&
         declared == partBytes && onCard == ask::kWavHeaderBytes + partBytes;
  }
  if (!ok) {
    fail("the finished recording did not pass the check; it was left to be recovered");
    return false;
  }
  if (!storage::moveFile(kDirRecording, partName, kDirPending)) {
    fail("could not queue the finished recording; it will be recovered at the next start");
    return false;
  }
  saved = saved + 1;
  status::counters.partsSaved = status::counters.partsSaved + 1;
  // The part is closed, checked, and waiting in /pending: from here the uploader owns it.
  Serial.printf("[EVENT] UPLOAD_QUEUED part=%lu bytes=%lu file=%s\n", (unsigned long)partNumber, (unsigned long)partBytes,
                partName);
  return true;
}

// ---- Writing, with recovery ----------------------------------------------------------------------------------

// The card stopped taking writes. Unmount it, mount it again, reopen the part and carry on from where the file
// really ends. Audio still buffered in the card library when it failed may not have reached the card; that is
// counted, not hidden.
static bool remountAndReopen() {
  {
    storage::Lock lock;
    if (lock.held() && wav) {
      wav.flush();
      wav.close();
    }
  }
  for (int attempt = 1; attempt <= kRemountRetries; attempt++) {
    status::counters.sdRemounts = status::counters.sdRemounts + 1;
    Serial.printf("[SD-WRITER] Remount attempt %d / %d\n", attempt, kRemountRetries);
    if (!storage::remountExclusive(100u * (uint32_t)attempt)) {
      Serial.println(F("[SD-WRITER] Mounting failed during recovery"));
      continue;
    }
    storage::Lock lock;
    if (!lock.held()) continue;
    wav = SD.open(storage::path(kDirRecording, partName), "r+");
    if (!wav) {
      Serial.println(F("[SD-WRITER] Could not reopen the recording file"));
      continue;
    }
    const uint64_t onCard = wav.size() > ask::kWavHeaderBytes ? wav.size() - ask::kWavHeaderBytes : 0;
    if (onCard < partBytes) {
      const uint64_t lost = partBytes - onCard;
      status::counters.bytesLost = status::counters.bytesLost + (uint32_t)lost;
      totalWritten = totalWritten - lost;
      partBytes = onCard;
      Serial.printf("[SD-WRITER] WARNING: %lu bytes of audio did not reach the card before the failure\n",
                    (unsigned long)lost);
    }
    if (!wav.seek(ask::kWavHeaderBytes + partBytes)) {
      Serial.println(F("[SD-WRITER] Could not seek to the recovery point"));
      wav.close();
      continue;
    }
    Serial.printf("[SD-WRITER] Recovered at audio byte %lu of this part\n", (unsigned long)partBytes);
    return true;
  }
  return false;
}

static bool writeWithRecovery(const uint8_t* data, size_t length) {
  size_t offset = 0;
  int zeroWrites = 0;
  while (offset < length) {
    size_t n;
    {
      storage::Lock lock;
      n = lock.held() ? wav.write(data + offset, length - offset) : 0;
    }
    if (n > 0) {
      offset += n;
      partBytes += n;
      totalWritten = totalWritten + n;
      zeroWrites = 0;
      continue;
    }
    zeroWrites++;
    status::counters.sdWriteRetries = status::counters.sdWriteRetries + 1;
    Serial.printf("[SD-WRITER] write() returned 0. Local retry %d / %d\n", zeroWrites, kZeroWriteRetries);
    if (zeroWrites < kZeroWriteRetries) {
      vTaskDelay(pdMS_TO_TICKS(10u * (uint32_t)zeroWrites));
      continue;
    }
    Serial.println(F("[SD-WRITER] Local retries exhausted; remounting the card"));
    if (!remountAndReopen()) return false;
    zeroWrites = 0;
  }
  return true;
}

static void task(void*) {
  Serial.println(F("[SD-WRITER] Background writer started"));
  uint32_t blocksSinceFlush = 0;

  if (!openPart()) {
    doneFlag = true;
    taskHandle = nullptr;
    vTaskDelete(nullptr);
    return;
  }
  openedFlag = true;

  while (true) {
    Job job;
    if (xQueueReceive(jobQueue, &job, pdMS_TO_TICKS(200)) == pdTRUE) {
      if (!partOpen && !openPart()) {
        xQueueSend(freeQueue, &job.block, 0);
        break;
      }
      const bool wrote = writeWithRecovery(blocks[job.block], job.bytes);
      int32_t released = job.block;
      xQueueSend(freeQueue, &released, portMAX_DELAY);
      if (!wrote) {
        fail("the memory card stopped taking writes and could not be recovered");
        break;
      }
      if (++blocksSinceFlush >= kFlushEveryBlocks) {
        storage::Lock lock;
        if (lock.held()) wav.flush();
        blocksSinceFlush = 0;
      }
      if (job.endsPart && !closePart()) break;
    } else if (finishedFlag && uxQueueMessagesWaiting(jobQueue) == 0) {
      break;
    }
  }

  // Whatever happened, close the part we have: a failed recording still keeps the audio it managed to write.
  if (partOpen) {
    storage::Lock lock;
    if (lock.held() && wav) closePart();
  }

  Serial.printf("[SD-WRITER] Finished. Audio bytes written: %lu\n", (unsigned long)totalWritten);
  doneFlag = true;
  taskHandle = nullptr;
  vTaskDelete(nullptr);
}

// ---- Interface for the capture task -----------------------------------------------------------------------

bool begin(const Session& s) {
  if (!allocated() && !allocate()) {
    status::setError("not enough memory for the audio buffers");
    return false;
  }
  session = s;
  finishedFlag = false;
  doneFlag = false;
  openedFlag = false;
  failedFlag = false;
  totalWritten = 0;
  saved = 0;
  partOpen = false;
  partNumber = 0;
  partBytes = 0;
  priorBytes = 0;
  errorText.set("");
  status::counters.sdQueueHighWater = 0;

  xQueueReset(jobQueue);
  xQueueReset(freeQueue);
  for (int32_t i = 0; i < (int32_t)kRingBlockCount; i++) xQueueSend(freeQueue, &i, 0);

  if (xTaskCreatePinnedToCore(task, "SDWriter", kWriterStackBytes, nullptr, 2, &taskHandle, 0) != pdPASS) {
    doneFlag = true;
    errorText.set("could not start the card writer");
    return false;
  }
  // The first part is created by the writer; wait for it, so a card that cannot be written is found before the
  // microphone starts rather than after.
  const uint32_t started = millis();
  while (!openedFlag && !doneFlag && millis() - started < 10000) vTaskDelay(pdMS_TO_TICKS(10));
  if (!openedFlag) {
    finishedFlag = true;
    waitDone(5000);
    return false;
  }
  return true;
}

uint8_t* acquire(int32_t* index) {
  int32_t block;
  if (xQueueReceive(freeQueue, &block, 0) != pdTRUE) return nullptr;
  *index = block;
  return blocks[block];
}

void submit(int32_t index, size_t bytes, bool endsPart) {
  Job job;
  job.block = index;
  job.bytes = (uint32_t)bytes;
  job.endsPart = endsPart;
  if (xQueueSend(jobQueue, &job, 0) != pdTRUE) {
    // Cannot happen: the job queue is as long as the number of blocks. If it somehow does, do not lose the block.
    xQueueSend(freeQueue, &index, 0);
    fail("internal card queue unexpectedly full");
    return;
  }
  const uint32_t waiting = uxQueueMessagesWaiting(jobQueue);
  if (waiting > status::counters.sdQueueHighWater) status::counters.sdQueueHighWater = waiting;
}

void release(int32_t index) { xQueueSend(freeQueue, &index, 0); }

void finish() { finishedFlag = true; }

bool waitDone(uint32_t timeoutMs) {
  const uint32_t started = millis();
  while (!doneFlag) {
    if (millis() - started >= timeoutMs) return false;
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  return true;
}

bool failed() { return failedFlag; }
void lastError(char* out, size_t capacity) { errorText.get(out, capacity); }
uint64_t bytesWritten() { return totalWritten; }
uint32_t queued() { return jobQueue == nullptr ? 0 : (uint32_t)uxQueueMessagesWaiting(jobQueue); }
uint32_t partsSaved() { return saved; }

}  // namespace sdwriter
