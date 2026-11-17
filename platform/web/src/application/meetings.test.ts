import { describe, expect, it } from "vitest";
import { groupMeetings } from "@/application/meetings";
import type { RecordingOut } from "@/infrastructure/api/types";

function recording(over: Partial<RecordingOut> & { id: string; startedAt: string }): RecordingOut {
  return {
    deviceId: "dev-1",
    deviceName: "Locket",
    simulated: false,
    endedAt: over.startedAt,
    durationMs: 60_000,
    sizeBytes: 1,
    format: "wav",
    uploadedAt: over.startedAt,
    part: null,
    sessionRef: null,
    partCount: null,
    meetingDurationMs: null,
    meetingStartedAt: null,
    state: "completed",
    retryCount: 0,
    error: null,
    snippet: null,
    speakers: [],
    placeholder: false,
    ...over,
  };
}

const part = (n: number, over: Partial<RecordingOut> = {}) =>
  recording({
    id: `part-${n}`,
    startedAt: `2026-11-17T10:0${n}:00Z`,
    part: n,
    sessionRef: "MEETING000000001",
    ...over,
  });

describe("groupMeetings", () => {
  it("shows the parts of one meeting as a single row led by the newest part", () => {
    const rows = groupMeetings([part(3), part(2), part(1)]); // newest first, as the server lists them
    expect(rows).toHaveLength(1);
    expect(rows[0].recording.id).toBe("part-3");
    expect(rows[0].parts).toBe(3);
    expect(rows[0].durationMs).toBe(180_000);
    expect(rows[0].startedAt).toBe("2026-11-17T10:01:00Z");
  });

  it("takes the totals from the server, which counts parts that are not on this page", () => {
    const rows = groupMeetings([
      part(3, { partCount: 300, meetingDurationMs: 17_000_000, meetingStartedAt: "2026-11-17T05:00:00Z" }),
    ]);
    expect(rows[0]).toMatchObject({ parts: 300, durationMs: 17_000_000, startedAt: "2026-11-17T05:00:00Z" });
  });

  it("leaves ordinary recordings alone and keeps the list order", () => {
    const alone = recording({ id: "alone", startedAt: "2026-11-17T11:00:00Z" });
    const rows = groupMeetings([alone, part(2), part(1)]);
    expect(rows.map((r) => r.recording.id)).toEqual(["alone", "part-2"]);
    expect(rows[0].parts).toBe(1);
  });

  it("two lockets that happen to share a meeting reference stay separate", () => {
    const rows = groupMeetings([part(2), part(1, { deviceId: "dev-2" })]);
    expect(rows).toHaveLength(2);
  });

  it("a part still being worked on makes the meeting look busy, and a failed part shows as failed", () => {
    expect(groupMeetings([part(3, { state: "transcribing" }), part(2), part(1)])[0].state).toBe("transcribing");
    expect(groupMeetings([part(3), part(2, { state: "failed" }), part(1)])[0].state).toBe("failed");
    expect(groupMeetings([part(2), part(1, { state: "needs_review" })])[0].state).toBe("needs_review");
  });

  it("uses the first words of the meeting as its snippet", () => {
    const rows = groupMeetings([part(2, { snippet: "second" }), part(1, { snippet: "first words" })]);
    expect(rows[0].snippet).toBe("first words");
  });
});
