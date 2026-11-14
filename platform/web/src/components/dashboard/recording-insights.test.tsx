import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RecordingSummary, RecordingVoices, engineNote } from "@/components/dashboard/recording-insights";
import type { Analysis, AnalysisItem, RecordingVoice, SummaryLine } from "@/lib/api/types";
import { expectNoA11yViolations } from "@/test/utils";

const voices: RecordingVoice[] = [
  {
    label: "SPEAKER_00",
    title: "Priya Nair",
    name: "Priya Nair",
    talkMs: 61_000,
    share: 0.58,
    turns: 4,
    pitch: { baseHz: 211.7, lowHz: 171.2, highHz: 268.4 },
    matchedBy: "signature",
    matchScore: 0.74,
  },
  {
    label: "SPEAKER_01",
    title: "Speaker 2",
    name: null,
    talkMs: 44_000,
    share: 0.42,
    turns: 3,
    pitch: null,
    matchedBy: null,
    matchScore: null,
  },
];

const send: SummaryLine = {
  activity: "COMMITMENT",
  activityLabel: "Commitment",
  text: "send the revised budget to everyone",
  said: "I will send the revised budget to everyone before the end of the day tomorrow.",
  speaker: "Priya Nair",
  owner: "Priya Nair",
  due: "before the end of the day tomorrow",
  open: null,
  atMs: 12_000,
  segmentIndex: 2,
  confidence: 0.84,
};
const question: SummaryLine = {
  activity: "QUESTION",
  activityLabel: "Question",
  text: "Who is responsible for the documentation?",
  said: "Who is responsible for the documentation?",
  speaker: "Speaker 2",
  owner: null,
  due: null,
  open: true,
  atMs: 70_000,
  segmentIndex: 7,
  confidence: 0.94,
};

const item = (over: Partial<AnalysisItem>): AnalysisItem => ({
  segmentIndex: 2,
  startMs: 12_000,
  endMs: 16_000,
  speaker: "Priya Nair",
  what: send.said,
  context: { previous: "It arrives on Tuesday.", previousSpeaker: "Speaker 2", topic: ["budget", "delivery"] },
  activity: "COMMITMENT",
  activityLabel: "Commitment",
  secondary: [],
  confidence: 0.84,
  task: send.text,
  owner: "Priya Nair",
  due: send.due,
  open: null,
  ...over,
});

const analysis: Analysis = {
  engine: "lexical",
  voices,
  summary: {
    narrative: [
      "This recording is 1 min 45 s long with 2 speakers: Priya Nair (58%) and Speaker 2 (42%).",
      "It contains 1 commitment and 1 question. 1 question is still unanswered.",
      "Priya Nair to send the revised budget to everyone (before the end of the day tomorrow).",
    ],
    sections: [
      { key: "commitments", title: "Commitments", total: 1, lines: [send] },
      { key: "questions", title: "Questions", total: 3, lines: [question] },
    ],
    sentences: 2,
    truncated: false,
  },
  items: [item({}), item({ segmentIndex: 7, startMs: 70_000, speaker: "Speaker 2", what: question.said, activity: "QUESTION", activityLabel: "Question", task: null, owner: null, due: null, open: true, context: { previous: null, previousSpeaker: null, topic: [] } })],
  counts: { COMMITMENT: 1, QUESTION: 1 },
  createdAt: "2026-11-12T10:00:00Z",
};

describe("RecordingVoices", () => {
  it("shows each speaker's share of the speech and their base, low and high pitch", () => {
    render(<RecordingVoices analysis={analysis} />);
    const priya = screen.getByLabelText("Pitch of Priya Nair");
    expect(priya).toHaveTextContent("Base pitch");
    expect(priya).toHaveTextContent("212 Hz");
    expect(priya).toHaveTextContent("Low pitch");
    expect(priya).toHaveTextContent("171 Hz");
    expect(priya).toHaveTextContent("High pitch");
    expect(priya).toHaveTextContent("268 Hz");
    expect(screen.getByRole("meter", { name: "Priya Nair's share of the speech" })).toHaveAttribute("aria-valuenow", "58");
    expect(screen.getByText(/58% of the speech · 1:01 · 4 turns/)).toBeInTheDocument();
  });

  it("names a speaker only where they were matched, and says how", () => {
    render(<RecordingVoices analysis={analysis} />);
    expect(screen.getByText("Matched by voice signature")).toBeInTheDocument();
    expect(screen.getByText("Not matched to anyone")).toBeInTheDocument();
    expect(screen.getByText("Speaker 2")).toBeInTheDocument();
  });

  it("shows how alike the voice was only when the server sent it (managers)", () => {
    const { rerender } = render(<RecordingVoices analysis={analysis} />);
    expect(screen.getByText("74% alike")).toBeInTheDocument();
    rerender(<RecordingVoices analysis={{ ...analysis, voices: voices.map((v) => ({ ...v, matchScore: null })) }} />);
    expect(screen.queryByText("74% alike")).not.toBeInTheDocument();
  });

  it("says when there was too little speech to measure pitch", () => {
    render(<RecordingVoices analysis={analysis} />);
    expect(screen.getByText(/too little clear speech to measure pitch/i)).toBeInTheDocument();
  });

  it("says pitch never names anyone", () => {
    render(<RecordingVoices analysis={analysis} />);
    expect(screen.getByText(/pitch alone never names anyone/i)).toBeInTheDocument();
  });

  it("renders nothing for a recording with no speakers", () => {
    const { container } = render(<RecordingVoices analysis={{ ...analysis, voices: [] }} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("RecordingSummary", () => {
  it("opens with the overview and then the lines about what was decided and asked", () => {
    render(<RecordingSummary analysis={analysis} />);
    expect(screen.getByText(/This recording is 1 min 45 s long with 2 speakers/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Commitments/ })).toBeInTheDocument();
    expect(screen.getAllByText("send the revised budget to everyone").length).toBeGreaterThan(0);
  });

  it("shows owner, due date and who said it and when", () => {
    render(<RecordingSummary analysis={analysis} />);
    const meta = screen.getAllByText(/before the end of the day tomorrow/);
    expect(meta.length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Priya Nair at 0:12/).length).toBeGreaterThan(0);
  });

  it("flags a question nobody answered, and says how many more are in the full list", () => {
    render(<RecordingSummary analysis={analysis} />);
    expect(screen.getAllByText("Unanswered").length).toBeGreaterThan(0);
    expect(screen.getByText(/2 more in the sentence-by-sentence list below/)).toBeInTheDocument();
  });

  it("lists every sentence with what was said, its context, task and owner", () => {
    render(<RecordingSummary analysis={analysis} />);
    const list = screen.getByRole("list", { name: "Every sentence, analysed", hidden: true });
    const first = within(list).getAllByRole("listitem", { hidden: true })[0]!;
    expect(first).toHaveTextContent("I will send the revised budget to everyone before the end of the day tomorrow.");
    expect(first).toHaveTextContent("Task");
    expect(first).toHaveTextContent("Owner");
    expect(first).toHaveTextContent("After Speaker 2");
    expect(first).toHaveTextContent("about budget, delivery");
  });

  it("says how it was built without naming any product", () => {
    const { container } = render(<RecordingSummary analysis={analysis} />);
    expect(screen.getByText(/by fixed rules and templates, with no generative AI/i)).toBeInTheDocument();
    expect(screen.getByText(/matched to activities by key phrases and shared words/i)).toBeInTheDocument();
    expect(engineNote("semantic")).toMatch(/by meaning and by key phrases/);
    for (const engine of ["semantic", "lexical"] as const) {
      expect(engineNote(engine)).not.toMatch(/transformer|minilm|sentence-|hugging|bert|openai|gpt|claude/i);
    }
    expect(container.textContent).not.toMatch(/whisper|pyannote|speechbrain|ecapa|transformer|hugging/i);
  });

  it("says when only the first part of a long recording was analysed", () => {
    render(<RecordingSummary analysis={{ ...analysis, summary: { ...analysis.summary, truncated: true } }} />);
    expect(screen.getByText(/only its first part was analysed/i)).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <>
        <RecordingVoices analysis={analysis} />
        <RecordingSummary analysis={analysis} />
      </>,
    );
    await expectNoA11yViolations(container);
  });
});
