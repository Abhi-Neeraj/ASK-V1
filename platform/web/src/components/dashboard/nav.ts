import { AudioLines, LayoutDashboard, Radio, ScrollText, Settings, Users, type LucideIcon } from "lucide-react";
import type { Crumb } from "@/components/ui/breadcrumbs";
import type { Role } from "@/lib/api/types";

export type NavItem = { href: string; label: string; icon: LucideIcon; exact?: boolean; roles?: Role[] };

export const DASHBOARD_NAV: NavItem[] = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard, exact: true },
  { href: "/dashboard/devices", label: "Devices", icon: Radio },
  { href: "/dashboard/recordings", label: "Recordings", icon: AudioLines },
  { href: "/dashboard/people", label: "People", icon: Users },
  { href: "/dashboard/audit", label: "Audit log", icon: ScrollText, roles: ["admin"] },
  { href: "/dashboard/settings", label: "Settings", icon: Settings },
];

/** What this role is shown. Hiding a link is a courtesy: the server decides who may read the page. */
export function navFor(role: Role): NavItem[] {
  return DASHBOARD_NAV.filter((item) => !item.roles || item.roles.includes(role));
}

const SEGMENT_LABELS: Record<string, string> = {
  devices: "Devices",
  recordings: "Recordings",
  people: "People",
  audit: "Audit log",
  settings: "Settings",
};

export function isActive(item: NavItem, pathname: string): boolean {
  return item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(`${item.href}/`);
}

/** Breadcrumb trail from the URL. The dynamic recording segment is shown as "Recording". */
export function breadcrumbsFor(pathname: string): Crumb[] {
  const segments = pathname.split("/").filter(Boolean).slice(1);
  const crumbs: Crumb[] = [{ label: "Dashboard", href: "/dashboard" }];
  let href = "/dashboard";
  segments.forEach((segment, index) => {
    href += `/${segment}`;
    const known = SEGMENT_LABELS[segment];
    const isLast = index === segments.length - 1;
    crumbs.push({ label: known ?? "Recording", href: isLast ? undefined : href });
  });
  return crumbs;
}
