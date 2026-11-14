import { LOCKET_PARTS, LocketExploded } from "@/components/brand/locket-exploded";
import { Neon, Section, SectionHeading } from "@/components/marketing/section";

export function Anatomy() {
  return (
    <Section id="locket" className="pt-0 sm:pt-0">
      <div className="grid items-center gap-16 lg:grid-cols-[1.05fr_0.95fr] lg:gap-20">
        <LocketExploded className="reveal order-2 lg:order-1" uid="home-exploded" />
        <div className="order-1 lg:order-2">
          <SectionHeading
            index="02"
            eyebrow="The locket"
            title={
              <>
                Small, simple, <Neon>self-contained</Neon>
              </>
            }
            description="Five parts, one job: capture audio and get it safely to your server."
          />
          <ol className="reveal mt-10 divide-y divide-line border-y border-line-strong">
            {LOCKET_PARTS.map((part) => (
              <li key={part.n} className="flex items-start gap-4 py-4">
                <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full border border-rose font-mono text-[0.6875rem] text-rose">
                  {part.n}
                </span>
                <div>
                  <p className="text-sm font-medium text-fg">{part.title}</p>
                  <p className="mt-0.5 text-sm text-fg-muted">{part.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </Section>
  );
}
