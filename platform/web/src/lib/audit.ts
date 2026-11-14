import type { AuditCategory, AuditEvent } from "@/lib/api/types";

/** Words for each recorded action. Anything not listed falls back to the action's own name. */
const ACTION_LABELS: Record<string, string> = {
  "auth.login": "Signed in",
  "auth.login_failed": "Failed sign-in attempt",
  "auth.logout": "Signed out",
  "auth.password_changed": "Changed their password",
  "auth.password_reset": "Reset their password",
  "auth.password_reset_by_operator": "Password reset by an operator",
  "auth.password_reset_requested": "Asked for a password reset",
  "auth.session_revoked": "Ended a sign-in session",
  "company.created": "Workspace created",
  "device.created": "Added a locket",
  "device.paired": "Locket paired",
  "device.assigned": "Changed who wears a locket",
  "device.connected": "Connected to a locket",
  "device.revoked": "Revoked a locket",
  "invitation.created": "Invited a teammate",
  "invitation.accepted": "Accepted an invitation",
  "invitation.revoked": "Withdrew an invitation",
  "person.created": "Added a person",
  "person.deleted": "Removed a person",
  "person.consent_granted": "Recorded consent",
  "person.consent_revoked": "Recorded withdrawn consent",
  "person.voice_sample_added": "Added a voice sample",
  "person.voice_profile_deleted": "Removed a voice profile",
  "recording.deleted": "Deleted a recording",
  "recording.downloaded": "Downloaded recording audio",
  "recording.played": "Listened to a recording",
  "member.role_changed": "Changed someone's role",
  "member.deactivated": "Switched someone off",
  "member.reactivated": "Switched someone back on",
  "member.sessions_ended": "Signed someone out everywhere",
  "recording.processed": "Recording finished processing",
  "recording.processing_failed": "Recording processing failed",
  "recording.purged": "Recording permanently removed",
  "recording.reprocess_requested": "Asked to process a recording again",
  "recording.retention_deleted": "Recording removed by the retention rule",
  "settings.updated": "Changed workspace settings",
  "transcript.corrected": "Corrected a transcript",
};

export const AUDIT_CATEGORIES: Array<{ value: AuditCategory; label: string }> = [
  { value: "auth", label: "Sign-ins and passwords" },
  { value: "device", label: "Lockets" },
  { value: "recording", label: "Recordings" },
  { value: "transcript", label: "Transcripts" },
  { value: "person", label: "People and consent" },
  { value: "invitation", label: "Invitations" },
  { value: "member", label: "Team members" },
  { value: "settings", label: "Settings" },
  { value: "company", label: "Workspace" },
];

const OBJECT_LABELS: Record<string, string> = {
  person: "Person",
  device: "Locket",
  recording: "Recording",
  user: "Account",
  invitation: "Invitation",
  company: "Workspace",
  session: "Session",
  transcript: "Transcript",
};

/** "person.created" becomes "Added a person". An action nobody wrote words for still reads sensibly. */
export function describeAction(action: string): string {
  const known = ACTION_LABELS[action];
  if (known) return known;
  const plain = action.replace(/[._]+/g, " ").trim();
  return plain ? plain.charAt(0).toUpperCase() + plain.slice(1) : "Unknown action";
}

export function describeObject(type: string | null): string | null {
  if (!type) return null;
  return OBJECT_LABELS[type] ?? type.replace(/_/g, " ");
}

/** The first eight characters are enough to tell objects apart on screen; the whole id stays in a tooltip. */
export function shortId(id: string | null): string | null {
  return id ? id.slice(0, 8) : null;
}

/** What the page can link to. A recording that was deleted or purged has no page to open. */
export function linkFor(event: Pick<AuditEvent, "objectType" | "objectId" | "action">): string | null {
  if (event.objectType !== "recording" || !event.objectId) return null;
  if (/deleted|purged/.test(event.action)) return null;
  return `/dashboard/recordings/${encodeURIComponent(event.objectId)}`;
}

/** "retention_days" becomes "retention days". Values are shown as they are, short. */
export function detailPairs(detail: AuditEvent["detail"]): Array<{ key: string; value: string }> {
  if (!detail) return [];
  return Object.entries(detail).map(([key, value]) => ({
    key: key.replace(/_/g, " "),
    value: value === null ? "none" : typeof value === "object" ? JSON.stringify(value) : String(value),
  }));
}

/** A person or a locket, never an id. No actor means the system did it, or the account is gone. */
export function describeActor(actor: AuditEvent["actor"]): { name: string; note: string | null } {
  if (!actor) return { name: "System or removed account", note: null };
  if (actor.kind === "device") return { name: actor.name, note: "Locket" };
  return { name: actor.name, note: actor.email };
}

/** A calendar day in the browser's time zone as the instant it starts, for the API. */
export function startOfDayIso(day: string): string | undefined {
  const value = new Date(`${day}T00:00:00`);
  return Number.isNaN(value.getTime()) ? undefined : value.toISOString();
}

/** The last moment of a calendar day in the browser's time zone. */
export function endOfDayIso(day: string): string | undefined {
  const value = new Date(`${day}T23:59:59.999`);
  return Number.isNaN(value.getTime()) ? undefined : value.toISOString();
}
