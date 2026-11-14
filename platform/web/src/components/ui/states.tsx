import { AlertTriangle, RotateCw, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
  headingLevel = 3,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: ReactNode;
  className?: string;
  /** 2 when it sits straight under the page title, 3 inside a titled section. Levels must not be skipped. */
  headingLevel?: 2 | 3;
}) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-3 rounded-[var(--radius-card)] border border-dashed border-line-strong px-6 py-14 text-center",
        className,
      )}
    >
      <span className="grid size-12 place-items-center rounded-full border border-line-strong bg-white/[0.03] text-cerulean-bright">
        <Icon className="size-5" aria-hidden="true" />
      </span>
      <Heading className="font-heading text-lg font-semibold text-fg">{title}</Heading>
      <p className="max-w-md text-sm leading-relaxed text-fg-muted">{description}</p>
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  description,
  onRetry,
  className,
  headingLevel = 3,
}: {
  title?: string;
  description: string;
  onRetry?: () => void;
  className?: string;
  headingLevel?: 2 | 3;
}) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center gap-3 rounded-[var(--radius-card)] border border-danger/35 bg-danger/[0.06] px-6 py-12 text-center",
        className,
      )}
    >
      <span className="grid size-12 place-items-center rounded-full border border-danger/40 text-danger">
        <AlertTriangle className="size-5" aria-hidden="true" />
      </span>
      <Heading className="font-heading text-lg font-semibold text-fg">{title}</Heading>
      <p className="max-w-md text-sm leading-relaxed text-fg-muted">{description}</p>
      {onRetry ? (
        <Button variant="secondary" size="sm" onClick={onRetry} className="mt-2">
          <RotateCw className="size-4" aria-hidden="true" />
          Try again
        </Button>
      ) : null}
    </div>
  );
}
