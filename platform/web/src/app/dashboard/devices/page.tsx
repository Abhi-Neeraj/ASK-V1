import type { Metadata } from "next";
import { DevicesView } from "@/components/dashboard/views/devices-view";

export const instant = false;

export const metadata: Metadata = { title: "Devices" };

export default function DevicesPage() {
  return <DevicesView />;
}
