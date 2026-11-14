"use client";

import { cn } from "@/lib/cn";
import { DEMO_MODE } from "@/lib/env";
import type { LiveStatus } from "@/lib/live/events";
import { useLiveStatus } from "@/lib/live/provider";

const LABELS: Record<LiveStatus, string> = {
  connecting: "Connecting",
  live: "Live",
  reconnecting: "Reconnecting, refreshing every few seconds",
  ended: "Signed out",
};

/**
 * Tells the person whether what they see is updating by itself. Text first, color second: a dot
 * alone would not be enough. In demo mode the feed is scripted, and the label says so.
 */
export function LiveStatusPill() {
  const status = useLiveStatus();
  const label = DEMO_MODE && status === "live" ? "Demo feed" : LABELS[status];
  const tone =
    status === "live" ? "text-cerulean-glow" : status === "reconnecting" ? "text-warning" : "text-fg-subtle";

  return (
    <span aria-live="polite" className={cn("caption inline-flex items-center gap-2 whitespace-nowrap", tone)}>
      <span
        aria-hidden="true"
        className={cn(
          "size-1.5 rounded-full bg-current",
          status === "live" && "motion-safe:animate-pulse",
          status === "reconnecting" && "motion-safe:animate-pulse",
        )}
      />
      {label}
    </span>
  );
}
