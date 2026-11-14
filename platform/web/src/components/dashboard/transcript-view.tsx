"use client";

import { Check, Pencil, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import { useUpdateTranscript } from "@/lib/api/hooks";
import type { RecordingDetail, TranscriptSegment } from "@/lib/api/types";
import { formatTimestamp } from "@/lib/format";
import { speakerName } from "@/lib/transcript-export";

/**
 * How closely the voice resembled the registered person's. It is a similarity score, not the chance that the
 * name is right, so it is worded that way and explained on hover.
 */
function confidenceLabel(segment: TranscriptSegment): string | null {
  return segment.confidence === null ? null : `Voice match ${Math.round(segment.confidence * 100)}%`;
}

const CONFIDENCE_HELP =
  "How closely this voice resembles the registered voice. It is a similarity score, not the probability that the name is right, and a match is not proof of who spoke.";

/** Timestamped, speaker-labelled transcript with inline correction of any segment's text. */
export function TranscriptView({ detail, canEdit }: { detail: RecordingDetail; canEdit: boolean }) {
  const update = useUpdateTranscript(detail.id);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const segments = detail.transcript?.segments ?? [];

  function startEdit(segment: TranscriptSegment) {
    setEditingId(segment.id);
    setDraft(segment.text);
  }

  function save(segment: TranscriptSegment) {
    const text = draft.trim();
    if (!text) return;
    if (text === segment.text) {
      setEditingId(null);
      return;
    }
    update.mutate([{ segmentId: segment.id, text }], {
      onSuccess: () => {
        setEditingId(null);
        toast.success("Correction saved.");
      },
      onError: (error) =>
        toast.error(error instanceof ApiError ? error.message : "Could not save the correction. Try again."),
    });
  }

  return (
    <ol className="divide-y divide-line" aria-label="Transcript">
      {segments.map((segment) => {
        const editing = editingId === segment.id;
        const unknown = segment.person === null;
        const confidence = confidenceLabel(segment);
        return (
          <li key={segment.id} className="grid gap-x-6 gap-y-2 px-5 py-5 sm:grid-cols-[9.5rem_1fr] sm:px-6">
            <p className="font-mono text-xs leading-6 text-fg-subtle">
              {formatTimestamp(segment.startMs)}
              <span className="block sm:inline"> – </span>
              {formatTimestamp(segment.endMs)}
            </p>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className={unknown ? "text-sm font-medium text-fg-muted" : "text-sm font-medium text-fg"}>
                  {speakerName(segment)}
                </span>
                <span className="font-mono text-[0.6875rem] text-fg-subtle">{segment.speakerLabel}</span>
                {confidence ? (
                  <Badge tone="neutral" title={CONFIDENCE_HELP}>
                    {confidence}
                  </Badge>
                ) : null}
                {segment.needsReview ? <Badge tone="rose">Needs review</Badge> : null}
                {segment.corrected ? <Badge tone="cerulean">Corrected</Badge> : null}
              </div>

              {editing ? (
                <div className="mt-3 space-y-3">
                  <Textarea
                    aria-label={`Edit text for ${speakerName(segment)} at ${formatTimestamp(segment.startMs)}`}
                    autoFocus
                    rows={3}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                  />
                  <div className="flex gap-2">
                    <Button size="sm" loading={update.isPending} onClick={() => save(segment)}>
                      <Check className="size-4" aria-hidden="true" />
                      Save
                    </Button>
                    <Button size="sm" variant="ghost" disabled={update.isPending} onClick={() => setEditingId(null)}>
                      <X className="size-4" aria-hidden="true" />
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="mt-2 flex items-start justify-between gap-4">
                  <p className="text-[0.9375rem] leading-relaxed text-fg">{segment.text}</p>
                  {canEdit ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="shrink-0"
                      aria-label={`Correct this segment (${speakerName(segment)}, ${formatTimestamp(segment.startMs)})`}
                      onClick={() => startEdit(segment)}
                    >
                      <Pencil className="size-3.5" aria-hidden="true" />
                      Correct
                    </Button>
                  ) : null}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
