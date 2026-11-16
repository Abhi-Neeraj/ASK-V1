// Records from the microphone into clips on the card, one clip every `chunkSeconds`, for as long as the
// locket is paired, has a card with room, and knows the time. It runs as its own task so a slow network can
// never make it miss audio.

#pragma once

#include <Arduino.h>

namespace recorder {

// Starts the microphone and the recording task. False if the microphone could not be started.
bool begin();

// What it is doing now, in words, for the `status` command. Never contains a secret.
String state();
// Clips finished since the locket started.
uint32_t clipsWritten();

}  // namespace recorder
