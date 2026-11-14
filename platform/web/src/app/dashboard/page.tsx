import type { Metadata } from "next";
import { OverviewView } from "@/components/dashboard/views/overview-view";

// Authenticated, client-rendered page: see the note in dashboard/layout.tsx.
export const instant = false;

export const metadata: Metadata = { title: "Overview" };

export default function DashboardPage() {
  return <OverviewView />;
}
