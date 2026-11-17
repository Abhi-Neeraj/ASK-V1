// The last answer the server gave for a screen, so opening the screen again (another tab, the Back button, a reload)
// shows it at once and the fresh answer replaces it a moment later. Every call to the server is a round trip away,
// so a screen that starts empty always feels slow; one that starts from the last answer does not.
//
// Kept in memory and, in the browser, in this tab's sessionStorage (gone when the tab closes, never shared with other
// tabs or kept on disk). Everything is emptied at sign-out. A value is only ever a first guess: the screen always asks
// the server again and shows what it says.

const PREFIX = "ask-cache:";
const memory = new Map<string, string>();

function storage(): Storage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null; // storage blocked (private window, site data cleared): memory alone still works
  }
}

export function readCache<T>(key: string): T | undefined {
  let raw = memory.get(key);
  if (raw === undefined) {
    try {
      raw = storage()?.getItem(PREFIX + key) ?? undefined;
    } catch {
      raw = undefined;
    }
    if (raw !== undefined) memory.set(key, raw);
  }
  if (raw === undefined) return undefined;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return undefined;
  }
}

/** `persist: false` keeps it in memory only (gone on reload): for what people said in a meeting. */
export function writeCache<T>(key: string, value: T, persist = true): void {
  let raw: string;
  try {
    raw = JSON.stringify(value);
  } catch {
    return;
  }
  if (raw === undefined) return;
  memory.set(key, raw);
  if (!persist) return;
  try {
    storage()?.setItem(PREFIX + key, raw);
  } catch {
    // full or blocked: the memory copy is enough
  }
}

/** Forget everything whose key starts with `prefix` (all of it when no prefix is given). */
export function clearCache(prefix = ""): void {
  for (const key of [...memory.keys()]) if (key.startsWith(prefix)) memory.delete(key);
  try {
    const store = storage();
    if (!store) return;
    for (let i = store.length - 1; i >= 0; i -= 1) {
      const name = store.key(i);
      if (name && name.startsWith(PREFIX + prefix)) store.removeItem(name);
    }
  } catch {
    // nothing more to clear
  }
}
