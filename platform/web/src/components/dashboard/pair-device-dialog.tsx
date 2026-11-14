"use client";

import { Check, Copy, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { FormField, Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import { useCreatePairing } from "@/lib/api/hooks";
import type { PairingCode } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";

/**
 * Starts pairing: an administrator names a new locket and receives a short-lived, single-use code.
 * The locket redeems the code to receive its own credential. A locket on the same Wi-Fi is never
 * trusted without this step.
 */
export function PairDeviceDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | undefined>();
  const [pairing, setPairing] = useState<PairingCode | null>(null);
  const [copied, setCopied] = useState(false);
  const create = useCreatePairing();

  function reset() {
    setName("");
    setError(undefined);
    setPairing(null);
    setCopied(false);
    create.reset();
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = name.trim();
    if (trimmed.length < 2) {
      setError("Give the locket a name of at least 2 characters.");
      return;
    }
    setError(undefined);
    create.mutate(trimmed, { onSuccess: setPairing });
  }

  async function copy() {
    if (!pairing) return;
    try {
      await navigator.clipboard.writeText(pairing.code);
      setCopied(true);
    } catch {
      toast.error("Could not copy. Select the code and copy it manually.");
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
          Pair a locket
        </Button>
      </DialogTrigger>
      <DialogContent
        title={pairing ? "Pairing code" : "Pair a new locket"}
        description={
          pairing
            ? "Enter this code on the locket during setup. It works once and expires soon."
            : "Name the locket. You will get a one-time code to enter on the device."
        }
      >
        {pairing ? (
          <div className="space-y-5">
            <div className="flex items-center justify-between gap-3 border border-cerulean/40 bg-cerulean/[0.07] px-5 py-4">
              <code className="font-mono text-2xl tracking-[0.2em] text-fg">{pairing.code}</code>
              <Button variant="secondary" size="sm" onClick={copy}>
                {copied ? <Check className="size-4" aria-hidden="true" /> : <Copy className="size-4" aria-hidden="true" />}
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
            <p className="text-sm leading-relaxed text-fg-muted">
              Pairing <strong className="font-medium text-fg">{pairing.deviceName}</strong>. This code expires{" "}
              {formatDateTime(pairing.expiresAt)} and is shown only once, so copy it now. The locket will appear
              as online after it redeems the code and sends its first heartbeat.
            </p>
            <DialogFooter>
              <Button onClick={() => setOpen(false)}>Done</Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={onSubmit} noValidate className="space-y-5">
            {create.isError ? (
              <p role="alert" className="border border-danger/40 bg-danger/[0.08] px-4 py-3 text-sm text-danger">
                {create.error instanceof ApiError ? create.error.message : "Something went wrong. Please try again."}
              </p>
            ) : null}
            <FormField label="Locket name" required hint="For example, the person or team who will wear it." error={error}>
              {(a11y) => <Input {...a11y} name="name" autoFocus value={name} onChange={(event) => setName(event.target.value)} />}
            </FormField>
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" loading={create.isPending}>
                Create pairing code
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
