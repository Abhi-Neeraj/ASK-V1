// Setup and diagnostics over the USB serial connection (115200 baud). Anyone holding the cable can use these
// commands: that is the same trust as holding the locket.

#pragma once

namespace console {

// Reads and runs any complete line that has arrived. Call from loop().
void poll();
// Prints one line every 10 seconds saying what the locket is doing now: recording or not, Wi-Fi, the server,
// how many recordings wait and whether one is being sent. Call from loop(). `live off` switches it off.
void tick();
void printHelp();

}  // namespace console
