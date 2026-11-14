import { ArrowUpRight, Check } from "lucide-react";
import Link from "next/link";
import { ConsentArt } from "@/components/brand/capability-art";
import { Eyebrow, Neon, Section } from "@/components/marketing/section";

const COMMITMENTS = [
  "Runs on hardware your organization controls.",
  "A voice profile needs a consent record first.",
  "You set retention, and deletion is a clear action.",
] as const;

export function PrivacyBand() {
  return (
    <Section id="privacy-approach" className="pt-0 sm:pt-0">
      <div className="reveal relative overflow-hidden border border-line-strong p-8 sm:p-12 lg:grid lg:grid-cols-[1fr_1fr] lg:items-center lg:gap-16 lg:p-16">
        <div aria-hidden="true" className="pointer-events-none absolute -left-32 -top-32 size-[28rem] bg-[radial-gradient(closest-side,rgb(0_123_167/0.32),transparent)]" />
        <div aria-hidden="true" className="pointer-events-none absolute -bottom-40 -right-24 size-[28rem] bg-[radial-gradient(closest-side,rgb(255_45_149/0.22),transparent)]" />
        <div className="relative">
          <Eyebrow index="05">Privacy</Eyebrow>
          <h2 className="mt-7 font-display text-[clamp(1.6rem,3.2vw,2.7rem)] uppercase leading-[1.14] tracking-[0.01em] text-fg">
            Built around <Neon>consent</Neon> and <Neon tone="rose">control</Neon>
          </h2>
          <ul className="mt-8 space-y-4">
            {COMMITMENTS.map((item) => (
              <li key={item} className="flex items-center gap-4 text-base text-fg-muted">
                <span className="grid size-6 shrink-0 place-items-center rounded-full border border-cerulean/50 text-cerulean-glow">
                  <Check className="size-3.5" aria-hidden="true" />
                </span>
                {item}
              </li>
            ))}
          </ul>
          <Link
            href="/privacy"
            className="group mt-9 inline-flex items-center gap-3 text-xs uppercase tracking-[0.3em] text-cerulean-glow transition-colors hover:text-fg"
          >
            Read the privacy approach
            <ArrowUpRight className="size-4 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden="true" />
          </Link>
        </div>
        <ConsentArt className="relative mt-12 lg:mt-0" />
      </div>
    </Section>
  );
}
