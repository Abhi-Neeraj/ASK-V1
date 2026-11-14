import type { Metadata } from "next";
import { Suspense, type ReactNode } from "react";
import { DashboardShell, ShellSkeleton } from "@/components/dashboard/shell";

// The dashboard is an authenticated, client-rendered app: what it shows depends on who is signed in,
// so there is no meaningful static shell to validate. Public pages keep instant-navigation validation.
export const instant = false;

export const metadata: Metadata = {
  title: { default: "Dashboard", template: "%s · ASK" },
  // Authenticated pages should never be indexed.
  robots: { index: false, follow: false },
};

export default function DashboardLayout({ children }: { children: ReactNode }) {
  // The shell reads the current URL (usePathname), which is request-time data under Cache
  // Components, so it streams in behind Suspense and the skeleton is part of the static shell.
  return (
    <Suspense fallback={<ShellSkeleton />}>
      <DashboardShell>{children}</DashboardShell>
    </Suspense>
  );
}
