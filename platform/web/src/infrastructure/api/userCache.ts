// The last signed-in person, so the phone app can open offline. The website keeps nothing (it cannot work
// without the server anyway); the phone build keeps it in the secure store (userCache.native.ts).
import type { UserOut } from "@/infrastructure/api/types";

export const userCache = {
  load: async (): Promise<UserOut | null> => null,
  save: async (_user: UserOut | null): Promise<void> => undefined,
};
