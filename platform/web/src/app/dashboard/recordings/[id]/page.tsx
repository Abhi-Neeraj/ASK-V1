import type { Metadata } from "next";
import { Suspense } from "react";
import { RecordingDetailView } from "@/components/dashboard/views/recording-detail-view";
import { Skeleton } from "@/components/ui/skeleton";

export const instant = false;

export const metadata: Metadata = { title: "Recording" };

// Under Cache Components, `params` is a request-time value, so it is awaited inside Suspense.
export default function RecordingPage({ params }: PageProps<"/dashboard/recordings/[id]">) {
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <RecordingFromParams params={params} />
    </Suspense>
  );
}

async function RecordingFromParams({ params }: { params: PageProps<"/dashboard/recordings/[id]">["params"] }) {
  const { id } = await params;
  return <RecordingDetailView id={id} />;
}
