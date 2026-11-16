// Where the CSRF token lives. The server returns it in the sign-in answer (`csrfToken`) and every write sends
// it back in X-CSRF-Token. On the web it is kept in memory only (a reload asks /api/auth/me again); the phone
// build keeps it in the secure store (tokenStore.native.ts). Never in AsyncStorage.

export type TokenStore = {
  get(): string | null;
  set(token: string | null): Promise<void>;
  load(): Promise<void>;
};

export function memoryTokenStore(): TokenStore {
  let token: string | null = null;
  return {
    get: () => token,
    set: async (value) => {
      token = value;
    },
    load: async () => undefined,
  };
}

export const defaultTokenStore: TokenStore = memoryTokenStore();
