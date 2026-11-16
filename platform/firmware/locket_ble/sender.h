// Sends finished meetings from the card to the paired phone over Bluetooth (spec 5.3).
//
// One Bluetooth session per meeting (the recorder's session reference, shortened to 16 characters). START is
// frame 0; AUDIO frames 1, 2, 3 ... carry 16 ms of IMA ADPCM audio each, numbered straight across the meeting's
// part files; END follows the last frame once the meeting is no longer being recorded. The phone's ACK covering
// END deletes the meeting from the card; RESEND moves back to the frame it names.
#pragma once

#include <Arduino.h>

#include "ble_core.h"

namespace sender {

bool begin();
void wake();  // a part was just finished, or the phone (re)connected: look at the card now
void onCommand(const askble::Command& c);  // ACK / RESEND / TIME from the phone, already parsed by the caller
const char* state();  // "idle", "recording", "sending", "updating" for STATUS
const char* sessionId();  // the meeting being sent, or "" if none
uint32_t lastSeq();       // the last frame sent

}  // namespace sender
