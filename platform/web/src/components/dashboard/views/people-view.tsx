"use client";

import { AudioLines, Plus, UserRound, Users } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/dashboard/page-header";
import { PersonActions } from "@/components/dashboard/person-actions";
import { SignatureCheck } from "@/components/dashboard/signature-check";
import { Callout } from "@/components/marketing/blocks";
import { ConsentBadge } from "@/components/status-badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { FormField, Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api/client";
import { useCreatePerson, useMe, usePeople, useProcessing } from "@/lib/api/hooks";
import type { Person } from "@/lib/api/types";
import { formatDateTime, formatPitch } from "@/lib/format";

function AddPersonDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | undefined>();
  const create = useCreatePerson();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = name.trim();
    if (trimmed.length < 2) {
      setError("Enter a display name of at least 2 characters.");
      return;
    }
    setError(undefined);
    create.mutate(trimmed, {
      onSuccess: () => {
        toast.success(`${trimmed} was added. Record consent before enrolling a voice.`);
        setName("");
        setOpen(false);
      },
      onError: (e) => setError(e instanceof ApiError ? e.message : "Could not add this person."),
    });
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus className="size-4" aria-hidden="true" />
          Add person
        </Button>
      </DialogTrigger>
      <DialogContent
        title="Add a person"
        description="Create a profile first. A voice can be enrolled only after their consent is recorded."
      >
        <form onSubmit={onSubmit} noValidate className="space-y-5">
          <FormField label="Display name" required error={error}>
            {(a11y) => (
              <Input {...a11y} name="displayName" autoFocus value={name} onChange={(event) => setName(event.target.value)} />
            )}
          </FormField>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={create.isPending}>
              Add person
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Where each person is with their voice: nothing yet, part way, ready, or needing to start again. */
function VoiceBadge({ person }: { person: Person }) {
  if (person.voiceOutdated) {
    return (
      <Badge tone="warning">
        <AudioLines className="size-3" aria-hidden="true" />
        Voice out of date · enroll again
      </Badge>
    );
  }
  if (person.enrolled) {
    return (
      <Badge tone="cerulean">
        <AudioLines className="size-3" aria-hidden="true" />
        Voice enrolled · {person.sampleCount} samples
      </Badge>
    );
  }
  if (person.sampleCount > 0) {
    return (
      <Badge tone="warning">
        <AudioLines className="size-3" aria-hidden="true" />
        Voice: {person.sampleCount} of {person.samplesRequired} samples
      </Badge>
    );
  }
  return <Badge tone="neutral">No voice enrolled</Badge>;
}

/** Where each person is with their voice signature (pitch plus voice colour). Pitch is shown to managers only. */
function SignatureBadge({ person }: { person: Person }) {
  const signature = person.signature;
  if (!signature || (signature.sampleCount === 0 && !signature.outdated)) return null;
  if (signature.outdated) return <Badge tone="warning">Signature out of date · add samples again</Badge>;
  if (signature.ready) {
    return (
      <Badge tone="cerulean">
        Voice signature ready{signature.pitch ? ` · ${formatPitch(signature.pitch.baseHz)} base` : ""}
      </Badge>
    );
  }
  return (
    <Badge tone="warning">
      Signature: {signature.sampleCount} of {signature.samplesRequired} samples
    </Badge>
  );
}

export function PeopleView() {
  const people = usePeople();
  const me = useMe();
  const processing = useProcessing();
  const canManage = me.data?.role === "admin" || me.data?.role === "manager";
  // Unknown until the status loads; the page never promises enrollment before it knows.
  const enrollmentAvailable = processing.data?.enrollmentAvailable ?? false;

  return (
    <>
      <PageHeader
        title="People"
        description="People whose voices ASK may recognize. Each profile has a consent record, and enrollment can be removed at any time."
        actions={canManage ? <AddPersonDialog /> : null}
      />

      <div className="mb-6">
        <Callout title="Voice enrollment">
          {enrollmentAvailable ? (
            <p>
              Once consent is recorded, a person speaks a few short samples. Each recording is turned into a voice
              print and then deleted, and a voice is only ever compared with people whose consent is on record.
              Anyone not enrolled appears as Unknown speaker.
            </p>
          ) : (
            <p>
              Voice enrollment is not available on this server yet: it needs a speaker model to be set up by an
              administrator. Until then you can create profiles and record who has consented, but no voice is
              enrolled and no one is identified by voice.
            </p>
          )}
        </Callout>
      </div>

      {canManage ? <SignatureCheck /> : null}

      {people.isPending ? (
        <div className="space-y-3" aria-busy="true" aria-label="Loading people">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      ) : people.isError ? (
        <ErrorState headingLevel={2} description={people.error.message} onRetry={() => people.refetch()} />
      ) : people.data.length === 0 ? (
        <EmptyState
          headingLevel={2}
          icon={Users}
          title="No people yet"
          description="Add the people whose voices you want ASK to recognize. Anyone not enrolled appears as Unknown speaker."
          action={canManage ? <AddPersonDialog /> : undefined}
        />
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-line">
            {people.data.map((person) => (
              <li key={person.id} className="grid gap-4 px-5 py-5 sm:px-6 xl:grid-cols-[1fr_auto] xl:items-center">
                <div className="flex items-center gap-4">
                  <span className="grid size-11 shrink-0 place-items-center rounded-full border border-line-strong bg-white/[0.03] text-fg-muted">
                    <UserRound className="size-5" aria-hidden="true" />
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-fg">{person.displayName}</p>
                    <p className="mt-1 text-xs text-fg-muted">
                      {person.consentRecordedAt
                        ? `Consent recorded ${formatDateTime(person.consentRecordedAt)}`
                        : "No consent recorded"}
                    </p>
                  </div>
                </div>
                <div className="flex flex-col gap-3 xl:items-end">
                  <div className="flex flex-wrap items-center gap-2 xl:justify-end">
                    <ConsentBadge status={person.consentStatus} />
                    <VoiceBadge person={person} />
                    <SignatureBadge person={person} />
                  </div>
                  {canManage ? <PersonActions person={person} enrollmentAvailable={enrollmentAvailable} /> : null}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
