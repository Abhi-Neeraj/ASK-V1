// The local maintenance dashboard: a small page served over the locket's own Wi-Fi, for checking on a locket
// without the company website (state, card and microphone health, connection, upload queue, last error).
//
// It is NOT the company dashboard and shows no transcripts. It is off unless switched on (serial: ap on), it is
// reachable only on the locket's own network, protected by that network's password (chosen by the locket, shown
// on the USB serial connection only), and it never shows a credential, a password or a voice recording unless
// the firmware was built with ASK_DASHBOARD_AUDIO_DOWNLOAD.

#pragma once

#include <Arduino.h>

namespace dashboard {

// Starts the network and the page if the maintenance Wi-Fi is switched on; does nothing otherwise.
void begin();

}  // namespace dashboard
