import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LoginForm } from "@/components/auth/login-form";
import { LocketExploded } from "@/components/brand/locket-exploded";
import { PipelineStates } from "@/components/brand/pipeline-states";
import { ProductPreview } from "@/components/brand/product-preview";
import { DeviceCard } from "@/components/dashboard/device-card";
import { DeviceDetectedPrompt } from "@/components/dashboard/device-prompt";
import { EnrollVoiceDialog } from "@/components/dashboard/enroll-voice-dialog";
import { LiveStatusPill } from "@/components/dashboard/live-status";
import { ProcessingNotice } from "@/components/dashboard/processing-notice";
import { RecordingRow } from "@/components/dashboard/recording-row";
import { TeamSection } from "@/components/dashboard/team-section";
import { AuditView } from "@/components/dashboard/views/audit-view";
import { PeopleView } from "@/components/dashboard/views/people-view";
import { RecordingDetailView } from "@/components/dashboard/views/recording-detail-view";
import { ContactForm } from "@/components/marketing/contact-form";
import { Button } from "@/components/ui/button";
import { Notice } from "@/components/ui/notice";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { api } from "@/lib/api";
import type { ProcessingStatus, RecordingDetail } from "@/lib/api/types";
import { demoDevices, demoPeople, demoRecordings, demoTranscript } from "@/lib/demo/data";
import { LiveUpdatesProvider } from "@/lib/live/provider";
import { expectNoA11yViolations, renderWithClient } from "@/test/utils";
import { Radio } from "lucide-react";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

// axe-core runs against rendered markup. Contrast is verified separately from the design tokens.
describe("accessibility (axe-core)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("login form", async () => {
    const { container } = renderWithClient(<LoginForm />);
    await expectNoA11yViolations(container);
  });

  it("login form with validation errors", async () => {
    const { container } = renderWithClient(<LoginForm />);
    (screen.getByRole("button", { name: /^sign in$/i }) as HTMLButtonElement).click();
    await screen.findByText("Enter your password.");
    await expectNoA11yViolations(container);
  });

  it("contact form", async () => {
    const { container } = render(<ContactForm />);
    await expectNoA11yViolations(container);
  });

  it("device cards in every status", async () => {
    const { container } = renderWithClient(
      <ul>
        {demoDevices.map((device) => (
          <li key={device.id}>
            <DeviceCard device={device} />
          </li>
        ))}
      </ul>,
    );
    await expectNoA11yViolations(container);
  });

  it("recording rows in every processing state", async () => {
    const { container } = render(
      <ul>
        {demoRecordings.map((recording) => (
          <RecordingRow key={recording.id} recording={recording} />
        ))}
      </ul>,
    );
    await expectNoA11yViolations(container);
  });

  it("simulated lockets and their recordings", async () => {
    const [device] = demoDevices;
    const [recording] = demoRecordings;
    const { container } = renderWithClient(
      <ul>
        <li>
          <DeviceCard device={{ ...device!, simulated: true }} />
        </li>
        <RecordingRow recording={{ ...recording!, simulated: true }} />
      </ul>,
    );
    await expectNoA11yViolations(container);
  });

  it.each(["connecting", "live", "reconnecting", "ended"] as const)("the live status pill (%s)", async (status) => {
    vi.spyOn(api.live, "subscribe").mockImplementation(({ onStatus }) => {
      onStatus(status);
      return () => {};
    });
    const { container } = renderWithClient(
      <LiveUpdatesProvider>
        <LiveStatusPill />
      </LiveUpdatesProvider>,
    );
    await expectNoA11yViolations(container);
  });

  it("the locket-detected prompt", async () => {
    window.localStorage.setItem("ask_demo_session", "1");
    vi.spyOn(api.devices, "list").mockResolvedValue([{ ...demoDevices[0]!, assignedTo: { id: "usr_demo", name: "Demo" } }]);
    const { container } = renderWithClient(<DeviceDetectedPrompt />);
    await screen.findByRole("region", { name: /locket detected/i });
    await expectNoA11yViolations(container);
  });

  it("empty and error states", async () => {
    const { container } = render(
      <>
        <EmptyState icon={Radio} title="No lockets yet" description="Pair your first locket." action={<Button>Pair</Button>} />
        <ErrorState description="The server had a problem." onRetry={() => {}} />
      </>,
    );
    await expectNoA11yViolations(container);
  });

  it("illustrations expose one clear description and hide their decoration", async () => {
    const { container } = render(
      <>
        <ProductPreview />
        <LocketExploded />
        <PipelineStates />
      </>,
    );
    await expectNoA11yViolations(container);
    expect(screen.getAllByRole("img").length).toBeGreaterThanOrEqual(3);
  });

  it("notices in both tones", async () => {
    const { container } = render(
      <>
        <Notice title="How this works">Plain explanation.</Notice>
        <Notice tone="warning" title="Placeholder mode">
          <p>Not what was said.</p>
        </Notice>
      </>,
    );
    await expectNoA11yViolations(container);
  });

  describe("processing and placeholder labelling", () => {
    const placeholderStatus: ProcessingStatus = {
      available: true,
      workers: 1,
      placeholder: true,
      transcription: true,
      diarization: false,
      speakerMatching: false,
      queued: 0,
      running: 0,
      notes: [],
      enrollmentAvailable: false,
    };

    it("the processing notice", async () => {
      window.localStorage.setItem("ask_demo_session", "1");
      vi.spyOn(api.processing, "status").mockResolvedValue(placeholderStatus);
      const { container } = renderWithClient(<ProcessingNotice />);
      await screen.findByRole("complementary", { name: /placeholder mode/i });
      await expectNoA11yViolations(container);
    });

    it("a placeholder recording in a list", async () => {
      const { container } = render(
        <ul>
          <RecordingRow recording={{ ...demoRecordings[0]!, placeholder: true, simulated: true }} />
        </ul>,
      );
      await expectNoA11yViolations(container);
    });

    it("a recording page with a placeholder transcript", async () => {
      window.localStorage.setItem("ask_demo_session", "1");
      const base = demoRecordings[0]!;
      const transcript = demoTranscript(base)!;
      const detail: RecordingDetail = {
        ...base,
        placeholder: true,
        audioUrl: null,
        failureDetail: null,
        transcript: { ...transcript, placeholder: true, diarizationModel: null, diarizationModelVersion: null },
      };
      vi.spyOn(api.recordings, "get").mockResolvedValue(detail);
      vi.spyOn(api.processing, "status").mockResolvedValue(placeholderStatus);
      const { container } = renderWithClient(<RecordingDetailView id={detail.id} />);
      await screen.findByRole("complementary", { name: /placeholder text/i });
      await expectNoA11yViolations(container);
    });

    it("a recording page that is waiting for a worker", async () => {
      window.localStorage.setItem("ask_demo_session", "1");
      const queued: RecordingDetail = { ...demoRecordings[2]!, transcript: null, audioUrl: null, failureDetail: null };
      vi.spyOn(api.recordings, "get").mockResolvedValue(queued);
      vi.spyOn(api.processing, "status").mockResolvedValue({ ...placeholderStatus, available: false, workers: 0, queued: 1 });
      const { container } = renderWithClient(<RecordingDetailView id={queued.id} />);
      await screen.findByText(/waiting for a processing worker/i);
      await expectNoA11yViolations(container);
    });

    it("the people page, with voices in every state", async () => {
      window.localStorage.setItem("ask_demo_session", "1");
      vi.spyOn(api.people, "list").mockResolvedValue([
        ...demoPeople,
        { ...demoPeople[0]!, id: "p_part", displayName: "Part Way", enrolled: false, sampleCount: 1 },
        { ...demoPeople[0]!, id: "p_old", displayName: "Out Of Date", enrolled: false, voiceOutdated: true },
      ]);
      vi.spyOn(api.processing, "status").mockResolvedValue({ ...placeholderStatus, placeholder: false, enrollmentAvailable: true });
      const { container } = renderWithClient(<PeopleView />);
      await screen.findByText("Part Way");
      await screen.findAllByRole("button", { name: /enroll voice|add voice samples/i });
      await expectNoA11yViolations(container);
    });

    it("the voice enrollment dialog", async () => {
      window.localStorage.setItem("ask_demo_session", "1");
      const user = userEvent.setup();
      renderWithClient(<EnrollVoiceDialog person={{ ...demoPeople[0]!, enrolled: false, sampleCount: 1 }} />);
      await user.click(screen.getByRole("button", { name: /add voice samples/i }));
      await screen.findByRole("dialog");
      // The dialog is portalled out of the container, so check the whole document.
      await expectNoA11yViolations(document.body);
    });
  });

  describe("administration", () => {
    it("the audit log, with actions of every kind", async () => {
      window.localStorage.setItem("ask_demo_session", "1");
      const { container } = renderWithClient(<AuditView />);
      await screen.findByRole("list", { name: "Recorded actions" });
      await expectNoA11yViolations(container);
    });

    it("the audit log when nothing matches and when it fails", async () => {
      window.localStorage.setItem("ask_demo_session", "1");
      vi.spyOn(api.audit, "list").mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
      const first = renderWithClient(<AuditView />);
      await screen.findByText("Nothing recorded yet");
      await expectNoA11yViolations(first.container);
      first.unmount();

      vi.spyOn(api.audit, "list").mockRejectedValue(new Error("down"));
      const second = renderWithClient(<AuditView />);
      await screen.findByRole("alert");
      await expectNoA11yViolations(second.container);
    });

    it("the team, with an administrator's controls and someone switched off", async () => {
      window.localStorage.setItem("ask_demo_session", "1");
      vi.spyOn(api.team, "members").mockResolvedValue([
        { id: "usr_demo", name: "Demo Administrator", email: "demo.admin@example.com", role: "admin", isActive: true },
        { id: "usr_1", name: "Priya Nair", email: "priya.nair@example.com", role: "employee", isActive: true },
        { id: "usr_3", name: "Sam Okafor", email: "sam.okafor@example.com", role: "employee", isActive: false },
      ]);
      const { container } = renderWithClient(<TeamSection viewerRole="admin" viewerId="usr_demo" />);
      await screen.findByText("Sam Okafor");
      await expectNoA11yViolations(container);
    });
  });
});
