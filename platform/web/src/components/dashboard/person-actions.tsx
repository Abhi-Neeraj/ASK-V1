"use client";

import { MicOff, ShieldCheck, ShieldOff, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { ConfirmDialog } from "@/components/dashboard/confirm-dialog";
import { EnrollVoiceDialog } from "@/components/dashboard/enroll-voice-dialog";
import { SignatureDialog } from "@/components/dashboard/signature-dialog";
import { Button } from "@/components/ui/button";
import { useDeletePerson, useRecordConsent, useRemoveVoice } from "@/lib/api/hooks";
import type { Person } from "@/lib/api/types";

/**
 * What a manager can do with a person's consent, voice and profile. Each step says what it changes,
 * because consent is a record about a real person and recording it is not a formality.
 * `enrollmentAvailable` is whether this server has a speaker model; without one, voices cannot be taken.
 */
export function PersonActions({ person, enrollmentAvailable = false }: { person: Person; enrollmentAvailable?: boolean }) {
  const consent = useRecordConsent();
  const remove = useDeletePerson();
  const removeVoice = useRemoveVoice();
  const [confirmed, setConfirmed] = useState(false);
  const checkboxId = useId();
  const granted = person.consentStatus === "granted";
  const name = person.displayName;
  const hasVoiceData = person.sampleCount > 0 || (person.signature?.sampleCount ?? 0) > 0;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {granted && enrollmentAvailable ? <EnrollVoiceDialog person={person} /> : null}
      {granted ? <SignatureDialog person={person} /> : null}
      {hasVoiceData ? (
        <ConfirmDialog
          trigger={
            <Button variant="ghost" size="sm">
              <MicOff className="size-4" aria-hidden="true" />
              Remove voice
            </Button>
          }
          title={`Remove ${name}'s voice?`}
          description={`This deletes ${name}'s voice print and voice signature, so they will no longer be recognized. Past transcript lines that named them will read "Unknown speaker". You can enroll them again later.`}
          confirmLabel="Remove voice"
          onConfirm={() => removeVoice.mutateAsync(person.id)}
          successMessage={`${name}'s voice was removed.`}
          failureMessage="Could not remove the voice."
        />
      ) : null}
      {granted ? (
        <ConfirmDialog
          trigger={
            <Button variant="secondary" size="sm">
              <ShieldOff className="size-4" aria-hidden="true" />
              Withdraw consent
            </Button>
          }
          title={`Withdraw consent for ${name}?`}
          description={`This deletes ${name}'s voice profile and voice signature and stops matching their voice. Past transcript lines that named them will read "Unknown speaker". Consent can be recorded again later.`}
          confirmLabel="Withdraw consent"
          onConfirm={() => consent.mutateAsync({ id: person.id, granted: false })}
          successMessage={`Consent withdrawn for ${name}.`}
          failureMessage="Could not withdraw consent."
        />
      ) : (
        <ConfirmDialog
          trigger={
            <Button variant="secondary" size="sm">
              <ShieldCheck className="size-4" aria-hidden="true" />
              {person.consentStatus === "revoked" ? "Record consent again" : "Record consent"}
            </Button>
          }
          title={`Record consent for ${name}?`}
          description={`Only record consent after ${name} has told you they agree. It is saved with your name and the time, and ${name} can withdraw it whenever they like.`}
          confirmLabel="Record consent"
          variant="primary"
          confirmDisabled={!confirmed}
          onConfirm={async () => {
            await consent.mutateAsync({ id: person.id, granted: true });
            setConfirmed(false);
          }}
          successMessage={`Consent recorded for ${name}.`}
          failureMessage="Could not record consent."
        >
          <p className="text-sm leading-relaxed text-fg-muted">
            They agree that their voice may be recorded by company lockets, and that a voice profile may be
            created from samples they provide so their speech can be recognized in transcripts.
          </p>
          <div className="mt-4 flex items-start gap-3">
            <input
              id={checkboxId}
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
              className="mt-0.5 size-4 shrink-0 accent-[var(--color-cerulean)]"
            />
            <label htmlFor={checkboxId} className="text-sm text-fg">
              I have confirmed this with {name}.
            </label>
          </div>
        </ConfirmDialog>
      )}

      <ConfirmDialog
        trigger={
          <Button variant="ghost" size="sm">
            <Trash2 className="size-4" aria-hidden="true" />
            Remove
          </Button>
        }
        title={`Remove ${name}?`}
        description={`This deletes ${name}'s profile, voice profile and consent records. Past transcript lines stay but will read "Unknown speaker". The removal is recorded in the audit log.`}
        confirmLabel="Remove person"
        onConfirm={() => remove.mutateAsync(person.id)}
        successMessage={`${name} was removed.`}
        failureMessage="Could not remove this person."
      />
    </div>
  );
}
