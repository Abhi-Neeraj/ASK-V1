import type { ReactNode } from "react";
import { AuditArt, DeviceArt, ProcessingArt, SearchArt, SpeakerArt, WorkspaceArt } from "@/components/brand/capability-art";
import { Neon, Section, SectionHeading } from "@/components/marketing/section";

const CAPABILITIES: ReadonlyArray<{ title: string; body: string; art: ReactNode }> = [
  { title: "Search", body: "Find a recording by what was said.", art: <SearchArt /> },
  { title: "Speakers", body: "Match voices to people who consented.", art: <SpeakerArt /> },
  { title: "Device status", body: "Online or offline, from real heartbeats.", art: <DeviceArt /> },
  { title: "Live processing", body: "Watch each clip move to a transcript.", art: <ProcessingArt /> },
  { title: "Workspaces", body: "Roles for administrators, managers and staff.", art: <WorkspaceArt /> },
  { title: "Review and audit", body: "Correct, export and keep a trail.", art: <AuditArt /> },
];

export function Capabilities() {
  return (
    <Section id="capabilities" className="pt-0 sm:pt-0">
      <SectionHeading
        index="04"
        eyebrow="Capabilities"
        title={
          <>
            Everything a team needs to work from <Neon tone="rose">what was said</Neon>
          </>
        }
      />
      <div className="mt-16 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {CAPABILITIES.map(({ title, body, art }) => (
          <article
            key={title}
            className="reveal group rounded-[var(--radius-card)] border border-line bg-ink-raised/60 p-4 transition-colors duration-300 hover:border-cerulean/45"
          >
            {art}
            <div className="px-2 pb-2 pt-5">
              <h3 className="font-heading text-sm font-medium uppercase tracking-[0.2em] text-fg">{title}</h3>
              <p className="mt-2 text-sm text-fg-muted">{body}</p>
            </div>
          </article>
        ))}
      </div>
    </Section>
  );
}
