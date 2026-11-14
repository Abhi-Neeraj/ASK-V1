"use client";

import { CheckCircle2, Fingerprint, Upload } from "lucide-react";
import { useId, useState, type ChangeEvent } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { ApiError } from "@/lib/api/client";
import { useAddSignatureSample } from "@/lib/api/hooks";
import type { Person } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { formatPitch } from "@/lib/format";

/** Larger than any allowed sample (30 seconds), so the server's own limit is what people read about. */
export const MAX_SAMPLE_BYTES = 8 * 1024 * 1024;

/** Why a chosen file cannot be sent, or null if it looks fine. The server checks the audio itself. */
export function sampleProblem(file: { name: string; type: string; size: number }): string | null {
  const isWav = /\.wav$/i.test(file.name) || /^audio\/(x-)?wave?$/i.test(file.type);
  if (!isWav) return "Choose a WAV file (.wav).";
  if (file.size === 0) return "That file is empty.";
  if (file.size > MAX_SAMPLE_BYTES) return "That file is too large. A sample is 3 to 30 seconds long.";
  return null;
}

/**
 * Takes the WAV samples that make a person's voice signature, one at a time: their pitch (how high or low their
 * voice sits) and the colour of their voice. It says plainly what happens to each file: it is measured and then
 * deleted. Needs the person's consent, which is checked again by the server.
 */
export function SignatureDialog({ person }: { person: Person }) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState<string | undefined>();
  const add = useAddSignatureSample();
  const fileId = useId();

  const signature = person.signature;
  const needed = signature?.samplesRequired ?? person.samplesRequired;
  const have = signature?.sampleCount ?? 0;
  const ready = signature?.ready ?? false;
  const pitch = signature?.pitch ?? null;

  function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // so choosing the same file again still counts
    if (!file) return;
    const problem = sampleProblem(file);
    if (problem) {
      setMessage(problem);
      return;
    }
    setMessage(undefined);
    add.mutate(
      { id: person.id, wav: file },
      { onError: (error) => setMessage(error instanceof ApiError ? error.message : "The sample could not be saved.") },
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="secondary" size="sm">
          <Fingerprint className="size-4" aria-hidden="true" />
          {have > 0 ? "Add signature samples" : "Voice signature"}
        </Button>
      </DialogTrigger>
      <DialogContent
        title={`${person.displayName}'s voice signature`}
        description={`Upload ${needed} short WAV recordings of ${person.displayName} speaking normally. ASK measures their pitch and the colour of their voice, then deletes the recording: only the measurements are kept.`}
      >
        <div className="space-y-5">
          <div>
            <p className="text-sm font-medium text-fg" aria-live="polite">
              {have} of {needed} samples saved
            </p>
            <div className="mt-2 flex gap-1.5" aria-hidden="true">
              {Array.from({ length: needed }, (_, index) => (
                <span key={index} className={cn("h-1.5 flex-1 rounded-full", index < have ? "bg-cerulean" : "bg-white/10")} />
              ))}
            </div>
          </div>

          {pitch ? (
            <dl className="grid grid-cols-3 gap-px border border-line-strong bg-line-strong text-center" aria-label="Pitch so far">
              {(
                [
                  ["Base pitch", pitch.baseHz],
                  ["Low pitch", pitch.lowHz],
                  ["High pitch", pitch.highHz],
                ] as const
              ).map(([label, hz]) => (
                <div key={label} className="bg-ink px-3 py-3">
                  <dt className="caption text-fg-subtle">{label}</dt>
                  <dd className="mt-1.5 font-mono text-sm tabular-nums text-fg">{formatPitch(hz)}</dd>
                </div>
              ))}
            </dl>
          ) : null}

          {ready ? (
            <p role="status" className="flex items-start gap-3 border border-success/40 bg-success/[0.07] px-4 py-3 text-sm text-success">
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              {person.displayName} can now be matched by voice signature. More samples make it more reliable.
            </p>
          ) : null}

          <div className="border border-line bg-white/[0.02] p-4 text-sm leading-relaxed text-fg-muted">
            <p className="caption text-fg-subtle">Good samples</p>
            <p className="mt-2">
              3 to 30 seconds of one person talking in their normal voice, close to the microphone, with little
              background noise. A recording with no clear speech cannot be measured.
            </p>
          </div>

          <div>
            <label
              htmlFor={fileId}
              className={cn(
                "inline-flex cursor-pointer items-center gap-2 text-sm text-fg underline-offset-4 hover:underline",
                add.isPending && "pointer-events-none opacity-60",
              )}
            >
              <Upload className="size-4" aria-hidden="true" />
              {add.isPending ? "Measuring…" : "Choose a WAV file"}
            </label>
            <input
              id={fileId}
              type="file"
              accept=".wav,audio/wav,audio/x-wav"
              className="sr-only"
              disabled={add.isPending}
              onChange={onFile}
            />
          </div>

          {message ? (
            <p role="alert" className="border border-danger/40 bg-danger/[0.08] px-4 py-3 text-sm text-danger">
              {message}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant={ready ? "primary" : "ghost"}>{ready ? "Done" : "Close"}</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
