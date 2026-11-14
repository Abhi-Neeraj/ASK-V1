import { Badge } from "@/components/ui/badge";
import type { ConsentStatus, DeviceStatus, ProcessingState } from "@/lib/api/types";
import { CONSENT_STATUSES, DEVICE_STATUSES, PROCESSING_STATES } from "@/lib/states";

export function ProcessingBadge({ state }: { state: ProcessingState }) {
  const meta = PROCESSING_STATES[state];
  return (
    <Badge tone={meta.tone} dot>
      {meta.label}
    </Badge>
  );
}

export function DeviceBadge({ status }: { status: DeviceStatus }) {
  const meta = DEVICE_STATUSES[status];
  return (
    <Badge tone={meta.tone} dot>
      {meta.label}
    </Badge>
  );
}

/** Marks a transcript written by the development stub, so placeholder text is never mistaken for speech. */
export function PlaceholderBadge() {
  return (
    <Badge tone="warning" title="Placeholder text from a development stand-in, not what was said">
      Placeholder
    </Badge>
  );
}

/** Marks a development stand-in so its data is never mistaken for a real locket's. */
export function SimulatedBadge() {
  return (
    <Badge tone="warning" title="A development stand-in, not real hardware">
      Simulated
    </Badge>
  );
}

export function ConsentBadge({ status }: { status: ConsentStatus }) {
  const meta = CONSENT_STATUSES[status];
  return <Badge tone={meta.tone}>{meta.label}</Badge>;
}
