import { cn } from "@/lib/cn";

// Fixed heights (percent). Deterministic on purpose: identical on server and client.
const HEIGHTS = [28, 46, 70, 38, 88, 56, 94, 64, 32, 76, 52, 98, 60, 36, 82, 48, 68, 30, 90, 54, 40, 72, 34, 58];

/** Decorative waveform. Bars pulse only when motion is allowed; otherwise it is a still graphic. */
export function Waveform({ className, bars = HEIGHTS.length }: { className?: string; bars?: number }) {
  return (
    <div aria-hidden="true" className={cn("flex h-24 items-center gap-1.5", className)}>
      {Array.from({ length: bars }, (_, i) => (
        <span
          key={i}
          className={cn(
            "block w-1 origin-center rounded-full motion-safe:animate-bar",
            i % 7 === 3 ? "bg-rose" : "bg-cerulean-bright",
          )}
          style={{
            height: `${HEIGHTS[i % HEIGHTS.length]}%`,
            animationDelay: `${(i * 90) % 1700}ms`,
            opacity: 0.35 + ((i * 13) % 65) / 100,
          }}
        />
      ))}
    </div>
  );
}
