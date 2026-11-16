// The one way the app talks to the ASK server. Cookies carry the session (credentials: "include"); writes add
// the CSRF token. Every error becomes an ApiError with the server's code and message, and a 401 anywhere tells
// the session to sign out.

import type { TokenStore } from "@/infrastructure/api/tokenStore";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export type RequestOptions = {
  json?: unknown;
  body?: BodyInit;
  headers?: Record<string, string>;
};

export type ApiClient = {
  base: string;
  request<T = unknown>(method: string, path: string, opts?: RequestOptions): Promise<T>;
  text(path: string): Promise<string>;
  bytes(path: string): Promise<Uint8Array>;
};

const WRITES = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// The hosted server (Render's free plan) sleeps when idle and takes about a minute to wake; until then the proxy
// in front of it answers 502, 503 or 504. Wait and try again for about 75 seconds before giving up. Repeating a
// request is safe here: uploads carry their own ID and the server acknowledges a repeat without storing it twice.
const WAKING = new Set([502, 503, 504]);
const WAKE_WAITS_MS = [5_000, 10_000, 20_000, 40_000];
const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export function createClient({
  base,
  fetchImpl = fetch,
  tokens,
  onUnauthenticated,
  wait = sleep,
}: {
  base: string;
  fetchImpl?: typeof fetch;
  tokens: TokenStore;
  onUnauthenticated?: () => void;
  wait?: (ms: number) => Promise<void>;
}): ApiClient {
  async function send(method: string, path: string, opts: RequestOptions = {}): Promise<Response> {
    for (const ms of WAKE_WAITS_MS) {
      try {
        return await sendOnce(method, path, opts);
      } catch (error) {
        if (!(error instanceof ApiError) || !WAKING.has(error.status)) throw error;
        await wait(ms);
      }
    }
    try {
      return await sendOnce(method, path, opts);
    } catch (error) {
      if (error instanceof ApiError && WAKING.has(error.status)) {
        throw new ApiError(
          error.status,
          "server_starting",
          "The ASK server is still starting up. Wait a minute and try again.",
        );
      }
      throw error;
    }
  }

  async function sendOnce(method: string, path: string, opts: RequestOptions): Promise<Response> {
    const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
    let body = opts.body;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.json);
    }
    const token = tokens.get();
    if (WRITES.has(method) && token) headers["X-CSRF-Token"] = token;
    let response: Response;
    try {
      response = await fetchImpl(`${base}${path}`, { method, headers, body, credentials: "include" });
    } catch {
      throw new ApiError(0, "network", "The ASK server could not be reached. Check your connection.");
    }
    if (response.ok) return response;
    let code = "error";
    let message = `The server answered ${response.status}.`;
    try {
      const parsed = (await response.json()) as { error?: { code?: string; message?: string } };
      code = parsed.error?.code ?? code;
      message = parsed.error?.message ?? message;
    } catch {
      // not JSON: keep the generic message
    }
    if (response.status === 401) onUnauthenticated?.();
    throw new ApiError(response.status, code, message);
  }

  return {
    base,
    async request<T>(method: string, path: string, opts?: RequestOptions): Promise<T> {
      const response = await send(method, path, opts);
      if (response.status === 204) return undefined as T;
      const text = await response.text();
      return (text ? JSON.parse(text) : undefined) as T;
    },
    async text(path: string): Promise<string> {
      const response = await send("GET", path, { headers: { Accept: "text/plain" } });
      return response.text();
    },
    async bytes(path: string): Promise<Uint8Array> {
      const response = await send("GET", path, { headers: { Accept: "*/*" } });
      return new Uint8Array(await response.arrayBuffer());
    },
  };
}
