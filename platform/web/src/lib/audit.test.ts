import { describe, expect, it } from "vitest";
import {
  AUDIT_CATEGORIES,
  describeAction,
  describeActor,
  describeObject,
  detailPairs,
  endOfDayIso,
  linkFor,
  shortId,
  startOfDayIso,
} from "@/lib/audit";

describe("describeAction", () => {
  it("puts known actions in plain words", () => {
    expect(describeAction("auth.login_failed")).toBe("Failed sign-in attempt");
    expect(describeAction("person.consent_revoked")).toBe("Recorded withdrawn consent");
    expect(describeAction("recording.downloaded")).toBe("Downloaded recording audio");
  });

  it("has words for listening to a recording and for managing team members", () => {
    expect(describeAction("recording.played")).toBe("Listened to a recording");
    expect(describeAction("member.role_changed")).toBe("Changed someone's role");
    expect(describeAction("member.deactivated")).toBe("Switched someone off");
    expect(describeAction("member.reactivated")).toBe("Switched someone back on");
    expect(describeAction("member.sessions_ended")).toBe("Signed someone out everywhere");
  });

  it("still reads sensibly for an action nobody wrote words for", () => {
    expect(describeAction("device.firmware_updated")).toBe("Device firmware updated");
    expect(describeAction("")).toBe("Unknown action");
  });

  it("has words for every category the server can filter by", () => {
    expect(AUDIT_CATEGORIES.map((c) => c.value).sort()).toEqual(
      ["auth", "company", "device", "invitation", "member", "person", "recording", "settings", "transcript"].sort(),
    );
    for (const { label } of AUDIT_CATEGORIES) expect(label).not.toMatch(/\./);
  });

  it("never names a product or part in what a person reads", () => {
    const everything = [...AUDIT_CATEGORIES.map((c) => c.label), describeAction("recording.processed")].join(" ");
    expect(everything).not.toMatch(/whisper|pyannote|speechbrain|esp32|xiao|i2s/i);
  });
});

describe("describeObject, shortId and linkFor", () => {
  it("names what an action was done to", () => {
    expect(describeObject("device")).toBe("Locket");
    expect(describeObject("company")).toBe("Workspace");
    expect(describeObject("something_new")).toBe("something new");
    expect(describeObject(null)).toBeNull();
  });

  it("shortens ids for the screen", () => {
    expect(shortId("0123456789abcdef")).toBe("01234567");
    expect(shortId(null)).toBeNull();
  });

  it("links a recording that still exists, and nothing else", () => {
    const base = { objectType: "recording", objectId: "abc/def" };
    expect(linkFor({ ...base, action: "recording.downloaded" })).toBe("/dashboard/recordings/abc%2Fdef");
    expect(linkFor({ ...base, action: "recording.deleted" })).toBeNull();
    expect(linkFor({ ...base, action: "recording.purged" })).toBeNull();
    expect(linkFor({ ...base, action: "recording.retention_deleted" })).toBeNull();
    expect(linkFor({ objectType: "person", objectId: "x", action: "person.created" })).toBeNull();
    expect(linkFor({ objectType: "recording", objectId: null, action: "recording.downloaded" })).toBeNull();
  });
});

describe("detailPairs", () => {
  it("lists counts and flags with readable names", () => {
    expect(detailPairs({ retention_days: 90, voice_profile_removed: false, to: null, nested: { a: 1 } })).toEqual([
      { key: "retention days", value: "90" },
      { key: "voice profile removed", value: "false" },
      { key: "to", value: "none" },
      { key: "nested", value: '{"a":1}' },
    ]);
    expect(detailPairs(null)).toEqual([]);
  });
});

describe("describeActor", () => {
  it("shows a person's name and email", () => {
    expect(describeActor({ kind: "user", id: "1", name: "Priya Nair", email: "p@example.com" })).toEqual({
      name: "Priya Nair",
      note: "p@example.com",
    });
  });

  it("marks a locket as a locket", () => {
    expect(describeActor({ kind: "device", id: "1", name: "Front desk", email: null })).toEqual({
      name: "Front desk",
      note: "Locket",
    });
  });

  it("says who it was when nobody signed in, without inventing a name", () => {
    expect(describeActor(null).name).toBe("System or removed account");
  });
});

describe("day boundaries", () => {
  it("turn a calendar day into the instants it starts and ends", () => {
    const start = new Date(startOfDayIso("2026-10-20")!);
    const end = new Date(endOfDayIso("2026-10-20")!);
    expect([start.getFullYear(), start.getMonth(), start.getDate(), start.getHours(), start.getMinutes()]).toEqual([
      2026, 9, 20, 0, 0,
    ]);
    expect([end.getHours(), end.getMinutes(), end.getSeconds()]).toEqual([23, 59, 59]);
    expect(end.getTime() - start.getTime()).toBe(24 * 3600_000 - 1);
  });

  it("ignores a value that is not a date", () => {
    expect(startOfDayIso("nonsense")).toBeUndefined();
    expect(endOfDayIso("")).toBeUndefined();
  });
});
