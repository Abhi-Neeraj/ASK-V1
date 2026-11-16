// Phone build: the CSRF token survives app restarts in the operating system's secure store.
import * as SecureStore from "expo-secure-store";
import type { TokenStore } from "@/infrastructure/api/tokenStore";

export type { TokenStore } from "@/infrastructure/api/tokenStore";
export { memoryTokenStore } from "@/infrastructure/api/tokenStore";

const KEY = "ask.csrf";

function secureTokenStore(): TokenStore {
  let token: string | null = null;
  return {
    get: () => token,
    set: async (value) => {
      token = value;
      if (value === null) await SecureStore.deleteItemAsync(KEY);
      else await SecureStore.setItemAsync(KEY, value);
    },
    load: async () => {
      token = await SecureStore.getItemAsync(KEY);
    },
  };
}

export const defaultTokenStore: TokenStore = secureTokenStore();
