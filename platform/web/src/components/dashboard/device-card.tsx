"use client";

import { Ban, BatteryMedium, Clock, Cpu, Link2, Link2Off, UploadCloud, User, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/dashboard/confirm-dialog";
import { DeviceBadge, SimulatedBadge } from "@/components/status-badges";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import {
  useAssignDevice,
  useConnectDevice,
  useDisconnectDevice,
  useMe,
  useMembers,
  useRevokeDevice,
} from "@/lib/api/hooks";
import type { Device } from "@/lib/api/types";
import { formatRelative } from "@/lib/format";

/** One label/value pair. The icon lives inside the <dt>, so each group is a valid <dt>+<dd> pair. */
function Fact({
  icon: Icon,
  label,
  className,
  children,
}: {
  icon: LucideIcon;
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <dt className="caption flex items-center gap-2 text-fg-subtle">
        <Icon className="size-4 shrink-0" aria-hidden="true" />
        {label}
      </dt>
      <dd className="mt-1 truncate pl-6 text-sm text-fg">{children}</dd>
    </div>
  );
}

export function DeviceCard({ device }: { device: Device }) {
  const connect = useConnectDevice();
  const disconnect = useDisconnectDevice();
  const assign = useAssignDevice();
  const revoke = useRevokeDevice();
  const me = useMe();

  const online = device.status === "online";
  const revoked = device.status === "revoked";
  const busy = connect.isPending || disconnect.isPending;
  const canManage = me.data?.role === "admin" || me.data?.role === "manager";
  const members = useMembers(canManage && !revoked);

  const fail = (error: unknown) =>
    toast.error(error instanceof ApiError ? error.message : "Something went wrong. Please try again.");

  return (
    <Card className="flex flex-col p-6">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate font-heading text-lg font-semibold text-fg">{device.name}</h3>
          <p className="mt-1 font-mono text-xs text-fg-muted">{device.deviceId}</p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
          {device.simulated ? <SimulatedBadge /> : null}
          <DeviceBadge status={device.status} />
        </div>
      </div>

      <dl className="mt-6 grid gap-5 sm:grid-cols-2">
        <Fact icon={Clock} label="Last heartbeat">
          {formatRelative(device.lastHeartbeatAt)}
        </Fact>
        <Fact icon={UploadCloud} label="Last upload">
          {formatRelative(device.lastUploadAt)}
        </Fact>
        <Fact icon={Cpu} label="Firmware">
          {device.firmwareVersion ?? "Not reported"}
        </Fact>
        <Fact icon={BatteryMedium} label="Battery">
          {device.batteryPercent === null ? "Not reported" : `${device.batteryPercent}%`}
        </Fact>
        <Fact icon={UploadCloud} label="Upload queue">
          {device.uploadQueueDepth === null
            ? "Not reported"
            : device.uploadQueueDepth === 0
              ? "Empty"
              : `${device.uploadQueueDepth} waiting`}
        </Fact>
        {/* The picker needs room for a full name, so it spans both columns. */}
        <Fact icon={User} label="Assigned to" className={canManage && !revoked ? "sm:col-span-2" : undefined}>
          {canManage && !revoked ? (
            // The cell above this one is a single truncated line; the hints below need to wrap.
            <div className="space-y-2 whitespace-normal">
              <Select
                aria-label={`Who wears ${device.name}`}
                className="h-9 py-0"
                value={device.assignedTo?.id ?? ""}
                disabled={assign.isPending || !members.data}
                onChange={(event) =>
                  assign.mutate(
                    { id: device.id, userId: event.target.value || null },
                    {
                      onSuccess: (updated) =>
                        toast.success(
                          updated.assignedTo
                            ? `${device.name} is now assigned to ${updated.assignedTo.name}.`
                            : `${device.name} is now unassigned.`,
                        ),
                      onError: fail,
                    },
                  )
                }
              >
                <option value="">Unassigned</option>
                {/* Someone switched off cannot be given a locket, but stays listed if they already hold one so the
                    box shows the truth and the locket can be moved to someone else. */}
                {(members.data ?? [])
                  .filter((member) => member.isActive || member.id === device.assignedTo?.id)
                  .map((member) => (
                    <option key={member.id} value={member.id}>
                      {member.name}
                    </option>
                  ))}
              </Select>
              <p className="text-xs leading-relaxed text-fg-muted">
                What this locket has already recorded stays with the person who had it. New recordings go to whoever
                has it when they arrive.
              </p>
              {device.uploadQueueDepth ? (
                <p className="text-xs leading-relaxed text-warning">
                  {device.uploadQueueDepth === 1
                    ? "1 recording is still waiting to upload from this locket"
                    : `${device.uploadQueueDepth} recordings are still waiting to upload from this locket`}{" "}
                  and will go to whoever has it when it arrives. To keep them with the current wearer, wait until the
                  upload queue is empty before changing who wears it.
                </p>
              ) : null}
            </div>
          ) : (
            (device.assignedTo?.name ?? "Unassigned")
          )}
        </Fact>
      </dl>

      <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-line pt-5">
        {device.connected ? (
          <Button
            variant="secondary"
            size="sm"
            loading={disconnect.isPending}
            disabled={busy}
            onClick={() =>
              disconnect.mutate(device.id, {
                onSuccess: () => toast.success(`Disconnected from ${device.name}. Recordings are kept.`),
                onError: fail,
              })
            }
          >
            <Link2Off className="size-4" aria-hidden="true" />
            Disconnect
          </Button>
        ) : (
          <Button
            size="sm"
            loading={connect.isPending}
            disabled={!online || busy}
            onClick={() =>
              connect.mutate(device.id, {
                onSuccess: () => toast.success(`Connected to ${device.name}.`),
                onError: fail,
              })
            }
          >
            <Link2 className="size-4" aria-hidden="true" />
            Connect
          </Button>
        )}
        {!online && !device.connected ? (
          <p className="text-xs text-fg-muted">
            {revoked
              ? "This locket was revoked and can no longer send recordings."
              : device.status === "pending"
                ? "Finish pairing on the locket first."
                : "Connect becomes available when the locket is online."}
          </p>
        ) : null}
        {canManage && !revoked ? (
          <div className="ml-auto">
            <ConfirmDialog
              trigger={
                <Button variant="ghost" size="sm" disabled={revoke.isPending}>
                  <Ban className="size-4" aria-hidden="true" />
                  Revoke
                </Button>
              }
              title={`Revoke ${device.name}?`}
              description="The locket's credential stops working immediately and any unused pairing code is voided. Recordings it already uploaded stay in your library. To use the hardware again, add it as a new locket."
              confirmLabel="Revoke locket"
              onConfirm={() => revoke.mutateAsync(device.id)}
              successMessage={`${device.name} was revoked.`}
              failureMessage="Could not revoke this locket."
            />
          </div>
        ) : null}
      </div>
    </Card>
  );
}
