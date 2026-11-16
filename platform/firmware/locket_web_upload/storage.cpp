#include "storage.h"

#include <SD.h>
#include <SPI.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

namespace storage {

static SemaphoreHandle_t mutex = nullptr;
static volatile bool mounted = false;
static volatile uint32_t mountCount = 0;
static volatile uint32_t activeReaders = 0;
static volatile bool remountWanted = false;
static volatile uint64_t cachedFree = 0;
static volatile uint32_t pending = 0;
static volatile uint32_t rejected = 0;
static volatile uint32_t untimed = 0;
static volatile uint32_t foreign = 0;  // in /pending, but not named the way this firmware names its recordings

Lock::Lock(uint32_t timeoutMs)
    : held_(mutex != nullptr && xSemaphoreTakeRecursive(mutex, pdMS_TO_TICKS(timeoutMs)) == pdTRUE) {}

Lock::~Lock() {
  if (held_) xSemaphoreGiveRecursive(mutex);
}

static void makeDir(const char* dir) {
  if (!SD.exists(dir)) SD.mkdir(dir);
}

// The caller holds the lock.
static bool mountCard() {
  SPI.begin(ASK_PIN_SD_SCK, ASK_PIN_SD_MISO, ASK_PIN_SD_MOSI, ASK_PIN_SD_CS);
  // Up to 8 files open at once: the writer's part, the uploader's, and directory scans.
  const bool ok = SD.begin(ASK_PIN_SD_CS, SPI, ASK_SD_SPI_FREQUENCY, "/sd", 8);
  if (ok) {
    makeDir(kDirRecording);
    makeDir(kDirPending);
    makeDir(kDirRejected);
    makeDir(kDirUntimed);
  }
  mounted = ok;
  return ok;
}

bool begin() {
  if (mutex == nullptr) mutex = xSemaphoreCreateRecursiveMutex();
  Lock lock;
  if (!lock.held()) return false;
  if (mounted) return true;
  mountCard();
  if (mounted) {
    recount();
    refreshFree();
  }
  return mounted;
}

bool ready() { return mounted; }
uint32_t generation() { return mountCount; }

bool remountExclusive(uint32_t settleMs) {
  remountWanted = true;  // readers notice at their next slice, close their files and step aside
  const uint32_t started = millis();
  while (activeReaders > 0 && millis() - started < kRemountWaitMs) vTaskDelay(pdMS_TO_TICKS(20));

  bool ok = false;
  {
    Lock lock;
    if (lock.held()) {
      mounted = false;
      mountCount = mountCount + 1;
      SD.end();
      SPI.end();
      vTaskDelay(pdMS_TO_TICKS(settleMs));
      ok = mountCard();
    }
  }
  remountWanted = false;
  return ok;
}

uint64_t freeBytes() { return cachedFree; }

void refreshFree() {
  Lock lock;
  if (!lock.held() || !mounted) {
    if (!mounted) cachedFree = 0;
    return;
  }
  cachedFree = SD.totalBytes() - SD.usedBytes();
}

String path(const char* dir, const char* name) { return String(dir) + "/" + name; }

bool oldest(const char* dir, char* name, size_t capacity) {
  Lock lock;
  if (!lock.held() || !mounted) return false;
  File folder = SD.open(dir);
  if (!folder) return false;
  char best[ask::kClipNameBytes] = "";
  for (File entry = folder.openNextFile(); entry; entry = folder.openNextFile()) {
    if (!entry.isDirectory()) {
      const char* candidate = entry.name();
      ask::ClipName parsed;
      // A file whose name is not one of ours is ignored rather than uploaded under a guessed id.
      if (ask::clip_name_parse(candidate, &parsed) && (best[0] == '\0' || strcmp(candidate, best) < 0)) {
        strncpy(best, candidate, sizeof(best) - 1);
        best[sizeof(best) - 1] = '\0';
      }
    }
    entry.close();
  }
  folder.close();
  if (best[0] == '\0' || strlen(best) >= capacity) return false;
  strcpy(name, best);
  return true;
}

void forEachFile(const char* dir, void (*show)(const char* name, uint32_t size)) {
  Lock lock;
  if (!lock.held() || !mounted) return;
  File folder = SD.open(dir);
  if (!folder) return;
  for (File entry = folder.openNextFile(); entry; entry = folder.openNextFile()) {
    if (!entry.isDirectory()) show(entry.name(), (uint32_t)entry.size());
    entry.close();
  }
  folder.close();
}

// /pending is counted in two: the files this firmware can send, and those it cannot (a name it did not make, such as a
// recording left by the other, continuous-clip sketch). The second kind is never sent, so it must not look like a queue.
static uint32_t countSendable(const char* dir, uint32_t* unreadable) {
  *unreadable = 0;
  File folder = SD.open(dir);
  if (!folder) return 0;
  uint32_t count = 0;
  for (File entry = folder.openNextFile(); entry; entry = folder.openNextFile()) {
    if (!entry.isDirectory()) {
      ask::ClipName parsed;
      if (ask::clip_name_parse(entry.name(), &parsed)) count++;
      else (*unreadable)++;
    }
    entry.close();
  }
  folder.close();
  return count;
}

static uint32_t countFiles(const char* dir) {
  File folder = SD.open(dir);
  if (!folder) return 0;
  uint32_t count = 0;
  for (File entry = folder.openNextFile(); entry; entry = folder.openNextFile()) {
    if (!entry.isDirectory()) count++;
    entry.close();
  }
  folder.close();
  return count;
}

void recount() {
  Lock lock;
  if (!lock.held() || !mounted) return;
  uint32_t unreadable = 0;
  pending = countSendable(kDirPending, &unreadable);
  foreign = unreadable;
  rejected = countFiles(kDirRejected);
  untimed = countFiles(kDirUntimed);
}

uint32_t pendingCount() { return pending; }
uint32_t rejectedCount() { return rejected; }
uint32_t untimedCount() { return untimed; }
uint32_t unreadableCount() { return foreign; }

bool moveFile(const char* fromDir, const char* name, const char* toDir) {
  Lock lock;
  if (!lock.held() || !mounted) return false;
  const bool ok = SD.rename(path(fromDir, name), path(toDir, name));
  recount();
  return ok;
}

bool renameFile(const char* dir, const char* from, const char* to) {
  Lock lock;
  if (!lock.held() || !mounted) return false;
  return SD.rename(path(dir, from), path(dir, to));
}

bool removeFile(const char* dir, const char* name) {
  Lock lock;
  if (!lock.held() || !mounted) return false;
  const String full = path(dir, name);
  if (SD.exists(full.c_str()) && !SD.remove(full)) return false;
  const bool gone = !SD.exists(full.c_str());
  recount();
  return gone;
}

bool exists(const char* dir, const char* name) {
  Lock lock;
  if (!lock.held() || !mounted) return false;
  return SD.exists(path(dir, name).c_str());
}

String readSmallFile(const char* filePath, size_t maxBytes) {
  Lock lock;
  if (!lock.held() || !mounted) return String();
  File f = SD.open(filePath, FILE_READ);
  if (!f) return String();
  String text;
  if (f.size() <= maxBytes) text = f.readString();
  f.close();
  return text;
}

void recoverInterrupted() {
  Lock lock;
  if (!lock.held() || !mounted) return;
  File folder = SD.open(kDirRecording);
  if (!folder) return;
  // Collect names first: renaming while walking the folder can skip entries.
  static char names[16][ask::kClipNameBytes];
  size_t found = 0;
  for (File entry = folder.openNextFile(); entry && found < 16; entry = folder.openNextFile()) {
    if (!entry.isDirectory()) {
      strncpy(names[found], entry.name(), ask::kClipNameBytes - 1);
      names[found][ask::kClipNameBytes - 1] = '\0';
      found++;
    }
    entry.close();
  }
  folder.close();

  for (size_t i = 0; i < found; i++) {
    ask::ClipName parsed;
    if (!ask::clip_name_parse(names[i], &parsed)) continue;  // not ours: leave it alone
    const String full = path(kDirRecording, names[i]);
    File f = SD.open(full, "r+");
    if (!f) continue;
    const uint32_t audio = ask::wav_recoverable_data_bytes((uint64_t)f.size(), kChannels, kBitsPerSample);
    if (audio < kMinClipBytes) {
      f.close();
      SD.remove(full);  // under half a second of audio: the server would only refuse it
      Serial.printf("[RECOVER] Discarded a fragment of less than half a second (%s)\n", names[i]);
      continue;
    }
    // Write the header the part should have had. A half-written last sample stays after the audio; the card
    // library cannot shorten a file and the server ignores bytes after the audio the header describes.
    uint8_t header[ask::kWavHeaderBytes];
    ask::wav_header(header, audio, kSampleRate, kChannels, kBitsPerSample);
    f.seek(0);
    f.write(header, sizeof(header));
    f.close();
    if (SD.rename(full, path(kDirPending, names[i]))) {
      Serial.printf("[RECOVER] Saved an interrupted recording (%lu bytes of audio): %s\n", (unsigned long)audio,
                    names[i]);
    }
  }
  recount();
}

// ---- Reader ------------------------------------------------------------------------------------------------

bool Reader::open(const char* dir, const char* name) {
  Lock lock;
  if (!lock.held() || !mounted || remountWanted) return false;
  file_ = SD.open(path(dir, name), FILE_READ);
  if (!file_) return false;
  generation_ = mountCount;
  size_ = file_.size();
  position_ = 0;
  aborted_ = false;
  activeReaders = activeReaders + 1;
  open_ = true;
  return true;
}

size_t Reader::read(uint8_t* buffer, size_t length) {
  if (!open_ || aborted_) return 0;
  Lock lock;
  if (!lock.held() || !mounted || remountWanted || generation_ != mountCount) {
    aborted_ = true;
    return 0;
  }
  const uint64_t left = size_ - position_;
  if (length > left) length = (size_t)left;
  if (length == 0) return 0;
  const size_t got = file_.read(buffer, length);
  if (got == 0) aborted_ = true;
  position_ += got;
  return got;
}

void Reader::close() {
  if (!open_) return;
  open_ = false;
  {
    Lock lock;
    // After a remount the old handle belongs to a card that was unmounted: closing it would touch freed memory.
    if (lock.held() && mounted && generation_ == mountCount) file_.close();
  }
  if (activeReaders > 0) activeReaders = activeReaders - 1;
}

}  // namespace storage
