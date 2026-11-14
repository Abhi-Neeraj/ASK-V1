import type { BadgeTone } from "@/components/ui/badge";
import type { ConsentStatus, DeviceStatus, ProcessingState } from "@/lib/api/types";

type Meta = { label: string; tone: BadgeTone };

/** User-facing wording for each processing state. "active" states are still moving through the pipeline. */
export const PROCESSING_STATES: Record<ProcessingState, Meta & { active: boolean }> = {
  uploaded: { label: "Uploaded", tone: "neutral", active: true },
  queued: { label: "Queued", tone: "neutral", active: true },
  validating: { label: "Validating", tone: "cerulean", active: true },
  transcribing: { label: "Transcribing", tone: "cerulean", active: true },
  diarizing: { label: "Separating speakers", tone: "cerulean", active: true },
  identifying_speakers: { label: "Identifying speakers", tone: "cerulean", active: true },
  aligning: { label: "Aligning", tone: "cerulean", active: true },
  completed: { label: "Completed", tone: "success", active: false },
  failed: { label: "Failed", tone: "danger", active: false },
  retrying: { label: "Retrying", tone: "warning", active: true },
  needs_review: { label: "Needs review", tone: "rose", active: false },
};

/** Active, but no stage has started: nothing is working on the recording yet, it is waiting its turn. */
export function isWaiting(state: ProcessingState): boolean {
  return state === "uploaded" || state === "queued" || state === "retrying";
}

export const DEVICE_STATUSES: Record<DeviceStatus, Meta> = {
  online: { label: "Online", tone: "cerulean" },
  offline: { label: "Offline", tone: "neutral" },
  pending: { label: "Awaiting pairing", tone: "warning" },
  revoked: { label: "Revoked", tone: "danger" },
};

export const CONSENT_STATUSES: Record<ConsentStatus, Meta> = {
  granted: { label: "Consent recorded", tone: "success" },
  pending: { label: "Consent pending", tone: "warning" },
  revoked: { label: "Consent revoked", tone: "danger" },
};

export const PROCESSING_FILTER_OPTIONS: ProcessingState[] = [
  "completed",
  "needs_review",
  "queued",
  "transcribing",
  "diarizing",
  "failed",
];
