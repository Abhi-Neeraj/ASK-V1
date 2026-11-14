"use client";

import { Check, Copy, LogOut, Mail, Plus, Power, UserRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/dashboard/confirm-dialog";
import { SectionTitle } from "@/components/dashboard/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { FormField, Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ui/states";
import { ApiError } from "@/lib/api/client";
import {
  useEndMemberSessions,
  useInvitations,
  useInvite,
  useMembers,
  useRevokeInvitation,
  useUpdateMember,
} from "@/lib/api/hooks";
import type { Invitation, Member, Role } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";
import { emailSchema } from "@/lib/validation";

const ROLE_LABELS: Record<Role, string> = { admin: "Administrator", manager: "Manager", employee: "Employee" };

const ROLE_HINTS: Record<Role, string> = {
  admin: "Full access, including retention settings.",
  manager: "Pairs lockets, manages people and invites employees.",
  employee: "Sees only the lockets assigned to them and their recordings.",
};

function InviteDialog({ viewerRole }: { viewerRole: Role }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("employee");
  const [error, setError] = useState<string | undefined>();
  const [created, setCreated] = useState<Invitation | null>(null);
  const [copied, setCopied] = useState(false);
  const invite = useInvite();

  // Managers may invite employees only; the server enforces the same rule.
  const roles: Role[] = viewerRole === "admin" ? ["employee", "manager", "admin"] : ["employee"];

  function reset() {
    setEmail("");
    setRole("employee");
    setError(undefined);
    setCreated(null);
    setCopied(false);
    invite.reset();
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = emailSchema.safeParse(email.trim());
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Enter a valid email address.");
      return;
    }
    setError(undefined);
    invite.mutate({ email: parsed.data, role }, { onSuccess: setCreated });
  }

  async function copy() {
    if (!created?.acceptUrl) return;
    try {
      await navigator.clipboard.writeText(created.acceptUrl);
      setCopied(true);
    } catch {
      toast.error("Could not copy. Select the link and copy it manually.");
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus className="size-4" aria-hidden="true" />
          Invite a teammate
        </Button>
      </DialogTrigger>
      <DialogContent
        title={created ? "Invitation created" : "Invite a teammate"}
        description={
          created
            ? created.acceptUrl
              ? "Send them this link. It works once and expires soon."
              : "The invitation has been emailed."
            : "They choose their own password from a link that only they receive."
        }
      >
        {created ? (
          <div className="space-y-5">
            {created.acceptUrl ? (
              <>
                <div className="space-y-3 border border-cerulean/40 bg-cerulean/[0.07] px-5 py-4">
                  <p className="break-all font-mono text-sm text-fg">{created.acceptUrl}</p>
                  <Button variant="secondary" size="sm" onClick={copy}>
                    {copied ? <Check className="size-4" aria-hidden="true" /> : <Copy className="size-4" aria-hidden="true" />}
                    {copied ? "Copied" : "Copy link"}
                  </Button>
                </div>
                <p className="text-sm leading-relaxed text-fg-muted">
                  Copy it now: this is the only time the link is shown. It is for{" "}
                  <strong className="font-medium text-fg">{created.email}</strong> and expires{" "}
                  {formatDateTime(created.expiresAt)}.
                </p>
              </>
            ) : (
              <p className="text-sm leading-relaxed text-fg-muted">
                We emailed <strong className="font-medium text-fg">{created.email}</strong> a link to create their
                account. It works once and expires {formatDateTime(created.expiresAt)}. The link is not shown here,
                so only the person it was sent to can use it. If it does not arrive, revoke the invitation and send a
                new one.
              </p>
            )}
            <DialogFooter>
              <Button onClick={() => setOpen(false)}>Done</Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={onSubmit} noValidate className="space-y-5">
            {invite.isError ? (
              <p role="alert" className="border border-danger/40 bg-danger/[0.08] px-4 py-3 text-sm text-danger">
                {invite.error instanceof ApiError ? invite.error.message : "Something went wrong. Please try again."}
              </p>
            ) : null}
            <FormField label="Work email" required error={error}>
              {(a11y) => (
                <Input
                  {...a11y}
                  name="email"
                  type="email"
                  autoComplete="off"
                  autoFocus
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              )}
            </FormField>
            <FormField
              label="Role"
              hint={viewerRole === "admin" ? ROLE_HINTS[role] : "Managers can invite employees."}
            >
              {(a11y) => (
                <Select
                  {...a11y}
                  name="role"
                  value={role}
                  disabled={roles.length === 1}
                  onChange={(event) => setRole(event.target.value as Role)}
                >
                  {roles.map((value) => (
                    <option key={value} value={value}>
                      {ROLE_LABELS[value]}
                    </option>
                  ))}
                </Select>
              )}
            </FormField>
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" loading={invite.isPending}>
                Create invitation
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** What an administrator can do about another person: their role, signing them out, switching them off or on. */
function MemberControls({ member }: { member: Member }) {
  const update = useUpdateMember();
  const endSessions = useEndMemberSessions();
  const name = member.name;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        aria-label={`Role for ${name}`}
        className="h-9 w-40 py-0"
        value={member.role}
        disabled={update.isPending || !member.isActive}
        onChange={(event) => {
          const role = event.target.value as Role;
          update.mutate(
            { id: member.id, change: { role } },
            {
              onSuccess: () => toast.success(`${name} is now ${ROLE_LABELS[role]}.`),
              onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not change the role."),
            },
          );
        }}
      >
        {(Object.keys(ROLE_LABELS) as Role[]).map((role) => (
          <option key={role} value={role}>
            {ROLE_LABELS[role]}
          </option>
        ))}
      </Select>
      {member.isActive ? (
        <>
          <ConfirmDialog
            trigger={
              <Button variant="ghost" size="sm">
                <LogOut className="size-4" aria-hidden="true" />
                Sign out everywhere
              </Button>
            }
            title={`Sign ${name} out everywhere?`}
            description={`Every browser ${name} is signed in on stops working at once. ${name} can sign in again with their password.`}
            confirmLabel="Sign out everywhere"
            onConfirm={() => endSessions.mutateAsync(member.id)}
            successMessage={`${name} was signed out everywhere.`}
            failureMessage="Could not sign them out."
          />
          <ConfirmDialog
            trigger={
              <Button variant="ghost" size="sm">
                <Power className="size-4" aria-hidden="true" />
                Switch off
              </Button>
            }
            title={`Switch off ${name}?`}
            description={`${name} is signed out everywhere and cannot sign in until you switch them back on. Their recordings stay, and lockets assigned to them stay assigned until you move them on the Devices page.`}
            confirmLabel="Switch off"
            onConfirm={() => update.mutateAsync({ id: member.id, change: { isActive: false } })}
            successMessage={`${name} was switched off.`}
            failureMessage="Could not switch them off."
          />
        </>
      ) : (
        <ConfirmDialog
          trigger={
            <Button variant="secondary" size="sm">
              <Power className="size-4" aria-hidden="true" />
              Switch back on
            </Button>
          }
          title={`Switch ${name} back on?`}
          description={`${name} can sign in again with their existing password.`}
          confirmLabel="Switch back on"
          variant="primary"
          onConfirm={() => update.mutateAsync({ id: member.id, change: { isActive: true } })}
          successMessage={`${name} was switched back on.`}
          failureMessage="Could not switch them on."
        />
      )}
    </div>
  );
}

function PendingInvitation({ invitation }: { invitation: Invitation }) {
  const revoke = useRevokeInvitation();
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 sm:px-6">
      <div className="flex min-w-0 items-center gap-4">
        <span className="grid size-10 shrink-0 place-items-center rounded-full border border-line-strong bg-white/[0.03] text-fg-muted">
          <Mail className="size-4" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-fg">{invitation.email}</p>
          <p className="mt-0.5 text-xs text-fg-muted">
            {ROLE_LABELS[invitation.role]} · expires {formatDateTime(invitation.expiresAt)}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <Badge tone="warning">Waiting</Badge>
        <ConfirmDialog
          trigger={
            <Button variant="ghost" size="sm">
              Revoke
            </Button>
          }
          title={`Revoke the invitation for ${invitation.email}?`}
          description="Their link stops working immediately. You can invite them again later."
          confirmLabel="Revoke invitation"
          onConfirm={() => revoke.mutateAsync(invitation.id)}
          successMessage="Invitation revoked."
          failureMessage="Could not revoke the invitation."
        />
      </div>
    </li>
  );
}

/**
 * Who has an account in this company, and who has been invited. Membership only ever comes from
 * an invitation or from company setup, so this is the single way a teammate joins.
 */
export function TeamSection({ viewerRole, viewerId }: { viewerRole: Role; viewerId: string }) {
  const members = useMembers();
  const invitations = useInvitations();

  return (
    <section aria-labelledby="team-title" className="mb-10">
      <SectionTitle action={<InviteDialog viewerRole={viewerRole} />}>
        <span id="team-title">Team</span>
      </SectionTitle>

      {members.isPending ? (
        <Skeleton className="h-40" />
      ) : members.isError ? (
        <ErrorState description={members.error.message} onRetry={() => members.refetch()} />
      ) : (
        <Card className="overflow-hidden">
          <ul className="divide-y divide-line">
            {members.data.map((member) => (
              <li key={member.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 sm:px-6">
                <div className="flex min-w-0 items-center gap-4">
                  <span className="grid size-10 shrink-0 place-items-center rounded-full border border-line-strong bg-white/[0.03] text-fg-muted">
                    <UserRound className="size-4" aria-hidden="true" />
                  </span>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-fg">
                      {member.name}
                      {member.id === viewerId ? <span className="ml-2 text-xs font-normal text-fg-muted">(you)</span> : null}
                      {member.isActive ? null : (
                        <Badge tone="warning" className="ml-2 align-middle">
                          Switched off
                        </Badge>
                      )}
                    </p>
                    {member.email ? <p className="mt-0.5 truncate text-xs text-fg-muted">{member.email}</p> : null}
                  </div>
                </div>
                {viewerRole === "admin" && member.id !== viewerId ? (
                  <MemberControls member={member} />
                ) : (
                  <Badge tone={member.role === "admin" ? "cerulean" : "neutral"}>{ROLE_LABELS[member.role]}</Badge>
                )}
              </li>
            ))}
            {invitations.data?.map((invitation) => <PendingInvitation key={invitation.id} invitation={invitation} />)}
          </ul>
        </Card>
      )}
      {invitations.isError ? (
        <p role="alert" className="mt-3 text-xs text-danger">
          Pending invitations could not be loaded.
        </p>
      ) : null}
    </section>
  );
}
