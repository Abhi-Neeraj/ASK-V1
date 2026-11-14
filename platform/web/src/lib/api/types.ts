// API contract types. JSON is camelCase end to end (the backend serializes by alias).
// Keep in step with platform/docs/API.md.

export type Role = "admin" | "manager" | "employee";

export type User = {
  id: string;
  email: string;
  name: string;
  role: Role;
  emailVerified: boolean;
  company: { id: string; name: string };
};

export type InvitationInfo = {
  email: string;
  companyName: string;
  role: Role;
  expiresAt: string;
};

/** A person with an account in this company. */
export type Member = {
  id: string;
  name: string;
  /** Only managers and administrators are shown addresses; everyone else gets null. */
  email: string | null;
  role: Role;
  /** False for someone an administrator has switched off. Only managers and administrators see them. */
  isActive: boolean;
};

export type MemberChange = { role?: Role; isActive?: boolean };

export type Invitation = {
  id: string;
  email: string;
  role: Role;
  expiresAt: string;
  createdAt: string;
  accepted: boolean;
  /** The link to share. Present only in the response that creates the invitation, never again. */
  acceptUrl: string | null;
};

export type DeviceStatus = "pending" | "online" | "offline" | "revoked";

export type Device = {
  id: string;
  /** Public device identifier printed on the locket and used by firmware. */
  deviceId: string;
  name: string;
  status: DeviceStatus;
  /** A development stand-in rather than real hardware. Always false on a production server. */
  simulated: boolean;
  /** This user has an active application-level connection to the device. */
  connected: boolean;
  lastHeartbeatAt: string | null;
  lastUploadAt: string | null;
  firmwareVersion: string | null;
  /** Null unless the firmware really reports it. */
  batteryPercent: number | null;
  /** Null until the device reports queue depth. */
  uploadQueueDepth: number | null;
  /** The company member who wears the locket. Employees see only lockets assigned to them. */
  assignedTo: { id: string; name: string } | null;
};

export type PairingCode = {
  deviceId: string;
  deviceName: string;
  /** One-time code the locket redeems to receive its own credential. Shown once. */
  code: string;
  expiresAt: string;
};

export type ProcessingState =
  | "uploaded"
  | "queued"
  | "validating"
  | "transcribing"
  | "diarizing"
  | "identifying_speakers"
  | "aligning"
  | "completed"
  | "failed"
  | "retrying"
  | "needs_review";

export type Recording = {
  id: string;
  deviceId: string;
  deviceName: string;
  /** Made by a simulated locket, so the audio is not a real recording. */
  simulated: boolean;
  startedAt: string;
  endedAt: string;
  durationMs: number;
  sizeBytes: number;
  format: string;
  uploadedAt: string;
  /**
   * 1, 2, 3 ... when this is one part of a longer recording that the locket cut up; null or absent for an
   * ordinary single recording. (Absent from servers that predate the field.)
   */
  part?: number | null;
  state: ProcessingState;
  retryCount: number;
  error: string | null;
  /** Short transcript preview, present once transcription finishes. */
  snippet: string | null;
  speakers: string[];
  /** The transcript is placeholder text from a development stand-in, not what was said. */
  placeholder: boolean;
};

export type TranscriptSegment = {
  id: string;
  startMs: number;
  endMs: number;
  /** Diarization label, for example "SPEAKER_00". */
  speakerLabel: string;
  /** Registered person, or null when the speaker is unknown. */
  person: { id: string; name: string } | null;
  /** Speaker-match confidence in 0..1, or null when not matched. */
  confidence: number | null;
  text: string;
  needsReview: boolean;
  corrected: boolean;
};

export type TranscriptInfo = {
  transcriptionModel: string;
  transcriptionModelVersion: string;
  diarizationModel: string | null;
  diarizationModelVersion: string | null;
  reviewStatus: "none" | "needs_review" | "reviewed";
  /** Placeholder text from a development stand-in. Every screen and export says so. */
  placeholder: boolean;
  /** Who spoke when came from an experimental method: the labels should be checked by listening. */
  separationExperimental: boolean;
  /** Detected language code, when the recognizer reports one. */
  language: string | null;
  segments: TranscriptSegment[];
};

/** How high or low a voice sits, in hertz. A summary of pitch: never the voice signature itself. */
export type Pitch = { baseHz: number; lowHz: number; highHz: number };

/** One speaker in a recording: how much they spoke and how high their voice sits. */
export type RecordingVoice = {
  /** Diarization label, for example "SPEAKER_00". */
  label: string;
  /** A name only where the voice was matched to a person with consent on record; otherwise "Speaker 1", ... */
  title: string;
  name: string | null;
  talkMs: number;
  /** Share of all speech in the recording, 0..1. */
  share: number;
  turns: number;
  /** Null when there was too little voiced speech to measure. */
  pitch: Pitch | null;
  /** How the name was found. Null when the speaker is not named. */
  matchedBy: "voice_model" | "signature" | null;
  /** How alike the voice was to the person's, 0..1. Managers only. A similarity, not a probability. */
  matchScore: number | null;
};

/** One sentence worked out by fixed rules. */
export type AnalysisItem = {
  segmentIndex: number;
  startMs: number;
  endMs: number;
  speaker: string;
  /** What was said. */
  what: string;
  context: {
    previous: string | null;
    previousSpeaker: string | null;
    topic: string[];
    /** Which context (topic) the sentence is in. Null or absent when contexts were not worked out. */
    group?: number | null;
    /** True when this sentence opens a new context. */
    newContext?: boolean | null;
    /** How similar it was to the closest of the three sentences before it, 0..1. */
    similarity?: number | null;
  };
  /** The key activity: DECISION, ACTION, COMMITMENT, QUESTION, REQUIREMENT, ISSUE, RISK, APPROVAL, REJECTION or NOTE. */
  activity: string;
  activityLabel: string;
  secondary: string[];
  confidence: number;
  /** The task the sentence gives rise to, where it has one. */
  task: string | null;
  /** Who it falls to, in words. Null when nobody is named. */
  owner: string | null;
  /** The deadline as spoken ("by Friday"). */
  due: string | null;
  /** An activity together with a date or time: a task with a deadline. (Absent from older servers.) */
  dated?: boolean;
  /** The action verb of the sentence, and its main words. (Absent from older servers.) */
  verb?: string | null;
  keywords?: string[];
  /** Questions only: true when nobody answered it. */
  open: boolean | null;
};

export type SummaryLine = {
  activity: string;
  activityLabel: string;
  text: string;
  said: string;
  speaker: string;
  owner: string | null;
  due: string | null;
  dated?: boolean;
  open: boolean | null;
  atMs: number;
  segmentIndex: number;
  confidence: number | null;
};

export type SummarySection = { key: string; title: string; total: number; lines: SummaryLine[] };

/** Built from the analysis by fixed templates. No generative AI. */
export type Summary = {
  narrative: string[];
  sections: SummarySection[];
  sentences: number;
  truncated: boolean;
};

/** One stretch of the conversation about one subject. */
export type AnalysisContext = {
  group: number;
  startMs: number;
  endMs: number;
  sentences: number;
  keywords: string[];
};

export type Analysis = {
  /** How sentences were matched to activities: by meaning and key phrases, or by key phrases and words. */
  engine: "semantic" | "lexical";
  voices: RecordingVoice[];
  summary: Summary;
  items: AnalysisItem[];
  counts: Record<string, number>;
  /** The topics, in order. Empty or absent when contexts were not worked out (the words engine, unless asked). */
  contexts?: AnalysisContext[];
  contextThreshold?: number | null;
  createdAt: string;
};

export type RecordingDetail = Recording & {
  transcript: TranscriptInfo | null;
  /** Authorized users get a short-lived URL for playback and download. Null when not allowed. */
  audioUrl: string | null;
  /** Why processing failed or is being retried. Managers and administrators only; null for everyone else. */
  failureDetail: string | null;
  /**
   * The speakers' voices and the summary. Null while processing, for placeholder transcripts and when it could
   * not be made. (Absent from servers that predate the field.)
   */
  analysis?: Analysis | null;
};

export type ConsentStatus = "granted" | "pending" | "revoked";

export type Person = {
  id: string;
  displayName: string;
  consentStatus: ConsentStatus;
  consentRecordedAt: string | null;
  /** Enough samples, made by the speaker model now in use: this person can be recognized. */
  enrolled: boolean;
  enrolledAt: string | null;
  sampleCount: number;
  samplesRequired: number;
  /** The speaker model changed since they enrolled, so they need to enroll again. */
  voiceOutdated: boolean;
  /** Their voice signature (pitch plus voice colour). Absent from servers that predate the field. */
  signature?: SignatureStatus;
};

export type SignatureStatus = {
  /** Enough samples, made by the version of the measurement now in use: they can be matched by it. */
  ready: boolean;
  sampleCount: number;
  samplesRequired: number;
  /** Made by an older version of the measurement, so they need to start again. */
  outdated: boolean;
  updatedAt: string | null;
  /** Base, low and high pitch. Managers and administrators only; null for everyone else. */
  pitch: Pitch | null;
};

export type SignatureCandidate = {
  personId: string;
  name: string;
  /** How alike the voices are, 0..1. Not a probability and not proof of identity. */
  score: number;
  pitchGapOctaves: number;
  /** False when the pitch alone ruled this person out. */
  passedPitchGate: boolean;
};

/** Whom an uploaded voice is most like among people who have consented and enrolled a signature. */
export type SignatureMatch = {
  outcome: "matched" | "no_confident_match" | "nobody_enrolled";
  pitch: Pitch;
  voicedSeconds: number;
  candidatesConsidered: number;
  /** Set only when the best match clears the threshold and beats the next person by the margin. */
  match: SignatureCandidate | null;
  /** The closest few, best first. A name here is NOT a match: only `match` is. */
  closest: SignatureCandidate[];
  notice: string;
};

/** Whether recordings are really being processed, and what the worker can do. */
export type ProcessingStatus = {
  /** A worker has reported in recently. If false, queued recordings are waiting for one. */
  available: boolean;
  workers: number;
  /** The running worker writes placeholder text rather than real transcripts. */
  placeholder: boolean;
  transcription: boolean;
  diarization: boolean;
  speakerMatching: boolean;
  queued: number;
  running: number;
  /** Developer-facing detail (it can name libraries). Never shown in the interface. */
  notes: string[];
  /** This server can take voice samples. */
  enrollmentAvailable: boolean;
};

export type AuditCategory =
  | "auth"
  | "company"
  | "device"
  | "invitation"
  | "member"
  | "person"
  | "recording"
  | "settings"
  | "transcript";

/** Who did it: a person in this company, or a locket. */
export type AuditActor = { kind: "user" | "device"; id: string; name: string; email: string | null };

export type AuditEvent = {
  id: string;
  at: string;
  /** Machine name such as `person.created`. The interface shows words for it, see `lib/audit.ts`. */
  action: string;
  category: string;
  /** Null for an operator command or housekeeping, or when the account has since been removed. */
  actor: AuditActor | null;
  objectType: string | null;
  objectId: string | null;
  /** Counts and flags only. Never content, secrets or file paths. */
  detail: Record<string, unknown> | null;
  ip: string | null;
};

export type AuditFilters = {
  category?: AuditCategory | "";
  /** ISO 8601 instants. */
  dateFrom?: string;
  dateTo?: string;
  page?: number;
  pageSize?: number;
};

export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
};

export type RecordingFilters = {
  q?: string;
  deviceId?: string;
  state?: ProcessingState | "";
  page?: number;
  pageSize?: number;
};

export type ProcessingSummary = {
  devicesOnline: number;
  devicesTotal: number;
  recordingsToday: number;
  inProgress: number;
  needsReview: number;
  failed: number;
};

export type WorkspaceSettings = {
  /** Null keeps recordings until they are deleted. */
  retentionDays: number | null;
  /** Days a deleted recording is held before its audio is permanently removed. */
  purgeGraceDays: number;
};

export type SegmentEdit = { segmentId: string; text: string };
