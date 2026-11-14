import { describe, expect, it } from "vitest";
import type { RecordingDetail, TranscriptSegment } from "@/lib/api/types";
import {
  PLACEHOLDER_NOTICE,
  UNKNOWN_SPEAKER,
  speakerName,
  transcriptToJson,
  transcriptToText,
} from "@/lib/transcript-export";

function segment(over: Partial<TranscriptSegment>): TranscriptSegment {
  return {
    id: "s1",
    startMs: 2100,
    endMs: 5800,
    speakerLabel: "SPEAKER_00",
    person: { id: "p1", name: "Priya Nair" },
    confidence: 0.93,
    text: "Good morning, everyone.",
    needsReview: false,
    corrected: false,
    ...over,
  };
}

function detail(segments: TranscriptSegment[]): RecordingDetail {
  return {
    id: "rec_001",
    deviceId: "dev_1",
    deviceName: "Locket A7F2",
    simulated: false,
    startedAt: "2026-10-25T10:00:00Z",
    endedAt: "2026-10-25T10:00:10Z",
    durationMs: 10_000,
    sizeBytes: 320_000,
    format: "WAV",
    uploadedAt: "2026-10-25T10:00:11Z",
    state: "completed",
    retryCount: 0,
    error: null,
    snippet: null,
    speakers: [],
    placeholder: false,
    audioUrl: null,
    failureDetail: null,
    transcript: {
      transcriptionModel: "whisper",
      transcriptionModelVersion: "1",
      diarizationModel: "pyannote",
      diarizationModelVersion: "1",
      reviewStatus: "none",
      placeholder: false,
      separationExperimental: false,
      language: "en",
      segments,
    },
  };
}

describe("speakerName", () => {
  it("never names a speaker without a matched person", () => {
    expect(speakerName(segment({ person: null }))).toBe(UNKNOWN_SPEAKER);
    expect(speakerName(segment({}))).toBe("Priya Nair");
  });
});

describe("transcriptToText", () => {
  it("writes one timestamped, speaker-labelled line per segment", () => {
    const text = transcriptToText(
      detail([
        segment({}),
        segment({ id: "s2", startMs: 9500, endMs: 11_000, person: null, text: "I'll send the files." }),
      ]),
    );
    expect(text.split("\n")).toEqual([
      "00:00:02.100–00:00:05.800 | Priya Nair | Good morning, everyone.",
      "00:00:09.500–00:00:11.000 | Unknown speaker | I'll send the files.",
    ]);
  });
  it("is empty when there is no transcript", () => {
    const d = detail([]);
    d.transcript = null;
    expect(transcriptToText(d)).toBe("");
  });

  it("opens with a notice when the text is placeholder, so a saved copy cannot be mistaken for real", () => {
    const d = detail([segment({ text: "[Placeholder 1]" })]);
    d.transcript!.placeholder = true;
    const lines = transcriptToText(d).split("\n");
    expect(lines[0]).toBe(PLACEHOLDER_NOTICE);
    expect(lines[1]).toBe("");
    expect(lines).toHaveLength(3);
  });

  it("has no notice on a real transcript", () => {
    expect(transcriptToText(detail([segment({})]))).not.toContain("NOTICE");
  });
});

describe("transcriptToJson", () => {
  it("carries timing, speaker, confidence and correction state", () => {
    const parsed = JSON.parse(transcriptToJson(detail([segment({ corrected: true })])));
    expect(parsed.recordingId).toBe("rec_001");
    expect(parsed.segments[0]).toMatchObject({
      startMs: 2100,
      endMs: 5800,
      speaker: "Priya Nair",
      confidence: 0.93,
      corrected: true,
    });
  });

  it("states plainly whether it is placeholder text and whether speakers were separated", () => {
    const real = JSON.parse(transcriptToJson(detail([segment({})])));
    expect(real).toMatchObject({ placeholder: false, notice: null, speakersSeparated: true, language: "en" });

    const stub = detail([segment({})]);
    stub.transcript = { ...stub.transcript!, placeholder: true, diarizationModel: null };
    expect(JSON.parse(transcriptToJson(stub))).toMatchObject({
      placeholder: true,
      notice: PLACEHOLDER_NOTICE,
      speakersSeparated: false,
    });
  });

  it("does not name the models that produced it", () => {
    const text = transcriptToJson(detail([segment({})]));
    expect(text).not.toMatch(/whisper|pyannote/i);
    expect(text).not.toContain("transcriptionModel");
    expect(text).not.toContain("diarizationModel");
  });
});

describe("exports with a summary", () => {
  const withAnalysis = (): RecordingDetail => {
    const d = detail([segment({})]);
    d.analysis = {
      engine: "lexical",
      voices: [
        {
          label: "SPEAKER_00",
          title: "Priya Nair",
          name: "Priya Nair",
          talkMs: 5000,
          share: 1,
          turns: 1,
          pitch: { baseHz: 211.7, lowHz: 171.2, highHz: 268.4 },
          matchedBy: "signature",
          matchScore: 0.74,
        },
      ],
      summary: {
        narrative: ["This recording is 10 seconds long with one speaker (Priya Nair)."],
        sections: [
          {
            key: "commitments",
            title: "Commitments",
            total: 1,
            lines: [
              {
                activity: "COMMITMENT",
                activityLabel: "Commitment",
                text: "send the report",
                said: "I will send the report by Friday.",
                speaker: "Priya Nair",
                owner: "Priya Nair",
                due: "by Friday",
                open: null,
                atMs: 3000,
                segmentIndex: 0,
                confidence: 0.8,
              },
            ],
          },
        ],
        sentences: 1,
        truncated: false,
      },
      items: [],
      counts: { COMMITMENT: 1 },
      createdAt: "2026-11-12T10:00:00Z",
    };
    return d;
  };

  it("adds the summary and the speakers' pitch to the text export, and says how it was built", () => {
    const text = transcriptToText(withAnalysis());
    expect(text).toContain("SUMMARY (built from the transcript by fixed rules, with no generative AI)");
    expect(text).toContain("Commitments (1)");
    expect(text).toContain("- [0:03] send the report  owner: Priya Nair  due: by Friday");
    expect(text).toContain("- Priya Nair: 100% of the speech, base 212 Hz, low 171 Hz, high 268 Hz");
  });

  it("leaves the text export exactly as it was when there is no summary", () => {
    expect(transcriptToText(detail([segment({})]))).toBe("00:00:02.100–00:00:05.800 | Priya Nair | Good morning, everyone.");
  });

  it("adds the summary and voices to the JSON export, without how alike a voice was", () => {
    const parsed = JSON.parse(transcriptToJson(withAnalysis()));
    expect(parsed.summary.builtBy).toMatch(/no generative AI/);
    expect(parsed.summary.sections[0].lines[0]).toMatchObject({ text: "send the report", owner: "Priya Nair", due: "by Friday" });
    expect(parsed.voices[0]).toMatchObject({ speaker: "Priya Nair", pitch: { baseHz: 211.7 } });
    expect(JSON.stringify(parsed)).not.toContain("matchScore");
    expect(JSON.stringify(parsed)).not.toContain("0.74");
  });

  it("has no summary keys when there is none", () => {
    const parsed = JSON.parse(transcriptToJson(detail([segment({})])));
    expect(parsed).not.toHaveProperty("summary");
    expect(parsed).not.toHaveProperty("voices");
  });
});
