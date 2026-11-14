import type { Metadata } from "next";
import { PeopleView } from "@/components/dashboard/views/people-view";

export const instant = false;

export const metadata: Metadata = { title: "People" };

export default function PeoplePage() {
  return <PeopleView />;
}
