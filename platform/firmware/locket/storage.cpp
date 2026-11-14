#include "storage.h"

#include <FS.h>
#include <SD.h>
#include <SPI.h>

#include "config.h"
#include "core.h"

namespace storage {

static bool mounted = false;
static volatile uint64_t cachedFree = 0;

static void makeDir(const char* dir) {
  if (!SD.exists(dir)) SD.mkdir(dir);
}

bool begin() {
  mounted = SD.begin(ASK_PIN_SD_CS);
  if (!mounted) return false;
  makeDir(kDirRecording);
  makeDir(kDirPending);
  makeDir(kDirRejected);
  refreshFree();
  return true;
}

bool ready() { return mounted; }

String path(const char* dir, const String& name) { return String(dir) + "/" + name; }

String nameFor(const String& clientId, int64_t startedAt) {
  return clientId + "." + String((long long)startedAt) + ".wav";
}

bool parseName(const String& name, String* clientId, int64_t* startedAt) {
  const int firstDot = name.indexOf('.');
  const int secondDot = firstDot < 0 ? -1 : name.indexOf('.', firstDot + 1);
  if (firstDot < 1 || secondDot < 0 || !name.endsWith(".wav")) return false;
  const String id = name.substring(0, firstDot);
  const String when = name.substring(firstDot + 1, secondDot);
  if (!ask::client_id_valid(id.c_str()) || when.length() == 0 || when.length() > 12) return false;
  for (size_t i = 0; i < when.length(); i++) {
    if (when[i] < '0' || when[i] > '9') return false;
  }
  *clientId = id;
  *startedAt = (int64_t)when.toInt();
  return true;
}

uint64_t freeBytes() { return cachedFree; }

void refreshFree() {
  if (!mounted) {
    cachedFree = 0;
    return;
  }
  cachedFree = SD.totalBytes() - SD.usedBytes();
}

bool oldestPending(String* name) {
  File dir = SD.open(kDirPending);
  if (!dir) return false;
  String best;
  for (File entry = dir.openNextFile(); entry; entry = dir.openNextFile()) {
    if (!entry.isDirectory()) {
      String candidate = entry.name();
      String id;
      int64_t when;
      // A file whose name is not one of ours is ignored rather than uploaded under a guessed id.
      if (parseName(candidate, &id, &when) && (best.length() == 0 || candidate < best)) best = candidate;
    }
    entry.close();
  }
  dir.close();
  if (best.length() == 0) return false;
  *name = best;
  return true;
}

uint32_t countIn(const char* dir) {
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

bool moveFile(const char* fromDir, const String& name, const char* toDir) {
  return SD.rename(path(fromDir, name), path(toDir, name));
}

bool renameFile(const char* dir, const String& from, const String& to) {
  return SD.rename(path(dir, from), path(dir, to));
}

bool removeFile(const char* dir, const String& name) { return SD.remove(path(dir, name)); }

void recoverInterrupted() {
  File dir = SD.open(kDirRecording);
  if (!dir) return;
  // Collect names first: renaming while walking the folder can skip entries.
  String names[16];
  size_t found = 0;
  for (File entry = dir.openNextFile(); entry && found < 16; entry = dir.openNextFile()) {
    if (!entry.isDirectory()) names[found++] = entry.name();
    entry.close();
  }
  dir.close();

  for (size_t i = 0; i < found; i++) {
    const String full = path(kDirRecording, names[i]);
    String id;
    int64_t when;
    File f = SD.open(full, "r+");
    if (!f || !parseName(names[i], &id, &when)) {
      if (f) f.close();
      continue;  // not ours: leave it alone
    }
    const uint32_t audio = ask::wav_recoverable_data_bytes((uint64_t)f.size(), kChannels, kBitsPerSample);
    if (audio < kMinClipBytes) {
      f.close();
      SD.remove(full);
      continue;
    }
    // Write the header the clip should have had. A half-written last sample stays after the audio; the card
    // library cannot shorten a file and the server ignores bytes after the audio the header describes.
    uint8_t header[ask::kWavHeaderBytes];
    ask::wav_header(header, audio, kSampleRate, kChannels, kBitsPerSample);
    f.seek(0);
    f.write(header, sizeof(header));
    f.close();
    moveFile(kDirRecording, names[i], kDirPending);
  }
}

}  // namespace storage
