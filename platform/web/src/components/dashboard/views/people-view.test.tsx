import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PeopleView } from "@/components/dashboard/views/people-view";
import { api } from "@/lib/api";
import type { Person, ProcessingStatus, User } from "@/lib/api/types";
import { renderWithClient, signInDemo } from "@/test/utils";

const person = (overrides: Partial<Person> & Pick<Person, "id" | "displayName">): Person => ({
  consentStatus: "granted",
  consentRecordedAt: "2026-10-01T10:00:00.000Z",
  enrolled: false,
  enrolledAt: null,
  sampleCount: 0,
  samplesRequired: 3,
  voiceOutdated: false,
  ...overrides,
});

const ready = person({ id: "p1", displayName: "Priya Nair", enrolled: true, enrolledAt: "2026-10-02T10:00:00.000Z", sampleCount: 3 });
const partway = person({ id: "p2", displayName: "Marcus Webb", sampleCount: 1 });
const outdated = person({ id: "p3", displayName: "Lena Fischer", enrolled: false, sampleCount: 3, voiceOutdated: true });
const nothing = person({ id: "p4", displayName: "Aiko Tanaka", consentStatus: "pending", consentRecordedAt: null });

const available: ProcessingStatus = {
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

const employee: User = {
  id: "usr_e",
  email: "e@example.com",
  name: "An Employee",
  role: "employee",
  emailVerified: true,
  company: { id: "cmp_demo", name: "Northwind Demo Co." },
};

function show(people: Person[], status: ProcessingStatus = available) {
  vi.spyOn(api.people, "list").mockResolvedValue(people);
  vi.spyOn(api.processing, "status").mockResolvedValue(status);
  return renderWithClient(<PeopleView />);
}

const row = (name: string) => screen.getByText(name).closest("li")!;

describe("PeopleView", () => {
  beforeEach(() => {
    signInDemo();
    vi.restoreAllMocks();
  });

  describe("where each person is with their voice", () => {
    it("says so for someone who is ready, someone part way, someone out of date, and someone with nothing", async () => {
      show([ready, partway, outdated, nothing]);
      await screen.findByText("Priya Nair");

      expect(within(row("Priya Nair")).getByText("Voice enrolled · 3 samples")).toBeInTheDocument();
      expect(within(row("Marcus Webb")).getByText("Voice: 1 of 3 samples")).toBeInTheDocument();
      expect(within(row("Lena Fischer")).getByText("Voice out of date · enroll again")).toBeInTheDocument();
      expect(within(row("Aiko Tanaka")).getByText("No voice enrolled")).toBeInTheDocument();
    });

    it("does not call someone enrolled just because they have samples", async () => {
      show([partway]);
      await screen.findByText("Marcus Webb");
      expect(screen.queryByText(/voice enrolled/i)).not.toBeInTheDocument();
    });

    it("does not call someone out of date ready, even with every sample", async () => {
      show([outdated]);
      await screen.findByText("Lena Fischer");
      expect(screen.queryByText(/voice enrolled/i)).not.toBeInTheDocument();
    });
  });

  describe("what the page promises about enrollment", () => {
    it("describes the process when the server can take a voice", async () => {
      show([ready]);
      expect(await screen.findByText(/turned into a voice print and then deleted/i)).toBeInTheDocument();
      expect(screen.queryByText(/not available on this server/i)).not.toBeInTheDocument();
    });

    it("says plainly that no one is identified when the server cannot", async () => {
      show([ready], { ...available, enrollmentAvailable: false, speakerMatching: false });
      expect(await screen.findByText(/not available on this server yet/i)).toBeInTheDocument();
      expect(screen.getByText(/no one is identified by voice/i)).toBeInTheDocument();
      expect(screen.queryByText(/turned into a voice print/i)).not.toBeInTheDocument();
    });

    it("promises nothing while it does not yet know", async () => {
      vi.spyOn(api.people, "list").mockResolvedValue([ready]);
      vi.spyOn(api.processing, "status").mockRejectedValue(new Error("down"));
      renderWithClient(<PeopleView />);
      await screen.findByText("Priya Nair");
      expect(screen.getByText(/not available on this server yet/i)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /enroll voice|add voice samples/i })).not.toBeInTheDocument();
    });

    it("does not name the speaker model it is waiting for", async () => {
      const { container } = show([ready], { ...available, enrollmentAvailable: false });
      await screen.findByText(/not available on this server yet/i);
      expect(container.textContent).not.toMatch(/speechbrain|ecapa|pyannote|whisper|hugging/i);
    });
  });

  describe("who can enroll a voice", () => {
    it("offers it to a manager for someone whose consent is on record", async () => {
      show([partway, nothing]);
      await screen.findByText("Marcus Webb");
      await waitFor(() => expect(within(row("Marcus Webb")).getByRole("button", { name: "Add voice samples" })).toBeInTheDocument());
    });

    it("does not offer it for someone who has not consented", async () => {
      show([partway, nothing]);
      await screen.findByText("Aiko Tanaka");
      await within(row("Marcus Webb")).findByRole("button", { name: "Add voice samples" });
      expect(within(row("Aiko Tanaka")).queryByRole("button", { name: /enroll voice|add voice samples/i })).not.toBeInTheDocument();
      expect(within(row("Aiko Tanaka")).getByRole("button", { name: /record consent/i })).toBeInTheDocument();
    });

    it("does not offer it where the server cannot take a voice", async () => {
      show([partway], { ...available, enrollmentAvailable: false });
      await screen.findByText("Marcus Webb");
      await within(row("Marcus Webb")).findByRole("button", { name: /withdraw consent/i });
      // Absence only means something once the server's answer is in.
      const status = vi.mocked(api.processing.status);
      await waitFor(() => expect(status).toHaveBeenCalled());
      await status.mock.results[0]!.value;
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(within(row("Marcus Webb")).queryByRole("button", { name: /add voice samples/i })).not.toBeInTheDocument();
    });

    it("shows nobody but a manager any buttons", async () => {
      const me = vi.spyOn(api.auth, "me").mockResolvedValue(employee);
      show([partway]);
      await screen.findByText("Marcus Webb");
      await waitFor(() => expect(me).toHaveBeenCalled());
      await me.mock.results[0]!.value;
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(within(row("Marcus Webb")).queryByRole("button")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /add person/i })).not.toBeInTheDocument();
    });
  });
});
