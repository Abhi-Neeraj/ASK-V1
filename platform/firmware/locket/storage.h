// The memory card: where clips live between being recorded and being acknowledged by the server.
//
//   /recording/<id>.<time>.wav   being written; a power loss can leave one half finished
//   /pending/<id>.<time>.wav     finished, waiting to upload; deleted only after the server confirms it
//   /rejected/<id>.<time>.wav    the server will never accept it; kept, not retried
//
// <id> is the clip id (see core.h) and <time> is the second recording started, in UTC. Both ride in the name so
// the card alone is enough to resume after any restart: there is no index to lose.

#pragma once

#include <Arduino.h>

namespace storage {

// Starts the card and makes the folders. False when there is no usable card.
bool begin();
bool ready();

// Finish clips left in /recording by a power loss: give them a correct header and move them to /pending, or
// delete them if there is less than the server's minimum.
void recoverInterrupted();

// Free bytes as last measured. Measuring walks the card's allocation table, which can take seconds on a big
// card, so only the uploader's task calls refreshFree(); the recorder just reads the last answer.
uint64_t freeBytes();
void refreshFree();

String path(const char* dir, const String& name);
String nameFor(const String& clientId, int64_t startedAt);
bool parseName(const String& name, String* clientId, int64_t* startedAt);

// The oldest clip waiting (names sort in the order clips were made). False when none.
bool oldestPending(String* name);
uint32_t countIn(const char* dir);
bool moveFile(const char* fromDir, const String& name, const char* toDir);
bool renameFile(const char* dir, const String& from, const String& to);
bool removeFile(const char* dir, const String& name);

}  // namespace storage
