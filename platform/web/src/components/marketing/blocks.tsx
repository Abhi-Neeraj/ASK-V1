import type { LucideIcon } from "lucide-react";
import { Info } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Eyebrow, Section } from "@/components/marketing/section";

export type Feature = { icon: LucideIcon; title: string; body: string };

/** Editorial hairline grid of short features. */
export function FeatureGrid({ items, className }: { items: readonly Feature[]; className?: string }) {
  return (
    <div className={cn("grid border-l border-t border-line-strong sm:grid-cols-2 lg:grid-cols-3", className)}>
      {items.map(({ icon: Icon, title, body }, index) => (
        <article
          key={title}
          className="reveal group relative border-b border-r border-line-strong p-8 transition-colors duration-300 hover:bg-cerulean/[0.06] sm:p-10"
        >
          <div className="flex items-center justify-between">
            <Icon className="size-6 text-cerulean-glow" aria-hidden="true" />
            <span className="font-mono text-xs text-fg-subtle">0{index + 1}</span>
          </div>
          <h3 className="mt-10 font-heading text-sm font-medium uppercase tracking-[0.2em] text-fg">{title}</h3>
          <p className="mt-4 text-sm leading-relaxed text-fg-muted">{body}</p>
        </article>
      ))}
    </div>
  );
}

/** Heading on the left, content on the right. Collapses to a single column on small screens. */
export function SplitSection({
  id,
  index,
  eyebrow,
  title,
  children,
}: {
  id?: string;
  index: string;
  eyebrow: string;
  title: ReactNode;
  children: ReactNode;
}) {
  return (
    <Section id={id} className="border-b border-line py-16 last:border-b-0 sm:py-24">
      <div className="grid gap-12 lg:grid-cols-[0.85fr_1.15fr] lg:gap-20">
        <div className="reveal lg:sticky lg:top-28 lg:self-start">
          <Eyebrow index={index}>{eyebrow}</Eyebrow>
          <h2 className="mt-7 font-display text-[clamp(1.5rem,3vw,2.4rem)] uppercase leading-[1.15] tracking-[0.01em] text-fg">
            {title}
          </h2>
        </div>
        <div className="reveal space-y-6 text-base leading-relaxed text-fg-muted">{children}</div>
      </div>
    </Section>
  );
}

/** Numbered vertical steps with a connecting hairline. */
export function Steps({ items }: { items: readonly { title: string; body: string }[] }) {
  return (
    <ol className="relative space-y-8 border-l border-line-strong pl-8">
      {items.map((item, index) => (
        <li key={item.title} className="relative">
          <span className="absolute -left-[2.45rem] top-0.5 grid size-5 place-items-center rounded-full border border-cerulean/60 bg-ink font-mono text-[0.625rem] text-cerulean-glow">
            {index + 1}
          </span>
          <h3 className="font-heading text-sm font-medium uppercase tracking-[0.18em] text-fg">{item.title}</h3>
          <p className="mt-2 text-sm leading-relaxed text-fg-muted">{item.body}</p>
        </li>
      ))}
    </ol>
  );
}

/** Specification list: label on the left, value on the right. */
export function SpecList({ items }: { items: readonly { label: string; value: string }[] }) {
  return (
    <dl className="divide-y divide-line border-y border-line-strong">
      {items.map((item) => (
        <div key={item.label} className="grid gap-1 py-4 sm:grid-cols-[10rem_1fr] sm:gap-6">
          <dt className="caption">{item.label}</dt>
          <dd className="text-sm text-fg">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Plain-language limitation or honesty note. */
export function Callout({ title, children }: { title: string; children: ReactNode }) {
  return (
    <aside className="flex gap-4 border border-cerulean/35 bg-cerulean/[0.06] p-6">
      <Info className="mt-0.5 size-5 shrink-0 text-cerulean-glow" aria-hidden="true" />
      <div className="space-y-2 text-sm leading-relaxed text-fg-muted">
        <p className="font-medium text-fg">{title}</p>
        {children}
      </div>
    </aside>
  );
}

/** Two-up comparison cards, used for diarization vs speaker recognition. */
export function Compare({
  items,
}: {
  items: readonly { label: string; title: string; body: string; tone: "blue" | "rose" }[];
}) {
  return (
    <div className="grid gap-px border border-line-strong bg-line-strong sm:grid-cols-2">
      {items.map((item) => (
        <div key={item.title} className="bg-ink p-7">
          <p className={cn("caption", item.tone === "blue" ? "text-cerulean-glow" : "text-rose")}>{item.label}</p>
          <h3 className="mt-4 font-heading text-base font-medium text-fg">{item.title}</h3>
          <p className="mt-3 text-sm leading-relaxed text-fg-muted">{item.body}</p>
        </div>
      ))}
    </div>
  );
}
