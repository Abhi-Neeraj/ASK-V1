import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { demoApi } from "@/lib/demo/api";
import type { LiveEvent, LiveStatus } from "@/lib/live/events";
import { signInDemo } from "@/test/utils";

/** The demo API pretends the network takes a moment, which fake timers have to be told to skip. */
async function statusOfSecondLocket() {
  const pending = demoApi.devices.list();
  await vi.advanceTimersByTimeAsync(500);
  return (await pending).find((d) => d.id === "dev_2")?.status;
}

describe("the demo live feed", () => {
  beforeEach(() => {
    signInDemo();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("cancels the script when the subscription is closed", async () => {
    const events: LiveEvent[] = [];
    const stop = demoApi.live.subscribe({ onStatus: () => {}, onEvent: (e) => events.push(e) });
    stop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(events).toEqual([]);
    expect(await statusOfSecondLocket()).toBe("offline");
  });

  it("scripts one locket coming online a few seconds after the dashboard opens", async () => {
    const statuses: LiveStatus[] = [];
    const events: LiveEvent[] = [];
    const stop = demoApi.live.subscribe({ onStatus: (s) => statuses.push(s), onEvent: (e) => events.push(e) });

    expect(statuses).toEqual(["live"]);
    expect(events).toEqual([]);
    expect(await statusOfSecondLocket()).toBe("offline");

    await vi.advanceTimersByTimeAsync(8_000);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "device.online", data: { deviceId: "dev_2", name: "Locket K3M9" } });
    expect(await statusOfSecondLocket()).toBe("online");
    stop();
  });

  it("does not repeat itself on a later visit", async () => {
    // The previous test already brought the locket online, so a new subscription has nothing to script.
    const events: LiveEvent[] = [];
    const stop = demoApi.live.subscribe({ onStatus: () => {}, onEvent: (e) => events.push(e) });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(events).toEqual([]);
    stop();
  });
});
