import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RecordingSummary, topicStartingAt } from "@/components/dashboard/recording-insights";
import type { Analysis, AnalysisItem } from "@/lib/api/types";
import { expectNoA11yViolations } from "@/test/utils";

const item = (over: Partial<AnalysisItem>): AnalysisItem => ({
  segmentIndex: 0,
  startMs: 0,
  endMs: 3000,
  speaker: "Priya Nair",
  what: "I will send the revised budget to everyone by Friday.",
  context: { previous: null, previousSpeaker: null, topic: ["budget"], group: 0, newContext: true, similarity: null },
  activity: "COMMITMENT",
  activityLabel: "Commitment",
  secondary: [],
  confidence: 0.84,
  task: "send the revised budget to everyone",
  owner: "Priya Nair",
  due: "by Friday",
  dated: true,
  verb: "send",
  keywords: ["revised", "budget", "everyone"],
  open: null,
  ...over,
});

const base: Analysis = {
  engine: "semantic",
  voices: [],
  summary: { narrative: ["This recording is 20 seconds long with one speaker (Priya Nair)."], sections: [], sentences: 3, truncated: false },
  items: [
    item({}),
    item({
      segmentIndex: 1,
      startMs: 4000,
      what: "Please check the totals before the call.",
      activity: "ACTION",
      activityLabel: "Action item",
      task: "check the totals before the call",
      due: null,
      dated: false,
      verb: "check",
      keywords: ["totals", "call"],
      context: { previous: "x", previousSpeaker: "Priya Nair", topic: [], group: 0, newContext: false, similarity: 0.52 },
    }),
    item({
      segmentIndex: 2,
      startMs: 9000,
      what: "Next, the office move is planned for March.",
      activity: "NOTE",
      activityLabel: "Note",
      task: null,
      owner: null,
      due: null,
      dated: false,
      verb: null,
      keywords: ["office", "move", "march"],
      context: { previous: "y", previousSpeaker: "Priya Nair", topic: [], group: 1, newContext: true, similarity: 0.12 },
    }),
  ],
  counts: { COMMITMENT: 1, ACTION: 1, NOTE: 1 },
  contexts: [
    { group: 0, startMs: 0, endMs: 8000, sentences: 2, keywords: ["budget", "totals"] },
    { group: 1, startMs: 9000, endMs: 12000, sentences: 1, keywords: ["office", "move"] },
  ],
  contextThreshold: 0.3,
  createdAt: "2026-11-13T10:00:00Z",
};

function list() {
  return screen.getByRole("list", { name: "Every sentence, analysed", hidden: true });
}

describe("the sentence-by-sentence list with topics", () => {
  it("marks where each new topic starts, with its main words", () => {
    render(<RecordingSummary analysis={base} />);
    const marks = within(list()).getAllByRole("listitem", { hidden: true }).filter((li) => li.getAttribute("aria-label")?.startsWith("New topic"));
    expect(marks.map((m) => m.getAttribute("aria-label"))).toEqual(["New topic 1", "New topic 2"]);
    expect(marks[0]).toHaveTextContent("budget, totals");
    expect(marks[1]).toHaveTextContent("office, move");
  });

  it("marks a task that has a deadline, and shows its action verb and main words", () => {
    render(<RecordingSummary analysis={base} />);
    const first = within(list()).getByText("I will send the revised budget to everyone by Friday.").closest("li")!;
    expect(first).toHaveTextContent("Has a deadline");
    expect(first).toHaveTextContent("Action verb");
    expect(first).toHaveTextContent("send");
    expect(first).toHaveTextContent("Main words");
    expect(first).toHaveTextContent("revised, budget, everyone");
    const second = within(list()).getByText("Please check the totals before the call.").closest("li")!;
    expect(second).not.toHaveTextContent("Has a deadline");
  });

  it("says nothing about topics where none were worked out", () => {
    const withoutContexts: Analysis = {
      ...base,
      engine: "lexical",
      contexts: [],
      contextThreshold: null,
      items: base.items.map((i) => ({ ...i, context: { ...i.context, group: null, newContext: null, similarity: null } })),
    };
    render(<RecordingSummary analysis={withoutContexts} />);
    expect(screen.queryByLabelText(/New topic/, { selector: "li" })).not.toBeInTheDocument();
  });

  it("copes with an older server that sends none of the new fields", () => {
    const older = {
      ...base,
      contexts: undefined,
      contextThreshold: undefined,
      items: base.items.map((original) => {
        const older: Partial<AnalysisItem> = { ...original };
        delete older.dated;
        delete older.verb;
        delete older.keywords;
        return {
          ...older,
          context: {
            previous: original.context.previous,
            previousSpeaker: original.context.previousSpeaker,
            topic: original.context.topic,
          },
        };
      }),
    } as unknown as Analysis;
    render(<RecordingSummary analysis={older} />);
    expect(screen.getByRole("heading", { name: "Summary" })).toBeInTheDocument();
    expect(screen.queryByText("Has a deadline")).not.toBeInTheDocument();
  });

  it("finds the topic an item opens", () => {
    expect(topicStartingAt(base.items[0]!, base)).toEqual({ number: 1, keywords: ["budget", "totals"] });
    expect(topicStartingAt(base.items[1]!, base)).toBeNull(); // continues the topic
    expect(topicStartingAt(base.items[2]!, base)).toEqual({ number: 2, keywords: ["office", "move"] });
  });

  it("does not name any product, library or model", () => {
    const { container } = render(<RecordingSummary analysis={base} />);
    expect(container.textContent).not.toMatch(/transformer|minilm|sentence-|hugging|whisper|pyannote|speechbrain/i);
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<RecordingSummary analysis={base} />);
    await expectNoA11yViolations(container);
  });
});
