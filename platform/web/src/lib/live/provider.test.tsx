import { act, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/api/hooks";
import type { LiveEvent, LiveHandlers, LiveEventType } from "@/lib/live/events";
import { FALLBACK_POLL_MS, LiveUpdatesProvider, REFRESH_DEBOUNCE_MS, useLiveStatus } from "@/lib/live/provider";
import { renderWithClient } from "@/test/utils";

function Status() {
  return <p>status: {useLiveStatus()}</p>;
}

const event = (type: LiveEventType): LiveEvent => ({ type, at: "2026-10-25T10:00:00Z", id: "1", data: {} });

/** Mounts the provider with a feed the test controls. */
function mount() {
  const feed: { handlers?: LiveHandlers } = {};
  const stop = vi.fn();
  vi.spyOn(api.live, "subscribe").mockImplementation((handlers) => {
    feed.handlers = handlers;
    return stop;
  });
  const view = renderWithClient(
    <LiveUpdatesProvider>
      <Status />
    </LiveUpdatesProvider>,
  );
  const invalidate = vi.spyOn(view.client, "invalidateQueries");
  const keys = () => invalidate.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey));
  return {
    ...view,
    stop,
    invalidate,
    keys,
    send: (type: LiveEventType) => act(() => feed.handlers!.onEvent(event(type))),
    status: (value: Parameters<LiveHandlers["onStatus"]>[0]) => act(() => feed.handlers!.onStatus(value)),
    wait: (ms: number) => act(() => vi.advanceTimersByTime(ms)),
  };
}

describe("LiveUpdatesProvider", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows the state of the feed to anything inside it", () => {
    const live = mount();
    expect(screen.getByText("status: connecting")).toBeInTheDocument();
    live.status("live");
    expect(screen.getByText("status: live")).toBeInTheDocument();
    live.status("reconnecting");
    expect(screen.getByText("status: reconnecting")).toBeInTheDocument();
  });

  it("refetches only what an event makes stale, and only after the burst settles", () => {
    const live = mount();
    live.send("device.online");
    expect(live.invalidate).not.toHaveBeenCalled(); // waits briefly in case more arrive
    live.wait(REFRESH_DEBOUNCE_MS);
    expect(live.keys()).toEqual(expect.arrayContaining([JSON.stringify(queryKeys.devices), JSON.stringify(queryKeys.summary)]));
    expect(live.keys()).not.toContain(JSON.stringify(["recordings"]));
    expect(live.keys()).not.toContain(JSON.stringify(queryKeys.me));
  });

  it("turns a burst of events into one refetch per kind of data", () => {
    const live = mount();
    for (let i = 0; i < 20; i++) {
      live.send("recording.uploaded");
      live.wait(50);
    }
    live.wait(REFRESH_DEBOUNCE_MS);
    const keys = live.keys();
    expect(keys.filter((key) => key === JSON.stringify(queryKeys.devices))).toHaveLength(1);
    expect(keys.filter((key) => key === JSON.stringify(queryKeys.summary))).toHaveLength(1);
    expect(keys.filter((key) => key === JSON.stringify(["recordings"]))).toHaveLength(1);
  });

  it("refetches the library and the open recording when uploads and processing change", () => {
    const live = mount();
    live.send("recording.state");
    live.wait(REFRESH_DEBOUNCE_MS);
    expect(live.keys()).toEqual(expect.arrayContaining([JSON.stringify(["recordings"]), JSON.stringify(["recording"])]));
  });

  it("checks who is signed in after a gap, so an ended session sends the person to sign in", () => {
    const live = mount();
    live.send("stream.resumed");
    live.wait(REFRESH_DEBOUNCE_MS);
    expect(live.keys()).toContain(JSON.stringify(queryKeys.me));

    live.invalidate.mockClear();
    live.send("session.ended");
    live.wait(REFRESH_DEBOUNCE_MS);
    expect(live.keys()).toEqual([JSON.stringify(queryKeys.me)]);
  });

  it("polls while the connection is down, and stops when it comes back", () => {
    const live = mount();
    live.status("reconnecting");
    live.wait(FALLBACK_POLL_MS - 1);
    expect(live.invalidate).not.toHaveBeenCalled();
    live.wait(1);
    expect(live.keys()).toEqual(
      expect.arrayContaining([
        JSON.stringify(queryKeys.devices),
        JSON.stringify(["recordings"]),
        JSON.stringify(queryKeys.summary),
        JSON.stringify(queryKeys.me),
      ]),
    );

    live.status("live");
    live.invalidate.mockClear();
    live.wait(FALLBACK_POLL_MS * 3);
    expect(live.invalidate).not.toHaveBeenCalled();
  });

  it("does not poll while healthy, nor before the first connection", () => {
    const live = mount();
    live.wait(FALLBACK_POLL_MS * 2);
    expect(live.invalidate).not.toHaveBeenCalled();
    live.status("live");
    live.wait(FALLBACK_POLL_MS * 2);
    expect(live.invalidate).not.toHaveBeenCalled();
  });

  it("stops following the feed, and drops pending refetches, when it unmounts", () => {
    const live = mount();
    live.send("device.online");
    live.unmount();
    expect(live.stop).toHaveBeenCalledTimes(1);
    live.wait(REFRESH_DEBOUNCE_MS * 3);
    expect(live.invalidate).not.toHaveBeenCalled();
  });
});
