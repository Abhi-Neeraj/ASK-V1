import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProcessingNotice, processingNotice } from "@/components/dashboard/processing-notice";
import { api } from "@/lib/api";
import type { ProcessingStatus } from "@/lib/api/types";
import { renderWithClient, signInDemo } from "@/test/utils";

const healthy: ProcessingStatus = {
  available: true,
  workers: 1,
  placeholder: false,
  transcription: true,
  diarization: true,
  speakerMatching: true,
  queued: 0,
  running: 0,
  notes: [],
  enrollmentAvailable: true,
};

describe("what a person is told about processing", () => {
  it("says nothing when everything is working", () => {
    expect(processingNotice(healthy)).toBeNull();
  });

  it("says recordings are waiting when no worker is running and something is queued", () => {
    const notice = processingNotice({ ...healthy, available: false, workers: 0, queued: 3 });
    expect(notice?.tone).toBe("warning");
    expect(notice?.title).toMatch(/waiting for a processing worker/i);
    expect(notice?.lines.join(" ")).toContain("3 recordings are waiting");
  });

  it("counts a single recording in the singular", () => {
    const notice = processingNotice({ ...healthy, available: false, workers: 0, queued: 1 });
    expect(notice?.lines.join(" ")).toContain("1 recording is waiting");
  });

  it("is quiet when there is no worker but also nothing to process", () => {
    expect(processingNotice({ ...healthy, available: false, workers: 0 })).toBeNull();
  });

  it("warns, as a warning, that placeholder mode produces text that is not real", () => {
    const notice = processingNotice({ ...healthy, placeholder: true, diarization: false, speakerMatching: false });
    expect(notice?.tone).toBe("warning");
    expect(notice?.title).toMatch(/not real/i);
    expect(notice?.lines.join(" ")).toMatch(/not what was said/i);
    expect(notice?.lines.join(" ")).toMatch(/nobody is identified/i);
  });

  it("explains each missing capability of a real worker without alarm", () => {
    const noSeparation = processingNotice({ ...healthy, diarization: false });
    expect(noSeparation?.tone).toBe("info");
    expect(noSeparation?.lines).toHaveLength(1);
    expect(noSeparation?.lines[0]).toMatch(/one speaker/i);

    const neither = processingNotice({ ...healthy, diarization: false, speakerMatching: false });
    expect(neither?.lines).toHaveLength(2);
  });

  it("never repeats the developer notes, which can name libraries", () => {
    const notice = processingNotice({
      ...healthy,
      diarization: false,
      notes: ["Speaker separation is unavailable: pyannote.audio is not installed."],
    });
    expect(JSON.stringify(notice)).not.toMatch(/pyannote|whisper|speechbrain|ASK_/i);
  });
});

describe("ProcessingNotice", () => {
  beforeEach(() => {
    signInDemo();
    vi.restoreAllMocks();
  });

  it("shows the notice once the status has loaded", async () => {
    vi.spyOn(api.processing, "status").mockResolvedValue({ ...healthy, placeholder: true });
    renderWithClient(<ProcessingNotice />);
    expect(await screen.findByRole("complementary", { name: /placeholder mode/i })).toBeInTheDocument();
  });

  it("shows nothing at all, rather than a guess, if the status cannot be read", async () => {
    const status = vi.spyOn(api.processing, "status").mockRejectedValue(new Error("down"));
    const { container } = renderWithClient(<ProcessingNotice />);
    await vi.waitFor(() => expect(status).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("shows nothing for a healthy worker", async () => {
    const status = vi.spyOn(api.processing, "status").mockResolvedValue(healthy);
    const { container } = renderWithClient(<ProcessingNotice />);
    await vi.waitFor(() => expect(status).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(container).toBeEmptyDOMElement();
  });
});
