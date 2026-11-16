// Phone build: the last signed-in person (name, email, role, company; never a password or token besides the
// CSRF token already kept apart) in the operating system's secure store, so the app opens without signal.
import * as SecureStore from "expo-secure-store";
import type { UserOut } from "@/infrastructure/api/types";

const KEY = "ask.user";

export const userCache = {
  load: async (): Promise<UserOut | null> => {
    const raw = await SecureStore.getItemAsync(KEY);
    try {
      return raw ? (JSON.parse(raw) as UserOut) : null;
    } catch {
      return null;
    }
  },
  save: async (user: UserOut | null): Promise<void> => {
    if (user === null) await SecureStore.deleteItemAsync(KEY);
    else await SecureStore.setItemAsync(KEY, JSON.stringify({ ...user, csrfToken: "" }));
  },
};
