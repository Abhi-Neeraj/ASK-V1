// Keep the screen on while it matters (a locket is connected and may be sending a meeting of hours). A phone that
// turns its screen off, or a browser that puts the page in the background, stops the page receiving Bluetooth data,
// and the transfer stalls. The browser's Screen Wake Lock is used on the website; it is released when the page is
// hidden, so it is asked for again when the page comes back. Where it is not available this does nothing.

import React from "react";
import { Platform } from "react-native";

type Sentinel = { release(): Promise<void> };
type WakeLockApi = { request(type: "screen"): Promise<Sentinel> };

export function useKeepAwake(active: boolean): void {
  React.useEffect(() => {
    if (!active || Platform.OS !== "web" || typeof navigator === "undefined" || typeof document === "undefined") return;
    const api = (navigator as unknown as { wakeLock?: WakeLockApi }).wakeLock;
    if (!api) return;
    let sentinel: Sentinel | null = null;
    let stopped = false;

    const acquire = async () => {
      if (stopped || sentinel) return;
      try {
        const lock = await api.request("screen");
        if (stopped) await lock.release().catch(() => undefined);
        else sentinel = lock;
      } catch {
        sentinel = null; // refused (a low battery, a hidden page): tried again when the page is shown
      }
    };
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      sentinel = null; // the browser released it when the page was hidden
      void acquire();
    };

    void acquire();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      document.removeEventListener("visibilitychange", onVisible);
      void sentinel?.release().catch(() => undefined);
    };
  }, [active]);
}
