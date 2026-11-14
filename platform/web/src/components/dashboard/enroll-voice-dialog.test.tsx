import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EnrollVoiceDialog, SAMPLE_PROMPTS } from "@/components/dashboard/enroll-voice-dialog";
import type { FinishedSample } from "@/hooks/use-voice-recorder";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import type { Person } from "@/lib/api/types";
import { renderWithClient, signInDemo } from "@/test/utils";

// There is no microphone in a test, so the recorder is replaced by one the test controls.
const mic = vi.hoisted(() => ({
  state: {
    status: "idle" as "idle" | "recording" | "converting",
    seconds: 0,
    level: 0,
    error: undefined as string | undefined,
    supported: true,
    start: vi.fn(),
    stop: vi.fn(),
  },
  finish: undefined as undefined | ((sample: FinishedSample) => void),
}));
vi.mock("@/hooks/use-voice-recorder", () => ({
  useVoiceRecorder: ({ onFinish }: { onFinish: (sample: FinishedSample) => void }) => {
    mic.finish = onFinish;
    return mic.state;
  },
}));

const ana: Person = {
  id: "per_ana",
  displayName: "Ana Silva",
  consentStatus: "granted",
  consentRecordedAt: new Date().toISOString(),
  enrolled: false,
  enrolledAt: null,
  sampleCount: 0,
  samplesRequired: 3,
  voiceOutdated: false,
};

const wavFile = (name = "sample.wav") => new File([new Uint8Array([82, 73, 70, 70])], name, { type: "audio/wav" });

async function openDialog(person: Person = ana) {
  const user = userEvent.setup();
  renderWithClient(<EnrollVoiceDialog person={person} />);
  await user.click(screen.getByRole("button", { name: /enroll voice|add voice samples/i }));
  return { user, dialog: await screen.findByRole("dialog") };
}

describe("EnrollVoiceDialog", () => {
  beforeEach(() => {
    signInDemo();
    vi.restoreAllMocks();
    Object.assign(mic.state, { status: "idle", seconds: 0, level: 0, error: undefined, supported: true });
    mic.state.start.mockReset();
    mic.state.stop.mockReset();
  });

  it("says what will happen to the recordings and how far along the person is", async () => {
    const { dialog } = await openDialog({ ...ana, sampleCount: 1 });
    expect(dialog).toHaveAccessibleName("Enroll Ana Silva's voice");
    expect(within(dialog).getByText(/not kept: each is turned into a voice print and then deleted/i)).toBeInTheDocument();
    expect(within(dialog).getByText("1 of 3 samples saved")).toBeInTheDocument();
  });

  it("gives them something to read, and a different sentence for each sample", async () => {
    const first = await openDialog({ ...ana, sampleCount: 0 });
    expect(within(first.dialog).getByText(new RegExp(SAMPLE_PROMPTS[0]!.slice(0, 30)))).toBeInTheDocument();
  });

  it("rotates through the sentences as samples are saved", async () => {
    const { dialog } = await openDialog({ ...ana, sampleCount: 2 });
    expect(within(dialog).getByText(new RegExp(SAMPLE_PROMPTS[2]!.slice(0, 30)))).toBeInTheDocument();
  });

  it("sends a chosen WAV file for this person", async () => {
    const enroll = vi.spyOn(api.people, "enrollSample").mockResolvedValue({ ...ana, sampleCount: 1 });
    const { user, dialog } = await openDialog();
    const file = wavFile();

    await user.upload(within(dialog).getByLabelText(/choose a wav file/i), file);

    await waitFor(() => expect(enroll).toHaveBeenCalledWith("per_ana", file));
  });

  it("shows the server's reason when a sample is refused, and nothing was claimed as saved", async () => {
    vi.spyOn(api.people, "enrollSample").mockRejectedValue(
      new ApiError(422, "sample_too_short", "Record at least 3 seconds of the person speaking."),
    );
    const { user, dialog } = await openDialog();
    await user.upload(within(dialog).getByLabelText(/choose a wav file/i), wavFile());

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Record at least 3 seconds of the person speaking.");
    expect(within(dialog).getByText("0 of 3 samples saved")).toBeInTheDocument();
  });

  it("saves a recorded sample", async () => {
    const enroll = vi.spyOn(api.people, "enrollSample").mockResolvedValue({ ...ana, sampleCount: 1 });
    await openDialog();
    const wav = new Blob(["x"], { type: "audio/wav" });

    mic.finish!({ wav, seconds: 6.4 });

    await waitFor(() => expect(enroll).toHaveBeenCalledWith("per_ana", wav));
  });

  it("refuses a recording that is too short without bothering the server", async () => {
    const enroll = vi.spyOn(api.people, "enrollSample");
    const { dialog } = await openDialog();

    mic.finish!({ wav: new Blob(["x"]), seconds: 1.2 });

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/too short.*at least 3 seconds/i);
    expect(enroll).not.toHaveBeenCalled();
  });

  it("starts recording on request", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: /record a sample/i }));
    expect(mic.state.start).toHaveBeenCalledTimes(1);
  });

  it("shows a timer and a level meter while recording, and stops on request", async () => {
    Object.assign(mic.state, { status: "recording", seconds: 4.2, level: 0.5 });
    const { user, dialog } = await openDialog();

    expect(within(dialog).getByLabelText("Time recorded")).toHaveTextContent("4.2s");
    expect(within(dialog).getByRole("meter", { name: /microphone level/i })).toHaveAttribute("aria-valuenow", "50");
    expect(within(dialog).queryByRole("button", { name: /record a sample/i })).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /stop and save/i }));
    expect(mic.state.stop).toHaveBeenCalledTimes(1);
  });

  it("does not let a file be chosen mid-recording", async () => {
    Object.assign(mic.state, { status: "recording", seconds: 1, level: 0.2 });
    const { dialog } = await openDialog();
    expect(within(dialog).getByLabelText(/choose a wav file/i)).toBeDisabled();
  });

  it("explains a microphone problem in words, and the file option still works", async () => {
    mic.state.error = "The browser was not allowed to use the microphone. Allow it for this site, or choose a WAV file instead.";
    const { dialog } = await openDialog();
    expect(within(dialog).getByRole("alert")).toHaveTextContent(/not allowed to use the microphone/i);
    expect(within(dialog).getByLabelText(/choose a wav file/i)).toBeEnabled();
  });

  it("offers only the file option where there is no microphone support", async () => {
    mic.state.supported = false;
    const { dialog } = await openDialog();
    expect(within(dialog).queryByRole("button", { name: /record a sample/i })).not.toBeInTheDocument();
    expect(within(dialog).getByText(/cannot record from a microphone here/i)).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/choose a wav file/i)).toBeInTheDocument();
  });

  it("says when the person is ready, and still allows more samples", async () => {
    const { dialog } = await openDialog({ ...ana, sampleCount: 3, enrolled: true });
    expect(within(dialog).getByRole("status")).toHaveTextContent(/can now be recognized/i);
    expect(within(dialog).getByRole("button", { name: "Done" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: /record a sample/i })).toBeInTheDocument();
  });

  it("does not name any speech product", async () => {
    const { dialog } = await openDialog();
    expect(dialog.textContent).not.toMatch(/whisper|pyannote|speechbrain|ecapa|hugging/i);
  });
});
