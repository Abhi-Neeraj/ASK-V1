"use client";

import { Notice } from "@/components/ui/notice";
import { useProcessing } from "@/lib/api/hooks";
import type { ProcessingStatus } from "@/lib/api/types";

/** What, if anything, a person should know about how recordings are being processed right now. */
export function processingNotice(status: ProcessingStatus): { tone: "info" | "warning"; title: string; lines: string[] } | null {
  const waiting = status.queued + status.running;

  if (!status.available) {
    if (waiting === 0) return null; // nothing is waiting, so nothing is being held up
    return {
      tone: "warning",
      title: "Recordings are waiting for a processing worker",
      lines: [
        `No processing worker is running, so ${waiting} ${waiting === 1 ? "recording is" : "recordings are"} waiting. ` +
          "They are processed as soon as one starts. Ask your administrator to start it.",
      ],
    };
  }

  if (status.placeholder) {
    return {
      tone: "warning",
      title: "Placeholder mode: transcripts are not real",
      lines: [
        "Recordings are being processed by a development stand-in. Their transcripts are placeholder text, not what was said, and nobody is identified by voice.",
      ],
    };
  }

  const lines: string[] = [];
  if (!status.diarization) lines.push("Speaker separation is not set up on this server, so each recording is treated as one speaker.");
  if (!status.speakerMatching) lines.push("Speaker matching is not set up on this server, so nobody is identified by voice.");
  return lines.length > 0 ? { tone: "info", title: "Processing is running with some features off", lines } : null;
}

/**
 * Shown on the overview and the recordings list. Says plainly when processing is not happening, or is
 * not real, so a queued or placeholder recording is never mistaken for one that is being handled
 * properly. If the status cannot be read, nothing is shown rather than a guess.
 */
export function ProcessingNotice({ className }: { className?: string }) {
  const processing = useProcessing();
  const notice = processing.data ? processingNotice(processing.data) : null;
  if (!notice) return null;
  return (
    <Notice tone={notice.tone} title={notice.title} className={className}>
      {notice.lines.map((line) => (
        <p key={line}>{line}</p>
      ))}
    </Notice>
  );
}
