import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { demoApi } from "@/lib/demo/api";
import { signInDemo } from "@/test/utils";

/** The demo API pretends the network takes a moment; fake timers let these tests skip it. */
async function settled<T>(pending: Promise<T>): Promise<T> {
  await vi.advanceTimersByTimeAsync(500);
  return pending;
}

/** Runs a call that is expected to be refused, and returns the refusal. */
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

describe("the demo processing status", () => {
  beforeEach(() => {
    signInDemo();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("describes a full worker, because demo data pretends to be one", async () => {
    const status = await settled(demoApi.processing.status());
    expect(status).toMatchObject({
      available: true,
      placeholder: false,
      transcription: true,
      diarization: true,
      speakerMatching: true,
      enrollmentAvailable: true,
    });
  });

  it("counts what is waiting and what is running from the demo recordings", async () => {
    const status = await settled(demoApi.processing.status());
    // The seed data has one queued recording, and one each transcribing and diarizing.
    expect(status.queued).toBe(1);
    expect(status.running).toBe(2);
  });

  it("is refused without a session", async () => {
    window.localStorage.clear();
    expect((await refusal(demoApi.processing.status())).status).toBe(401);
  });
});

describe("demo reprocessing", () => {
  beforeEach(() => {
    signInDemo();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("refuses a recording that is already being worked on", async () => {
    const error = await refusal(demoApi.recordings.reprocess("rec_002")); // transcribing
    expect(error.status).toBe(409);
    expect(error.code).toBe("already_processing");
  });

  it("refuses a recording that does not exist", async () => {
    expect((await refusal(demoApi.recordings.reprocess("rec_999"))).status).toBe(404);
  });

  it("queues a finished recording again and drops the old transcript with its corrections", async () => {
    const before = await settled(demoApi.recordings.get("rec_001"));
    expect(before.transcript).not.toBeNull();
    const edit = before.transcript!.segments[0]!;
    await settled(demoApi.recordings.updateTranscript("rec_001", [{ segmentId: edit.id, text: "Corrected by hand." }]));

    const queued = await settled(demoApi.recordings.reprocess("rec_001"));
    expect(queued.state).toBe("queued");
    expect(queued.retryCount).toBe(1);
    expect(queued.error).toBeNull();

    const after = await settled(demoApi.recordings.get("rec_001"));
    expect(after.state).toBe("queued");
    expect(after.transcript).toBeNull();
  });

  it("clears the reason a failed recording failed, and counts the retry", async () => {
    const queued = await settled(demoApi.recordings.reprocess("rec_007")); // failed, 3 earlier tries
    expect(queued.state).toBe("queued");
    expect(queued.error).toBeNull();
    expect(queued.retryCount).toBe(4);
  });

  it("refuses to queue the same recording twice", async () => {
    await settled(demoApi.recordings.reprocess("rec_010"));
    const error = await refusal(demoApi.recordings.reprocess("rec_010"));
    expect(error.code).toBe("already_processing");
  });
});

describe("demo voice enrollment", () => {
  beforeEach(() => {
    signInDemo();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const wav = new Blob(["x"], { type: "audio/wav" });

  it("will not take a voice before consent is on record", async () => {
    const error = await refusal(demoApi.people.enrollSample("per_3", wav)); // consent pending
    expect(error.status).toBe(409);
    expect(error.code).toBe("consent_required");
  });

  it("will not take a voice from someone who withdrew consent", async () => {
    expect((await refusal(demoApi.people.enrollSample("per_4", wav))).code).toBe("consent_required");
  });

  it("refuses an unknown person", async () => {
    expect((await refusal(demoApi.people.enrollSample("per_999", wav))).status).toBe(404);
  });

  it("is ready only after the required number of samples", async () => {
    await settled(demoApi.people.recordConsent("per_3", true));
    const states = [];
    for (let i = 0; i < 3; i += 1) {
      const person = await settled(demoApi.people.enrollSample("per_3", wav));
      states.push([person.sampleCount, person.enrolled]);
    }
    expect(states).toEqual([
      [1, false],
      [2, false],
      [3, true],
    ]);
  });

  it("removes a voice but keeps the person and their consent", async () => {
    await settled(demoApi.people.removeVoice("per_3"));
    const person = (await settled(demoApi.people.list())).find((p) => p.id === "per_3")!;
    expect(person).toMatchObject({ sampleCount: 0, enrolled: false, enrolledAt: null, consentStatus: "granted" });
  });

  it("deletes the voice when consent is withdrawn", async () => {
    const person = await settled(demoApi.people.recordConsent("per_1", false));
    expect(person).toMatchObject({ consentStatus: "revoked", sampleCount: 0, enrolled: false });
  });

  it("refuses to remove the voice of someone who does not exist", async () => {
    expect((await refusal(demoApi.people.removeVoice("per_999"))).status).toBe(404);
  });
});
