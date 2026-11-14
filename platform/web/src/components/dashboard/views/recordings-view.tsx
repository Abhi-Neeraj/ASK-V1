"use client";

import { AudioLines, Search, SearchX } from "lucide-react";
import { useState } from "react";
import { PageHeader } from "@/components/dashboard/page-header";
import { ProcessingNotice } from "@/components/dashboard/processing-notice";
import { RecordingRow } from "@/components/dashboard/recording-row";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { useDevices, useRecordings } from "@/lib/api/hooks";
import type { ProcessingState } from "@/lib/api/types";
import { PROCESSING_FILTER_OPTIONS, PROCESSING_STATES } from "@/lib/states";

const PAGE_SIZE = 8;

type Filters = { q: string; deviceId: string; state: ProcessingState | ""; page: number };
const INITIAL: Filters = { q: "", deviceId: "", state: "", page: 1 };

export function RecordingsView() {
  const [filters, setFilters] = useState<Filters>(INITIAL);
  const q = useDebouncedValue(filters.q.trim(), 300);
  const devices = useDevices();
  const recordings = useRecordings({
    q: q || undefined,
    deviceId: filters.deviceId || undefined,
    state: filters.state,
    page: filters.page,
    pageSize: PAGE_SIZE,
  });

  const filtered = Boolean(filters.q || filters.deviceId || filters.state);
  // Changing a filter always returns to the first page.
  const change = (patch: Partial<Omit<Filters, "page">>) => setFilters((current) => ({ ...current, ...patch, page: 1 }));

  const total = recordings.data?.total ?? 0;

  return (
    <>
      <PageHeader
        title="Recordings"
        description="Every recording your locket has uploaded. Search by what was said, or filter by device and status."
      />
      <ProcessingNotice className="mb-6" />

      <Card className="mb-6 grid gap-3 p-4 sm:grid-cols-[1fr_12rem_12rem]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-fg-subtle" aria-hidden="true" />
          <Input
            type="search"
            aria-label="Search transcripts"
            placeholder="Search transcripts"
            className="pl-11"
            value={filters.q}
            onChange={(event) => change({ q: event.target.value })}
          />
        </div>
        <Select aria-label="Filter by device" value={filters.deviceId} onChange={(event) => change({ deviceId: event.target.value })}>
          <option value="">All devices</option>
          {devices.data?.map((device) => (
            <option key={device.id} value={device.id}>
              {device.name}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by status"
          value={filters.state}
          onChange={(event) => change({ state: event.target.value as ProcessingState | "" })}
        >
          <option value="">All statuses</option>
          {PROCESSING_FILTER_OPTIONS.map((state) => (
            <option key={state} value={state}>
              {PROCESSING_STATES[state].label}
            </option>
          ))}
        </Select>
      </Card>

      {recordings.isPending ? (
        <Card className="divide-y divide-line" aria-busy="true" aria-label="Loading recordings">
          {Array.from({ length: 5 }, (_, i) => (
            <div key={i} className="grid gap-3 px-6 py-5 sm:grid-cols-[11rem_1fr_6rem]">
              <Skeleton className="h-9" />
              <Skeleton className="h-9" />
              <Skeleton className="h-7" />
            </div>
          ))}
        </Card>
      ) : recordings.isError ? (
        <ErrorState headingLevel={2} description={recordings.error.message} onRetry={() => recordings.refetch()} />
      ) : total === 0 ? (
        filtered ? (
          <EmptyState
            headingLevel={2}
            icon={SearchX}
            title="Nothing matches"
            description="No recordings match these filters. Try a different search or clear the filters."
            action={
              <Button variant="secondary" size="sm" onClick={() => setFilters(INITIAL)}>
                Clear filters
              </Button>
            }
          />
        ) : (
          <EmptyState
            headingLevel={2}
            icon={AudioLines}
            title="No recordings yet"
            description="Recordings appear here after a locket uploads its first clip and the server confirms it."
          />
        )
      ) : (
        <>
          <Card className="divide-y divide-line overflow-hidden">
            <ul className="divide-y divide-line">
              {recordings.data.items.map((recording) => (
                <RecordingRow key={recording.id} recording={recording} />
              ))}
            </ul>
          </Card>

          <Pagination
            page={filters.page}
            pageSize={PAGE_SIZE}
            total={total}
            onPage={(page) => setFilters((current) => ({ ...current, page }))}
          />
        </>
      )}
    </>
  );
}
