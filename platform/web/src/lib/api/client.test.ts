import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "@/lib/api/client";

function respond(status: number, body?: unknown) {
  return new Response(body === undefined ? null : JSON.stringify(body), { status });
}

describe("apiFetch", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    document.cookie = "ask_csrf=token-123; path=/";
  });
  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
    document.cookie = "ask_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });

  const lastCall = () => {
    const [url, init] = fetchMock.mock.calls[0];
    return { url: String(url), init: init as RequestInit, headers: new Headers((init as RequestInit).headers) };
  };

  it("sends GET requests to /api without a CSRF header", async () => {
    fetchMock.mockResolvedValue(respond(200, { ok: true }));
    await expect(apiFetch("/devices")).resolves.toEqual({ ok: true });
    const { url, headers, init } = lastCall();
    expect(url).toBe("/api/devices");
    expect(headers.get("x-csrf-token")).toBeNull();
    expect(init.credentials).toBe("same-origin");
  });

  it("adds the double-submit CSRF token to state-changing requests", async () => {
    fetchMock.mockResolvedValue(respond(200, {}));
    await apiFetch("/auth/logout", { method: "POST" });
    expect(lastCall().headers.get("x-csrf-token")).toBe("token-123");
  });

  it("serializes json bodies with a content type", async () => {
    fetchMock.mockResolvedValue(respond(200, {}));
    await apiFetch("/people", { method: "POST", json: { displayName: "Sam" } });
    const { init, headers } = lastCall();
    expect(init.body).toBe(JSON.stringify({ displayName: "Sam" }));
    expect(headers.get("content-type")).toBe("application/json");
  });

  it("returns undefined for 204 responses", async () => {
    fetchMock.mockResolvedValue(respond(204));
    await expect(apiFetch("/recordings/rec_1", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("turns the error envelope into an ApiError", async () => {
    fetchMock.mockResolvedValue(
      respond(422, { error: { code: "validation_error", message: "Check the fields.", details: { email: "Bad" } } }),
    );
    const error = await apiFetch("/contact", { method: "POST", json: {} }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: "validation_error", message: "Check the fields.", details: { email: "Bad" } });
  });

  it("falls back to a generic message when the body is not the envelope", async () => {
    fetchMock.mockResolvedValue(new Response("<html>Bad gateway</html>", { status: 502 }));
    const error = (await apiFetch("/devices").catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(502);
    expect(error.message).toBe("The request failed (502).");
  });

  it("reports an unreachable server as a status-0 network error", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = (await apiFetch("/devices").catch((e: unknown) => e)) as ApiError;
    expect(error.isNetwork).toBe(true);
    expect(error.status).toBe(0);
    expect(error.message).toMatch(/can't reach the ask server/i);
  });
});
