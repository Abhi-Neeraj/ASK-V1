import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { Neon } from "@/components/marketing/section";

export function FinalCta() {
  return (
    <section className="relative isolate overflow-hidden border-t border-line">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute left-1/2 -top-24 h-[30rem] w-[64rem] -translate-x-1/2 bg-[radial-gradient(closest-side,rgb(0_123_167/0.38),transparent)]" />
        <div className="absolute -bottom-24 -right-24 size-[30rem] bg-[radial-gradient(closest-side,rgb(255_45_149/0.2),transparent)]" />
        <div className="bg-grain absolute inset-0 opacity-[0.04]" />
      </div>
      <div className="reveal mx-auto flex max-w-7xl flex-col items-center px-5 py-28 text-center sm:px-8 sm:py-36">
        <p className="caption">Take a closer look</p>
        <h2 className="mt-8 max-w-4xl font-display text-[clamp(1.9rem,5vw,4.2rem)] uppercase leading-[1.1] tracking-[0.01em] text-fg">
          Hear the <Neon>room</Neon>. Keep the <Neon tone="rose">memory</Neon>.
        </h2>
        <p className="mt-8 max-w-xl text-base leading-relaxed text-fg-muted sm:text-lg">
          See the locket, the pipeline and the dashboard up close, or talk to us about a pilot.
        </p>
        <div className="mt-12 flex flex-wrap items-center justify-center gap-x-10 gap-y-6">
          <Link href="/product" className="group inline-flex items-center gap-5 text-fg">
            <span className="grid size-16 place-items-center rounded-full border border-cerulean-glow/70 text-cerulean-glow shadow-[0_0_32px_-6px_rgb(56_182_224/0.7)] transition-all duration-300 group-hover:bg-cerulean group-hover:text-white">
              <ArrowRight className="size-6" aria-hidden="true" />
            </span>
            <span className="text-xs uppercase tracking-[0.3em]">Explore the platform</span>
          </Link>
          <Link
            href="/contact"
            className="text-xs uppercase tracking-[0.3em] text-fg-muted underline-offset-8 transition-colors hover:text-fg hover:underline"
          >
            Contact us
          </Link>
        </div>
      </div>
    </section>
  );
}
