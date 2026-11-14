import type { Metadata } from "next";
import { RecordingsView } from "@/components/dashboard/views/recordings-view";

export const instant = false;

export const metadata: Metadata = { title: "Recordings" };

export default function RecordingsPage() {
  return <RecordingsView />;
}
