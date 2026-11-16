// Pairing a phone with a locket (spec 5.2): read a fresh number from the locket, have the server email the
// six-digit code for it, let the operating system's pairing box take the code, then send the server the
// locket's proof that the pairing really happened. Any failure disconnects, so the next attempt starts with a
// new number (and so a new code): an old code is never retried.

import { type LocketLink, toHex } from "@/domain/locket/link";
import { ApiError } from "@/infrastructure/api/client";
import type { CodeSent } from "@/infrastructure/api/types";

export type PairApi = {
  requestPairingCode(productId: string, nonce: string): Promise<CodeSent>;
  confirmPaired(productId: string, nonce: string, proof: string): Promise<void>;
};

export type PairFailure = "locket_full" | "wrong_code" | "proof_rejected" | "expired" | "network";
export type PairOutcome = { ok: true } | { ok: false; reason: PairFailure; message: string };

function failure(error: unknown, fallback: PairFailure): PairOutcome {
  if (error instanceof ApiError) {
    if (error.code === "locket_full") return { ok: false, reason: "locket_full", message: error.message };
    if (error.code === "pairing_proof_invalid") return { ok: false, reason: "proof_rejected", message: error.message };
    if (error.code === "pairing_request_not_found") return { ok: false, reason: "expired", message: error.message };
    if (error.status === 0) return { ok: false, reason: "network", message: error.message };
    return { ok: false, reason: fallback, message: error.message };
  }
  return {
    ok: false,
    reason: fallback,
    message:
      fallback === "wrong_code"
        ? "The code did not match. Ask for a new code and try again."
        : "Pairing did not finish. Keep the locket next to your phone and try again.",
  };
}

export async function pairLocket({
  link,
  api,
  deviceId,
  productId,
  onCodeSent,
}: {
  link: LocketLink;
  api: PairApi;
  deviceId: string;
  productId: string;
  onCodeSent: (sentTo: string) => void;
}): Promise<PairOutcome> {
  let stage: PairFailure = "network";
  try {
    await link.connect(deviceId);
    const nonce = toHex(await link.readNonce());
    const sent = await api.requestPairingCode(productId, nonce);
    onCodeSent(sent.sentTo);
    stage = "wrong_code";
    await link.bond();
    stage = "proof_rejected";
    const proof = await link.readProof();
    await api.confirmPaired(productId, nonce, proof);
    return { ok: true };
  } catch (error) {
    await link.disconnect().catch(() => undefined);
    return failure(error, stage);
  }
}
