import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { PlaceholderBadge, ProcessingBadge, SimulatedBadge } from "@/components/status-badges";
import type { Recording } from "@/lib/api/types";
import { formatDateTime, formatDuration } from "@/lib/format";
import { PROCESSING_STATES, isWaiting } from "@/lib/states";

function summary(recording: Recording): { text: string; muted: boolean } {
  if (recording.snippet) return { text: recording.snippet, muted: false };
  if (recording.state === "failed") return { text: recording.error ?? "Processing failed.", muted: true };
  if (isWaiting(recording.state)) return { text: "Waiting to be processed. The transcript will appear here.", muted: true };
  if (PROCESSING_STATES[recording.state].active) return { text: "Processing. The transcript will appear here.", muted: true };
  return { text: "No transcript.", muted: true };
}

export function RecordingRow({ recording }: { recording: Recording }) {
  const { text, muted } = summary(recording);
  return (
    <li>
      <Link
        href={`/dashboard/recordings/${encodeURIComponent(recording.id)}`}
        className="group grid gap-x-6 gap-y-3 px-5 py-5 transition-colors hover:bg-white/[0.03] sm:grid-cols-[11rem_1fr_auto] sm:items-center sm:px-6"
      >
        <div className="min-w-0">
          <p className="text-sm font-medium text-fg">{formatDateTime(recording.startedAt)}</p>
          <p className="mt-1 truncate text-xs text-fg-muted">
            {recording.deviceName} · {formatDuration(recording.durationMs)}
            {recording.part ? ` · Part ${recording.part}` : ""}
          </p>
        </div>
        <div className="min-w-0">
          <p className={muted ? "line-clamp-2 text-sm text-fg-muted" : "line-clamp-2 text-sm text-fg"}>{text}</p>
          {recording.speakers.length > 0 ? (
            <p className="mt-1.5 truncate text-xs text-fg-subtle">{recording.speakers.join(", ")}</p>
          ) : null}
        </div>
        <div className="flex items-center justify-between gap-3 sm:justify-end">
          <div className="flex flex-wrap items-center gap-2">
            {recording.simulated ? <SimulatedBadge /> : null}
            {recording.placeholder ? <PlaceholderBadge /> : null}
            <ProcessingBadge state={recording.state} />
          </div>
          <ChevronRight
            className="size-4 text-fg-subtle transition-transform group-hover:translate-x-0.5 group-hover:text-fg"
            aria-hidden="true"
          />
        </div>
      </Link>
    </li>
  );
}
