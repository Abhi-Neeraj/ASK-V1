import { CheckCircle2, Radio } from "lucide-react";
import { Waveform } from "@/components/brand/waveform";
import { cn } from "@/lib/cn";

type Tone = "blue" | "rose" | "muted";

// Illustrative lines only (the example transcript from the product brief), not real results.
const LINES: Array<{ start: string; end: string; who: string; tone: Tone; text: string; review?: boolean }> = [
  { start: "00:00:02.100", end: "00:00:05.800", who: "Priya Nair", tone: "blue", text: "Good morning, everyone." },
  { start: "00:00:06.000", end: "00:00:09.200", who: "Marcus Webb", tone: "rose", text: "Let's discuss the project." },
  { start: "00:00:09.500", end: "00:00:11.000", who: "Unknown speaker", tone: "muted", text: "I'll send the files.", review: true },
];

const CHIP: Record<Tone, string> = {
  blue: "border-cerulean/50 bg-cerulean/12 text-cerulean-glow",
  rose: "border-rose/45 bg-rose/10 text-rose-soft",
  muted: "border-line-strong bg-white/[0.04] text-fg-muted",
};

/**
 * Illustration of the transcript view: waveform, timestamps, speaker labels and a review flag.
 * Built from plain markup so it stays sharp and light. It is artwork, not live data.
 */
export function ProductPreview({ className }: { className?: string }) {
  return (
    <figure className={cn("relative", className)}>
      <div
        role="img"
        aria-label="Illustration of the ASK transcript view: a waveform above timestamped lines with speaker names, one marked as needing review."
        className="relative"
      >
        <div aria-hidden="true" className="glass overflow-hidden rounded-2xl shadow-[0_40px_120px_-36px_rgb(0_123_167/0.6)]">
          <div className="flex items-center gap-3 border-b border-line px-5 py-3.5">
            <span className="flex gap-1.5">
              <span className="size-2.5 rounded-full bg-white/15" />
              <span className="size-2.5 rounded-full bg-white/15" />
              <span className="size-2.5 rounded-full bg-white/15" />
            </span>
            <span className="caption truncate text-fg-subtle">Recording · Locket A7F2</span>
          </div>

          <div className="space-y-6 p-5 sm:p-7">
            <div className="relative">
              <Waveform className="h-14 w-full justify-between" bars={48} />
              <span className="absolute inset-y-0 left-[38%] w-px bg-gradient-to-b from-transparent via-rose to-transparent" />
            </div>

            <ul className="divide-y divide-line">
              {LINES.map((line) => (
                <li key={line.start} className="grid gap-x-5 gap-y-1.5 py-4 first:pt-0 last:pb-0 sm:grid-cols-[8.5rem_1fr]">
                  <span className="font-mono text-[0.6875rem] leading-6 text-fg-subtle">
                    {line.start}
                    <span className="hidden sm:block">{line.end}</span>
                  </span>
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={cn("rounded-full border px-2.5 py-0.5 text-xs font-medium", CHIP[line.tone])}>
                        {line.who}
                      </span>
                      {line.review ? (
                        <span className="rounded-full border border-warning/40 bg-warning/10 px-2 py-0.5 text-[0.6875rem] text-warning">
                          Needs review
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-2 text-sm text-fg">{line.text}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </div>

        {/* floating status cards add depth; hidden on small screens to avoid overflow */}
        <div
          aria-hidden="true"
          className="glass absolute -top-5 right-6 hidden items-center gap-3 rounded-xl bg-ink px-4 py-3 shadow-xl sm:flex"
        >
          <span className="relative grid size-8 place-items-center rounded-full bg-cerulean/20 text-cerulean-glow">
            <Radio className="size-4" />
            <span className="absolute inset-0 rounded-full border border-cerulean-glow/60 motion-safe:animate-ring" />
          </span>
          <span>
            <span className="block text-xs font-medium text-fg">Locket A7F2</span>
            <span className="block text-[0.6875rem] text-cerulean-glow">Online</span>
          </span>
        </div>
        <div
          aria-hidden="true"
          className="glass absolute -bottom-5 left-6 hidden items-center gap-3 rounded-xl bg-ink px-4 py-3 shadow-xl sm:flex"
        >
          <CheckCircle2 className="size-5 text-success" />
          <span>
            <span className="block text-xs font-medium text-fg">Upload verified</span>
            <span className="block text-[0.6875rem] text-fg-muted">Stored and acknowledged</span>
          </span>
        </div>
      </div>
      <figcaption className="caption mt-10 text-fg-subtle">Illustrative example, not a real recording</figcaption>
    </figure>
  );
}
