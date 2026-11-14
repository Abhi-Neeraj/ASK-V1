// DEMO MODE ONLY. Invented sample data for reviewing the UI without a backend.
// Names, devices and transcripts here are fictional and are never shown unless
// NEXT_PUBLIC_DEMO_MODE=true, in which case the dashboard displays a "Demo mode" banner.

import type {
  Analysis,
  AnalysisItem,
  AuditActor,
  AuditEvent,
  Device,
  Member,
  Person,
  Pitch,
  ProcessingState,
  Recording,
  RecordingVoice,
  SummaryLine,
  SummarySection,
  TranscriptInfo,
  TranscriptSegment,
  User,
} from "@/lib/api/types";

const NOW = Date.now();
const minutesAgo = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

export const demoUser: User = {
  id: "usr_demo",
  email: "demo.admin@example.com",
  name: "Demo Administrator",
  role: "admin",
  emailVerified: true,
  company: { id: "cmp_demo", name: "Northwind Demo Co." },
};

/** People with accounts. Lockets are assigned to these, not to the voice profiles in `demoPeople`. */
export const demoMembers: Member[] = [
  { id: demoUser.id, name: demoUser.name, email: demoUser.email, role: "admin", isActive: true },
  { id: "usr_1", name: "Priya Nair", email: "priya.nair@example.com", role: "employee", isActive: true },
  { id: "usr_2", name: "Marcus Webb", email: "marcus.webb@example.com", role: "manager", isActive: true },
];

/** Invented pitch for the demo's voices. Nothing here was measured from audio. */
export const DEMO_PITCH: Record<string, Pitch> = {
  "Priya Nair": { baseHz: 212, lowHz: 171, highHz: 268 },
  "Marcus Webb": { baseHz: 118, lowHz: 96, highHz: 151 },
};
const DEMO_UNKNOWN_PITCH: Pitch = { baseHz: 164, lowHz: 138, highHz: 203 };

const noSignature = { ready: false, sampleCount: 0, samplesRequired: 3, outdated: false, updatedAt: null, pitch: null };

export const demoPeople: Person[] = [
  { id: "per_1", displayName: "Priya Nair", consentStatus: "granted", consentRecordedAt: minutesAgo(60 * 24 * 12), enrolled: true, enrolledAt: minutesAgo(60 * 24 * 12), sampleCount: 3, samplesRequired: 3, voiceOutdated: false, signature: { ready: true, sampleCount: 3, samplesRequired: 3, outdated: false, updatedAt: minutesAgo(60 * 24 * 12), pitch: DEMO_PITCH["Priya Nair"] } },
  { id: "per_2", displayName: "Marcus Webb", consentStatus: "granted", consentRecordedAt: minutesAgo(60 * 24 * 9), enrolled: true, enrolledAt: minutesAgo(60 * 24 * 9), sampleCount: 3, samplesRequired: 3, voiceOutdated: false, signature: { ready: true, sampleCount: 3, samplesRequired: 3, outdated: false, updatedAt: minutesAgo(60 * 24 * 9), pitch: DEMO_PITCH["Marcus Webb"] } },
  { id: "per_3", displayName: "Aiko Tanaka", consentStatus: "pending", consentRecordedAt: null, enrolled: false, enrolledAt: null, sampleCount: 0, samplesRequired: 3, voiceOutdated: false, signature: noSignature },
  { id: "per_4", displayName: "Sam Okafor", consentStatus: "revoked", consentRecordedAt: minutesAgo(60 * 24 * 30), enrolled: false, enrolledAt: null, sampleCount: 0, samplesRequired: 3, voiceOutdated: false, signature: noSignature },
];

const admin: AuditActor = { kind: "user", id: demoUser.id, name: demoUser.name, email: demoUser.email };
const priya: AuditActor = { kind: "user", id: "usr_1", name: "Priya Nair", email: "priya.nair@example.com" };
const marcus: AuditActor = { kind: "user", id: "usr_2", name: "Marcus Webb", email: "marcus.webb@example.com" };
const locket: AuditActor = { kind: "device", id: "dev_1", name: "Locket A7F2", email: null };

type AuditSeed = Pick<AuditEvent, "action" | "actor" | "objectType" | "objectId" | "detail" | "ip"> & { minutes: number };

/** Invented history so the audit page can be reviewed. Newest first once built. */
const AUDIT_SEEDS: AuditSeed[] = [
  { minutes: 4, action: "auth.login", actor: admin, objectType: "user", objectId: demoUser.id, detail: null, ip: "198.51.100.24" },
  { minutes: 6, action: "recording.played", actor: priya, objectType: "recording", objectId: "rec_005", detail: null, ip: "198.51.100.17" },
  { minutes: 9, action: "recording.downloaded", actor: marcus, objectType: "recording", objectId: "rec_001", detail: null, ip: "198.51.100.41" },
  { minutes: 17, action: "transcript.corrected", actor: priya, objectType: "recording", objectId: "rec_005", detail: { segments: 4, changed: 1 }, ip: "198.51.100.17" },
  { minutes: 26, action: "auth.login_failed", actor: marcus, objectType: "user", objectId: "usr_2", detail: null, ip: "203.0.113.9" },
  { minutes: 28, action: "auth.login", actor: marcus, objectType: "user", objectId: "usr_2", detail: null, ip: "198.51.100.41" },
  { minutes: 60, action: "recording.processed", actor: null, objectType: "recording", objectId: "rec_004", detail: { placeholder: false }, ip: null },
  { minutes: 95, action: "person.consent_granted", actor: marcus, objectType: "person", objectId: "per_2", detail: { voice_profile_removed: false }, ip: "198.51.100.41" },
  { minutes: 96, action: "person.created", actor: marcus, objectType: "person", objectId: "per_2", detail: null, ip: "198.51.100.41" },
  { minutes: 180, action: "device.assigned", actor: admin, objectType: "device", objectId: "dev_2", detail: { to: "usr_2" }, ip: "198.51.100.24" },
  { minutes: 185, action: "device.paired", actor: locket, objectType: "device", objectId: "dev_2", detail: null, ip: "198.51.100.77" },
  { minutes: 190, action: "device.created", actor: admin, objectType: "device", objectId: "dev_2", detail: null, ip: "198.51.100.24" },
  { minutes: 60 * 5, action: "recording.processing_failed", actor: null, objectType: "recording", objectId: "rec_007", detail: { attempts: 3 }, ip: null },
  { minutes: 60 * 8, action: "person.consent_revoked", actor: admin, objectType: "person", objectId: "per_4", detail: { voice_profile_removed: true }, ip: "198.51.100.24" },
  { minutes: 60 * 12, action: "member.role_changed", actor: admin, objectType: "user", objectId: "usr_2", detail: { from: "employee", to: "manager" }, ip: "198.51.100.24" },
  { minutes: 60 * 20, action: "settings.updated", actor: admin, objectType: "company", objectId: "cmp_demo", detail: { retention_days: 90 }, ip: "198.51.100.24" },
  { minutes: 60 * 26, action: "invitation.accepted", actor: priya, objectType: "invitation", objectId: "inv_1", detail: { role: "employee" }, ip: "198.51.100.17" },
  { minutes: 60 * 27, action: "invitation.created", actor: admin, objectType: "invitation", objectId: "inv_1", detail: { role: "employee" }, ip: "198.51.100.24" },
  { minutes: 60 * 30, action: "recording.deleted", actor: marcus, objectType: "recording", objectId: "rec_012", detail: { purge_after: null }, ip: "198.51.100.41" },
  { minutes: 60 * 50, action: "device.revoked", actor: admin, objectType: "device", objectId: "dev_9", detail: null, ip: "198.51.100.24" },
  { minutes: 60 * 72, action: "auth.password_changed", actor: priya, objectType: "user", objectId: "usr_1", detail: null, ip: "198.51.100.17" },
  { minutes: 60 * 96, action: "company.created", actor: admin, objectType: "company", objectId: "cmp_demo", detail: null, ip: null },
];

export const demoAuditEvents: AuditEvent[] = AUDIT_SEEDS.map((seed, index) => ({
  id: `aud_${String(index + 1).padStart(3, "0")}`,
  at: minutesAgo(seed.minutes),
  category: seed.action.split(".")[0] ?? "",
  action: seed.action,
  actor: seed.actor,
  objectType: seed.objectType,
  objectId: seed.objectId,
  detail: seed.detail,
  ip: seed.ip,
}));

export const demoDevices: Device[] = [
  {
    id: "dev_1",
    deviceId: "ASK-A7F2",
    name: "Locket A7F2",
    status: "online",
    simulated: false,
    connected: false,
    lastHeartbeatAt: minutesAgo(0.3),
    lastUploadAt: minutesAgo(3),
    firmwareVersion: "0.9.2",
    batteryPercent: 78,
    uploadQueueDepth: 0,
    assignedTo: { id: "usr_1", name: "Priya Nair" },
  },
  {
    id: "dev_2",
    deviceId: "ASK-K3M9",
    name: "Locket K3M9",
    status: "offline",
    simulated: false,
    connected: false,
    lastHeartbeatAt: minutesAgo(190),
    lastUploadAt: minutesAgo(205),
    firmwareVersion: "0.9.2",
    batteryPercent: null,
    uploadQueueDepth: 3,
    assignedTo: { id: "usr_2", name: "Marcus Webb" },
  },
  {
    id: "dev_3",
    deviceId: "ASK-T5Q1",
    name: "Spare locket",
    status: "pending",
    simulated: false,
    connected: false,
    lastHeartbeatAt: null,
    lastUploadAt: null,
    firmwareVersion: null,
    batteryPercent: null,
    uploadQueueDepth: null,
    assignedTo: null,
  },
];

const SNIPPETS = [
  "Let's confirm the delivery window for the east warehouse before Friday.",
  "I can send the revised drawings once the review comments are in.",
  "The client asked whether the workstation count changed since last week.",
  "We should move the safety briefing to the start of the shift.",
  "Can you check the purchase order status with procurement today?",
  "Okay, I'll book the room and share the agenda this afternoon.",
  "The second batch passed inspection, so we can ship on Monday.",
  "Remind me to follow up on the supplier quote tomorrow morning.",
];

type Seed = { state: ProcessingState; device: number; minutes: number; seconds: number; speakers: string[]; error?: string; retries?: number };

const SEEDS: Seed[] = [
  { state: "completed", device: 0, minutes: 3, seconds: 10, speakers: ["Priya Nair", "Marcus Webb"] },
  { state: "transcribing", device: 0, minutes: 0.6, seconds: 10, speakers: [] },
  { state: "queued", device: 0, minutes: 0.2, seconds: 10, speakers: [] },
  { state: "needs_review", device: 0, minutes: 18, seconds: 10, speakers: ["Priya Nair", "Unknown speaker"] },
  { state: "completed", device: 0, minutes: 42, seconds: 10, speakers: ["Priya Nair"] },
  { state: "completed", device: 1, minutes: 215, seconds: 10, speakers: ["Marcus Webb"] },
  { state: "failed", device: 1, minutes: 230, seconds: 10, speakers: [], error: "The audio file could not be decoded (unsupported sample rate).", retries: 3 },
  { state: "completed", device: 0, minutes: 60 * 5, seconds: 24, speakers: ["Priya Nair", "Marcus Webb", "Unknown speaker"] },
  { state: "diarizing", device: 0, minutes: 1.1, seconds: 10, speakers: [] },
  { state: "completed", device: 1, minutes: 60 * 26, seconds: 10, speakers: ["Marcus Webb"] },
  { state: "completed", device: 0, minutes: 60 * 27, seconds: 10, speakers: ["Priya Nair"] },
  { state: "completed", device: 0, minutes: 60 * 49, seconds: 24, speakers: ["Priya Nair", "Marcus Webb"] },
];

export const demoRecordings: Recording[] = SEEDS.map((seed, index) => {
  const device = demoDevices[seed.device];
  const startedAt = minutesAgo(seed.minutes + seed.seconds / 60);
  const done = seed.state === "completed" || seed.state === "needs_review";
  return {
    id: `rec_${String(index + 1).padStart(3, "0")}`,
    deviceId: device.id,
    deviceName: device.name,
    simulated: false,
    startedAt,
    endedAt: minutesAgo(seed.minutes),
    durationMs: seed.seconds * 1000,
    sizeBytes: seed.seconds * 32_000,
    format: "WAV, 16 kHz, mono, 16-bit",
    uploadedAt: minutesAgo(Math.max(seed.minutes - 0.1, 0)),
    state: seed.state,
    retryCount: seed.retries ?? 0,
    error: seed.error ?? null,
    snippet: done ? SNIPPETS[index % SNIPPETS.length] : null,
    speakers: seed.speakers,
    placeholder: false,
  };
});

/** Fictional transcript for a completed demo recording. */
export function demoTranscript(recording: Recording): TranscriptInfo | null {
  if (recording.state !== "completed" && recording.state !== "needs_review") return null;

  const named = recording.speakers.filter((name) => name !== "Unknown speaker");
  const lines: Array<{ text: string; speaker: string | null; confidence: number | null }> = [
    { text: recording.snippet ?? SNIPPETS[0], speaker: named[0] ?? null, confidence: named[0] ? 0.93 : null },
    { text: "Understood. I'll write that up and send it over after lunch.", speaker: named[1] ?? named[0] ?? null, confidence: named[1] ? 0.88 : named[0] ? 0.9 : null },
  ];
  if (recording.speakers.includes("Unknown speaker")) {
    lines.push({ text: "I'll send the files.", speaker: null, confidence: null });
  }

  const per = Math.max(Math.floor(recording.durationMs / lines.length), 1000);
  const segments: TranscriptSegment[] = lines.map((line, index) => {
    const person = demoPeople.find((p) => p.displayName === line.speaker);
    return {
      id: `${recording.id}_seg_${index + 1}`,
      startMs: index * per + 200,
      endMs: (index + 1) * per - 150,
      speakerLabel: `SPEAKER_0${index % 3}`,
      person: person ? { id: person.id, name: person.displayName } : null,
      confidence: line.confidence,
      text: line.text,
      needsReview: line.speaker === null,
      corrected: false,
    };
  });

  return {
    transcriptionModel: "Speech model (demo)",
    transcriptionModelVersion: "demo",
    diarizationModel: "Speaker model (demo)",
    diarizationModelVersion: "demo",
    reviewStatus: recording.state === "needs_review" ? "needs_review" : "none",
    placeholder: false,
    separationExperimental: false,
    language: "en",
    segments,
  };
}

// --- the demo's summary -----------------------------------------------------------------------------------
// Worked out from the invented demo transcript with a few plain checks, so the Speakers and Summary sections can
// be reviewed. It is not the real analysis, which runs on the server.

const DEMO_LABELS: Record<string, string> = {
  ACTION: "Action item",
  COMMITMENT: "Commitment",
  QUESTION: "Question",
  NOTE: "Note",
};

function demoActivity(text: string): string {
  if (/\?\s*$/.test(text)) return "QUESTION";
  if (/\b(I will|I'll|I can)\b/i.test(text)) return "COMMITMENT";
  if (/\b(let's|should|please|remind me)\b/i.test(text)) return "ACTION";
  return "NOTE";
}

function demoDue(text: string): string | null {
  return /\b(after lunch|today|tomorrow(?: morning)?|this afternoon|on Monday|before Friday)\b/i.exec(text)?.[1] ?? null;
}

function demoTask(text: string, activity: string): string | null {
  if (activity !== "COMMITMENT" && activity !== "ACTION") return null;
  const match = /(?:I will|I'll|I can|let's|should|remind me to)\s+(.*?)[.?!]*$/i.exec(text);
  const due = demoDue(text);
  const task = match?.[1] ?? null;
  return task && due ? task.replace(new RegExp(`\\s*${due}$`, "i"), "") : task;
}

/** The demo's version of the analysis of a recording: its speakers, summary and sentences. */
export function demoAnalysis(recording: Recording, transcript: TranscriptInfo | null): Analysis | null {
  if (!transcript || transcript.segments.length === 0) return null;

  const spoken = new Map<string, { name: string | null; ms: number; turns: number }>();
  for (const segment of transcript.segments) {
    const entry = spoken.get(segment.speakerLabel) ?? { name: segment.person?.name ?? null, ms: 0, turns: 0 };
    entry.ms += segment.endMs - segment.startMs;
    entry.turns += 1;
    spoken.set(segment.speakerLabel, entry);
  }
  const total = [...spoken.values()].reduce((sum, v) => sum + v.ms, 0) || 1;
  const voices: RecordingVoice[] = [...spoken.entries()].map(([label, v], index) => ({
    label,
    title: v.name ?? `Speaker ${index + 1}`,
    name: v.name,
    talkMs: v.ms,
    share: v.ms / total,
    turns: v.turns,
    pitch: (v.name ? DEMO_PITCH[v.name] : undefined) ?? DEMO_UNKNOWN_PITCH,
    matchedBy: v.name ? "signature" : null,
    matchScore: v.name ? 0.74 : null,
  }));
  const titleOf = (label: string) => voices.find((v) => v.label === label)?.title ?? "Speaker 1";

  const items: AnalysisItem[] = transcript.segments.map((segment, index) => {
    const activity = demoActivity(segment.text);
    const previous = transcript.segments[index - 1];
    const speaker = titleOf(segment.speakerLabel);
    return {
      segmentIndex: index,
      startMs: segment.startMs,
      endMs: segment.endMs,
      speaker,
      what: segment.text,
      context: { previous: previous?.text ?? null, previousSpeaker: previous ? titleOf(previous.speakerLabel) : null, topic: [] },
      activity,
      activityLabel: DEMO_LABELS[activity] ?? "Note",
      secondary: [],
      confidence: 0.7,
      task: demoTask(segment.text, activity),
      owner: activity === "COMMITMENT" ? speaker : activity === "ACTION" ? "The team" : null,
      due: demoDue(segment.text),
      dated: (activity === "COMMITMENT" || activity === "ACTION") && demoDue(segment.text) !== null,
      verb: demoTask(segment.text, activity)?.split(" ", 1)[0]?.toLowerCase() ?? null,
      keywords: [],
      open: activity === "QUESTION" ? true : null,
    };
  });

  const counts: Record<string, number> = {};
  for (const item of items) counts[item.activity] = (counts[item.activity] ?? 0) + 1;

  const toLine = (item: AnalysisItem): SummaryLine => ({
    activity: item.activity,
    activityLabel: item.activityLabel,
    text: item.task ?? item.what,
    said: item.what,
    speaker: item.speaker,
    owner: item.owner,
    due: item.due,
    open: item.open,
    atMs: item.startMs,
    segmentIndex: item.segmentIndex,
    confidence: item.confidence,
  });
  const section = (key: string, title: string, activity: string): SummarySection | null => {
    const lines = items.filter((i) => i.activity === activity).map(toLine);
    return lines.length ? { key, title, total: lines.length, lines } : null;
  };
  const sections = [
    section("actions", "Action items", "ACTION"),
    section("commitments", "Commitments", "COMMITMENT"),
    section("questions", "Questions", "QUESTION"),
  ].filter((s): s is SummarySection => s !== null);

  const seconds = Math.round(recording.durationMs / 1000);
  const who = voices.map((v) => `${v.title} (${Math.round(v.share * 100)}%)`).join(" and ");
  const narrative = [
    `This recording is ${seconds} seconds long with ${voices.length === 1 ? "one speaker" : `${voices.length} speakers`}: ${who}.`,
    `It contains ${items.length} sentence${items.length === 1 ? "" : "s"} (demo data, not a real analysis).`,
  ];
  return {
    engine: "lexical",
    voices,
    summary: { narrative, sections, sentences: items.length, truncated: false },
    items,
    counts,
    createdAt: recording.endedAt,
  };
}
