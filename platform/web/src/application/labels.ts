// Words and formats shown on screen for server values.
import type { RecordingState } from "@/infrastructure/api/types";

const STATE_LABELS: Record<RecordingState, string> = {
  uploaded: "Uploaded",
  queued: "Waiting",
  validating: "Checking",
  transcribing: "Writing the transcript",
  diarizing: "Finding speakers",
  identifying_speakers: "Finding speakers",
  aligning: "Writing the summary",
  completed: "Ready",
  failed: "Failed",
  retrying: "Trying again",
  needs_review: "Ready, needs review",
};

export const stateLabel = (state: RecordingState) => STATE_LABELS[state] ?? state;

export const isWorking = (state: RecordingState) =>
  !["completed", "failed", "needs_review"].includes(state);

export function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  const minutes = Math.floor(seconds / 60);
  return minutes ? `${minutes} min ${seconds % 60} s` : `${seconds} s`;
}

export function clock(ms: number): string {
  const s = Math.floor(ms / 1000);
  const two = (n: number) => String(n).padStart(2, "0");
  return `${two(Math.floor(s / 3600))}:${two(Math.floor((s % 3600) / 60))}:${two(s % 60)}`;
}
