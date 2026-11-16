// Web build: Chrome and Edge on a computer (Windows, for example) can talk to the locket through the computer's own
// Bluetooth (webBluetoothLink.ts). Browsers without Bluetooth (Safari, Firefox, every iPhone browser) get no link,
// and every Bluetooth button is hidden. The phone build uses getLocketLink.native.ts.
import type { LocketLink } from "@/domain/locket/link";
import { type WebBluetooth, WebBluetoothLocketLink } from "@/infrastructure/ble/webBluetoothLink";

type BrowserNavigator = Navigator & { bluetooth?: WebBluetooth; userActivation?: { isActive: boolean } };

let shared: LocketLink | null | undefined;

export function getLocketLink(): LocketLink | null {
  if (shared !== undefined) return shared;
  const nav = typeof navigator === "undefined" ? undefined : (navigator as BrowserNavigator);
  shared = nav?.bluetooth ? new WebBluetoothLocketLink(nav.bluetooth, () => nav.userActivation?.isActive ?? true) : null;
  return shared;
}
