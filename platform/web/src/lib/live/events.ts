// What the live feed (GET /api/events) says, and what each message means for the screens.
// Keep in step with platform/docs/API.md.

/** Events the server sends. Anything else is ignored rather than guessed at. */
export const LIVE_EVENT_TYPES = [
  "device.online",
  "device.offline",
  "device.paired",
  "device.assigned",
  "device.revoked",
  "device.connected",
  "device.disconnected",
  "recording.uploaded",
  "recording.state",
  "recording.deleted",
] as const;

export type ServerEventType = (typeof LIVE_EVENT_TYPES)[number];

/** Raised by the client itself, never by the server. */
export type ClientEventType = "stream.resumed" | "session.ended";

export type LiveEventType = ServerEventType | ClientEventType;

export type LiveEvent = {
  type: LiveEventType;
  /** When the server recorded it (ISO 8601). */
  at: string;
  /** The server's event ID, used to resume. Null for events the client raised. */
  id: string | null;
  data: Record<string, unknown>;
};

/**
 * connecting: first attempt. live: connected. reconnecting: the connection dropped and the client is
 * trying again (screens keep refreshing by polling meanwhile). ended: signed out, nothing more to try.
 */
export type LiveStatus = "connecting" | "live" | "reconnecting" | "ended";

export type LiveHandlers = {
  onStatus(status: LiveStatus): void;
  onEvent(event: LiveEvent): void;
};

const SERVER_TYPES: ReadonlySet<string> = new Set(LIVE_EVENT_TYPES);

/** Read one message from the server. Returns null for anything that is not a well-formed known event. */
export function parseLiveEvent(type: string, raw: unknown, lastEventId: string | null): LiveEvent | null {
  if (!SERVER_TYPES.has(type) || typeof raw !== "string") return null;
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof body !== "object" || body === null) return null;
  const { at, data } = body as { at?: unknown; data?: unknown };
  if (typeof at !== "string") return null;
  return {
    type: type as ServerEventType,
    at,
    id: lastEventId || null,
    data: typeof data === "object" && data !== null && !Array.isArray(data) ? (data as Record<string, unknown>) : {},
  };
}

/** Which cached data an event makes stale. */
export type RefreshGroup = "devices" | "recordings" | "summary" | "me" | "processing";

const GROUPS: Record<LiveEventType, readonly RefreshGroup[]> = {
  "device.online": ["devices", "summary"],
  "device.offline": ["devices", "summary"],
  "device.paired": ["devices", "summary"],
  "device.assigned": ["devices", "recordings", "summary"],
  "device.revoked": ["devices", "summary"],
  "device.connected": ["devices"],
  "device.disconnected": ["devices"],
  "recording.uploaded": ["recordings", "devices", "summary", "processing"],
  "recording.state": ["recordings", "summary", "processing"],
  "recording.deleted": ["recordings", "summary", "processing"],
  // After a gap nothing can be trusted, so everything is refetched.
  "stream.resumed": ["devices", "recordings", "summary", "me", "processing"],
  "session.ended": ["me"],
};

export function refreshGroupsFor(type: LiveEventType): readonly RefreshGroup[] {
  return GROUPS[type];
}

/** What to poll for while the live connection is down. */
export const FALLBACK_GROUPS: readonly RefreshGroup[] = ["devices", "recordings", "summary", "me", "processing"];
