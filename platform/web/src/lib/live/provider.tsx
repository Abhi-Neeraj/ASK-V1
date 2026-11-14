"use client";

import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/api/hooks";
import { FALLBACK_GROUPS, refreshGroupsFor, type LiveStatus, type RefreshGroup } from "@/lib/live/events";

/** Events often arrive in bursts (a batch of uploads). Refetch once per burst, not once per event. */
export const REFRESH_DEBOUNCE_MS = 300;
/** While the live connection is down the screens still refresh, just more slowly. */
export const FALLBACK_POLL_MS = 15_000;

const LiveStatusContext = createContext<LiveStatus>("connecting");

/** Whether the screens are being updated live, or are catching up. */
export function useLiveStatus(): LiveStatus {
  return useContext(LiveStatusContext);
}

function refresh(client: QueryClient, groups: Iterable<RefreshGroup>) {
  for (const group of groups) {
    switch (group) {
      case "devices":
        void client.invalidateQueries({ queryKey: queryKeys.devices });
        break;
      case "recordings":
        void client.invalidateQueries({ queryKey: ["recordings"] });
        void client.invalidateQueries({ queryKey: ["recording"] });
        break;
      case "summary":
        void client.invalidateQueries({ queryKey: queryKeys.summary });
        break;
      case "processing":
        void client.invalidateQueries({ queryKey: queryKeys.processing });
        break;
      case "me":
        // A 401 here is how the shell learns the session ended, and sends the person to sign in.
        void client.invalidateQueries({ queryKey: queryKeys.me });
        break;
    }
  }
}

/**
 * Keeps what is on screen current. It follows the live feed and, for each event, refetches only the
 * data that event makes stale. If the connection drops it says so and polls until it returns.
 * Mount it only while someone is signed in.
 */
export function LiveUpdatesProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [status, setStatus] = useState<LiveStatus>("connecting");

  useEffect(() => {
    // The queue lives and dies with this subscription, so nothing refetches after unmount.
    const pending = new Set<RefreshGroup>();
    let timer: ReturnType<typeof setTimeout> | undefined;

    const queue = (groups: Iterable<RefreshGroup>) => {
      for (const group of groups) pending.add(group);
      clearTimeout(timer);
      timer = setTimeout(() => {
        const due = [...pending];
        pending.clear();
        refresh(client, due);
      }, REFRESH_DEBOUNCE_MS);
    };

    const stop = api.live.subscribe({
      onStatus: setStatus,
      onEvent: (event) => queue(refreshGroupsFor(event.type)),
    });
    return () => {
      stop();
      clearTimeout(timer);
      pending.clear();
    };
  }, [client]);

  useEffect(() => {
    if (status !== "reconnecting") return;
    const poll = setInterval(() => refresh(client, FALLBACK_GROUPS), FALLBACK_POLL_MS);
    return () => clearInterval(poll);
  }, [status, client]);

  return <LiveStatusContext.Provider value={status}>{children}</LiveStatusContext.Provider>;
}
