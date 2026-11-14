import Link from "next/link";
import { cn } from "@/lib/cn";

/** Original mark: a locket ring holding a small voice waveform, with one rose accent. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={cn("size-7", className)} fill="none">
      <circle cx="16" cy="16" r="13" stroke="#38b6e0" strokeWidth="2" />
      <path d="M13.5 12.5v7M16.5 9.5v13M19.5 12.5v7" stroke="#fff" strokeWidth="2" strokeLinecap="round" />
      <circle cx="25.5" cy="6.5" r="2.4" fill="#ff2d95" />
    </svg>
  );
}

export function Logo({ className, href = "/" }: { className?: string; href?: string }) {
  return (
    <Link href={href} aria-label="ASK home" className={cn("inline-flex items-center gap-3", className)}>
      <LogoMark />
      <span className="font-display text-lg tracking-[0.12em] text-fg">ASK</span>
    </Link>
  );
}
