import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LiveStatusPill } from "@/components/dashboard/live-status";
import { api } from "@/lib/api";
import type { LiveStatus } from "@/lib/live/events";
import { LiveUpdatesProvider } from "@/lib/live/provider";
import { renderWithClient } from "@/test/utils";

// A real server's feed, not the scripted demo one: the pill must not call a real feed a demo.
vi.mock("@/lib/env", async (original) => ({ ...(await original<typeof import("@/lib/env")>()), DEMO_MODE: false }));

function pillIn(status: LiveStatus) {
  vi.spyOn(api.live, "subscribe").mockImplementation(({ onStatus }) => {
    onStatus(status);
    return () => {};
  });
  return renderWithClient(
    <LiveUpdatesProvider>
      <LiveStatusPill />
    </LiveUpdatesProvider>,
  );
}

describe("LiveStatusPill", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    ["connecting", "Connecting"],
    ["live", "Live"],
    ["reconnecting", "Reconnecting, refreshing every few seconds"],
    ["ended", "Signed out"],
  ] as const)("says it in words when the feed is %s", (status, words) => {
    pillIn(status);
    expect(screen.getByText(words)).toBeInTheDocument();
  });

  it("is announced politely rather than interrupting", () => {
    pillIn("reconnecting");
    expect(screen.getByText(/reconnecting/i).closest("[aria-live]")).toHaveAttribute("aria-live", "polite");
  });
});
