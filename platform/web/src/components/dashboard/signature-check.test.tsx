import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SignatureCheck, similarityLabel } from "@/components/dashboard/signature-check";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import type { SignatureMatch } from "@/lib/api/types";
import { expectNoA11yViolations, renderWithClient, signInDemo } from "@/test/utils";

const NOTICE = "A similarity score says how alike two voices sound. It is not identity verification.";
const pitch = { baseHz: 191.6, lowHz: 150.2, highHz: 238.9 };

const matched: SignatureMatch = {
  outcome: "matched",
  pitch,
  voicedSeconds: 8.4,
  candidatesConsidered: 2,
  match: { personId: "per_1", name: "Priya Nair", score: 0.74, pitchGapOctaves: 0.05, passedPitchGate: true },
  closest: [
    { personId: "per_1", name: "Priya Nair", score: 0.74, pitchGapOctaves: 0.05, passedPitchGate: true },
    { personId: "per_2", name: "Marcus Webb", score: 0.2, pitchGapOctaves: 0.8, passedPitchGate: false },
  ],
  notice: NOTICE,
};

const wavFile = () => new File([new Uint8Array([82, 73, 70, 70])], "voice.wav", { type: "audio/wav" });

async function upload(result: SignatureMatch | ApiError) {
  const spy = vi.spyOn(api.people, "matchSignature");
  if (result instanceof ApiError) spy.mockRejectedValue(result);
  else spy.mockResolvedValue(result);
  const user = userEvent.setup();
  const view = renderWithClient(<SignatureCheck />);
  await user.upload(screen.getByLabelText(/choose a wav file/i), wavFile());
  return { spy, view };
}

describe("SignatureCheck", () => {
  beforeEach(() => {
    signInDemo();
    vi.restoreAllMocks();
  });

  it("explains what it does and that it is not proof", () => {
    renderWithClient(<SignatureCheck />);
    expect(screen.getByRole("heading", { name: "Check a voice" })).toBeInTheDocument();
    expect(screen.getByText(/deleted straight away, and the check is written to the audit log/i)).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Not identity verification" })).toBeInTheDocument();
  });

  it("shows the pitch and the closest match, and says the others are not matches", async () => {
    const { spy } = await upload(matched);

    expect(await screen.findByText("Closest match: Priya Nair")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(1);
    const pitchBox = screen.getByLabelText("Pitch of this voice");
    expect(pitchBox).toHaveTextContent("192 Hz");
    expect(pitchBox).toHaveTextContent("150 Hz");
    expect(pitchBox).toHaveTextContent("239 Hz");

    const list = screen.getByRole("list", { name: "Nearest voices" });
    expect(within(list).getByText("Closest match")).toBeInTheDocument(); // the badge, on one person only
    expect(within(list).getByText("74% alike")).toBeInTheDocument();
    expect(within(list).getByText("Pitch too different")).toBeInTheDocument();
    expect(screen.getByText(/not identity verification/i)).toBeInTheDocument();
  });

  it("does not claim a match when there was none", async () => {
    await upload({ ...matched, outcome: "no_confident_match", match: null });
    expect(await screen.findByText("No confident match")).toBeInTheDocument();
    expect(screen.getByText(/being listed is not a match/i)).toBeInTheDocument();
    expect(screen.queryByText(/^Closest match:/)).not.toBeInTheDocument();
    expect(screen.queryByText("Closest match")).not.toBeInTheDocument();
  });

  it("says so when nobody has a signature yet", async () => {
    await upload({ ...matched, outcome: "nobody_enrolled", match: null, closest: [], candidatesConsidered: 0 });
    expect(await screen.findByText("Nobody has a voice signature yet")).toBeInTheDocument();
  });

  it("shows why a file was refused", async () => {
    await upload(new ApiError(422, "sample_too_short", "Record at least 3 seconds of the person speaking."));
    expect(await screen.findByRole("alert")).toHaveTextContent("Record at least 3 seconds of the person speaking.");
  });

  it("does not send a file that is not a WAV", async () => {
    const spy = vi.spyOn(api.people, "matchSignature");
    renderWithClient(<SignatureCheck />);
    const input = screen.getByLabelText(/choose a wav file/i) as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [new File(["x"], "voice.mp3", { type: "audio/mpeg" })], configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/choose a wav file/i);
    expect(spy).not.toHaveBeenCalled();
  });

  it("words a similarity as a similarity, never as a chance of being right", () => {
    expect(similarityLabel(0.744)).toBe("74% alike");
  });

  it("does not name any speech product, library or part", async () => {
    const { view } = await upload(matched);
    await screen.findByText("Closest match: Priya Nair");
    expect(view.container.textContent).not.toMatch(/whisper|pyannote|speechbrain|ecapa|hugging|librosa|numpy|mfcc|esp32/i);
  });

  it("has no accessibility violations", async () => {
    const { view } = await upload(matched);
    await screen.findByText("Closest match: Priya Nair");
    await expectNoA11yViolations(view.container);
  });
});
