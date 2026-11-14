import { Plus } from "lucide-react";
import { Neon, Section, SectionHeading } from "@/components/marketing/section";

const FAQS = [
  {
    q: "Does the locket record all the time?",
    a: "Only while recording is turned on, in short clips. Your organization decides when lockets are worn and tells the people being recorded. A consent record is required before any voice profile.",
  },
  {
    q: "How quickly do transcripts appear?",
    a: "Once a clip uploads it is queued on your server, and the dashboard shows each stage. The time depends on your hardware and the clip length, so ASK does not promise a fixed number.",
  },
  {
    q: "How does it know a locket is online?",
    a: "The locket sends authenticated heartbeats over Wi-Fi. While they keep arriving it is online, and when they stop it is offline.",
  },
  {
    q: "Where is audio stored?",
    a: "On the locket until your server confirms it, then on your server's storage. Never in the browser.",
  },
  {
    q: "What if the Wi-Fi is down?",
    a: "Clips stay on the locket and upload later. A retry never creates a duplicate.",
  },
  {
    q: "Who can see recordings?",
    a: "Only members of your company workspace, by role and device permission. No other company can.",
  },
] as const;

export function Faq() {
  return (
    <Section id="faq" className="pt-0 sm:pt-0">
      <div className="grid gap-14 lg:grid-cols-[0.8fr_1.2fr] lg:gap-20">
        <SectionHeading
          index="06"
          eyebrow="FAQ"
          title={
            <>
              Questions teams <Neon>ask first</Neon>
            </>
          }
        />
        <div className="reveal border-t border-line-strong">
          {FAQS.map(({ q, a }) => (
            <details key={q} className="group border-b border-line-strong py-6">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-6 text-base font-medium text-fg transition-colors hover:text-cerulean-glow [&::-webkit-details-marker]:hidden">
                {q}
                <Plus
                  className="size-5 shrink-0 text-cerulean-glow transition-transform duration-300 group-open:rotate-45"
                  aria-hidden="true"
                />
              </summary>
              <p className="mt-4 max-w-2xl text-base leading-relaxed text-fg-muted">{a}</p>
            </details>
          ))}
        </div>
      </div>
    </Section>
  );
}
