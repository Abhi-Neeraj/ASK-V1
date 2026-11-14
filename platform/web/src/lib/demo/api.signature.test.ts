import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { demoApi } from "@/lib/demo/api";
import { signInDemo } from "@/test/utils";

/** The demo API pretends the network takes a moment, which fake timers have to be told to skip. */
async function settle<T>(pending: Promise<T>): Promise<T> {
  await vi.advanceTimersByTimeAsync(1000);
  return pending;
}

const wav = new Blob([new Uint8Array([82, 73, 70, 70])], { type: "audio/wav" });

describe("the demo's voice signatures and summaries", () => {
  beforeEach(() => {
    signInDemo();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("lists people with their signature status", async () => {
    const people = await settle(demoApi.people.list());
    const priya = people.find((p) => p.id === "per_1")!;
    expect(priya.signature).toMatchObject({ ready: true, sampleCount: 3 });
    expect(priya.signature?.pitch?.baseHz).toBeGreaterThan(priya.signature!.pitch!.lowHz);
    expect(people.find((p) => p.id === "per_3")?.signature).toMatchObject({ ready: false, sampleCount: 0 });
  });

  it("counts a signature sample for someone who has consented, and refuses without consent", async () => {
    const added = await settle(demoApi.people.addSignatureSample("per_3", wav).catch((e: unknown) => e));
    expect(added).toBeInstanceOf(ApiError);
    expect((added as ApiError).status).toBe(409);

    await settle(demoApi.people.recordConsent("per_3", true));
    const first = await settle(demoApi.people.addSignatureSample("per_3", wav));
    expect(first.signature).toMatchObject({ sampleCount: 1, ready: false });
    await settle(demoApi.people.addSignatureSample("per_3", wav));
    const third = await settle(demoApi.people.addSignatureSample("per_3", wav));
    expect(third.signature).toMatchObject({ sampleCount: 3, ready: true });
  });

  it("deletes the signature with the voice, and with consent", async () => {
    await settle(demoApi.people.removeVoice("per_3"));
    const after = (await settle(demoApi.people.list())).find((p) => p.id === "per_3");
    expect(after?.signature).toMatchObject({ sampleCount: 0, ready: false, pitch: null });
  });

  it("does not pretend to match a voice: that needs a real server", async () => {
    const result = await settle(demoApi.people.matchSignature(wav).catch((e: unknown) => e));
    expect(result).toBeInstanceOf(ApiError);
    expect((result as ApiError).message).toMatch(/not part of the demo/i);
  });

  it("gives a completed recording its speakers and a summary, and says the analysis is demo data", async () => {
    const detail = await settle(demoApi.recordings.get("rec_001"));
    expect(detail.analysis).not.toBeNull();
    expect(detail.analysis!.voices.length).toBeGreaterThan(0);
    for (const voice of detail.analysis!.voices) expect(voice.pitch?.baseHz).toBeGreaterThan(0);
    expect(detail.analysis!.summary.narrative.join(" ")).toMatch(/demo data/i);
  });

  it("has no analysis for a recording that has no transcript yet", async () => {
    const queued = await settle(demoApi.recordings.get("rec_003"));
    expect(queued.transcript).toBeNull();
    expect(queued.analysis).toBeNull();
  });
});
