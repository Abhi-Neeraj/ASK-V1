import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { demoApi } from "@/lib/demo/api";
import { demoAuditEvents } from "@/lib/demo/data";
import { signInDemo } from "@/test/utils";

/** The demo API pretends the network takes a moment; fake timers skip it. */
async function settled<T>(pending: Promise<T>): Promise<T> {
  await vi.advanceTimersByTimeAsync(500);
  return pending;
}

describe("the demo audit log", () => {
  beforeEach(() => {
    signInDemo();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("is newest first and pages through everything once", async () => {
    const seen: string[] = [];
    for (const page of [1, 2, 3]) {
      const result = await settled(demoApi.audit.list({ page, pageSize: 8 }));
      expect(result.total).toBe(demoAuditEvents.length);
      seen.push(...result.items.map((event) => event.id));
    }
    expect(seen).toHaveLength(demoAuditEvents.length);
    expect(new Set(seen).size).toBe(seen.length);
    const all = await settled(demoApi.audit.list({ pageSize: 100 }));
    const times = all.items.map((event) => new Date(event.at).getTime());
    expect(times).toEqual([...times].sort((a, b) => b - a));
  });

  it("filters by kind of action", async () => {
    const result = await settled(demoApi.audit.list({ category: "device", pageSize: 100 }));
    expect(result.total).toBeGreaterThan(0);
    expect(result.items.every((event) => event.category === "device")).toBe(true);
  });

  it("filters by a window of time", async () => {
    const hourAgo = new Date(Date.now() - 3600_000).toISOString();
    const recent = await settled(demoApi.audit.list({ dateFrom: hourAgo, pageSize: 100 }));
    expect(recent.total).toBeGreaterThan(0);
    expect(recent.total).toBeLessThan(demoAuditEvents.length);
    expect(recent.items.every((event) => event.at >= hourAgo)).toBe(true);

    const none = await settled(demoApi.audit.list({ dateTo: new Date(0).toISOString() }));
    expect(none.total).toBe(0);
  });

  it("keeps secrets out of the details, as the real log does", () => {
    // Action names such as "auth.password_changed" are fine. What must never appear is a value.
    const details = JSON.stringify(demoAuditEvents.map((event) => event.detail));
    expect(details).not.toMatch(/password|token|secret|credential|embedding|\.wav/i);
  });

  it("is refused without a session", async () => {
    window.localStorage.clear();
    const pending = demoApi.audit.list({}).then(
      () => undefined,
      (error: unknown) => error,
    );
    await vi.advanceTimersByTimeAsync(500);
    expect(await pending).toBeInstanceOf(ApiError);
  });
});
