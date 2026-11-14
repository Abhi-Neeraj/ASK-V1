"use client";

import { Search, Upload } from "lucide-react";
import { useId, useState, type ChangeEvent } from "react";
import { sampleProblem } from "@/components/dashboard/signature-dialog";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Notice } from "@/components/ui/notice";
import { ApiError } from "@/lib/api/client";
import { useMatchSignature } from "@/lib/api/hooks";
import type { SignatureCandidate, SignatureMatch } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { formatPitch } from "@/lib/format";

/** A similarity as a percentage. It says how alike two voices are, not how likely the name is to be right. */
export function similarityLabel(score: number): string {
  return `${Math.round(score * 100)}% alike`;
}

function Candidate({ candidate, matched }: { candidate: SignatureCandidate; matched: boolean }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 sm:px-6">
      <span className={cn("text-sm", matched ? "font-medium text-fg" : "text-fg-muted")}>{candidate.name}</span>
      <span className="flex flex-wrap items-center gap-2">
        {matched ? <Badge tone="cerulean">Closest match</Badge> : null}
        {candidate.passedPitchGate ? (
          <Badge tone="neutral">{similarityLabel(candidate.score)}</Badge>
        ) : (
          <Badge tone="neutral" title="Their pitch is too different for the voices to be compared.">
            Pitch too different
          </Badge>
        )}
      </span>
    </li>
  );
}

function Result({ result }: { result: SignatureMatch }) {
  const headline =
    result.outcome === "matched"
      ? `Closest match: ${result.match?.name ?? "unknown"}`
      : result.outcome === "nobody_enrolled"
        ? "Nobody has a voice signature yet"
        : "No confident match";
  const explanation =
    result.outcome === "matched"
      ? "This voice is clearly closer to this person than to everyone else with a signature."
      : result.outcome === "nobody_enrolled"
        ? "Add samples for people who have given consent, then check again."
        : "This voice is not clearly closer to any one person. Anyone listed below is only the nearest, and being listed is not a match.";
  return (
    <div className="space-y-5" aria-live="polite">
      <div>
        <p className="text-sm font-medium text-fg">{headline}</p>
        <p className="mt-1 text-sm leading-relaxed text-fg-muted">{explanation}</p>
      </div>

      <dl className="grid grid-cols-3 gap-px border border-line-strong bg-line-strong text-center" aria-label="Pitch of this voice">
        {(
          [
            ["Base pitch", result.pitch.baseHz],
            ["Low pitch", result.pitch.lowHz],
            ["High pitch", result.pitch.highHz],
          ] as const
        ).map(([label, hz]) => (
          <div key={label} className="bg-ink px-3 py-3">
            <dt className="caption text-fg-subtle">{label}</dt>
            <dd className="mt-1.5 font-mono text-sm tabular-nums text-fg">{formatPitch(hz)}</dd>
          </div>
        ))}
      </dl>

      {result.closest.length > 0 ? (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-line" aria-label="Nearest voices">
            {result.closest.map((candidate) => (
              <Candidate key={candidate.personId} candidate={candidate} matched={candidate.personId === result.match?.personId} />
            ))}
          </ul>
        </Card>
      ) : null}

      <p className="text-xs leading-relaxed text-fg-subtle">{result.notice}</p>
    </div>
  );
}

/**
 * Upload a WAV of someone speaking and see whom it most resembles among the people who have given consent and have
 * a voice signature. Managers only; the page that shows it decides that.
 */
export function SignatureCheck() {
  const match = useMatchSignature();
  const [message, setMessage] = useState<string | undefined>();
  const fileId = useId();

  function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const problem = sampleProblem(file);
    if (problem) {
      match.reset();
      setMessage(problem);
      return;
    }
    setMessage(undefined);
    match.mutate(file, {
      onError: (error) => setMessage(error instanceof ApiError ? error.message : "The voice could not be checked."),
    });
  }

  return (
    <section aria-labelledby="signature-check-title" className="mb-6">
      <Card className="p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl">
            <h2 id="signature-check-title" className="flex items-center gap-2 font-heading text-lg font-semibold tracking-tight text-fg">
              <Search className="size-4 text-cerulean-bright" aria-hidden="true" />
              Check a voice
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-fg-muted">
              Upload a WAV recording of someone speaking to measure its pitch and see who it most resembles among people
              with a voice signature. Pitch is compared first, then the colour of the voice. The recording is deleted
              straight away, and the check is written to the audit log.
            </p>
          </div>
          <div>
            <label
              htmlFor={fileId}
              className={cn(
                "inline-flex h-9 cursor-pointer items-center justify-center gap-2 rounded-full px-4 text-sm font-medium glass text-fg hover:border-line-strong hover:bg-white/[0.08]",
                match.isPending && "pointer-events-none opacity-60",
              )}
            >
              <Upload className="size-4" aria-hidden="true" />
              {match.isPending ? "Measuring…" : "Choose a WAV file"}
            </label>
            <input id={fileId} type="file" accept=".wav,audio/wav,audio/x-wav" className="sr-only" disabled={match.isPending} onChange={onFile} />
          </div>
        </div>

        {message ? (
          <p role="alert" className="mt-5 border border-danger/40 bg-danger/[0.08] px-4 py-3 text-sm text-danger">
            {message}
          </p>
        ) : null}

        {match.data ? (
          <div className="mt-6 border-t border-line pt-6">
            <Result result={match.data} />
          </div>
        ) : null}

        {!match.data && !message ? (
          <Notice title="Not identity verification" className="mt-6">
            <p>
              Voices can sound alike, and this has not been calibrated on real people. Use a result as a lead to check,
              never as proof of who is speaking.
            </p>
          </Notice>
        ) : null}
      </Card>
    </section>
  );
}
