"use client";

import { useState, type ReactElement, type ReactNode } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { ApiError } from "@/lib/api/client";

type Props = {
  /** The button that opens the dialog. */
  trigger: ReactElement;
  title: string;
  description: string;
  confirmLabel: string;
  /** Rose-outlined "danger" for anything that removes or ends something. */
  variant?: "primary" | "danger";
  /** Extra copy or a required acknowledgement, shown above the buttons. */
  children?: ReactNode;
  /** Keeps the confirm button disabled until, for example, a checkbox is ticked. */
  confirmDisabled?: boolean;
  /** Runs the action. Resolve to close the dialog; reject (or throw an ApiError) to keep it open. */
  onConfirm: () => Promise<unknown>;
  successMessage: string;
  failureMessage: string;
};

/**
 * A confirmation step for actions that cannot be casually undone. It states exactly what will
 * happen, closes only when the server accepts the change, and otherwise leaves the dialog open
 * with the server's own explanation.
 */
export function ConfirmDialog({
  trigger,
  title,
  description,
  confirmLabel,
  variant = "danger",
  children,
  confirmDisabled,
  onConfirm,
  successMessage,
  failureMessage,
}: Props) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  async function confirm() {
    setPending(true);
    try {
      await onConfirm();
      toast.success(successMessage);
      setOpen(false);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : failureMessage);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent title={title} description={description}>
        {children}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button variant={variant} loading={pending} disabled={confirmDisabled} onClick={confirm}>
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
