import { describe, expect, it } from "vitest";
import { ApiError, createClient } from "@/infrastructure/api/client";
import { memoryTokenStore } from "@/infrastructure/api/tokenStore";

type Call = { url: string; init: RequestInit };

function fakeFetch(respond: (call: Call) => Response) {
  const calls: Call[] = [];
  const fetchImpl = async (url: string | URL | Request, init?: RequestInit) => {
    const call = { url: String(url), init: init ?? {} };
    calls.push(call);
    return respond(call);
  };
  return { calls, fetchImpl: fetchImpl as typeof fetch };
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("createClient", () => {

  it("waits_and_retries_while_a_sleeping_server_wakes_up", async () => {
    // A free host sleeps when idle; the proxy answers 502/503/504 until it is up again.
    const statuses = [502, 504, 503];
    const { calls, fetchImpl } = fakeFetch(() => {
      const next = statuses.shift();
      return next ? new Response("Bad gateway", { status: next }) : json(200, { ok: true });
    });
    const waits: number[] = [];
    const client = createClient({
      base: "http://api",
      fetchImpl,
      tokens: memoryTokenStore(),
      wait: async (ms) => void waits.push(ms),
    });

    await expect(client.request("POST", "/api/auth/login", { json: { email: "a" } })).resolves.toEqual({ ok: true });
    expect(calls).toHaveLength(4);
    expect(waits).toHaveLength(3);
    expect(calls[3].init.body).toBe(JSON.stringify({ email: "a" }));
  });

  it("a_dropped_request_is_tried_again_within_a_second_and_the_whole_wait_stays_short", async () => {
    const { fetchImpl } = fakeFetch(() => new Response("Bad gateway", { status: 502 }));
    const waits: number[] = [];
    const client = createClient({
      base: "http://api",
      fetchImpl,
      tokens: memoryTokenStore(),
      wait: async (ms) => void waits.push(ms),
    });
    await client.request("GET", "/api/lockets").catch(() => undefined);
    expect(waits[0]).toBeLessThanOrEqual(1000);
    expect(waits.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(40_000);
  });

  it("gives_up_with_a_clear_message_when_the_server_stays_down", async () => {
    const { calls, fetchImpl } = fakeFetch(() => new Response("Bad gateway", { status: 502 }));
    const client = createClient({ base: "http://api", fetchImpl, tokens: memoryTokenStore(), wait: async () => {} });

    const error = await client.request("GET", "/api/lockets").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(502);
    expect((error as ApiError).message).toMatch(/starting up/i);
    expect(calls.length).toBeGreaterThan(1);
  });

  it("does_not_retry_an_ordinary_server_error", async () => {
    const { calls, fetchImpl } = fakeFetch(() => json(500, { error: { code: "internal", message: "Boom" } }));
    const client = createClient({ base: "http://api", fetchImpl, tokens: memoryTokenStore(), wait: async () => {} });
    await expect(client.request("GET", "/api/lockets")).rejects.toThrow("Boom");
    expect(calls).toHaveLength(1);
  });
  it("sends_credentials_and_csrf_on_writes_only", async () => {
    const { calls, fetchImpl } = fakeFetch(() => json(200, { ok: true }));
    const tokens = memoryTokenStore();
    await tokens.set("token-123");
    const client = createClient({ base: "http://api", fetchImpl, tokens });

    await client.request("GET", "/api/lockets");
    await client.request("POST", "/api/lockets", { json: { name: "x" } });

    expect(calls[0].url).toBe("http://api/api/lockets");
    expect(calls[0].init.credentials).toBe("include");
    expect((calls[0].init.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
    const writeHeaders = calls[1].init.headers as Record<string, string>;
    expect(writeHeaders["X-CSRF-Token"]).toBe("token-123");
    expect(writeHeaders["Content-Type"]).toBe("application/json");
    expect(calls[1].init.body).toBe(JSON.stringify({ name: "x" }));
  });

  it("turns_error_json_into_ApiError", async () => {
    const { fetchImpl } = fakeFetch(() =>
      json(409, { error: { code: "locket_full", message: "This locket allows 1 person." } }),
    );
    const client = createClient({ base: "", fetchImpl, tokens: memoryTokenStore() });
    const error = await client.request("POST", "/api/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).code).toBe("locket_full");
    expect((error as ApiError).message).toBe("This locket allows 1 person.");
  });

  it("client_raises_unauthenticated_and_session_signs_out", async () => {
    const { fetchImpl } = fakeFetch(() => json(401, { error: { code: "unauthenticated", message: "Sign in." } }));
    let signedOut = 0;
    const client = createClient({
      base: "",
      fetchImpl,
      tokens: memoryTokenStore(),
      onUnauthenticated: () => {
        signedOut += 1;
      },
    });
    await expect(client.request("GET", "/api/recordings")).rejects.toMatchObject({ status: 401 });
    expect(signedOut).toBe(1);
  });

  it("returns_undefined_for_204_and_text_when_asked", async () => {
    const { fetchImpl } = fakeFetch((call) =>
      call.url.endsWith(".txt") ? new Response("hello\n", { status: 200 }) : new Response(null, { status: 204 }),
    );
    const client = createClient({ base: "", fetchImpl, tokens: memoryTokenStore() });
    expect(await client.request("DELETE", "/api/x")).toBeUndefined();
    expect(await client.text("/api/recordings/1/summary.txt")).toBe("hello\n");
  });

  it("bytes_returns_the_body_as_bytes", async () => {
    const { fetchImpl } = fakeFetch(() => new Response(new Uint8Array([1, 2, 3]), { status: 200 }));
    const client = createClient({ base: "", fetchImpl, tokens: memoryTokenStore() });
    expect(Array.from(await client.bytes("/api/recordings/1/audio"))).toEqual([1, 2, 3]);
  });

  it("a_network_failure_is_an_ApiError_with_status_0", async () => {
    const fetchImpl = (async () => {
      throw new TypeError("Failed to fetch");
    }) as typeof fetch;
    const client = createClient({ base: "", fetchImpl, tokens: memoryTokenStore() });
    await expect(client.request("GET", "/api/x")).rejects.toMatchObject({ status: 0, code: "network" });
  });
});
