"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { PageHeader, SectionTitle } from "@/components/dashboard/page-header";
import { TeamSection } from "@/components/dashboard/team-section";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { FormField, Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api/client";
import { useLogout, useMe, useSettings, useUpdateSettings } from "@/lib/api/hooks";
import type { WorkspaceSettings } from "@/lib/api/types";

const RETENTION_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "Keep until deleted" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "180", label: "180 days" },
  { value: "365", label: "1 year" },
];

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 py-4 sm:grid-cols-[11rem_1fr] sm:gap-6">
      <dt className="caption text-fg-subtle">{label}</dt>
      <dd className="text-sm text-fg">{children}</dd>
    </div>
  );
}

export function SettingsView() {
  const me = useMe();
  const settings = useSettings();
  const router = useRouter();
  const logout = useLogout();

  return (
    <>
      <PageHeader
        title="Settings"
        description="Your account, your team and the retention rules for this workspace."
      />

      <section aria-labelledby="account-title" className="mb-10">
        <SectionTitle>
          <span id="account-title">Account</span>
        </SectionTitle>
        <Card>
          <CardBody className="py-2">
            {me.data ? (
              <dl className="divide-y divide-line">
                <Row label="Name">{me.data.name}</Row>
                <Row label="Email">
                  {me.data.email} {me.data.emailVerified ? null : <span className="text-warning">(not verified)</span>}
                </Row>
                <Row label="Company">{me.data.company.name}</Row>
                <Row label="Role">{me.data.role}</Row>
              </dl>
            ) : (
              <Skeleton className="my-4 h-32" />
            )}
          </CardBody>
        </Card>
      </section>

      {me.data && (me.data.role === "admin" || me.data.role === "manager") ? (
        <TeamSection viewerRole={me.data.role} viewerId={me.data.id} />
      ) : null}

      <section aria-labelledby="retention-title" className="mb-10">
        <SectionTitle>
          <span id="retention-title">Retention</span>
        </SectionTitle>
        {settings.isPending ? (
          <Skeleton className="h-52" />
        ) : settings.isError ? (
          <ErrorState description={settings.error.message} onRetry={() => settings.refetch()} />
        ) : (
          // Keyed by the saved values so the form re-initializes after a successful save.
          <RetentionForm
            key={`${settings.data.retentionDays}-${settings.data.purgeGraceDays}`}
            initial={settings.data}
            canEdit={me.data?.role === "admin"}
          />
        )}
      </section>

      <section aria-labelledby="security-title">
        <SectionTitle>
          <span id="security-title">Security</span>
        </SectionTitle>
        <Card>
          <CardBody className="flex flex-wrap items-center justify-between gap-4">
            <p className="max-w-lg text-sm leading-relaxed text-fg-muted">
              Signing out ends this session on the server, so the sign-in cannot be reused from this browser.
            </p>
            <Button
              variant="secondary"
              size="sm"
              loading={logout.isPending}
              onClick={() => logout.mutate(undefined, { onSettled: () => router.replace("/login") })}
            >
              <LogOut className="size-4" aria-hidden="true" />
              Sign out
            </Button>
          </CardBody>
        </Card>
      </section>
    </>
  );
}

function RetentionForm({ initial, canEdit }: { initial: WorkspaceSettings; canEdit: boolean }) {
  const update = useUpdateSettings();
  const [retention, setRetention] = useState(initial.retentionDays === null ? "" : String(initial.retentionDays));
  const [grace, setGrace] = useState(String(initial.purgeGraceDays));
  const [graceError, setGraceError] = useState<string | undefined>();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const days = Number(grace);
    if (!Number.isInteger(days) || days < 0 || days > 365) {
      setGraceError("Enter a whole number of days from 0 to 365.");
      return;
    }
    setGraceError(undefined);
    update.mutate(
      { retentionDays: retention === "" ? null : Number(retention), purgeGraceDays: days },
      {
        onSuccess: () => toast.success("Retention settings saved."),
        onError: (error) => toast.error(error instanceof ApiError ? error.message : "Could not save settings."),
      },
    );
  }

  return (
    <Card>
      <CardBody>
        <form onSubmit={onSubmit} noValidate className="space-y-6">
          <fieldset disabled={!canEdit} className="grid gap-6 sm:grid-cols-2">
            <FormField label="Keep recordings for" hint="After this period, recordings are queued for deletion.">
              {(a11y) => (
                <Select {...a11y} value={retention} onChange={(event) => setRetention(event.target.value)}>
                  {RETENTION_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              )}
            </FormField>
            <FormField
              label="Deleted recordings are removed after (days)"
              hint="Audio and transcripts are held this long after deletion."
              error={graceError}
            >
              {(a11y) => (
                <Input {...a11y} inputMode="numeric" value={grace} onChange={(event) => setGrace(event.target.value)} />
              )}
            </FormField>
          </fieldset>
          <div className="flex flex-wrap items-center gap-4">
            <Button type="submit" size="sm" loading={update.isPending} disabled={!canEdit}>
              Save retention settings
            </Button>
            {canEdit ? null : <p className="text-xs text-fg-muted">Only a company administrator can change retention.</p>}
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
