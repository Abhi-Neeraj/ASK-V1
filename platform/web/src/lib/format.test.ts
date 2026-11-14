import { describe, expect, it } from "vitest";
import { formatBytes, formatDuration, formatRelative, formatTimestamp } from "@/lib/format";

describe("formatDuration", () => {
  it("formats minutes and seconds", () => {
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(10_000)).toBe("0:10");
    expect(formatDuration(65_000)).toBe("1:05");
  });
  it("adds hours when needed and never goes negative", () => {
    expect(formatDuration(3_729_000)).toBe("1:02:09");
    expect(formatDuration(-5000)).toBe("0:00");
  });
});

describe("formatTimestamp", () => {
  it("matches the transcript style HH:MM:SS.mmm", () => {
    expect(formatTimestamp(2100)).toBe("00:00:02.100");
    expect(formatTimestamp(9500)).toBe("00:00:09.500");
    expect(formatTimestamp(3_723_456)).toBe("01:02:03.456");
  });
});

describe("formatBytes", () => {
  it("scales units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

describe("formatRelative", () => {
  const now = Date.parse("2026-10-25T12:00:00Z");
  it("says Never for no timestamp and Just now under a minute", () => {
    expect(formatRelative(null, now)).toBe("Never");
    expect(formatRelative("2026-10-25T11:59:40Z", now)).toBe("Just now");
  });
  it("uses minutes, hours and days", () => {
    expect(formatRelative("2026-10-25T11:57:00Z", now)).toBe("3 minutes ago");
    expect(formatRelative("2026-10-25T09:00:00Z", now)).toBe("3 hours ago");
    expect(formatRelative("2026-10-23T12:00:00Z", now)).toBe("2 days ago");
  });
});
