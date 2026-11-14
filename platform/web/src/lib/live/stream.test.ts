import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveEvent, LiveStatus } from "@/lib/live/events";
import { openEventStream, type SourceLike } from "@/lib/live/stream";

/** Stands in for the browser's EventSource, and lets a test play the part of the network. */
class FakeSource implements SourceLike {
  static all: FakeSource[] = [];
  readyState = 0;
  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  private listeners = new Map<string, Array<(event: MessageEvent) => void>>();

  constructor(readonly url: string) {
    FakeSource.all.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }

  /** The connection is established. */
  open() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }

  /** The server sent a named event. */
  send(type: string, payload: unknown = { type, at: "2026-10-25T10:00:00Z", data: {} }, id = "") {
    const raw = typeof payload === "string" ? payload : JSON.stringify(payload);
    for (const listener of this.listeners.get(type) ?? []) {
      listener({ data: raw, lastEventId: id } as MessageEvent);
    }
  }

  /** The connection dropped and the browser is already trying again (it sets readyState to CONNECTING). */
  dropWhileBrowserRetries() {
    this.readyState = 0;
    this.onerror?.(new Event("error"));
  }

  /** The browser gave up: an error response, or a proxy that broke the stream. */
  failForGood() {
    this.readyState = 2;
    this.onerror?.(new Event("error"));
  }

  get latest() {
    return FakeSource.all.at(-1);
  }
}

function start(options: Parameters<typeof openEventStream>[1] = {}) {
  const statuses: LiveStatus[] = [];
  const events: LiveEvent[] = [];
  const stop = openEventStream(
    { onStatus: (s) => statuses.push(s), onEvent: (e) => events.push(e) },
    { createSource: (url) => new FakeSource(url), ...options },
  );
  return { statuses, events, stop, source: () => FakeSource.all.at(-1)! };
}

describe("openEventStream", () => {
  beforeEach(() => {
    FakeSource.all = [];
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("says connecting, then live once the connection opens", () => {
    const { statuses, source } = start();
    expect(statuses).toEqual(["connecting"]);
    expect(source().url).toBe("/api/events");
    source().open();
    expect(statuses).toEqual(["connecting", "live"]);
  });

  it("passes events on with their server id", () => {
    const { events, source } = start();
    source().open();
    source().send("device.online", { type: "device.online", at: "2026-10-25T10:00:00Z", data: { deviceId: "d1" } }, "17");
    expect(events).toEqual([
      { type: "device.online", at: "2026-10-25T10:00:00Z", id: "17", data: { deviceId: "d1" } },
    ]);
  });

  it("drops anything malformed instead of passing it on", () => {
    const { events, source } = start();
    source().open();
    source().send("device.online", "{ not json", "1");
    source().send("recording.uploaded", { at: 5 }, "2");
    expect(events).toEqual([]);
  });

  it("reports reconnecting while the browser retries, then refetches everything once it is back", () => {
    const { statuses, events, source } = start();
    source().open();
    source().send("device.online", undefined, "9");
    source().dropWhileBrowserRetries();
    expect(statuses.at(-1)).toBe("reconnecting");

    source().open(); // the same EventSource reconnects by itself and replays what was missed
    expect(statuses.at(-1)).toBe("live");
    expect(events.filter((e) => e.type === "stream.resumed")).toHaveLength(1);
    expect(FakeSource.all).toHaveLength(1); // no second connection was needed
  });

  it("does not announce a gap on the very first connection", () => {
    const { events, source } = start();
    source().open();
    expect(events.filter((e) => e.type === "stream.resumed")).toHaveLength(0);
  });

  it("opens a fresh connection with the last event id when the browser gives up, backing off each time", () => {
    const { statuses, source } = start();
    source().open();
    source().send("device.online", undefined, "42");

    source().failForGood();
    expect(statuses.at(-1)).toBe("reconnecting");
    expect(FakeSource.all[0]!.closed).toBe(true);
    expect(FakeSource.all).toHaveLength(1);

    vi.advanceTimersByTime(999);
    expect(FakeSource.all).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSource.all).toHaveLength(2);
    expect(source().url).toBe("/api/events?lastEventId=42");

    source().failForGood(); // second failure in a row waits twice as long
    vi.advanceTimersByTime(1_999);
    expect(FakeSource.all).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeSource.all).toHaveLength(3);
  });

  it("starts the backoff over after a successful connection", () => {
    const { source } = start();
    source().failForGood();
    vi.advanceTimersByTime(1_000);
    source().failForGood();
    vi.advanceTimersByTime(2_000);
    source().open();
    source().failForGood();
    vi.advanceTimersByTime(1_000);
    expect(FakeSource.all).toHaveLength(4); // 1s again, not 4s
  });

  it("caps the delay between attempts", () => {
    const { source } = start({ backoffMs: [100, 200] });
    for (let i = 0; i < 4; i++) {
      source().failForGood();
      vi.advanceTimersByTime(200);
    }
    expect(FakeSource.all).toHaveLength(5);
  });

  it("does not flash 'reconnecting' for a reconnect the server asked for, and does not refetch", () => {
    const { statuses, events, source } = start();
    source().open();
    source().send("stream.renew", "{}");
    source().dropWhileBrowserRetries();
    expect(statuses.at(-1)).toBe("live");

    source().open();
    expect(statuses.at(-1)).toBe("live");
    expect(events.filter((e) => e.type === "stream.resumed")).toHaveLength(0);
  });

  it("reconnects at once if a planned reconnect leaves the browser with a dead connection", () => {
    const { statuses, source } = start();
    source().open();
    source().send("stream.renew", "{}");
    source().failForGood();
    expect(statuses.at(-1)).toBe("live");
    vi.advanceTimersByTime(0);
    expect(FakeSource.all).toHaveLength(2);
  });

  it("stops for good when the session has ended", () => {
    const { statuses, events, source } = start();
    source().open();
    source().send("session.ended", "{}");

    expect(statuses.at(-1)).toBe("ended");
    expect(events.at(-1)?.type).toBe("session.ended");
    expect(source().closed).toBe(true);

    source().failForGood();
    vi.advanceTimersByTime(60_000);
    expect(FakeSource.all).toHaveLength(1);
  });

  it("closes the connection and cancels pending retries when stopped, and is safe to stop twice", () => {
    const { stop, source } = start();
    source().failForGood();
    stop();
    stop();
    vi.advanceTimersByTime(60_000);
    expect(FakeSource.all).toHaveLength(1);

    const second = start();
    second.source().open();
    second.stop();
    expect(second.source().closed).toBe(true);
  });

  it("ignores anything a connection it already left behind says", () => {
    const { events, source } = start();
    const old = source();
    old.failForGood();
    vi.advanceTimersByTime(1_000);
    old.send("device.online", undefined, "99");
    old.onopen?.(new Event("open"));
    expect(events).toEqual([]);
  });

  it("falls back to polling when the browser has no EventSource at all", () => {
    const statuses: LiveStatus[] = [];
    expect(() =>
      openEventStream({ onStatus: (s) => statuses.push(s), onEvent: () => {} }, { createSource: () => null }),
    ).not.toThrow();
    expect(statuses).toEqual(["connecting", "reconnecting"]);
  });

  it("adds the resume position correctly to a URL that already has a query", () => {
    const { source } = start({ url: "/api/events?x=1" });
    source().send("device.online", undefined, "5");
    source().failForGood();
    vi.advanceTimersByTime(1_000);
    expect(source().url).toBe("/api/events?x=1&lastEventId=5");
  });
});
