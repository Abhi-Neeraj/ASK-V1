"use client";

import { Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogTrigger } from "@/components/ui/dialog";
import { ApiError } from "@/lib/api/client";
import { useDeleteRecording, useSettings } from "@/lib/api/hooks";

/**
 * Confirmation for deleting a recording. The dialog states the exact retention behavior (taken
 * from the workspace settings), so nobody has to guess what "delete" means.
 */
export function DeleteRecordingDialog({ id }: { id: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const settings = useSettings();
  const remove = useDeleteRecording();

  const grace = settings.data?.purgeGraceDays;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="danger" size="sm">
          <Trash2 className="size-4" aria-hidden="true" />
          Delete
        </Button>
      </DialogTrigger>
      <DialogContent
        title="Delete this recording?"
        description={
          grace === undefined
            ? "The recording leaves your library immediately. Its audio file and transcript are then permanently removed."
            : `The recording leaves your library immediately. Its audio file and transcript are permanently removed after ${grace} ${grace === 1 ? "day" : "days"}.`
        }
      >
        <p className="text-sm leading-relaxed text-fg-muted">
          This action is recorded in the audit log. Export the transcript first if you want to keep a copy.
        </p>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button
            variant="danger"
            loading={remove.isPending}
            onClick={() =>
              remove.mutate(id, {
                onSuccess: () => {
                  toast.success("Recording deleted.");
                  setOpen(false);
                  router.replace("/dashboard/recordings");
                },
                onError: (error) =>
                  toast.error(error instanceof ApiError ? error.message : "Could not delete the recording."),
              })
            }
          >
            Delete recording
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
