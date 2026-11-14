import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SignatureDialog, sampleProblem } from "@/components/dashboard/signature-dialog";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import type { Person } from "@/lib/api/types";
import { expectNoA11yViolations, renderWithClient, signInDemo } from "@/test/utils";

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
  signature: { ready: false, sampleCount: 0, samplesRequired: 3, outdated: false, updatedAt: null, pitch: null },
};

const wavFile = (name = "sample.wav") => new File([new Uint8Array([82, 73, 70, 70])], name, { type: "audio/wav" });

async function openDialog(person: Person = ana) {
  const user = userEvent.setup();
  renderWithClient(<SignatureDialog person={person} />);
  await user.click(screen.getByRole("button", { name: /voice signature|add signature samples/i }));
  return { user, dialog: await screen.findByRole("dialog") };
}

describe("SignatureDialog", () => {
  beforeEach(() => {
    signInDemo();
    vi.restoreAllMocks();
  });

  it("says what is measured and that the recording is not kept", async () => {
    const { dialog } = await openDialog();
    expect(dialog).toHaveAccessibleName("Ana Silva's voice signature");
    expect(within(dialog).getByText(/measures their pitch and the colour of their voice, then deletes the recording/i)).toBeInTheDocument();
    expect(within(dialog).getByText("0 of 3 samples saved")).toBeInTheDocument();
  });

  it("sends a chosen WAV file for this person", async () => {
    const add = vi.spyOn(api.people, "addSignatureSample").mockResolvedValue(ana);
    const { user, dialog } = await openDialog();
    const file = wavFile();

    await user.upload(within(dialog).getByLabelText(/choose a wav file/i), file);

    await waitFor(() => expect(add).toHaveBeenCalledWith("per_ana", file));
  });

  it("shows the server's reason when a sample is refused, and claims nothing was saved", async () => {
    vi.spyOn(api.people, "addSignatureSample").mockRejectedValue(
      new ApiError(422, "not_enough_speech", "There is not enough clear speech in this sample to measure a voice."),
    );
    const { user, dialog } = await openDialog();

    await user.upload(within(dialog).getByLabelText(/choose a wav file/i), wavFile());

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/not enough clear speech/i);
    expect(within(dialog).getByText("0 of 3 samples saved")).toBeInTheDocument();
  });

  it("does not send a file that is not a WAV", async () => {
    const add = vi.spyOn(api.people, "addSignatureSample");
    const { dialog } = await openDialog();
    const input = within(dialog).getByLabelText(/choose a wav file/i) as HTMLInputElement;

    // The picker's accept filter is bypassed on purpose: the dialog must check for itself.
    const notAudio = new File(["hello"], "notes.txt", { type: "text/plain" });
    Object.defineProperty(input, "files", { value: [notAudio], configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/choose a wav file/i);
    expect(add).not.toHaveBeenCalled();
  });

  it("shows progress and the pitch so far, to a manager", async () => {
    const { dialog } = await openDialog({
      ...ana,
      signature: {
        ready: false,
        sampleCount: 2,
        samplesRequired: 3,
        outdated: false,
        updatedAt: new Date().toISOString(),
        pitch: { baseHz: 192.4, lowHz: 151.2, highHz: 240.9 },
      },
    });
    expect(within(dialog).getByText("2 of 3 samples saved")).toBeInTheDocument();
    const pitch = within(dialog).getByLabelText("Pitch so far");
    expect(pitch).toHaveTextContent("Base pitch");
    expect(pitch).toHaveTextContent("192 Hz");
    expect(pitch).toHaveTextContent("151 Hz");
    expect(pitch).toHaveTextContent("241 Hz");
  });

  it("says when the person is ready and still allows more samples", async () => {
    const { dialog } = await openDialog({
      ...ana,
      signature: { ready: true, sampleCount: 3, samplesRequired: 3, outdated: false, updatedAt: null, pitch: null },
    });
    expect(within(dialog).getByRole("status")).toHaveTextContent(/can now be matched by voice signature/i);
    expect(within(dialog).getByRole("button", { name: "Done" })).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/choose a wav file/i)).toBeEnabled();
  });

  it("does not name any speech product, library or part", async () => {
    const { dialog } = await openDialog();
    expect(dialog.textContent).not.toMatch(/whisper|pyannote|speechbrain|ecapa|hugging|librosa|numpy|mfcc|esp32/i);
  });

  it("has no accessibility violations", async () => {
    const { dialog } = await openDialog();
    await expectNoA11yViolations(dialog);
  });
});

describe("sampleProblem", () => {
  it("accepts a WAV by name or by type", () => {
    expect(sampleProblem({ name: "a.wav", type: "", size: 100 })).toBeNull();
    expect(sampleProblem({ name: "a", type: "audio/x-wav", size: 100 })).toBeNull();
    expect(sampleProblem({ name: "A.WAV", type: "audio/wav", size: 100 })).toBeNull();
  });

  it("refuses other files, empty files and huge files", () => {
    expect(sampleProblem({ name: "a.mp3", type: "audio/mpeg", size: 100 })).toMatch(/WAV/);
    expect(sampleProblem({ name: "a.wav", type: "audio/wav", size: 0 })).toMatch(/empty/);
    expect(sampleProblem({ name: "a.wav", type: "audio/wav", size: 50 * 1024 * 1024 })).toMatch(/too large/);
  });
});
