import { describe, expect, it } from "vitest";
import { FALLBACK_GROUPS, LIVE_EVENT_TYPES, parseLiveEvent, refreshGroupsFor } from "@/lib/live/events";

const body = (over: Record<string, unknown> = {}) =>
  JSON.stringify({ type: "device.online", at: "2026-10-25T10:00:00Z", data: { deviceId: "d1", name: "Front desk" }, ...over });

describe("parseLiveEvent", () => {
  it("reads a well-formed event", () => {
    expect(parseLiveEvent("device.online", body(), "41")).toEqual({
      type: "device.online",
      at: "2026-10-25T10:00:00Z",
      id: "41",
      data: { deviceId: "d1", name: "Front desk" },
    });
  });

  it("treats a missing event id as null", () => {
    expect(parseLiveEvent("device.online", body(), "")?.id).toBeNull();
    expect(parseLiveEvent("device.online", body(), null)?.id).toBeNull();
  });

  it.each([
    ["an unknown type", "device.exploded", body()],
    ["a type the client raises itself", "session.ended", body()],
    ["a body that is not JSON", "device.online", "not json"],
    ["a body that is not an object", "device.online", "42"],
    ["a null body", "device.online", "null"],
    ["a body without a timestamp", "device.online", JSON.stringify({ data: {} })],
    ["a non-string payload", "device.online", undefined],
  ])("ignores %s", (_label, type, raw) => {
    expect(parseLiveEvent(type, raw, "1")).toBeNull();
  });

  it("never lets a hostile data field be anything but a plain object", () => {
    expect(parseLiveEvent("device.online", body({ data: ["a"] }), "1")?.data).toEqual({});
    expect(parseLiveEvent("device.online", body({ data: "text" }), "1")?.data).toEqual({});
    expect(parseLiveEvent("device.online", body({ data: null }), "1")?.data).toEqual({});
  });
});

describe("what each event makes stale", () => {
  it("covers every event the server can send", () => {
    for (const type of LIVE_EVENT_TYPES) expect(refreshGroupsFor(type).length).toBeGreaterThan(0);
  });

  it("refreshes lockets and the overview when presence changes", () => {
    expect(refreshGroupsFor("device.online")).toEqual(expect.arrayContaining(["devices", "summary"]));
    expect(refreshGroupsFor("device.offline")).toEqual(expect.arrayContaining(["devices", "summary"]));
  });

  it("refreshes the library when a recording arrives, changes or is deleted", () => {
    for (const type of ["recording.uploaded", "recording.state", "recording.deleted"] as const) {
      expect(refreshGroupsFor(type)).toContain("recordings");
    }
  });

  it("refreshes the library too when a locket changes hands, because who may see it changed", () => {
    expect(refreshGroupsFor("device.assigned")).toContain("recordings");
  });

  it("refetches everything, including who is signed in, after a gap", () => {
    expect(new Set(refreshGroupsFor("stream.resumed"))).toEqual(
      new Set(["devices", "recordings", "summary", "me", "processing"]),
    );
  });

  it("refreshes whether anything is processing when recordings arrive, change or go", () => {
    for (const type of ["recording.uploaded", "recording.state", "recording.deleted"] as const) {
      expect(refreshGroupsFor(type)).toContain("processing");
    }
  });

  it("only asks who is signed in when the session ends", () => {
    expect(refreshGroupsFor("session.ended")).toEqual(["me"]);
  });

  it("polls everything while the connection is down", () => {
    expect(new Set(FALLBACK_GROUPS)).toEqual(new Set(["devices", "recordings", "summary", "me", "processing"]));
  });
});
