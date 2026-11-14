"use client";

import { Radio } from "lucide-react";
import { DeviceCard } from "@/components/dashboard/device-card";
import { PageHeader } from "@/components/dashboard/page-header";
import { PairDeviceDialog } from "@/components/dashboard/pair-device-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { useDevices, useMe } from "@/lib/api/hooks";

export function DevicesView() {
  const devices = useDevices();
  const me = useMe();
  const canPair = me.data?.role === "admin" || me.data?.role === "manager";

  return (
    <>
      <PageHeader
        title="Devices"
        description="Lockets registered to your company. A locket shows as online while its authenticated heartbeats keep arriving."
        actions={canPair ? <PairDeviceDialog /> : null}
      />

      {devices.isPending ? (
        <div className="grid gap-5 lg:grid-cols-2" aria-busy="true" aria-label="Loading devices">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-72" />
          ))}
        </div>
      ) : devices.isError ? (
        <ErrorState headingLevel={2} description={devices.error.message} onRetry={() => devices.refetch()} />
      ) : devices.data.length === 0 ? (
        <EmptyState
          headingLevel={2}
          icon={Radio}
          title="No lockets yet"
          description={
            canPair
              ? "Pair your first locket to start receiving recordings. You will get a one-time code to enter on the device."
              : "Ask a manager or administrator to pair a locket and assign it to you."
          }
          action={canPair ? <PairDeviceDialog /> : undefined}
        />
      ) : (
        <ul className="grid gap-5 lg:grid-cols-2">
          {devices.data.map((device) => (
            <li key={device.id}>
              <DeviceCard device={device} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
