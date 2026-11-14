import { CSRF_COOKIE, CSRF_HEADER } from "@/lib/env";

/**
 * Error envelope returned by the API for every non-2xx response:
 *   { "error": { "code": "snake_case_code", "message": "Human readable.", "details": ... } }
 * Network failures (status 0) are normalised into the same shape.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  get isNetwork() {
    return this.status === 0;
  }
}

function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  const entry = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return entry ? decodeURIComponent(entry.slice(name.length + 1)) : undefined;
}

type FetchOptions = Omit<RequestInit, "body"> & { json?: unknown; body?: BodyInit | null };

/**
 * Same-origin fetch to /api/* (proxied to FastAPI by Next.js). State-changing requests
 * carry the double-submit CSRF token read from the non-HttpOnly CSRF cookie.
 */
export async function apiFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {
  const { json, headers, body, ...rest } = options;
  const method = (rest.method ?? "GET").toUpperCase();

  const requestHeaders = new Headers(headers);
  requestHeaders.set("Accept", "application/json");
  if (json !== undefined) requestHeaders.set("Content-Type", "application/json");
  if (method !== "GET" && method !== "HEAD") {
    const token = readCookie(CSRF_COOKIE);
    if (token) requestHeaders.set(CSRF_HEADER, token);
  }

  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...rest,
      method,
      headers: requestHeaders,
      body: json !== undefined ? JSON.stringify(json) : body,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "network_error", "Can't reach the ASK server. Check that it is running, then try again.");
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let data: unknown;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    data = undefined;
  }

  if (!response.ok) {
    const envelope = data as { error?: { code?: string; message?: string; details?: unknown } } | undefined;
    throw new ApiError(
      response.status,
      envelope?.error?.code ?? "http_error",
      envelope?.error?.message ?? `The request failed (${response.status}).`,
      envelope?.error?.details,
    );
  }
  return data as T;
}
