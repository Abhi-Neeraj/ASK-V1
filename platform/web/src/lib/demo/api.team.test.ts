import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { demoApi } from "@/lib/demo/api";
import { demoUser } from "@/lib/demo/data";
import { signInDemo } from "@/test/utils";

/** The demo API pretends the network takes a moment; fake timers skip it. */
async function settled<T>(pending: Promise<T>): Promise<T> {
  await vi.advanceTimersByTimeAsync(500);
  return pending;
}

async function refusal(pending: Promise<unknown>): Promise<ApiError> {
  const outcome = pending.then(
    () => undefined,
    (error: unknown) => error,
  );
  await vi.advanceTimersByTimeAsync(500);
  const error = await outcome;
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}

describe("the demo team", () => {
  beforeEach(() => {
    signInDemo();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("changes a role", async () => {
    const changed = await settled(demoApi.team.updateMember("usr_1", { role: "manager" }));
    expect(changed).toMatchObject({ id: "usr_1", role: "manager", isActive: true });
    const listed = (await settled(demoApi.team.members())).find((m) => m.id === "usr_1");
    expect(listed?.role).toBe("manager");
  });

  it("switches someone off and back on", async () => {
    expect((await settled(demoApi.team.updateMember("usr_2", { isActive: false }))).isActive).toBe(false);
    expect((await settled(demoApi.team.updateMember("usr_2", { isActive: true }))).isActive).toBe(true);
  });

  it("will not let the signed-in administrator change themselves", async () => {
    const error = await refusal(demoApi.team.updateMember(demoUser.id, { role: "employee" }));
    expect(error).toMatchObject({ status: 409, code: "cannot_change_self" });
    expect((await refusal(demoApi.team.endMemberSessions(demoUser.id))).code).toBe("cannot_change_self");
  });

  it("does not find people who are not there", async () => {
    expect((await refusal(demoApi.team.updateMember("usr_404", { role: "admin" }))).status).toBe(404);
    expect((await refusal(demoApi.team.endMemberSessions("usr_404"))).status).toBe(404);
  });

  it("signs someone out without changing them", async () => {
    await settled(demoApi.team.endMemberSessions("usr_1"));
    const listed = (await settled(demoApi.team.members())).find((m) => m.id === "usr_1");
    expect(listed?.isActive).toBe(true);
  });

  it("will not hand a locket to someone who is switched off", async () => {
    await settled(demoApi.team.updateMember("usr_1", { isActive: false }));
    const error = await refusal(demoApi.devices.assign("dev_1", "usr_1"));
    expect(error).toMatchObject({ status: 422, code: "validation_error" });
    await settled(demoApi.team.updateMember("usr_1", { isActive: true }));
  });
});
