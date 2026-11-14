import { LIVE_EVENT_TYPES, parseLiveEvent, type LiveHandlers } from "@/lib/live/events";

/** The slice of EventSource this client uses, so tests can stand in for the browser. */
export type SourceLike = {
  readonly readyState: number;
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
  addEventListener(type: string, listener: (event: MessageEvent) => void): void;
  close(): void;
};

const CONNECTING = 0;

export type StreamOptions = {
  url?: string;
  createSource?: (url: string) => SourceLike | null;
  /** Delays between attempts after the browser gives up on a connection. The last one repeats. */
  backoffMs?: readonly number[];
};

const DEFAULT_BACKOFF = [1_000, 2_000, 4_000, 8_000, 15_000, 30_000] as const;

function browserSource(url: string): SourceLike | null {
  if (typeof EventSource === "undefined") return null;
  return new EventSource(url, { withCredentials: true }) as unknown as SourceLike;
}

/**
 * Follows the server's live feed and reports a plain status plus each event.
 *
 * The browser reconnects on its own after a dropped connection and sends `Last-Event-ID`, so nothing
 * is missed. When it gives up (the server answered with an error, or a proxy broke the stream) this
 * opens a fresh connection after a growing delay, passing the last ID it saw in the URL instead.
 * After any gap a `stream.resumed` event tells the screens to refetch rather than trust what they have.
 *
 * Returns a function that closes everything. It is safe to call twice.
 */
export function openEventStream(handlers: LiveHandlers, options: StreamOptions = {}): () => void {
  const base = options.url ?? "/api/events";
  const create = options.createSource ?? browserSource;
  const backoff = options.backoffMs ?? DEFAULT_BACKOFF;

  let source: SourceLike | null = null;
  let stopped = false;
  let everOpened = false;
  let renewing = false;
  let attempt = 0;
  let lastId: string | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;

  function scheduleRetry(delay: number) {
    clearTimeout(retryTimer);
    retryTimer = setTimeout(connect, delay);
  }

  function connect() {
    if (stopped) return;
    const url = lastId ? `${base}${base.includes("?") ? "&" : "?"}lastEventId=${encodeURIComponent(lastId)}` : base;
    const next = create(url);
    if (next === null) {
      // No EventSource here (very old browser or an unusual runtime): screens poll instead.
      handlers.onStatus("reconnecting");
      return;
    }
    source = next;

    next.onopen = () => {
      if (stopped || source !== next) return;
      attempt = 0;
      // A planned reconnect replays whatever it missed from the last event ID, so nothing needs
      // refetching. Any other gap might have outlasted the server's memory, so it does.
      const resumedAfterGap = everOpened && !renewing;
      renewing = false;
      everOpened = true;
      handlers.onStatus("live");
      if (resumedAfterGap) {
        handlers.onEvent({ type: "stream.resumed", at: new Date().toISOString(), id: lastId, data: {} });
      }
    };

    for (const type of LIVE_EVENT_TYPES) {
      next.addEventListener(type, (message) => {
        if (stopped || source !== next) return;
        const event = parseLiveEvent(type, message.data, message.lastEventId || null);
        if (!event) return;
        if (event.id) lastId = event.id;
        handlers.onEvent(event);
      });
    }

    // The server ends a stream on purpose every so often. The browser reconnects by itself.
    next.addEventListener("stream.renew", () => {
      renewing = true;
    });

    next.addEventListener("session.ended", () => {
      if (stopped) return;
      stopped = true;
      next.close();
      clearTimeout(retryTimer);
      handlers.onEvent({ type: "session.ended", at: new Date().toISOString(), id: null, data: {} });
      handlers.onStatus("ended");
    });

    next.onerror = () => {
      if (stopped || source !== next) return;
      // CONNECTING means the browser is already retrying by itself, with Last-Event-ID.
      const browserWillRetry = next.readyState === CONNECTING;
      if (renewing) {
        // A planned reconnect: do not flash "reconnecting" at the person.
        if (!browserWillRetry) {
          next.close();
          scheduleRetry(0);
        }
        return;
      }
      handlers.onStatus("reconnecting");
      if (!browserWillRetry) {
        // The browser has given up (an error response, or a proxy that broke the stream).
        next.close();
        const delay = backoff[Math.min(attempt, backoff.length - 1)] ?? 30_000;
        attempt += 1;
        scheduleRetry(delay);
      }
    };
  }

  handlers.onStatus("connecting");
  connect();

  return () => {
    stopped = true;
    clearTimeout(retryTimer);
    source?.close();
    source = null;
  };
}
