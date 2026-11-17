// A meeting of hours reaches the server as many one-minute parts, each its own recording. Lists show it as ONE row:
// the newest part is what opening it shows (that page carries the whole meeting), and the totals come from the
// server, so they are right even when only the newest parts are on the page.

import { isWorking } from "@/application/labels";
import type { RecordingOut, RecordingState } from "@/infrastructure/api/types";

export type MeetingView = {
  /** The newest part: its page shows the whole meeting. */
  recording: RecordingOut;
  startedAt: string;
  durationMs: number;
  parts: number;
  state: RecordingState;
  snippet: string | null;
};

const time = (iso: string) => new Date(iso).getTime();

export function groupMeetings(recordings: readonly RecordingOut[]): MeetingView[] {
  const groups = new Map<string, RecordingOut[]>();
  for (const recording of recordings) {
    const key = recording.sessionRef ? `${recording.deviceId}:${recording.sessionRef}` : recording.id;
    const members = groups.get(key);
    if (members) members.push(recording);
    else groups.set(key, [recording]);
  }
  return [...groups.values()].map((members) => {
    const byTime = [...members].sort((a, b) => time(a.startedAt) - time(b.startedAt));
    const oldest = byTime[0];
    const newest = byTime[byTime.length - 1];
    // A part still being worked on shows the meeting as working; a failed part shows it as failed.
    const working = [...byTime].reverse().find((r) => isWorking(r.state));
    const failed = byTime.find((r) => r.state === "failed");
    const review = byTime.find((r) => r.state === "needs_review");
    return {
      recording: newest,
      startedAt: newest.meetingStartedAt ?? oldest.startedAt,
      durationMs: newest.meetingDurationMs ?? members.reduce((sum, r) => sum + r.durationMs, 0),
      parts: newest.partCount ?? members.length,
      state: working?.state ?? (failed ? "failed" : review ? "needs_review" : newest.state),
      snippet: byTime.find((r) => r.snippet)?.snippet ?? null,
    };
  });
}
