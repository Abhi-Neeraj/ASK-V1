"use client";

import { ScrollText, SearchX } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { PageHeader } from "@/components/dashboard/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { Notice } from "@/components/ui/notice";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useAuditEvents, useMe } from "@/lib/api/hooks";
import type { AuditCategory, AuditEvent } from "@/lib/api/types";
import {
  AUDIT_CATEGORIES,
  describeActor,
  describeAction,
  describeObject,
  detailPairs,
  endOfDayIso,
  linkFor,
  shortId,
  startOfDayIso,
} from "@/lib/audit";
import { formatDateTime } from "@/lib/format";

const PAGE_SIZE = 20;

type Filters = { category: AuditCategory | ""; from: string; to: string; page: number };
const INITIAL: Filters = { category: "", from: "", to: "", page: 1 };

/** Failed sign-ins and anything that removes things are the lines an administrator looks for first. */
function toneFor(action: string): "neutral" | "warning" | "danger" {
  if (action === "auth.login_failed" || action === "recording.processing_failed") return "danger";
  if (/revoked|deleted|purged|removed/.test(action)) return "warning";
  return "neutral";
}

export function AuditView() {
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const [filters, setFilters] = useState<Filters>(INITIAL);

  const events = useAuditEvents(
    {
      category: filters.category,
      dateFrom: filters.from ? startOfDayIso(filters.from) : undefined,
      dateTo: filters.to ? endOfDayIso(filters.to) : undefined,
      page: filters.page,
      pageSize: PAGE_SIZE,
    },
    // Only administrators can read it, so nobody else's browser asks. The server checks again.
    isAdmin,
  );

  const filtered = Boolean(filters.category || filters.from || filters.to);
  const change = (patch: Partial<Omit<Filters, "page">>) => setFilters((current) => ({ ...current, ...patch, page: 1 }));

  return (
    <>
      <PageHeader
        title="Audit log"
        description="Who did what in this workspace: sign-ins, changes to lockets and people, and who listened to or removed recordings. It records actions, never what was said."
      />

      {me.isPending ? (
        <Skeleton className="h-40" aria-busy="true" aria-label="Loading" />
      ) : !isAdmin ? (
        <Notice tone="warning" title="Only administrators can read the audit log">
          <p>Ask an administrator of this workspace if you need to know about an action.</p>
        </Notice>
      ) : (
        <>
          <Card className="mb-6 grid gap-3 p-4 sm:grid-cols-[1fr_11rem_11rem]">
            <Select
              aria-label="Filter by kind of action"
              value={filters.category}
              onChange={(event) => change({ category: event.target.value as AuditCategory | "" })}
            >
              <option value="">All activity</option>
              {AUDIT_CATEGORIES.map((category) => (
                <option key={category.value} value={category.value}>
                  {category.label}
                </option>
              ))}
            </Select>
            <Input
              type="date"
              aria-label="From date"
              value={filters.from}
              max={filters.to || undefined}
              onChange={(event) => change({ from: event.target.value })}
            />
            <Input
              type="date"
              aria-label="To date"
              value={filters.to}
              min={filters.from || undefined}
              onChange={(event) => change({ to: event.target.value })}
            />
          </Card>

          {events.isPending ? (
            <Card className="divide-y divide-line" aria-busy="true" aria-label="Loading audit log">
              {Array.from({ length: 6 }, (_, i) => (
                <div key={i} className="grid gap-3 px-6 py-5 sm:grid-cols-[11rem_1fr_8rem]">
                  <Skeleton className="h-7" />
                  <Skeleton className="h-7" />
                  <Skeleton className="h-7" />
                </div>
              ))}
            </Card>
          ) : events.isError ? (
            <ErrorState headingLevel={2} description={events.error.message} onRetry={() => events.refetch()} />
          ) : events.data.total === 0 ? (
            filtered ? (
              <EmptyState
                headingLevel={2}
                icon={SearchX}
                title="Nothing matches"
                description="No actions were recorded for these filters. Try a wider date range or clear the filters."
                action={
                  <Button variant="secondary" size="sm" onClick={() => setFilters(INITIAL)}>
                    Clear filters
                  </Button>
                }
              />
            ) : (
              <EmptyState
                headingLevel={2}
                icon={ScrollText}
                title="Nothing recorded yet"
                description="Actions such as sign-ins and changes to lockets and people appear here as they happen."
              />
            )
          ) : (
            <>
              <Card className="overflow-hidden">
                <ul className="divide-y divide-line" aria-label="Recorded actions">
                  {events.data.items.map((event) => (
                    <AuditRow key={event.id} event={event} />
                  ))}
                </ul>
              </Card>
              <Pagination
                page={filters.page}
                pageSize={PAGE_SIZE}
                total={events.data.total}
                onPage={(page) => setFilters((current) => ({ ...current, page }))}
              />
            </>
          )}
        </>
      )}
    </>
  );
}

function AuditRow({ event }: { event: AuditEvent }) {
  const actor = describeActor(event.actor);
  const object = describeObject(event.objectType);
  const id = shortId(event.objectId);
  const link = linkFor(event);
  const details = detailPairs(event.detail);

  return (
    <li className="grid gap-x-6 gap-y-2 px-5 py-4 sm:grid-cols-[11rem_1fr] sm:px-6">
      <div className="text-sm text-fg-muted">
        <time dateTime={event.at}>{formatDateTime(event.at)}</time>
      </div>
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium text-fg">{describeAction(event.action)}</p>
          <Badge tone={toneFor(event.action)}>{event.action}</Badge>
        </div>
        <p className="text-sm text-fg-muted">
          <span className="text-fg">{actor.name}</span>
          {actor.note ? <span> · {actor.note}</span> : null}
          {object ? (
            <span>
              {" "}
              · {object}
              {id ? (
                <>
                  {" "}
                  {link ? (
                    <Link href={link} className="font-mono text-xs text-cerulean-glow underline-offset-4 hover:underline" title={event.objectId ?? undefined}>
                      {id}
                    </Link>
                  ) : (
                    <span className="font-mono text-xs" title={event.objectId ?? undefined}>
                      {id}
                    </span>
                  )}
                </>
              ) : null}
            </span>
          ) : null}
          {event.ip ? <span> · from {event.ip}</span> : null}
        </p>
        {details.length > 0 ? (
          <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-subtle">
            {details.map((pair) => (
              <div key={pair.key} className="flex gap-1.5">
                <dt>{pair.key}</dt>
                <dd className="text-fg-muted">{pair.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>
    </li>
  );
}
