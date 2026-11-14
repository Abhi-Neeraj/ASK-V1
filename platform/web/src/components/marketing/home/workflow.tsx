import { AudioLines, FileText, HardDrive, ShieldCheck, Wifi } from "lucide-react";
import { Waveform } from "@/components/brand/waveform";
import { Neon, Section, SectionHeading } from "@/components/marketing/section";

const STEPS = [
  { icon: AudioLines, title: "Record", body: "Short audio clips." },
  { icon: HardDrive, title: "Save", body: "On the locket first." },
  { icon: Wifi, title: "Upload", body: "Over Wi-Fi, with retries." },
  { icon: ShieldCheck, title: "Confirm", body: "Stored, then acknowledged." },
  { icon: FileText, title: "Understand", body: "Transcript and speakers." },
] as const;

export function Workflow() {
  return (
    <Section id="workflow" className="pt-0 sm:pt-0">
      <SectionHeading
        index="03"
        eyebrow="How it works"
        title={
          <>
            From the locket to a <Neon>searchable</Neon> <Neon tone="rose">transcript</Neon>
          </>
        }
        description="A dropped connection never costs you a recording."
      />

      <ol className="relative mt-16 grid gap-10 lg:grid-cols-5 lg:gap-8">
        <span
          aria-hidden="true"
          className="absolute left-0 right-0 top-[1.625rem] hidden h-px bg-gradient-to-r from-cerulean via-line-strong to-rose lg:block"
        />
        {STEPS.map(({ icon: Icon, title, body }, index) => (
          <li key={title} className="reveal relative flex gap-5 lg:block">
            <span className="relative z-10 grid size-[3.25rem] shrink-0 place-items-center rounded-full border border-cerulean/60 bg-ink text-cerulean-glow shadow-[0_0_30px_-8px_rgb(56_182_224/0.85)]">
              <Icon className="size-5" aria-hidden="true" />
            </span>
            <div>
              <p className="caption text-rose lg:mt-7">0{index + 1}</p>
              <h3 className="mt-1.5 font-heading text-sm font-medium uppercase tracking-[0.2em] text-fg">{title}</h3>
              <p className="mt-2 text-sm text-fg-muted">{body}</p>
            </div>
          </li>
        ))}
      </ol>

      <div className="reveal mt-16 border-y border-line-strong py-6">
        <Waveform className="w-full justify-between" bars={52} />
      </div>
    </Section>
  );
}
