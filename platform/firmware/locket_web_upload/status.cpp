#include "status.h"

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <string.h>

namespace status {

Counters counters = {};

static volatile State current = State::Booting;
static portMUX_TYPE guard = portMUX_INITIALIZER_UNLOCKED;
static Text errorText;

const char* stateName(State s) {
  switch (s) {
    case State::Booting: return "STARTING";
    case State::Idle: return "READY - PRESS BUTTON";
    case State::Recording: return "RECORDING";
    case State::Finalizing: return "FINALIZING";
    case State::Error: return "ERROR";
  }
  return "UNKNOWN";
}

const char* stateToken(State s) {
  switch (s) {
    case State::Booting: return "BOOTING";
    case State::Idle: return "IDLE";
    case State::Recording: return "RECORDING";
    case State::Finalizing: return "FINALIZING";
    case State::Error: return "ERROR";
  }
  return "UNKNOWN";
}

State state() { return current; }
bool busyRecording() { return current == State::Recording || current == State::Finalizing; }

ask::PressAction send(ask::RecEvent event) {
  State before, after;
  ask::PressAction action;
  portENTER_CRITICAL(&guard);
  before = current;
  const ask::RecStep step = ask::rec_step(before, event);
  current = step.next;
  after = step.next;
  action = step.action;
  portEXIT_CRITICAL(&guard);

  if (after != before) {
    counters.transitions = counters.transitions + 1;
    Serial.printf("[STATE] %s\n", stateToken(after));
  } else if (event == ask::RecEvent::Press && action == ask::PressAction::Ignore) {
    counters.pressesIgnored = counters.pressesIgnored + 1;
    Serial.printf("[BUTTON] press ignored while %s\n", stateToken(before));
  }
  return action;
}

void Text::set(const char* text) {
  portENTER_CRITICAL(&guard);
  strncpy(buffer_, text == nullptr ? "" : text, sizeof(buffer_) - 1);
  buffer_[sizeof(buffer_) - 1] = '\0';
  portEXIT_CRITICAL(&guard);
}

void Text::get(char* out, size_t capacity) const {
  if (capacity == 0) return;
  portENTER_CRITICAL(&guard);
  strncpy(out, buffer_, capacity - 1);
  out[capacity - 1] = '\0';
  portEXIT_CRITICAL(&guard);
}

bool Text::empty() const { return buffer_[0] == '\0'; }

void setError(const char* text) {
  errorText.set(text);
  Serial.print(F("[ERROR] "));
  Serial.println(text);
}

void clearError() { errorText.set(""); }
void error(char* out, size_t capacity) { errorText.get(out, capacity); }

}  // namespace status
