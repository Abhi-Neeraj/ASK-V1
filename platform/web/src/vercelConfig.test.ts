import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const config = JSON.parse(readFileSync(resolve(__dirname, "../vercel.json"), "utf8")) as {
  headers: { source: string; headers: { key: string; value: string }[] }[];
  rewrites: { source: string; destination: string }[];
};

describe("vercel.json", () => {
  it("forwards /api to an https server", () => {
    const api = config.rewrites.find((r) => r.source === "/api/:path*");
    expect(api?.destination).toMatch(/^https:\/\/[a-z0-9.-]+\/api\/:path\*$/);
  });

  it("sends every other page to the app but never swallows the build's files or the API", () => {
    const fallback = config.rewrites.find((r) => r.destination === "/index.html");
    expect(fallback?.source).toContain("_expo/");
    expect(fallback?.source).toContain("assets/");
    expect(fallback?.source).toContain("api/");
  });

  it("lets the browser keep the build's hashed files for good, so a repeat visit does not ask for them again", () => {
    for (const source of ["/_expo/static/(.*)", "/assets/(.*)"]) {
      const rule = config.headers.find((h) => h.source === source);
      const cache = rule?.headers.find((h) => h.key === "Cache-Control")?.value ?? "";
      expect(cache).toContain("immutable");
      expect(cache).toContain("max-age=31536000");
    }
    // The page itself (index.html) must stay revalidated so a new version is picked up.
    expect(config.headers.some((h) => h.source === "/" || h.source === "/(.*)")).toBe(false);
  });
});
