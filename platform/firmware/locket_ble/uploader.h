// The recording path (recorder.cpp, copied unchanged from locket_web_upload) talks to "the uploader". In this
// locket there is no upload to a server: finished parts go to the phone over Bluetooth, so these two calls are
// answered by the Bluetooth sender.
#pragma once

#include "sender.h"

namespace uploader {

// The server can never refuse this locket's credential (it has none): nothing blocks recording here.
inline bool needsPairing() { return false; }
// A part was just finished: send it now rather than at the sender's next look at the card.
inline void retryNow() { sender::wake(); }

}  // namespace uploader
