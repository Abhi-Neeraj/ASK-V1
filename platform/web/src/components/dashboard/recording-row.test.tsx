import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RecordingRow } from "@/components/dashboard/recording-row";
import { demoRecordings } from "@/lib/demo/data";

const recording = demoRecordings[0]!;

describe("RecordingRow", () => {
  it("labels a recording made by a simulated locket", () => {
    render(
      <ul>
        <RecordingRow recording={{ ...recording, simulated: true }} />
      </ul>,
    );
    expect(screen.getByText("Simulated")).toBeInTheDocument();
  });

  it("does not label a real recording", () => {
    render(
      <ul>
        <RecordingRow recording={{ ...recording, simulated: false }} />
      </ul>,
    );
    expect(screen.queryByText("Simulated")).not.toBeInTheDocument();
    expect(screen.queryByText("Placeholder")).not.toBeInTheDocument();
  });

  it.each(["uploaded", "queued", "retrying"] as const)("says a %s recording is waiting, not being processed", (state) => {
    render(
      <ul>
        <RecordingRow recording={{ ...recording, state, snippet: null, speakers: [] }} />
      </ul>,
    );
    expect(screen.getByText(/waiting to be processed/i)).toBeInTheDocument();
    expect(screen.queryByText(/^processing\./i)).not.toBeInTheDocument();
  });

  it("says a recording in a running stage is being processed", () => {
    render(
      <ul>
        <RecordingRow recording={{ ...recording, state: "transcribing", snippet: null, speakers: [] }} />
      </ul>,
    );
    expect(screen.getByText(/^processing\./i)).toBeInTheDocument();
  });

  it("labels a transcript that is placeholder text, in the list as well as on its own page", () => {
    render(
      <ul>
        <RecordingRow recording={{ ...recording, placeholder: true }} />
      </ul>,
    );
    expect(screen.getByText("Placeholder")).toBeInTheDocument();
  });

  it("numbers a part of a longer recording, and says nothing for an ordinary one", () => {
    const { rerender } = render(
      <ul>
        <RecordingRow recording={{ ...recording, part: 2 }} />
      </ul>,
    );
    expect(screen.getByText(/· Part 2/)).toBeInTheDocument();
    rerender(
      <ul>
        <RecordingRow recording={{ ...recording, part: null }} />
      </ul>,
    );
    expect(screen.queryByText(/Part \d/)).not.toBeInTheDocument();
    rerender(
      <ul>
        <RecordingRow recording={{ ...recording }} />
      </ul>,
    );
    expect(screen.queryByText(/Part \d/)).not.toBeInTheDocument();
  });

  it("can be both simulated and placeholder, and says both", () => {
    render(
      <ul>
        <RecordingRow recording={{ ...recording, simulated: true, placeholder: true }} />
      </ul>,
    );
    expect(screen.getByText("Simulated")).toBeInTheDocument();
    expect(screen.getByText("Placeholder")).toBeInTheDocument();
  });
});
