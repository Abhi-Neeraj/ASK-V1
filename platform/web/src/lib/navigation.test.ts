import { describe, expect, it } from "vitest";
import { safeNextPath } from "@/lib/navigation";

describe("safeNextPath (post-login redirect)", () => {
  it("allows same-site paths, including query strings", () => {
    expect(safeNextPath("/dashboard/recordings")).toBe("/dashboard/recordings");
    expect(safeNextPath("/dashboard/recordings?page=2")).toBe("/dashboard/recordings?page=2");
  });

  it("falls back to the dashboard when nothing is given", () => {
    expect(safeNextPath(null)).toBe("/dashboard");
    expect(safeNextPath(undefined)).toBe("/dashboard");
    expect(safeNextPath("")).toBe("/dashboard");
  });

  it.each([
    "https://evil.example/phish",
    "//evil.example",
    "///evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "javascript:alert(1)",
    "dashboard",
  ])("rejects an open-redirect attempt: %s", (value) => {
    expect(safeNextPath(value)).toBe("/dashboard");
  });

  it("uses the supplied fallback", () => {
    expect(safeNextPath("https://evil.example", "/login")).toBe("/login");
  });
});
