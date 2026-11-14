import type { ReactNode } from "react";
import { DEMO_MODE } from "@/lib/env";

/** Title block and body shared by every authentication page. */
export function AuthCard({
  title,
  description,
  children,
  footer,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="w-full max-w-md">
      <h1 className="font-display text-[clamp(1.5rem,3vw,2rem)] uppercase leading-[1.15] tracking-[0.01em] text-fg">
        {title}
      </h1>
      {description ? <p className="mt-4 text-sm leading-relaxed text-fg-muted">{description}</p> : null}
      {DEMO_MODE ? (
        <p className="mt-6 border border-warning/40 bg-warning/[0.08] px-4 py-3 text-xs leading-relaxed text-warning">
          Demo mode: this build uses sample data and no real account. Any details will sign you in.
        </p>
      ) : null}
      <div className="mt-8">{children}</div>
      {footer ? <div className="mt-8 text-sm text-fg-muted">{footer}</div> : null}
    </div>
  );
}

export function FormAlert({ id, children }: { id: string; children: ReactNode }) {
  return (
    <p id={id} role="alert" className="border border-danger/40 bg-danger/[0.08] px-4 py-3 text-sm text-danger">
      {children}
    </p>
  );
}
