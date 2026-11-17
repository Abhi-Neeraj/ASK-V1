// The last signed-in person, so the app opens at once on reload instead of waiting for the server to say who is
// signed in. On the website it is kept in this tab's sessionStorage only (name, email, role: gone when the tab
// closes, never on disk) and is only ever a first guess: the server confirms it a moment later, and decides what the
// person may do. The phone build keeps it in the secure store (userCache.native.ts).
import type { UserOut } from "@/infrastructure/api/types";

const KEY = "ask-user";

function storage(): Storage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}

export const userCache = {
  load: async (): Promise<UserOut | null> => {
    try {
      const raw = storage()?.getItem(KEY);
      return raw ? (JSON.parse(raw) as UserOut) : null;
    } catch {
      return null;
    }
  },
  save: async (user: UserOut | null): Promise<void> => {
    try {
      if (user) storage()?.setItem(KEY, JSON.stringify({ ...user, csrfToken: "" }));
      else storage()?.removeItem(KEY);
    } catch {
      // storage blocked: the app simply waits for the server on the next reload
    }
  },
};
