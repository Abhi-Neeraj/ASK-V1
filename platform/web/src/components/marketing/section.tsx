import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** Accent word inside a display heading: lit blue or neon rose. */
export function Neon({ tone = "blue", children }: { tone?: "blue" | "rose"; children: ReactNode }) {
  return <span className={tone === "blue" ? "neon-blue" : "neon-rose"}>{children}</span>;
}

export function Section({
  id,
  className,
  children,
}: {
  id?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className={cn("relative mx-auto max-w-7xl scroll-mt-24 px-5 py-20 sm:px-8 sm:py-28", className)}>
      {children}
    </section>
  );
}

/** Tracked-caps eyebrow with an index and a hairline, as in the masthead. */
export function Eyebrow({ index, children }: { index?: string; children: ReactNode }) {
  return (
    <p className="caption flex items-center gap-4">
      {index ? <span className="text-rose">{index}</span> : null}
      <span aria-hidden="true" className="h-px w-10 bg-line-strong" />
      <span>{children}</span>
    </p>
  );
}

export function SectionHeading({
  index,
  eyebrow,
  title,
  description,
  className,
}: {
  index?: string;
  eyebrow: string;
  title: ReactNode;
  description?: string;
  className?: string;
}) {
  return (
    <div className={cn("reveal max-w-4xl", className)}>
      <Eyebrow index={index}>{eyebrow}</Eyebrow>
      <h2 className="mt-7 font-display text-[clamp(1.6rem,3.7vw,3.1rem)] uppercase leading-[1.14] tracking-[0.01em] text-fg">
        {title}
      </h2>
      {description ? (
        <p className="mt-6 max-w-2xl text-base leading-relaxed text-fg-muted sm:text-lg">{description}</p>
      ) : null}
    </div>
  );
}

/** Page-level title block for inner marketing pages. */
export function PageHero({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: ReactNode;
  description: string;
}) {
  return (
    <div className="relative isolate overflow-hidden border-b border-line">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute -left-40 -top-32 size-[40rem] bg-[radial-gradient(closest-side,rgb(0_123_167/0.4),transparent)]" />
        <div className="absolute -right-32 -top-10 size-[36rem] bg-[radial-gradient(closest-side,rgb(255_45_149/0.22),transparent)]" />
        <div className="hero-grid" />
        <div className="bg-grain absolute inset-0 opacity-[0.04]" />
      </div>
      <div className="mx-auto max-w-7xl px-5 pb-20 pt-20 sm:px-8 sm:pb-28 sm:pt-28">
        <Eyebrow>{eyebrow}</Eyebrow>
        <h1 className="mt-8 max-w-5xl font-display text-[clamp(2rem,5.4vw,4.6rem)] uppercase leading-[1.08] tracking-[0.01em] text-fg">
          {title}
        </h1>
        <p className="mt-8 max-w-2xl text-base leading-relaxed text-fg-muted sm:text-lg">{description}</p>
      </div>
    </div>
  );
}
