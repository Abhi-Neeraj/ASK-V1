"use client";

import { AudioLines, CheckCircle2, Mic, Square, Upload } from "lucide-react";
import { useId, useRef, useState, type ChangeEvent } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { useVoiceRecorder, type FinishedSample } from "@/hooks/use-voice-recorder";
import { ApiError } from "@/lib/api/client";
import { useEnrollSample } from "@/lib/api/hooks";
import type { Person } from "@/lib/api/types";
import { cn } from "@/lib/cn";

/** What the person reads aloud. Ordinary sentences, varied, with nothing private in them. */
export const SAMPLE_PROMPTS = [
  "The delivery arrives on Thursday morning, so please keep the loading bay clear until noon.",
  "I checked the figures twice and they match, but I would like someone else to look before we send them.",
  "We could move the meeting to the afternoon, as long as everyone has time to read the notes first.",
  "Please confirm the order number, the delivery address and the name of the person receiving the parcel.",
];

const MIN_SECONDS = 3;
const MAX_SECONDS = 20;

/**
 * Takes the voice samples that let ASK recognize someone, one at a time, and says plainly what
 * happens to them: the recording is turned into a voice print and thrown away. A person can speak
 * into the microphone or hand over a WAV file, whichever works for them.
 */
export function EnrollVoiceDialog({ person }: { person: Person }) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState<string | undefined>();
  const enroll = useEnrollSample();
  const fileInput = useRef<HTMLInputElement>(null);
  const fileId = useId();

  const needed = person.samplesRequired;
  const have = person.sampleCount;
  const done = person.enrolled;
  const prompt = SAMPLE_PROMPTS[have % SAMPLE_PROMPTS.length];

  function save(wav: Blob) {
    setMessage(undefined);
    enroll.mutate(
      { id: person.id, wav },
      { onError: (error) => setMessage(error instanceof ApiError ? error.message : "The sample could not be saved.") },
    );
  }

  function onRecorded(sample: FinishedSample) {
    if (sample.seconds < MIN_SECONDS) {
      setMessage(`That was too short. Record at least ${MIN_SECONDS} seconds of ${person.displayName} speaking.`);
      return;
    }
    save(sample.wav);
  }

  const recorder = useVoiceRecorder({ maxSeconds: MAX_SECONDS, onFinish: onRecorded });
  const busy = enroll.isPending || recorder.status === "converting";
  const recording = recorder.status === "recording";

  function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // so choosing the same file again still counts
    if (file) save(file);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="secondary" size="sm">
          <Mic className="size-4" aria-hidden="true" />
          {have > 0 ? "Add voice samples" : "Enroll voice"}
        </Button>
      </DialogTrigger>
      <DialogContent
        title={`Enroll ${person.displayName}'s voice`}
        description={`Record ${needed} short samples of ${person.displayName} speaking normally. The recordings are not kept: each is turned into a voice print and then deleted.`}
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

          {done ? (
            <p role="status" className="flex items-start gap-3 border border-success/40 bg-success/[0.07] px-4 py-3 text-sm text-success">
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              {person.displayName} can now be recognized in new recordings. More samples make it more reliable.
            </p>
          ) : null}

          <div className="border border-line bg-white/[0.02] p-4">
            <p className="caption text-fg-subtle">Ask {person.displayName} to read</p>
            <p className="mt-2 text-sm leading-relaxed text-fg">&ldquo;{prompt}&rdquo;</p>
          </div>

          {recorder.supported ? (
            <div className="space-y-3">
              {recording ? (
                <div className="flex items-center gap-4">
                  <Button variant="primary" size="sm" onClick={() => void recorder.stop()}>
                    <Square className="size-4" aria-hidden="true" />
                    Stop and save
                  </Button>
                  <span className="font-mono text-sm tabular-nums text-fg" aria-label="Time recorded">
                    {recorder.seconds.toFixed(1)}s
                  </span>
                  <span
                    role="meter"
                    aria-label="Microphone level"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(recorder.level * 100)}
                    className="h-2 flex-1 overflow-hidden rounded-full bg-white/10"
                  >
                    <span className="block h-full bg-cerulean-glow transition-[width] duration-75" style={{ width: `${Math.round(recorder.level * 100)}%` }} />
                  </span>
                </div>
              ) : (
                <Button size="sm" loading={busy} disabled={busy} onClick={() => void recorder.start()}>
                  <AudioLines className="size-4" aria-hidden="true" />
                  Record a sample
                </Button>
              )}
              {recording ? (
                <p className="text-xs text-fg-muted">Recording stops by itself after {MAX_SECONDS} seconds.</p>
              ) : null}
            </div>
          ) : (
            <p className="text-sm text-fg-muted">
              This browser cannot record from a microphone here. Choose a WAV file of {person.displayName} speaking instead.
            </p>
          )}

          <div>
            <label htmlFor={fileId} className="inline-flex cursor-pointer items-center gap-2 text-sm text-fg-muted underline-offset-4 hover:text-fg hover:underline">
              <Upload className="size-4" aria-hidden="true" />
              {recorder.supported ? "Or choose a WAV file" : "Choose a WAV file"}
            </label>
            <input
              ref={fileInput}
              id={fileId}
              type="file"
              accept=".wav,audio/wav,audio/x-wav"
              className="sr-only"
              disabled={busy || recording}
              onChange={onFile}
            />
          </div>

          {message || recorder.error ? (
            <p role="alert" className="border border-danger/40 bg-danger/[0.08] px-4 py-3 text-sm text-danger">
              {message ?? recorder.error}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant={done ? "primary" : "ghost"}>{done ? "Done" : "Close"}</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
