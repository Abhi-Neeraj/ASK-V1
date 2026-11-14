"use client";

import { ArrowRight, AudioLines, Radio } from "lucide-react";
import Link from "next/link";
import { DeviceBadge } from "@/components/status-badges";
import { PageHeader, SectionTitle } from "@/components/dashboard/page-header";
import { ProcessingNotice } from "@/components/dashboard/processing-notice";
import { RecordingRow } from "@/components/dashboard/recording-row";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useDevices, useMe, useRecordings, useSummary } from "@/lib/api/hooks";
import { formatRelative } from "@/lib/format";

function Stat({
  label,
  value,
  note,
  tone = "default",
}: {
  label: string;
  value: string | number;
  note?: string;
  tone?: "default" | "rose";
}) {
  return (
    <Card className="p-5">
      <p className="caption text-fg-subtle">{label}</p>
      <p className={tone === "rose" ? "mt-3 font-heading text-4xl font-semibold text-rose" : "mt-3 font-heading text-4xl font-semibold text-fg"}>
        {value}
      </p>
      {note ? <p className="mt-1.5 text-xs text-fg-muted">{note}</p> : null}
    </Card>
  );
}

export function OverviewView() {
  const me = useMe();
  const summary = useSummary();
  const devices = useDevices();
  const recent = useRecordings({ page: 1, pageSize: 5 });

  return (
    <>
      <PageHeader
        title={me.data ? `Welcome back, ${me.data.name.split(" ")[0]}` : "Overview"}
        description="Live device status and recent processing activity for your company."
      />
      <ProcessingNotice className="mb-8" />

      <section aria-label="Summary" className="mb-10">
        {summary.isPending ? (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-busy="true" aria-label="Loading summary">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-28" />
            ))}
          </div>
        ) : summary.isError ? (
          <ErrorState description={summary.error.message} onRetry={() => summary.refetch()} />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Stat
              label="Devices online"
              value={summary.data.devicesOnline}
              note={`of ${summary.data.devicesTotal} registered`}
            />
            <Stat label="Recordings today" value={summary.data.recordingsToday} />
            <Stat label="In progress" value={summary.data.inProgress} note="Waiting or being processed" />
            <Stat
              label="Needs attention"
              value={summary.data.needsReview + summary.data.failed}
              note={`${summary.data.needsReview} to review, ${summary.data.failed} failed`}
              tone={summary.data.needsReview + summary.data.failed > 0 ? "rose" : "default"}
            />
          </div>
        )}
      </section>

      <div className="grid gap-10 xl:grid-cols-[1fr_22rem]">
        <section aria-labelledby="recent-title">
          <SectionTitle
            action={
              <Link href="/dashboard/recordings" className="inline-flex items-center gap-1.5 text-xs uppercase tracking-[0.2em] text-cerulean-glow hover:text-fg">
                All recordings <ArrowRight className="size-3.5" aria-hidden="true" />
              </Link>
            }
          >
            <span id="recent-title">Recent recordings</span>
          </SectionTitle>
          {recent.isPending ? (
            <Card aria-busy="true" aria-label="Loading recordings">
              <div className="space-y-4 p-6">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-12" />
                ))}
              </div>
            </Card>
          ) : recent.isError ? (
            <ErrorState description={recent.error.message} onRetry={() => recent.refetch()} />
          ) : recent.data.items.length === 0 ? (
            <EmptyState
              icon={AudioLines}
              title="No recordings yet"
              description="Recordings appear here after a locket uploads its first clip and the server confirms it."
            />
          ) : (
            <Card className="overflow-hidden">
              <ul className="divide-y divide-line">
                {recent.data.items.map((recording) => (
                  <RecordingRow key={recording.id} recording={recording} />
                ))}
              </ul>
            </Card>
          )}
        </section>

        <section aria-labelledby="devices-title">
          <SectionTitle
            action={
              <Link href="/dashboard/devices" className="inline-flex items-center gap-1.5 text-xs uppercase tracking-[0.2em] text-cerulean-glow hover:text-fg">
                Manage <ArrowRight className="size-3.5" aria-hidden="true" />
              </Link>
            }
          >
            <span id="devices-title">Devices</span>
          </SectionTitle>
          {devices.isPending ? (
            <Skeleton className="h-48" />
          ) : devices.isError ? (
            <ErrorState description={devices.error.message} onRetry={() => devices.refetch()} />
          ) : devices.data.length === 0 ? (
            <EmptyState icon={Radio} title="No lockets yet" description="Pair a locket from the Devices page." />
          ) : (
            <Card className="overflow-hidden">
              <ul className="divide-y divide-line">
                {devices.data.map((device) => (
                  <li key={device.id} className="flex items-center justify-between gap-3 px-5 py-4">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-fg">{device.name}</p>
                      <p className="mt-0.5 text-xs text-fg-muted">Seen {formatRelative(device.lastHeartbeatAt).toLowerCase()}</p>
                    </div>
                    <DeviceBadge status={device.status} />
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </section>
      </div>
    </>
  );
}
