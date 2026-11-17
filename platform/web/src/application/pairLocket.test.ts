import { describe, expect, it } from "vitest";
import { pairLocket } from "@/application/pairLocket";
import type { LocketLink } from "@/domain/locket/link";
import { ApiError } from "@/infrastructure/api/client";

function fakeLink(opts: { bondFails?: boolean } = {}) {
  const log: string[] = [];
  let nonceCounter = 0;
  const link: LocketLink = {
    scan: () => () => undefined,
    connect: async (id) => {
      log.push(`connect ${id}`);
    },
    disconnect: async () => {
      log.push("disconnect");
    },
    onDisconnected: () => () => undefined,
    readInfo: async () => "ASKINFO/1 ASK-7KQ2MX 1.0.0 80 0",
    readNonce: async () => {
      nonceCounter += 1;
      log.push("readNonce");
      return new Uint8Array(16).fill(nonceCounter);
    },
    bond: async () => {
      log.push("bond");
      if (opts.bondFails) throw new Error("passkey rejected");
    },
    readProof: async () => {
      log.push("readProof");
      return "ab".repeat(32);
    },
    send: async () => undefined,
    onStatus: () => () => undefined,
    onAudio: () => () => undefined,
  };
  return { link, log };
}

function fakeApi(fail: { code?: ApiError; confirm?: ApiError } = {}) {
  const calls: string[] = [];
  return {
    calls,
    api: {
      requestPairingCode: async (productId: string, nonce: string) => {
        calls.push(`code ${productId} ${nonce}`);
        if (fail.code) throw fail.code;
        return { sentTo: "e***@ask.test", expiresAt: "2026-11-14T10:10:00Z" };
      },
      confirmPaired: async (productId: string, nonce: string, proof: string) => {
        calls.push(`paired ${productId} ${nonce} ${proof}`);
        if (fail.confirm) throw fail.confirm;
      },
    },
  };
}

const NONCE_1 = "01".repeat(16);

describe("pairLocket", () => {
  it("pairs_in_order_and_sends_the_proof", async () => {
    const { link, log } = fakeLink();
    const { api, calls } = fakeApi();
    const sent: string[] = [];
    const outcome = await pairLocket({
      link,
      api,
      deviceId: "dev-1",
      productId: "ASK-7KQ2MX",
      onCodeSent: (to) => {
        sent.push(to);
      },
    });
    expect(outcome).toEqual({ ok: true });
    expect(log).toEqual(["connect dev-1", "readNonce", "bond", "readProof"]);
    expect(calls).toEqual([`code ASK-7KQ2MX ${NONCE_1}`, `paired ASK-7KQ2MX ${NONCE_1} ${"ab".repeat(32)}`]);
    expect(sent).toEqual(["e***@ask.test"]);
  });

  it("a_full_locket_stops_before_bonding", async () => {
    const { link, log } = fakeLink();
    const { api } = fakeApi({ code: new ApiError(409, "locket_full", "This locket allows 1 person.") });
    const outcome = await pairLocket({ link, api, deviceId: "d", productId: "ASK-7KQ2MX", onCodeSent: () => {} });
    expect(outcome).toEqual({ ok: false, reason: "locket_full", message: "This locket allows 1 person." });
    expect(log).not.toContain("bond");
    expect(log.at(-1)).toBe("disconnect");
  });

  it("a_failed_bond_asks_for_a_new_code", async () => {
    const { link, log } = fakeLink({ bondFails: true });
    const { api, calls } = fakeApi();
    const first = await pairLocket({ link, api, deviceId: "d", productId: "ASK-7KQ2MX", onCodeSent: () => {} });
    expect(first.ok).toBe(false);
    expect(first.ok === false && first.reason).toBe("wrong_code");
    expect(log.at(-1)).toBe("disconnect");
    await pairLocket({ link, api, deviceId: "d", productId: "ASK-7KQ2MX", onCodeSent: () => {} });
    expect(calls[0]).not.toBe(calls[1]); // a new nonce, so a new code
    expect(calls.every((c) => c.startsWith("code"))).toBe(true);
  });

  it("a_rejected_proof_is_reported", async () => {
    const { link } = fakeLink();
    const { api } = fakeApi({ confirm: new ApiError(403, "pairing_proof_invalid", "The locket did not confirm.") });
    const outcome = await pairLocket({ link, api, deviceId: "d", productId: "ASK-7KQ2MX", onCodeSent: () => {} });
    expect(outcome).toMatchObject({ ok: false, reason: "proof_rejected" });
  });

  it("an_empty_proof_is_not_sent_to_the_server", async () => {
    const { link } = fakeLink();
    link.readProof = async () => "";
    const { api, calls } = fakeApi();
    const outcome = await pairLocket({ link, api, deviceId: "d", productId: "ASK-7KQ2MX", onCodeSent: () => {} });
    expect(outcome).toMatchObject({ ok: false, reason: "proof_rejected" });
    expect(outcome.ok === false && outcome.message).toContain("forget the ASK-");
    expect(calls.every((c) => c.startsWith("code"))).toBe(true); // confirmPaired was never called
  });

  it("an_expired_request_is_reported", async () => {
    const { link } = fakeLink();
    const { api } = fakeApi({ confirm: new ApiError(404, "pairing_request_not_found", "Expired.") });
    const outcome = await pairLocket({ link, api, deviceId: "d", productId: "ASK-7KQ2MX", onCodeSent: () => {} });
    expect(outcome).toMatchObject({ ok: false, reason: "expired" });
  });

  it("no_connection_is_a_network_problem", async () => {
    const { link } = fakeLink();
    const { api } = fakeApi({ code: new ApiError(0, "network", "No connection.") });
    const outcome = await pairLocket({ link, api, deviceId: "d", productId: "ASK-7KQ2MX", onCodeSent: () => {} });
    expect(outcome).toMatchObject({ ok: false, reason: "network" });
  });
});
