import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LiveStatusPill } from "@/components/dashboard/live-status";
import { api } from "@/lib/api";
import { LiveUpdatesProvider } from "@/lib/live/provider";
import { renderWithClient } from "@/test/utils";

// Tests run in demo mode by default (see vitest.config.mts).
describe("LiveStatusPill in demo mode", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("calls the scripted feed a demo feed, never 'Live'", () => {
    vi.spyOn(api.live, "subscribe").mockImplementation(({ onStatus }) => {
      onStatus("live");
      return () => {};
    });
    renderWithClient(
      <LiveUpdatesProvider>
        <LiveStatusPill />
      </LiveUpdatesProvider>,
    );
    expect(screen.getByText("Demo feed")).toBeInTheDocument();
    expect(screen.queryByText("Live")).not.toBeInTheDocument();
  });

  it("still reports trouble honestly", () => {
    vi.spyOn(api.live, "subscribe").mockImplementation(({ onStatus }) => {
      onStatus("reconnecting");
      return () => {};
    });
    renderWithClient(
      <LiveUpdatesProvider>
        <LiveStatusPill />
      </LiveUpdatesProvider>,
    );
    expect(screen.getByText(/reconnecting/i)).toBeInTheDocument();
  });
});
