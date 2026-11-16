import { describe, expect, it } from "vitest";
import { afterMeFailed } from "@/application/sessionStart";
import { ApiError } from "@/infrastructure/api/client";
import type { UserOut } from "@/infrastructure/api/types";

const user = { id: "u1", email: "a@b.c", name: "A", role: "admin" } as UserOut;

describe("afterMeFailed", () => {
  it("offline_with_a_known_user_stays_signed_in", () => {
    expect(afterMeFailed(new ApiError(0, "network", "offline"), user)).toEqual({
      status: "signedIn",
      user,
      offline: true,
    });
  });

  it("offline_with_nobody_known_goes_to_sign_in", () => {
    expect(afterMeFailed(new ApiError(0, "network", "offline"), null)).toEqual({
      status: "signedOut",
      user: null,
      offline: true,
    });
  });

  it("a_rejected_session_always_signs_out", () => {
    expect(afterMeFailed(new ApiError(401, "unauthenticated", "Sign in."), user)).toEqual({
      status: "signedOut",
      user: null,
      offline: false,
    });
  });
});
