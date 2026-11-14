import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { HeroLocket } from "@/components/brand/hero-locket";

/**
 * Cinematic hero: wide display type stacked in blue and rose on the left, the locket as a
 * neon-graded product object on the right, tiny tracked captions, and an outlined circular
 * arrow as the primary action. Glows are plain radial gradients (cheap to paint).
 */
export function Hero() {
  return (
    <div className="relative isolate overflow-hidden">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute -left-48 -top-24 size-[52rem] bg-[radial-gradient(closest-side,rgb(0_123_167/0.42),transparent)]" />
        <div className="absolute -right-40 top-8 size-[56rem] bg-[radial-gradient(closest-side,rgb(255_45_149/0.26),transparent)]" />
        <div className="bg-grain absolute inset-0 opacity-[0.04]" />
        <div className="absolute inset-x-0 bottom-0 h-56 bg-gradient-to-b from-transparent to-ink" />
      </div>

      <div className="mx-auto grid max-w-7xl items-center px-5 sm:px-8 lg:min-h-[44rem] lg:grid-cols-[1fr_1fr]">
        <div className="relative z-10 py-14 lg:py-20">
          <p className="caption">
            Voice intelligence
            <br />
            for teams
          </p>

          <h1 className="mt-8 font-display text-[clamp(2.6rem,7.4vw,6.6rem)] uppercase leading-[1.02] tracking-[0.01em]">
            <span className="neon-blue block">Voice</span>
            <span className="neon-rose block">Memory</span>
          </h1>

          <p className="mt-9 max-w-md text-base leading-relaxed text-fg-muted sm:text-lg">
            A discreet locket that records, and a private platform that turns every clip into a searchable,
            speaker-labelled transcript.
          </p>

          <p className="caption mt-9">
            Where every conversation meets <span className="text-rose">memory</span>
          </p>

          <div className="mt-10 flex flex-wrap items-center gap-x-8 gap-y-5">
            <Link href="/product" className="group inline-flex items-center gap-5 rounded-full text-fg">
              <span className="grid size-16 place-items-center rounded-full border border-cerulean-glow/70 text-cerulean-glow shadow-[0_0_32px_-6px_rgb(56_182_224/0.7)] transition-all duration-300 group-hover:bg-cerulean group-hover:text-white group-hover:shadow-glow-cerulean">
                <ArrowRight
                  className="size-6 transition-transform duration-300 group-hover:translate-x-0.5"
                  aria-hidden="true"
                />
              </span>
              <span className="text-xs uppercase tracking-[0.3em]">Explore the platform</span>
            </Link>
            <Link
              href="/login"
              className="rounded-full border border-line-strong px-6 py-3 text-xs uppercase tracking-[0.3em] text-fg-muted transition-colors hover:border-cerulean/60 hover:text-fg"
            >
              Sign in
            </Link>
          </div>
        </div>

        <div className="relative -mx-5 flex justify-center sm:-mx-8 lg:mx-0 lg:justify-end">
          <HeroLocket className="w-full max-w-[30rem] lg:w-[122%] lg:max-w-none lg:-mr-16" />
        </div>
      </div>
    </div>
  );
}
