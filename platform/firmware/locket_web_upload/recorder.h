// The capture task: owns the button and the microphone.
//
// One press of the button starts a recording, the next press stops it. While recording it does nothing but read the
// microphone and copy audio into the card writer's ring; it never touches the network or a web page, so nothing
// else can make it miss audio. What a press MEANS is decided in one place, the state machine in core.cpp
// (ask::rec_step, reached through status::send), so a press while the last part is being saved can neither start
// nor stop anything.

#pragma once

#include <Arduino.h>

namespace recorder {

enum class Mic : uint8_t { Unknown, Ok, Failed };

// Sets up the button, tests the microphone once and starts the capture task. False if the task cannot start.
bool begin();
Mic microphone();

// The button's level right now, straight from the pin (no debounce): true while it is held down. For checking the
// wiring from the serial console.
bool buttonIsDown();

// The same as one press of the button, for the serial console and for tests. It goes through the same debounce-free
// path as a real press and meets the same state machine: it starts a recording when ready, stops one when recording,
// and does nothing while the last part is being saved.
void requestPress();

}  // namespace recorder
