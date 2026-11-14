import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  RecordingDetailView,
  audioUnavailableMessage,
} from "@/components/dashboard/views/recording-detail-view";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import type { ProcessingStatus, RecordingDetail, TranscriptInfo } from "@/lib/api/types";
import { renderWithClient, signInDemo } from "@/test/utils";

// The delete dialog on this page navigates away after a delete; there is no app router in a unit test.
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));

const download = vi.hoisted(() => vi.fn());
vi.mock("@/lib/transcript-export", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/transcript-export")>()),
  downloadTextFile: download,
}));

const transcript: TranscriptInfo = {
  transcriptionModel: "secret-speech-model-v9",
  transcriptionModelVersion: "9.1.2",
  diarizationModel: "secret-separation-model",
  diarizationModelVersion: "3.0",
  reviewStatus: "none",
  placeholder: false,
  separationExperimental: false,
  language: "en",
  segments: [
    {
      id: "seg_1",
      startMs: 200,
      endMs: 4000,
      speakerLabel: "SPEAKER_00",
      person: { id: "per_1", name: "Priya Nair" },
      confidence: 0.93,
      text: "Let us confirm the delivery window.",
      needsReview: false,
      corrected: false,
    },
  ],
};

const done: RecordingDetail = {
  id: "rec_x",
  deviceId: "dev_1",
  deviceName: "Locket A7F2",
  simulated: false,
  startedAt: "2026-10-20T09:00:00.000Z",
  endedAt: "2026-10-20T09:00:10.000Z",
  durationMs: 10_000,
  sizeBytes: 320_000,
  format: "WAV, 16 kHz, mono, 16-bit",
  uploadedAt: "2026-10-20T09:00:12.000Z",
  state: "completed",
  retryCount: 0,
  error: null,
  snippet: "Let us confirm the delivery window.",
  speakers: ["Priya Nair"],
  placeholder: false,
  transcript,
  audioUrl: null,
  failureDetail: null,
};

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

function show(detail: RecordingDetail, status: ProcessingStatus = healthy) {
  vi.spyOn(api.recordings, "get").mockResolvedValue(detail);
  vi.spyOn(api.processing, "status").mockResolvedValue(status);
  return renderWithClient(<RecordingDetailView id={detail.id} />);
}

describe("RecordingDetailView", () => {
  beforeEach(() => {
    signInDemo();
    vi.restoreAllMocks();
    download.mockReset();
  });

  describe("a finished recording", () => {
    it("shows the transcript and says nothing alarming about it", async () => {
      show(done);
      expect(await screen.findByText("Let us confirm the delivery window.")).toBeInTheDocument();
      expect(screen.queryByText(/placeholder/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/speakers were not separated/i)).not.toBeInTheDocument();
    });

    it("describes what ran in plain words, without naming the models that did it", async () => {
      const { container } = show(done);
      await screen.findByText("Let us confirm the delivery window.");
      expect(screen.getByText("Done · language en")).toBeInTheDocument();
      expect(screen.getByText("Speech to text")).toBeInTheDocument();
      expect(screen.getByText("Speaker separation")).toBeInTheDocument();
      expect(container.textContent).not.toMatch(/secret-speech-model|secret-separation-model|9\.1\.2/);
    });

    it("says so when the recording had no speech in it, rather than showing an empty list", async () => {
      show({ ...done, snippet: null, speakers: [], transcript: { ...transcript, segments: [] } });
      expect(await screen.findByText("No speech was found in this recording.")).toBeInTheDocument();
      expect(screen.queryByRole("list", { name: /transcript/i })).not.toBeInTheDocument();
    });

    it("says plainly when the viewer may not play the audio, without mentioning builds or demos", () => {
      // The test environment is demo mode, so the real-deployment wording is checked on its own.
      expect(audioUnavailableMessage(false)).toBe("Audio playback is not available to you for this recording.");
      expect(audioUnavailableMessage(false)).not.toMatch(/build|demo/i);
      expect(audioUnavailableMessage(true)).toMatch(/demo/i);
    });

    it("shows that message in place of the player when the server withheld playback", async () => {
      show(done); // audioUrl is null
      expect(await screen.findByText(audioUnavailableMessage(true))).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: /download audio/i })).not.toBeInTheDocument();
    });

    it("offers playback and a download when the server allows it", async () => {
      show({ ...done, audioUrl: "/api/recordings/rec_x/audio" });
      await screen.findByText("Let us confirm the delivery window.");
      expect(screen.getByRole("link", { name: /download audio/i })).toHaveAttribute("href", "/api/recordings/rec_x/audio");
    });

    it("shows a voice match as a similarity score, never as certainty about who spoke", async () => {
      show(done);
      const badge = await screen.findByText("Voice match 93%");
      expect(badge).toHaveAttribute("title", expect.stringMatching(/not the probability.*not proof of who spoke/i));
    });

    it("calls a speaker nobody matched 'Unknown speaker', never a guess", async () => {
      const unmatched: RecordingDetail = {
        ...done,
        transcript: {
          ...transcript,
          segments: [{ ...transcript.segments[0]!, person: null, confidence: null }],
        },
      };
      show(unmatched);
      expect(await screen.findByText("Unknown speaker")).toBeInTheDocument();
      expect(screen.queryByText(/voice match/i)).not.toBeInTheDocument();
    });

    it("exports the transcript as text and as JSON", async () => {
      const user = userEvent.setup();
      show(done);
      await screen.findByText("Let us confirm the delivery window.");

      await user.click(screen.getByRole("button", { name: /export txt/i }));
      expect(download).toHaveBeenLastCalledWith("recording-rec_x.txt", "text/plain", expect.stringContaining("delivery window"));

      await user.click(screen.getByRole("button", { name: /export json/i }));
      expect(download).toHaveBeenLastCalledWith("recording-rec_x.json", "application/json", expect.any(String));
    });
  });

  describe("a placeholder transcript", () => {
    const placeholder: RecordingDetail = {
      ...done,
      placeholder: true,
      transcript: {
        ...transcript,
        placeholder: true,
        diarizationModel: null,
        diarizationModelVersion: null,
        language: null,
        segments: [{ ...transcript.segments[0]!, text: "[placeholder transcript]", person: null, confidence: null }],
      },
    };

    it("warns that it is not what was said, before anyone reads it as if it were", async () => {
      show(placeholder);
      const notice = await screen.findByRole("complementary", { name: /placeholder text/i });
      expect(notice).toHaveTextContent(/not what was said/i);
      expect(notice).toHaveTextContent(/nobody was identified by voice/i);
    });

    it("badges the recording and describes the speech step honestly", async () => {
      show(placeholder);
      await screen.findByRole("complementary", { name: /placeholder text/i });
      expect(screen.getAllByText(/placeholder/i).length).toBeGreaterThan(1);
      expect(screen.getByText("Placeholder only (no speech recognition ran)")).toBeInTheDocument();
    });

    it("does not also claim that speakers were merely not separated", async () => {
      show(placeholder);
      await screen.findByRole("complementary", { name: /placeholder text/i });
      expect(screen.queryByText(/speakers were not separated/i)).not.toBeInTheDocument();
    });
  });

  describe("a real transcript where speakers could not be told apart", () => {
    const single: RecordingDetail = {
      ...done,
      transcript: { ...transcript, diarizationModel: null, diarizationModelVersion: null },
    };

    it("says so, instead of letting one speaker look like the whole conversation", async () => {
      show(single);
      const notice = await screen.findByRole("complementary", { name: /speakers were not separated/i });
      expect(notice).toHaveTextContent(/one speaker/i);
      expect(screen.getByText("Did not run for this recording")).toBeInTheDocument();
    });
  });

  describe("a transcript whose speakers were separated by an experimental method", () => {
    const experimental: RecordingDetail = { ...done, transcript: { ...transcript, separationExperimental: true } };

    it("tells people to check who said what, without naming the method or calling a match proof", async () => {
      const { container } = show(experimental);
      const notice = await screen.findByRole("complementary", { name: /speaker separation was experimental/i });
      expect(notice).toHaveTextContent(/listen to the recording/i);
      expect(notice).toHaveTextContent(/not proof of who spoke/i);
      expect(container.textContent).not.toMatch(/clustering|secret-separation-model/i);
      expect(screen.getByText("Done (experimental method)")).toBeInTheDocument();
    });

    it("says nothing of the kind about an ordinary transcript", async () => {
      show(done);
      await screen.findByText("Let us confirm the delivery window.");
      expect(screen.queryByText(/experimental/i)).not.toBeInTheDocument();
    });

    it("marks the exported JSON too", async () => {
      const user = userEvent.setup();
      show(experimental);
      await screen.findByText("Let us confirm the delivery window.");
      await user.click(screen.getByRole("button", { name: /export json/i }));
      const [, , content] = download.mock.calls.at(-1)!;
      expect(JSON.parse(content as string)).toMatchObject({ speakersSeparated: true, speakerSeparationExperimental: true });
    });
  });

  describe("a recording that has no transcript", () => {
    const queued: RecordingDetail = { ...done, state: "queued", transcript: null, snippet: null, speakers: [] };

    it("says it is waiting its turn when a worker is running but has not started on it", async () => {
      show(queued);
      const status = await screen.findByRole("status");
      expect(status).toHaveTextContent(/waiting its turn/i);
      expect(status).not.toHaveTextContent(/being processed/i);
    });

    it("says it is being processed once a stage is running", async () => {
      show({ ...queued, state: "transcribing" });
      expect(await screen.findByRole("status")).toHaveTextContent(/is being processed/i);
    });

    it("says nothing is working on it when no worker is running", async () => {
      show(queued, { ...healthy, available: false, workers: 0, queued: 1 });
      expect(await screen.findByText(/waiting for a processing worker/i)).toBeInTheDocument();
      expect(screen.queryByText(/waiting its turn/i)).not.toBeInTheDocument();
    });

    it("does not claim a worker is missing before it knows", async () => {
      vi.spyOn(api.recordings, "get").mockResolvedValue(queued);
      vi.spyOn(api.processing, "status").mockRejectedValue(new Error("down"));
      renderWithClient(<RecordingDetailView id="rec_x" />);
      expect(await screen.findByText(/waiting its turn/i)).toBeInTheDocument();
      expect(screen.queryByText(/waiting for a processing worker/i)).not.toBeInTheDocument();
    });

    it("offers no exports, and no way to process it again while it is queued", async () => {
      show(queued);
      await screen.findByRole("status");
      expect(screen.queryByRole("button", { name: /export/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /process again/i })).not.toBeInTheDocument();
    });

    it("explains a failure and how many attempts were made", async () => {
      show({ ...queued, state: "failed", error: "The audio file could not be decoded.", retryCount: 3 });
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("Processing failed");
      expect(alert).toHaveTextContent("The audio file could not be decoded.");
      expect(alert).toHaveTextContent("Attempts so far: 3");
    });

    it("tells a manager what went wrong and what to do next", async () => {
      show({
        ...queued,
        state: "failed",
        error: "Processing failed.",
        retryCount: 3,
        failureDetail: "There is not enough memory to run the speech recognition model (small).",
      });
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(/what went wrong: there is not enough memory/i);
      expect(alert).toHaveTextContent(/use process again/i);
    });

    it("still explains a failed run when an earlier transcript is on screen, and labels that transcript as older", async () => {
      show({
        ...done,
        state: "failed",
        error: "The stored audio no longer matches its checksum, so it may be damaged.",
        failureDetail: "The stored audio no longer matches its checksum, so it may be damaged.",
      });
      const notice = await screen.findByRole("complementary", { name: /latest processing attempt failed/i });
      expect(notice).toHaveTextContent(/no longer matches its checksum/i);
      expect(notice).not.toHaveTextContent(/what went wrong/i); // the same sentence is not said twice
      expect(notice).toHaveTextContent(/from an earlier run/i);
      expect(notice).toHaveTextContent(/use process again/i);
      expect(await screen.findByText("Let us confirm the delivery window.")).toBeInTheDocument();
    });

    it("says nothing extra when the server gave no detail (an employee, or an unknown cause)", async () => {
      show({ ...queued, state: "failed", error: "Processing failed.", retryCount: 1 });
      const alert = await screen.findByRole("alert");
      expect(alert).not.toHaveTextContent(/what went wrong/i);
    });

    it("explains that a failed attempt will be tried again", async () => {
      show({ ...queued, state: "retrying", failureDetail: "The disk is full." });
      const paragraph = (await screen.findByText(/will be tried again/i)).closest("p");
      expect(paragraph).toHaveTextContent("The disk is full.");
    });
  });

  describe("processing again", () => {
    it("asks first, and says the old transcript and its corrections will be replaced", async () => {
      const reprocess = vi.spyOn(api.recordings, "reprocess");
      const user = userEvent.setup();
      show(done);
      await user.click(await screen.findByRole("button", { name: /process again/i }));
      const dialog = await screen.findByRole("dialog", { name: /process this recording again/i });
      expect(dialog).toHaveTextContent(/corrections made to it are lost/i);
      expect(reprocess).not.toHaveBeenCalled();
    });

    it("queues the recording once it is confirmed, and closes", async () => {
      const reprocess = vi.spyOn(api.recordings, "reprocess").mockResolvedValue({ ...done, state: "queued" });
      const user = userEvent.setup();
      show(done);
      await user.click(await screen.findByRole("button", { name: /process again/i }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /^process again$/i }));

      await waitFor(() => expect(reprocess).toHaveBeenCalledWith("rec_x"));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("stays open when the server refuses, because the recording was not queued", async () => {
      vi.spyOn(api.recordings, "reprocess").mockRejectedValue(
        new ApiError(409, "already_processing", "This recording is already being processed."),
      );
      const user = userEvent.setup();
      show(done);
      await user.click(await screen.findByRole("button", { name: /process again/i }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /^process again$/i }));

      await waitFor(() => expect(api.recordings.reprocess).toHaveBeenCalled());
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("is offered for a failed recording, because that is when someone wants it most", async () => {
      show({ ...done, state: "failed", transcript: null, error: "Could not decode." });
      expect(await screen.findByRole("button", { name: /process again/i })).toBeInTheDocument();
    });

    it("is not offered to someone who cannot manage recordings", async () => {
      vi.spyOn(api.auth, "me").mockResolvedValue({
        id: "usr_e",
        email: "e@example.com",
        name: "An Employee",
        role: "employee",
        emailVerified: true,
        company: { id: "cmp_demo", name: "Northwind Demo Co." },
      });
      const me = vi.mocked(api.auth.me);
      show(done);
      await screen.findByText("Let us confirm the delivery window.");
      // The button depends on the role, so the role has to have arrived before its absence means anything.
      await waitFor(() => expect(me).toHaveBeenCalled());
      await me.mock.results[0]!.value;
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(screen.queryByRole("button", { name: /process again/i })).not.toBeInTheDocument();
    });
  });

  it("says the recording was not found, rather than that something broke", async () => {
    vi.spyOn(api.recordings, "get").mockRejectedValue(new ApiError(404, "recording_not_found", "Not found."));
    vi.spyOn(api.processing, "status").mockResolvedValue(healthy);
    renderWithClient(<RecordingDetailView id="rec_gone" />);
    expect(await screen.findByText("Recording not found")).toBeInTheDocument();
  });
});
