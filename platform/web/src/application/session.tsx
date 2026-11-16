// Who is signed in, and the server API for everything else. A 401 from any call signs the person out, so an
// expired session always lands on Sign in instead of an empty or broken screen.

import React from "react";
import { apiBase } from "@/infrastructure/api/base";
import { ApiError, createClient } from "@/infrastructure/api/client";
import { type Api, endpoints } from "@/infrastructure/api/endpoints";
import { defaultTokenStore } from "@/infrastructure/api/tokenStore";
import { userCache } from "@/infrastructure/api/userCache";
import { afterMeFailed } from "@/application/sessionStart";
import type { UserOut } from "@/infrastructure/api/types";

type Status = "loading" | "signedOut" | "signedIn";

type SessionValue = {
  status: Status;
  user: UserOut | null;
  /** Signed in from the phone's memory because the server could not be reached. */
  offline: boolean;
  api: Api;
  signIn(email: string, password: string): Promise<void>;
  signOut(): Promise<void>;
  refresh(): Promise<void>;
};

const SessionContext = React.createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = React.useState<Status>("loading");
  const [user, setUser] = React.useState<UserOut | null>(null);
  const [offline, setOffline] = React.useState(false);

  const api = React.useMemo(() => {
    const client = createClient({
      base: apiBase(),
      tokens: defaultTokenStore,
      onUnauthenticated: () => {
        setUser(null);
        setStatus("signedOut");
        void userCache.save(null);
      },
    });
    return endpoints(client, defaultTokenStore);
  }, []);

  const refresh = React.useCallback(async () => {
    try {
      const me = await api.me();
      setUser(me);
      setOffline(false);
      setStatus("signedIn");
      await userCache.save(me);
    } catch (error) {
      const decision = afterMeFailed(error, await userCache.load());
      setUser(decision.user);
      setOffline(decision.offline);
      setStatus(decision.status);
      if (!(error instanceof ApiError)) throw error;
    }
  }, [api]);

  React.useEffect(() => {
    defaultTokenStore
      .load()
      .then(refresh)
      .catch(() => setStatus("signedOut"));
  }, [refresh]);

  const value = React.useMemo<SessionValue>(
    () => ({
      status,
      user,
      offline,
      api,
      signIn: async (email, password) => {
        const signedIn = await api.login(email, password);
        setUser(signedIn);
        setOffline(false);
        setStatus("signedIn");
        await userCache.save(signedIn);
      },
      signOut: async () => {
        try {
          await api.logout();
        } finally {
          setUser(null);
          setStatus("signedOut");
          await userCache.save(null);
        }
      },
      refresh,
    }),
    [status, user, offline, api, refresh],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = React.useContext(SessionContext);
  if (value === null) throw new Error("useSession must be used inside SessionProvider");
  return value;
}

export function canManage(user: UserOut | null): boolean {
  return user?.role === "admin" || user?.role === "manager";
}
