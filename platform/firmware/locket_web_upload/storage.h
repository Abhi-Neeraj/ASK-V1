// The memory card, shared by the card writer, the uploader and (optionally) the dashboard.
//
// One lock guards every use of the card, so tasks never interleave their reads and writes on the one SPI bus.
// The card writer holds the lock only for the length of one block's write; readers (the uploader) take it for
// one small slice at a time, so the writer is never kept waiting long and its ring of buffers rides out the rest.
//
// Recovery can unmount and mount the card again. Anything still holding an open file from before would then be
// using a dead handle, so readers go through Reader below, which steps aside when a remount is wanted and notices
// when one happened.

#pragma once

#include <Arduino.h>
#include <FS.h>

#include "config.h"
#include "core.h"

namespace storage {

class Lock {
 public:
  explicit Lock(uint32_t timeoutMs = kStorageLockMs);
  ~Lock();
  bool held() const { return held_; }
  Lock(const Lock&) = delete;
  Lock& operator=(const Lock&) = delete;

 private:
  bool held_;
};

// Creates the lock, mounts the card and makes the folders. False if there is no usable card. Safe to call again.
bool begin();
bool ready();
// Counts every unmount, so a holder of an old open file can tell it is dead.
uint32_t generation();

// Unmounts and mounts again after the other users of the card have stepped aside (or a few seconds have passed).
// `settleMs` is how long to leave the card unpowered-in-spirit between the two. Do not call with the Lock held.
bool remountExclusive(uint32_t settleMs);

// Free bytes as last measured. Measuring walks the card's allocation table and can take seconds, so refreshFree()
// is never called from the capture task or while recording.
uint64_t freeBytes();
void refreshFree();

// Queue folders. Names are only ever ones that ask::clip_name_parse accepts.
String path(const char* dir, const char* name);
// The oldest part in `dir` (names sort in the order they were recorded). False if there is none.
bool oldest(const char* dir, char* name, size_t capacity);
// Calls `show` once for every file in `dir` (its name and its size in bytes), holding the card lock throughout.
// For the `files` command. Not while recording.
void forEachFile(const char* dir, void (*show)(const char* name, uint32_t size));
bool moveFile(const char* fromDir, const char* name, const char* toDir);
bool renameFile(const char* dir, const char* from, const char* to);
// Removes the file and checks it is gone.
bool removeFile(const char* dir, const char* name);
bool exists(const char* dir, const char* name);
// Counts as last tallied (updated whenever the queue changes), cheap to read.
uint32_t pendingCount();
uint32_t rejectedCount();
uint32_t untimedCount();
// Files in /pending whose names this firmware does not recognise: kept, never sent (not counted in pendingCount()).
uint32_t unreadableCount();
void recount();

// After a power cut a part can be left half written in /recording. Gives each a correct header for the audio
// that is in it and queues it. Run once at start-up, before recording.
void recoverInterrupted();

// A small text file read whole (the certificate authority). Empty if missing or larger than `maxBytes`.
String readSmallFile(const char* path, size_t maxBytes);

// Reads one file in slices, for the uploader. Gives up (aborted() becomes true) as soon as the card was
// remounted or a remount is wanted, so the caller can retry from the start.
class Reader {
 public:
  ~Reader() { close(); }
  bool open(const char* dir, const char* name);
  size_t read(uint8_t* buffer, size_t length);
  void close();
  uint64_t size() const { return size_; }
  uint64_t position() const { return position_; }
  bool aborted() const { return aborted_; }

 private:
  File file_;
  bool open_ = false;
  bool aborted_ = false;
  uint32_t generation_ = 0;
  uint64_t size_ = 0;
  uint64_t position_ = 0;
};

}  // namespace storage
