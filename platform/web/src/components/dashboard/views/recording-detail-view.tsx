"use client";

import { Download, FileJson, FileText, RotateCw } from "lucide-react";
import Link from "next/link";
import { ConfirmDialog } from "@/components/dashboard/confirm-dialog";
import { DeleteRecordingDialog } from "@/components/dashboard/delete-recording-dialog";
import { SectionTitle } from "@/components/dashboard/page-header";
import { RecordingSummary, RecordingVoices } from "@/components/dashboard/recording-insights";
import { TranscriptView } from "@/components/dashboard/transcript-view";
import { PlaceholderBadge, ProcessingBadge, SimulatedBadge } from "@/components/status-badges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Notice } from "@/components/ui/notice";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api/client";
import { useMe, useProcessing, useRecording, useReprocess } from "@/lib/api/hooks";
import type { ProcessingStatus, RecordingDetail } from "@/lib/api/types";
import { DEMO_MODE } from "@/lib/env";
import { formatBytes, formatDateTime, formatDuration } from "@/lib/format";
import { PROCESSING_STATES, isWaiting } from "@/lib/states";
import { downloadTextFile, transcriptToJson, transcriptToText } from "@/lib/transcript-export";

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="caption text-fg-subtle">{label}</dt>
      <dd className="mt-1.5 text-sm text-fg">{children}</dd>
    </div>
  );
}

/** States from which a recording can be processed again: the work is finished, one way or another. */
const REPROCESSABLE = new Set(["completed", "needs_review", "failed"]);

export function RecordingDetailView({ id }: { id: string }) {
  const recording = useRecording(id);
  const me = useMe();
  const processing = useProcessing();
  const canManage = me.data?.role === "admin" || me.data?.role === "manager";

  if (recording.isPending) {
    return (
      <div className="space-y-6" aria-busy="true" aria-label="Loading recording">
        <Skeleton className="h-10 w-80" />
        <Skeleton className="h-32" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (recording.isError) {
    const notFound = recording.error instanceof ApiError && recording.error.status === 404;
    return (
      <ErrorState
        headingLevel={2}
        title={notFound ? "Recording not found" : "We could not load this recording"}
        description={
          notFound
            ? "It may have been deleted, or you may not have access to it."
            : recording.error.message
        }
        onRetry={notFound ? undefined : () => recording.refetch()}
      />
    );
  }

  const detail = recording.data;
  const stateMeta = PROCESSING_STATES[detail.state];
  const base = `recording-${detail.id}`;
  const transcript = detail.transcript;

  return (
    <>
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-heading text-3xl font-semibold tracking-tight text-fg">
              {formatDateTime(detail.startedAt)}
            </h1>
            <ProcessingBadge state={detail.state} />
            {detail.simulated ? <SimulatedBadge /> : null}
            {detail.placeholder ? <PlaceholderBadge /> : null}
          </div>
          <p className="mt-2 text-sm text-fg-muted">
            {detail.deviceName} · {formatDuration(detail.durationMs)}
            {detail.part ? ` · Part ${detail.part} of a longer recording` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {transcript ? (
            <>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => downloadTextFile(`${base}.txt`, "text/plain", transcriptToText(detail))}
              >
                <FileText className="size-4" aria-hidden="true" />
                Export TXT
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => downloadTextFile(`${base}.json`, "application/json", transcriptToJson(detail))}
              >
                <FileJson className="size-4" aria-hidden="true" />
                Export JSON
              </Button>
            </>
          ) : null}
          {canManage && REPROCESSABLE.has(detail.state) ? <ReprocessDialog id={detail.id} /> : null}
          {canManage ? <DeleteRecordingDialog id={detail.id} /> : null}
        </div>
      </div>

      <section aria-labelledby="audio-title" className="mb-8">
        <SectionTitle>
          <span id="audio-title">Audio</span>
        </SectionTitle>
        <Card className="p-5">
          {detail.audioUrl ? (
            <div className="space-y-4">
              <audio controls preload="none" src={detail.audioUrl} className="w-full">
                Your browser does not support audio playback.
              </audio>
              <Button asChild variant="ghost" size="sm" className="-ml-3">
                <a href={detail.audioUrl} download>
                  <Download className="size-4" aria-hidden="true" />
                  Download audio
                </a>
              </Button>
            </div>
          ) : (
            <p className="text-sm text-fg-muted">
              {audioUnavailableMessage(DEMO_MODE)}
            </p>
          )}
        </Card>
      </section>

      <section aria-labelledby="transcript-title" className="mb-8">
        <SectionTitle>
          <span id="transcript-title">Transcript</span>
        </SectionTitle>
        {detail.state === "failed" && transcript ? (
          <Notice tone="warning" title="The latest processing attempt failed" className="mb-4">
            <p>
              {detail.error ?? "The recording could not be processed."} The transcript below is from an earlier run.
              {detail.failureDetail && detail.failureDetail !== detail.error
                ? ` What went wrong: ${detail.failureDetail}`
                : ""}{" "}
              Once the cause is fixed, use Process again.
            </p>
          </Notice>
        ) : null}
        {transcript?.placeholder ? (
          <Notice tone="warning" title="This transcript is placeholder text" className="mb-4">
            <p>
              It was produced by a development stand-in, not by speech recognition, so it is not what was said. Nobody
              was identified by voice. Exports of it say the same.
            </p>
          </Notice>
        ) : transcript && !transcript.diarizationModel ? (
          <Notice title="Speakers were not separated" className="mb-4">
            <p>
              Speaker separation did not run for this recording, so every line is attributed to one speaker. Who spoke
              when cannot be told from this transcript.
            </p>
          </Notice>
        ) : transcript?.separationExperimental ? (
          <Notice title="Speaker separation was experimental" className="mb-4">
            <p>
              Who spoke when was worked out by an experimental method. Listen to the recording and check the speaker
              labels before relying on them. A name appears only where a voice clearly matched a registered person; it
              is not proof of who spoke.
            </p>
          </Notice>
        ) : null}
        <Card className="overflow-hidden">
          {transcript && transcript.segments.length === 0 ? (
            <div role="status" className="p-6">
              <p className="text-sm font-medium text-fg">No speech was found in this recording.</p>
              <p className="mt-1 text-sm text-fg-muted">
                It may be silent, or too quiet to make out. If you expected speech, listen to the audio above.
              </p>
            </div>
          ) : transcript ? (
            <TranscriptView detail={detail} canEdit />
          ) : (
            <TranscriptPlaceholder detail={detail} active={stateMeta.active} processing={processing.data} />
          )}
        </Card>
      </section>

      {detail.analysis ? (
        <>
          <RecordingVoices analysis={detail.analysis} />
          <RecordingSummary analysis={detail.analysis} />
        </>
      ) : transcript && transcript.segments.length > 0 && !transcript.placeholder ? (
        <section aria-labelledby="summary-title" className="mb-8">
          <SectionTitle>
            <span id="summary-title">Summary</span>
          </SectionTitle>
          <Card className="p-6">
            <p role="status" className="text-sm text-fg-muted">
              There is no summary for this recording. It may have been processed before summaries existed
              {canManage ? ": use Process again to make one." : "."}
            </p>
          </Card>
        </section>
      ) : null}

      <section aria-labelledby="details-title">
        <SectionTitle>
          <span id="details-title">Processing details</span>
        </SectionTitle>
        <Card className="p-6">
          <dl className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            <Meta label="Status">{stateMeta.label}</Meta>
            <Meta label="Retries">{detail.retryCount}</Meta>
            <Meta label="Uploaded">{formatDateTime(detail.uploadedAt)}</Meta>
            <Meta label="File size">{formatBytes(detail.sizeBytes)}</Meta>
            <Meta label="Format">{detail.format}</Meta>
            <Meta label="Review">{transcript ? reviewLabel(transcript.reviewStatus) : "Not applicable"}</Meta>
            <Meta label="Speech to text">{speechLabel(detail)}</Meta>
            <Meta label="Speaker separation">{separationLabel(detail)}</Meta>
          </dl>
        </Card>
      </section>

      <p className="mt-8 text-sm">
        <Link href="/dashboard/recordings" className="text-fg-muted underline-offset-4 hover:text-fg hover:underline">
          Back to all recordings
        </Link>
      </p>
    </>
  );
}

function ReprocessDialog({ id }: { id: string }) {
  const reprocess = useReprocess();
  return (
    <ConfirmDialog
      trigger={
        <Button variant="secondary" size="sm">
          <RotateCw className="size-4" aria-hidden="true" />
          Process again
        </Button>
      }
      title="Process this recording again?"
      description="A new transcript replaces the current one. Any corrections made to it are lost, and speakers are matched again."
      confirmLabel="Process again"
      variant="primary"
      onConfirm={() => reprocess.mutateAsync(id)}
      successMessage="The recording is queued to be processed again."
      failureMessage="Could not queue the recording."
    />
  );
}

/** Why there is no audio player. Only the demo talks about the build; a real deployment says whose choice it is. */
export function audioUnavailableMessage(demo: boolean): string {
  return demo
    ? "Audio playback is not part of the demo: this build has no audio."
    : "Audio playback is not available to you for this recording.";
}

function reviewLabel(status: NonNullable<RecordingDetail["transcript"]>["reviewStatus"]) {
  return status === "needs_review" ? "Segments need review" : status === "reviewed" ? "Reviewed" : "No review needed";
}

/** What was done, in plain words. The product that did it is not named. */
function speechLabel(detail: RecordingDetail): string {
  const transcript = detail.transcript;
  if (!transcript) return "Not run yet";
  if (transcript.placeholder) return "Placeholder only (no speech recognition ran)";
  return transcript.language ? `Done · language ${transcript.language}` : "Done";
}

function separationLabel(detail: RecordingDetail): string {
  const transcript = detail.transcript;
  if (!transcript) return "Not run yet";
  if (!transcript.diarizationModel) return "Did not run for this recording";
  return transcript.separationExperimental ? "Done (experimental method)" : "Done";
}

function TranscriptPlaceholder({
  detail,
  active,
  processing,
}: {
  detail: RecordingDetail;
  active: boolean;
  processing: ProcessingStatus | undefined;
}) {
  if (detail.state === "failed") {
    return (
      <div role="alert" className="space-y-2 p-6">
        <p className="text-sm font-medium text-danger">Processing failed</p>
        <p className="text-sm text-fg-muted">{detail.error ?? "The recording could not be processed."}</p>
        {detail.failureDetail ? (
          <p className="text-sm text-fg-muted">
            <span className="font-medium text-fg">What went wrong: </span>
            {detail.failureDetail}
          </p>
        ) : null}
        <p className="text-xs text-fg-subtle">
          Attempts so far: {detail.retryCount}. Once the cause is fixed, use Process again.
        </p>
      </div>
    );
  }
  // "Queued" is only true if something is going to pick it up. Say when nothing will.
  const nobodyWorking = active && processing !== undefined && !processing.available;
  return (
    <div role="status" className="p-6">
      <p className="text-sm text-fg-muted">
        {nobodyWorking
          ? "This recording is waiting for a processing worker. No worker is running right now, so it will start when one does."
          : isWaiting(detail.state)
            ? "This recording is waiting its turn to be processed. The transcript appears here as soon as it is ready."
            : active
              ? "This recording is being processed. The transcript appears here as soon as it is ready."
              : "There is no transcript for this recording."}
      </p>
      {detail.state === "retrying" && detail.failureDetail ? (
        <p className="mt-3 text-sm text-fg-muted">
          <span className="font-medium text-fg">The last attempt failed and will be tried again: </span>
          {detail.failureDetail}
        </p>
      ) : null}
    </div>
  );
}
