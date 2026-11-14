import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

/** Loading placeholder. The shimmer only runs when the user has not asked for reduced motion. */
export function Skeleton({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "relative overflow-hidden rounded-lg bg-white/[0.06]",
        "after:absolute after:inset-0 after:bg-gradient-to-r after:from-transparent after:via-white/[0.07] after:to-transparent motion-safe:after:animate-shimmer",
        className,
      )}
      {...props}
    />
  );
}
