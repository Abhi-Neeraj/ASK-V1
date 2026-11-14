import type { Metadata } from "next";
import Link from "next/link";
import { Logo } from "@/components/brand/logo";
import { Neon } from "@/components/marketing/section";

export const metadata: Metadata = { title: "Page not found" };

export default function NotFound() {
  return (
    <div className="relative isolate grid min-h-dvh place-items-center overflow-hidden px-5">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute -left-40 top-0 size-[44rem] bg-[radial-gradient(closest-side,rgb(0_123_167/0.3),transparent)]" />
        <div className="absolute -bottom-40 -right-32 size-[44rem] bg-[radial-gradient(closest-side,rgb(255_45_149/0.2),transparent)]" />
      </div>
      <main id="main" className="flex max-w-xl flex-col items-center text-center">
        <Logo />
        <p className="caption mt-14">Error 404</p>
        <h1 className="mt-6 font-display text-[clamp(2rem,6vw,3.6rem)] uppercase leading-[1.1]">
          This page <Neon tone="rose">went quiet</Neon>
        </h1>
        <p className="mt-6 text-base leading-relaxed text-fg-muted">
          The page you are looking for does not exist, or it moved.
        </p>
        <div className="mt-10 flex flex-wrap justify-center gap-4">
          <Link
            href="/"
            className="rounded-full border border-cerulean-glow/70 px-7 py-3 text-xs uppercase tracking-[0.3em] text-cerulean-glow transition-colors hover:bg-cerulean hover:text-white"
          >
            Back to home
          </Link>
          <Link
            href="/dashboard"
            className="rounded-full border border-line-strong px-7 py-3 text-xs uppercase tracking-[0.3em] text-fg-muted transition-colors hover:text-fg"
          >
            Dashboard
          </Link>
        </div>
      </main>
    </div>
  );
}
