import { Check, Search, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/*
 * Small decorative vignettes for the capability cards. Pure markup, no data: each one is
 * hidden from assistive tech because the card's own heading and text carry the meaning.
 */

function Frame({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn("relative flex h-36 flex-col justify-center overflow-hidden rounded-xl border border-line bg-ink/70 p-4", className)}
    >
      {children}
    </div>
  );
}

export function SearchArt() {
  return (
    <Frame>
      <div className="flex h-9 items-center gap-2.5 rounded-lg border border-cerulean/50 bg-cerulean/[0.07] px-3 text-xs text-fg">
        <Search className="size-3.5 text-cerulean-glow" />
        delivery window
        <span className="ml-auto h-4 w-px bg-cerulean-glow motion-safe:animate-pulse" />
      </div>
      <div className="mt-3.5 space-y-2.5 text-[0.6875rem] leading-snug text-fg-muted">
        <p>
          …confirm the <mark className="rounded bg-cerulean/30 px-0.5 text-fg">delivery window</mark> for the east…
        </p>
        <p>
          …the <mark className="rounded bg-cerulean/30 px-0.5 text-fg">delivery window</mark> moved to Friday…
        </p>
      </div>
    </Frame>
  );
}

export function SpeakerArt() {
  return (
    <Frame>
      <div className="flex h-2.5 gap-1">
        <span className="w-[28%] rounded-full bg-cerulean-glow" />
        <span className="w-[22%] rounded-full bg-rose" />
        <span className="w-[12%] rounded-full bg-white/25" />
        <span className="w-[26%] rounded-full bg-cerulean-glow" />
        <span className="w-[12%] rounded-full bg-rose" />
      </div>
      <div className="mt-4 flex flex-wrap gap-2 text-[0.6875rem]">
        <span className="rounded-full border border-cerulean/50 bg-cerulean/10 px-2.5 py-1 text-cerulean-glow">Priya · 93%</span>
        <span className="rounded-full border border-rose/45 bg-rose/10 px-2.5 py-1 text-rose-soft">Marcus · 88%</span>
        <span className="rounded-full border border-line-strong bg-white/[0.04] px-2.5 py-1 text-fg-muted">Unknown</span>
      </div>
    </Frame>
  );
}

export function DeviceArt() {
  return (
    <Frame>
      <svg viewBox="0 0 240 56" className="h-14 w-full" fill="none" preserveAspectRatio="none">
        <path
          d="M0 30H62l8-4 8 8 8-26 10 46 9-24h20l8-6 8 10 8-12h55"
          stroke="#7fd0f5"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
        <circle cx="228" cy="26" r="4" fill="#38b6e0" />
      </svg>
      <div className="mt-3 flex items-center gap-2 text-[0.6875rem] text-cerulean-glow">
        <span className="relative flex size-2">
          <span className="absolute inline-flex size-full rounded-full bg-cerulean-glow opacity-70 motion-safe:animate-ping" />
          <span className="relative inline-flex size-2 rounded-full bg-cerulean-glow" />
        </span>
        Online · heartbeat received
      </div>
    </Frame>
  );
}

export function ProcessingArt() {
  const stages = ["Uploaded", "Queued", "Transcribing", "Speakers", "Done"];
  return (
    <Frame>
      <div className="relative flex items-center justify-between px-1">
        <span className="absolute inset-x-2 top-1/2 h-px -translate-y-1/2 bg-line-strong" />
        <span className="absolute left-2 top-1/2 h-px w-[52%] -translate-y-1/2 bg-cerulean-glow" />
        {stages.map((stage, index) => (
          <span
            key={stage}
            className={cn(
              "relative z-10 grid size-5 place-items-center rounded-full border text-[0.5rem]",
              index < 2 && "border-cerulean-glow bg-cerulean text-white",
              index === 2 && "border-cerulean-glow bg-ink text-cerulean-glow motion-safe:animate-pulse",
              index > 2 && "border-line-strong bg-ink text-transparent",
            )}
          >
            {index < 2 ? <Check className="size-3" /> : "•"}
          </span>
        ))}
      </div>
      <div className="mt-4 flex justify-between text-[0.5625rem] uppercase tracking-wider text-fg-subtle">
        <span>Uploaded</span>
        <span className="text-cerulean-glow">Transcribing</span>
        <span>Done</span>
      </div>
    </Frame>
  );
}

export function WorkspaceArt() {
  const roles = [
    { name: "Administrator", tone: "border-rose/45 text-rose-soft" },
    { name: "Manager", tone: "border-cerulean/50 text-cerulean-glow" },
    { name: "Employee", tone: "border-line-strong text-fg-muted" },
  ];
  return (
    <Frame>
      <div className="space-y-2">
        {roles.map((role, index) => (
          <div
            key={role.name}
            style={{ marginLeft: `${index * 14}px` }}
            className="flex items-center gap-2.5 rounded-lg border border-line bg-white/[0.03] px-3 py-1.5"
          >
            <span className="size-5 rounded-full bg-gradient-to-br from-cerulean-glow to-rose" />
            <span className="h-1.5 w-16 rounded-full bg-white/15" />
            <span className={cn("ml-auto rounded-full border px-2 py-0.5 text-[0.5625rem]", role.tone)}>{role.name}</span>
          </div>
        ))}
      </div>
    </Frame>
  );
}

export function AuditArt() {
  const rows = ["Device paired", "Transcript exported", "Recording deleted"];
  return (
    <Frame>
      <ul className="space-y-2.5">
        {rows.map((row, index) => (
          <li key={row} className="flex items-center gap-2.5 text-[0.6875rem] text-fg-muted">
            <ShieldCheck className="size-3.5 text-cerulean-glow" />
            {row}
            <span className="ml-auto font-mono text-fg-subtle">{`0${index + 1}:2${index}`}</span>
          </li>
        ))}
      </ul>
      <div className="mt-3 flex gap-2 text-[0.5625rem] uppercase tracking-wider">
        <span className="rounded border border-line-strong px-1.5 py-0.5 text-fg-muted">TXT</span>
        <span className="rounded border border-line-strong px-1.5 py-0.5 text-fg-muted">JSON</span>
      </div>
    </Frame>
  );
}

/** Consent card for the privacy section: a profile with its consent record and a delete control. */
export function ConsentArt({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn("glass relative rounded-2xl p-6 shadow-[0_30px_90px_-40px_rgb(255_45_149/0.55)]", className)}
    >
      <div className="flex items-center gap-4">
        <span className="grid size-12 place-items-center rounded-full bg-gradient-to-br from-cerulean-glow/80 to-rose/80 text-ink">
          <ShieldCheck className="size-6" />
        </span>
        <div>
          <p className="text-sm font-medium text-fg">Voice profile</p>
          <p className="text-xs text-fg-muted">Priya Nair</p>
        </div>
        <span className="ml-auto rounded-full border border-success/40 bg-success/10 px-2.5 py-1 text-[0.6875rem] text-success">
          Consent recorded
        </span>
      </div>
      <dl className="mt-6 grid grid-cols-2 gap-4 border-t border-line pt-5 text-xs">
        <div>
          <dt className="caption text-fg-subtle">Recorded by</dt>
          <dd className="mt-1.5 text-fg">Administrator</dd>
        </div>
        <div>
          <dt className="caption text-fg-subtle">Samples</dt>
          <dd className="mt-1.5 text-fg">3 accepted</dd>
        </div>
      </dl>
      <div className="mt-6 flex gap-3">
        <span className="rounded-full border border-line-strong px-4 py-2 text-xs text-fg-muted">Replace voice</span>
        <span className="rounded-full border border-danger/50 bg-danger/10 px-4 py-2 text-xs text-danger">Delete profile</span>
      </div>
    </div>
  );
}
