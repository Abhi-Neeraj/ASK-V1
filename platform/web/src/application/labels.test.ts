import { describe, expect, it } from "vitest";
import { formatDuration } from "@/application/labels";

describe("formatDuration", () => {
  it("keeps short times as they were", () => {
    expect(formatDuration(45_000)).toBe("45 s");
    expect(formatDuration(125_000)).toBe("2 min 5 s");
  });

  it("shows a meeting of hours in hours, not as hundreds of minutes", () => {
    expect(formatDuration(((4 * 60 + 12) * 60 + 30) * 1000)).toBe("4 h 12 min");
    expect(formatDuration(5 * 3600 * 1000)).toBe("5 h 0 min");
    expect(formatDuration(59 * 60 * 1000)).toBe("59 min 0 s");
  });
});
