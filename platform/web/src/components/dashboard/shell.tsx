"use client";

import { LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { Logo } from "@/components/brand/logo";
import { DeviceDetectedPrompt } from "@/components/dashboard/device-prompt";
import { LiveStatusPill } from "@/components/dashboard/live-status";
import { breadcrumbsFor, isActive, navFor } from "@/components/dashboard/nav";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api/client";
import { useLogout, useMe } from "@/lib/api/hooks";
import type { User } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { DEMO_MODE } from "@/lib/env";
import { LiveUpdatesProvider } from "@/lib/live/provider";

/**
 * Authenticated frame. The signed-in user comes from the API on every load; if the API says the
 * session is missing or expired, the browser is sent to the sign-in page and returns here after.
 * This check is a convenience only: every API call is authorized again on the server.
 */
export function DashboardShell({ children }: { children: ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const pathname = usePathname();

  const unauthenticated = me.isError && me.error instanceof ApiError && me.error.status === 401;
  useEffect(() => {
    if (unauthenticated) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [unauthenticated, pathname, router]);

  if (me.isPending || unauthenticated) return <ShellSkeleton />;

  if (me.isError) {
    return (
      <div className="grid min-h-dvh place-items-center p-6">
        <ErrorState
          title="We could not load your workspace"
          description={me.error.message}
          onRetry={() => me.refetch()}
          className="max-w-lg"
        />
      </div>
    );
  }

  return (
    // Live updates only run while someone is signed in, which is exactly when this renders.
    <LiveUpdatesProvider>
      <div className="min-h-dvh lg:grid lg:grid-cols-[16.5rem_1fr]">
        <Sidebar user={me.data} />
        <div className="min-w-0">
          {DEMO_MODE ? <DemoBanner /> : null}
          <header className="flex items-center justify-between gap-4 border-b border-line px-5 py-4 sm:px-10">
            <Breadcrumbs items={breadcrumbsFor(pathname)} />
            <LiveStatusPill />
          </header>
          <MobileNav pathname={pathname} role={me.data.role} />
          <main id="main" className="mx-auto max-w-6xl px-5 py-8 sm:px-10 sm:py-10">
            {children}
          </main>
        </div>
      </div>
      <DeviceDetectedPrompt />
    </LiveUpdatesProvider>
  );
}

function DemoBanner() {
  return (
    <div role="status" className="border-b border-warning/40 bg-warning/[0.09] px-5 py-2.5 text-center text-xs text-warning sm:px-10">
      Demo mode. This build shows invented sample data and is not connected to a backend. Nothing here is real.
    </div>
  );
}

function Sidebar({ user }: { user: User }) {
  const pathname = usePathname();
  const router = useRouter();
  const logout = useLogout();

  return (
    <aside className="hidden border-r border-line bg-ink-raised/40 lg:block">
      <div className="sticky top-0 flex h-dvh flex-col px-5 py-6">
        <Logo href="/dashboard" />
        <nav aria-label="Dashboard" className="mt-10 flex flex-1 flex-col gap-1">
          {navFor(user.role).map((item) => {
            const active = isActive(item, pathname);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors",
                  active ? "bg-cerulean/12 text-fg" : "text-fg-muted hover:bg-white/[0.05] hover:text-fg",
                )}
              >
                {active ? <span aria-hidden="true" className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-cerulean-glow" /> : null}
                <item.icon className={cn("size-[1.15rem]", active && "text-cerulean-glow")} aria-hidden="true" />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-line pt-5">
          <p className="truncate text-sm font-medium text-fg">{user.name}</p>
          <p className="truncate text-xs text-fg-muted">{user.company.name}</p>
          <p className="caption mt-2 text-fg-subtle">{user.role}</p>
          <Button
            variant="ghost"
            size="sm"
            className="-ml-3 mt-3 justify-start"
            loading={logout.isPending}
            onClick={() => logout.mutate(undefined, { onSettled: () => router.replace("/login") })}
          >
            <LogOut className="size-4" aria-hidden="true" />
            Sign out
          </Button>
        </div>
      </div>
    </aside>
  );
}

/** Small screens get the same destinations as a horizontally scrollable strip. */
function MobileNav({ pathname, role }: { pathname: string; role: User["role"] }) {
  return (
    <nav aria-label="Dashboard" className="flex gap-1 overflow-x-auto border-b border-line px-3 py-2 lg:hidden">
      {navFor(role).map((item) => {
        const active = isActive(item, pathname);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex shrink-0 items-center gap-2 rounded-full px-4 py-2 text-sm transition-colors",
              active ? "bg-cerulean/15 text-fg" : "text-fg-muted hover:text-fg",
            )}
          >
            <item.icon className="size-4" aria-hidden="true" />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

export function ShellSkeleton() {
  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[16.5rem_1fr]" aria-busy="true" aria-label="Loading your workspace">
      <div className="hidden space-y-4 border-r border-line p-6 lg:block">
        <Skeleton className="h-8 w-24" />
        <Skeleton className="mt-10 h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
      <div className="space-y-6 p-6 sm:p-10">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-10 w-72" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      </div>
    </div>
  );
}
