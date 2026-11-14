// Setting the locket up over its USB cable. Everything is typed into a serial monitor at 115200 baud.
//
//   help                      list the commands
//   status                    what the locket is doing (never shows the Wi-Fi password or the credential)
//   wifi <name> <password>    join this network (a name with spaces: use wifi "My Network" password)
//   server <https://address>  where the ASK server is
//   pair <CODE>               redeem the code from the dashboard (Devices, Pair a locket)
//   unpair                    forget the credential (recordings on the card are kept)
//   reboot                    restart
//
// Physical access to the cable is the only protection these commands have, the same as holding the locket.

#pragma once

namespace console {

// Call often from loop(). Reads a line when one has been typed and runs it.
void poll();
void printHelp();

}  // namespace console
