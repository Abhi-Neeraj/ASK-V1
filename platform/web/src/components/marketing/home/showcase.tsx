import { Clock, Pencil, Users } from "lucide-react";
import { ProductPreview } from "@/components/brand/product-preview";
import { Neon, Section, SectionHeading } from "@/components/marketing/section";

const POINTS = [
  { icon: Clock, text: "Timestamped to the millisecond" },
  { icon: Users, text: "Labelled by speaker" },
  { icon: Pencil, text: "Easy to review and correct" },
] as const;

export function Showcase() {
  return (
    <Section id="showcase" className="pt-8 sm:pt-12">
      <div className="grid items-center gap-16 lg:grid-cols-[0.8fr_1.2fr] lg:gap-20">
        <div>
          <SectionHeading
            index="01"
            eyebrow="The result"
            title={
              <>
                Every clip, <Neon>read</Neon> and <Neon tone="rose">labelled</Neon>
              </>
            }
            description="Open a recording to hear it, read it and see who said what."
          />
          <ul className="reveal mt-10 space-y-5">
            {POINTS.map(({ icon: Icon, text }) => (
              <li key={text} className="flex items-center gap-4 text-base text-fg">
                <span className="grid size-10 shrink-0 place-items-center rounded-full border border-cerulean/50 bg-cerulean/10 text-cerulean-glow">
                  <Icon className="size-[1.1rem]" aria-hidden="true" />
                </span>
                {text}
              </li>
            ))}
          </ul>
        </div>
        <ProductPreview className="reveal" />
      </div>
    </Section>
  );
}
