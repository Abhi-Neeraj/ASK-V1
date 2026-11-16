// What the locket is doing, shared between its tasks.
//
// The capture task, the card writer, the uploader and the dashboard all run at once. Text is kept in fixed
// buffers and copied under a lock, so a reader never sees a string that is half rewritten; counters are single
// words that one task writes and any task may read.

#pragma once

#include <Arduino.h>

#include "core.h"

namespace status {

// The recording state machine (the rules are ask::rec_step in core.cpp, and are tested there):
//   Booting    start-up; presses are ignored
//   Idle       ready: press the button
//   Recording  capturing audio
//   Finalizing stopped; the last audio is being written and the part checked; presses are ignored
//   Error      the last recording could not start or ended badly (the reason is in error()); press to try again
using State = ask::RecState;
const char* stateName(State s);   // for the maintenance page and `status`: "READY - PRESS BUTTON"
const char* stateToken(State s);  // for the log: "IDLE", "RECORDING", ...

State state();
// The ONLY way the state changes. Applies the event under a lock, logs "[STATE] RECORDING" and the like when the
// state changes, and returns what a Press should do (Ignore for every other event, and for a Press that
// does not apply). Safe to call from any task.
ask::PressAction send(ask::RecEvent event);
// True while audio is being captured or the card is still catching up with it.
bool busyRecording();

// A short line of text shared between tasks.
class Text {
 public:
  void set(const char* text);
  void get(char* out, size_t capacity) const;
  bool empty() const;

 private:
  char buffer_[96] = "";
};

// The reason the last recording ended badly. Cleared when the next one starts.
void setError(const char* text);
void clearError();
void error(char* out, size_t capacity);

struct Counters {
  volatile uint32_t sessions;          // recordings started since the locket started
  volatile uint32_t partsSaved;        // parts finished and queued since the locket started
  volatile uint32_t tailsDropped;      // endings too short for the server to take, not kept
  volatile uint32_t micPeak;           // loudest sample in the current recording
  volatile uint32_t sessionSeconds;    // length of the current recording so far
  volatile uint32_t sessionPart;       // the part being written
  volatile uint32_t sdWriteRetries;    // times the card returned zero from a write
  volatile uint32_t sdRemounts;        // times the card was unmounted and mounted again
  volatile uint32_t sdQueueHighWater;  // most blocks that ever waited for the card
  volatile uint32_t bytesLost;         // audio the card lost across a remount (should stay 0)
  volatile uint32_t transitions;       // state changes since start-up
  volatile uint32_t pressesIgnored;    // presses that meant nothing in the state they arrived in
};
extern Counters counters;

}  // namespace status
