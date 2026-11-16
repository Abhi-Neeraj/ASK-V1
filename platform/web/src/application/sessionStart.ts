// What to do when "who am I?" (/api/auth/me) fails at start-up. No connection is not the same as signed out:
// a phone without signal must still open, connect to its locket and keep recordings for later, so the last
// known person stays signed in (marked offline). Only the server saying "not signed in" signs out.

import { ApiError } from "@/infrastructure/api/client";
import type { UserOut } from "@/infrastructure/api/types";

export type StartDecision = { status: "signedIn" | "signedOut"; user: UserOut | null; offline: boolean };

export function afterMeFailed(error: unknown, cached: UserOut | null): StartDecision {
  const offline = error instanceof ApiError ? error.status === 0 : true;
  if (offline && cached) return { status: "signedIn", user: cached, offline: true };
  return { status: "signedOut", user: null, offline };
}
