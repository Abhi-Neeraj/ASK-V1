"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Radio } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { SimulatedBadge } from "@/components/status-badges";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import { useConnectDevice, useDevices, useMe } from "@/lib/api/hooks";
import type { Device, User } from "@/lib/api/types";

const STORAGE_KEY = "ask.device-prompts";

function readPrompted(): ReadonlySet<string> {
  try {
    const stored: unknown = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) ?? "[]");
    return new Set(Array.isArray(stored) ? stored.filter((id): id is string => typeof id === "string") : []);
  } catch {
    return new Set(); // storage blocked or corrupt: the prompt still works, it just cannot remember across reloads
  }
}

function remember(id: string) {
  try {
    const next = new Set(readPrompted());
    next.add(id);
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify([...next]));
  } catch {
    // see readPrompted
  }
}

/**
 * Whose business is a locket coming online? The person wearing it, and managers for a locket nobody
 * has been assigned yet. A manager is not interrupted for lockets that belong to someone else.
 */
export function isMineToConnect(device: Device, user: User): boolean {
  if (device.status !== "online" || device.connected) return false;
  if (device.assignedTo) return device.assignedTo.id === user.id;
  return user.role === "admin" || user.role === "manager";
}

/**
 * Offers to connect when a locket of yours comes online, once per locket per browser session.
 * It watches the device list, which live events keep fresh, so it needs no feed of its own and the
 * same logic covers a locket that was already online when the page opened.
 */
export function DeviceDetectedPrompt() {
  const me = useMe();
  const devices = useDevices();
  // Lockets already offered earlier in this browser session, fixed when the page loaded.
  const [alreadyOffered] = useState(readPrompted);
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(() => new Set());

  const candidate =
    me.data && devices.data
      ? devices.data.find((d) => isMineToConnect(d, me.data) && !alreadyOffered.has(d.id) && !dismissed.has(d.id))
      : undefined;

  const candidateId = candidate?.id;
  useEffect(() => {
    if (candidateId) remember(candidateId);
  }, [candidateId]);

  return (
    <AnimatePresence>
      {candidate ? (
        <PromptCard
          key={candidate.id}
          device={candidate}
          onDismiss={() => setDismissed((current) => new Set(current).add(candidate.id))}
        />
      ) : null}
    </AnimatePresence>
  );
}

function PromptCard({ device, onDismiss }: { device: Device; onDismiss: () => void }) {
  const connect = useConnectDevice();
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // Escape belongs to an open dialog first.
      if (event.key === "Escape" && !document.querySelector('[role="dialog"]')) onDismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onDismiss]);

  const message =
    connect.error instanceof ApiError ? connect.error.message : connect.isError ? "Could not connect. Please try again." : null;

  return (
    <motion.section
      role="region"
      aria-label="Locket detected"
      aria-live="polite"
      className="fixed inset-x-4 top-4 z-40 border border-cerulean/45 bg-ink-overlay p-5 shadow-2xl sm:inset-x-auto sm:right-6 sm:top-20 sm:w-[24rem] rounded-[var(--radius-card)]"
      initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -16, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -8 }}
      transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
    >
      <div className="flex items-start gap-4">
        <span className="relative grid size-10 shrink-0 place-items-center rounded-full bg-cerulean/15 text-cerulean-glow">
          <span aria-hidden="true" className="absolute inset-0 rounded-full border border-cerulean/50 motion-safe:animate-ping" />
          <Radio className="size-5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="truncate font-heading text-base font-semibold text-fg">{device.name} is online</h2>
          <p className="mt-1 flex flex-wrap items-center gap-2 font-mono text-xs text-fg-muted">
            {device.deviceId}
            {device.simulated ? <SimulatedBadge /> : null}
          </p>
          <p className="mt-3 text-sm leading-relaxed text-fg-muted">
            Connect to start working with this locket. You can disconnect at any time, and every recording is kept.
          </p>
        </div>
      </div>
      {message ? (
        <p role="alert" className="mt-4 border border-danger/40 bg-danger/[0.08] px-3 py-2 text-sm text-danger">
          {message}
        </p>
      ) : null}
      <div className="mt-5 flex justify-end gap-3">
        <Button variant="ghost" size="sm" onClick={onDismiss}>
          Not now
        </Button>
        <Button
          size="sm"
          loading={connect.isPending}
          onClick={() =>
            connect.mutate(device.id, {
              onSuccess: () => {
                toast.success(`Connected to ${device.name}.`);
                onDismiss();
              },
            })
          }
        >
          Connect
        </Button>
      </div>
    </motion.section>
  );
}
