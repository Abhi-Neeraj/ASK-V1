import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PersonActions } from "@/components/dashboard/person-actions";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import type { Person } from "@/lib/api/types";
import { renderWithClient, signInDemo } from "@/test/utils";

const waiting: Person = {
  id: "per_9",
  displayName: "Aiko Tanaka",
  consentStatus: "pending",
  consentRecordedAt: null,
  enrolled: false,
  enrolledAt: null,
  sampleCount: 0,
  samplesRequired: 3,
  voiceOutdated: false,
};

const agreed: Person = {
  ...waiting,
  consentStatus: "granted",
  consentRecordedAt: new Date().toISOString(),
  enrolled: true,
  enrolledAt: new Date().toISOString(),
  sampleCount: 3,
};

describe("PersonActions", () => {
  beforeEach(() => {
    signInDemo();
    vi.restoreAllMocks();
  });

  it("will not record consent until the manager confirms the person agreed", async () => {
    const record = vi.spyOn(api.people, "recordConsent").mockResolvedValue(agreed);
    const user = userEvent.setup();
    renderWithClient(<PersonActions person={waiting} />);

    await user.click(screen.getByRole("button", { name: /record consent/i }));
    const dialog = await screen.findByRole("dialog", { name: /record consent for aiko tanaka/i });
    const confirm = within(dialog).getByRole("button", { name: /^record consent$/i });
    expect(confirm).toBeDisabled();
    expect(record).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("checkbox", { name: /confirmed this with aiko tanaka/i }));
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    await waitFor(() => expect(record).toHaveBeenCalledWith("per_9", true));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("keeps the dialog open when the server refuses", async () => {
    vi.spyOn(api.people, "recordConsent").mockRejectedValue(new ApiError(403, "forbidden", "Not allowed."));
    const user = userEvent.setup();
    renderWithClient(<PersonActions person={waiting} />);

    await user.click(screen.getByRole("button", { name: /record consent/i }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: /^record consent$/i }));

    await waitFor(() => expect(api.people.recordConsent).toHaveBeenCalled());
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("offers to withdraw consent, and says it deletes the voice profile", async () => {
    const record = vi.spyOn(api.people, "recordConsent").mockResolvedValue({ ...waiting, consentStatus: "revoked" });
    const user = userEvent.setup();
    renderWithClient(<PersonActions person={agreed} />);

    expect(screen.queryByRole("button", { name: /^record consent/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /withdraw consent/i }));
    const dialog = await screen.findByRole("dialog", { name: /withdraw consent for aiko tanaka/i });
    expect(within(dialog).getByText(/deletes aiko tanaka's voice profile/i)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /^withdraw consent$/i }));

    await waitFor(() => expect(record).toHaveBeenCalledWith("per_9", false));
  });

  it("lets consent be recorded again after it was withdrawn", () => {
    renderWithClient(<PersonActions person={{ ...waiting, consentStatus: "revoked" }} />);
    expect(screen.getByRole("button", { name: /record consent again/i })).toBeInTheDocument();
  });

  describe("voice", () => {
    const part: Person = { ...agreed, enrolled: false, enrolledAt: null, sampleCount: 1 };

    it("offers enrollment only once consent is on record and the server can take a voice", () => {
      const { unmount } = renderWithClient(<PersonActions person={waiting} enrollmentAvailable />);
      expect(screen.queryByRole("button", { name: /enroll voice|add voice samples/i })).not.toBeInTheDocument();
      unmount();

      const second = renderWithClient(<PersonActions person={{ ...agreed, sampleCount: 0, enrolled: false }} />);
      expect(screen.queryByRole("button", { name: /enroll voice|add voice samples/i })).not.toBeInTheDocument();
      second.unmount();

      renderWithClient(<PersonActions person={{ ...agreed, sampleCount: 0, enrolled: false }} enrollmentAvailable />);
      expect(screen.getByRole("button", { name: "Enroll voice" })).toBeInTheDocument();
    });

    it("says add samples, not enroll, for someone who has started", () => {
      renderWithClient(<PersonActions person={part} enrollmentAvailable />);
      expect(screen.getByRole("button", { name: "Add voice samples" })).toBeInTheDocument();
    });

    it("offers removing a voice only when there is one to remove", () => {
      const { unmount } = renderWithClient(<PersonActions person={{ ...agreed, sampleCount: 0, enrolled: false }} />);
      expect(screen.queryByRole("button", { name: /remove voice/i })).not.toBeInTheDocument();
      unmount();

      renderWithClient(<PersonActions person={part} />);
      expect(screen.getByRole("button", { name: /remove voice/i })).toBeInTheDocument();
    });

    it("removes a voice only after confirmation, and says what that does to old transcripts", async () => {
      const removeVoice = vi.spyOn(api.people, "removeVoice").mockResolvedValue(undefined);
      const user = userEvent.setup();
      renderWithClient(<PersonActions person={agreed} />);

      await user.click(screen.getByRole("button", { name: /remove voice/i }));
      expect(removeVoice).not.toHaveBeenCalled();
      const dialog = await screen.findByRole("dialog", { name: /remove aiko tanaka's voice/i });
      expect(within(dialog).getByText(/deletes aiko tanaka's voice print/i)).toBeInTheDocument();
      expect(within(dialog).getByText(/unknown speaker/i)).toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: /^remove voice$/i }));

      await waitFor(() => expect(removeVoice).toHaveBeenCalledWith("per_9"));
    });

    it("keeps the voice if the server refuses to remove it", async () => {
      vi.spyOn(api.people, "removeVoice").mockRejectedValue(new ApiError(500, "server_error", "Try again."));
      const user = userEvent.setup();
      renderWithClient(<PersonActions person={agreed} />);

      await user.click(screen.getByRole("button", { name: /remove voice/i }));
      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: /^remove voice$/i }));

      await waitFor(() => expect(api.people.removeVoice).toHaveBeenCalled());
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  it("removes a person only after confirmation", async () => {
    const remove = vi.spyOn(api.people, "remove").mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithClient(<PersonActions person={waiting} />);

    await user.click(screen.getByRole("button", { name: /^remove$/i }));
    expect(remove).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog", { name: /remove aiko tanaka/i });
    expect(within(dialog).getByText(/audit log/i)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /^remove person$/i }));

    await waitFor(() => expect(remove).toHaveBeenCalledWith("per_9"));
  });
});
