import type { Metadata } from "next";
import { AuditView } from "@/components/dashboard/views/audit-view";

export const instant = false;

export const metadata: Metadata = { title: "Audit log" };

export default function AuditPage() {
  return <AuditView />;
}
