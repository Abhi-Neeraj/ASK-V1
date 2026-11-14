// The memory-card writer: the recording pipeline of the original recorder, kept as it was.
//
//   microphone -> small read buffer -> one of 32 PSRAM blocks (32 KB, about a second of audio each)
//                                   -> job queue -> writer task -> WAV part on the memory card
//
// The capture task never writes to the card. It only copies audio into a free block and queues it; the writer
// task drains the queue. If the card stalls, the blocks absorb about 32 seconds of audio before anything is lost.
// A zero-length write is retried, then the card is unmounted and mounted again and the file reopened where it
// left off. When a recording is longer than one part, the writer closes the part and opens the next without the
// capture task noticing.
//
// Each finished part is checked (header, format, size) and queued by moving it from /recording to /pending.

#pragma once

#include <Arduino.h>

#include "core.h"

namespace sdwriter {

struct Session {
  char ref[ask::kClientIdBytes];  // sent as X-Session-Ref: every part of one recording shares it
  bool timed;                     // was the clock set when the button was pressed?
  int64_t startEpoch;             // if timed: seconds since 1970 at the press
  uint32_t startUptimeSeconds;    // if not: seconds since the locket started at the press
  uint32_t partBlocks;            // whole ring blocks in each full part
};

// Reserves the PSRAM blocks and queues. Done once at start-up so a failed allocation is found early.
bool allocate();
bool allocated();

// Starts a recording: creates its first part on the card and starts the writer task. False (with the reason in
// lastError()) if the card cannot be written; nothing is left running then.
bool begin(const Session& session);

// Hands out a free block, or null if all 32 are waiting for the card.
uint8_t* acquire(int32_t* index);
// Gives back a block that was filled with `bytes` of audio. `endsPart` marks the last block of a part.
void submit(int32_t index, size_t bytes, bool endsPart);
// Gives back a block that was never filled.
void release(int32_t index);
// No more blocks are coming: write what is queued, close the last part and queue it.
void finish();
// True once the writer task has finished (successfully or not).
bool waitDone(uint32_t timeoutMs);

bool failed();
void lastError(char* out, size_t capacity);
uint64_t bytesWritten();  // audio bytes written to the card this recording
uint32_t queued();        // blocks waiting for the card right now
uint32_t partsSaved();    // parts finished and queued this recording

}  // namespace sdwriter
