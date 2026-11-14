import { AlertTriangle, Info } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * A boxed message that stays on the page: `info` for how something works, `warning` for something that
 * changes how much a person should trust what they see. Not a toast, because it must not disappear.
 */
export function Notice({
  tone = "info",
  title,
  children,
  className,
  label,
}: {
  tone?: "info" | "warning";
  title: string;
  children?: ReactNode;
  className?: string;
  /** Names the region for screen readers when the title alone is not enough. */
  label?: string;
}) {
  const Icon = tone === "warning" ? AlertTriangle : Info;
  return (
    <aside
      aria-label={label ?? title}
      className={cn(
        "flex gap-4 border p-5",
        tone === "warning" ? "border-warning/40 bg-warning/[0.07]" : "border-cerulean/35 bg-cerulean/[0.06]",
        className,
      )}
    >
      <Icon
        className={cn("mt-0.5 size-5 shrink-0", tone === "warning" ? "text-warning" : "text-cerulean-glow")}
        aria-hidden="true"
      />
      <div className="space-y-1.5 text-sm leading-relaxed text-fg-muted">
        <p className={cn("font-medium", tone === "warning" ? "text-warning" : "text-fg")}>{title}</p>
        {children}
      </div>
    </aside>
  );
}
