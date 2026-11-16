// The server's JSON, as declared in platform/api/app/schemas (camelCase on the wire).

export type Role = "admin" | "manager" | "employee";

export type UserOut = {
  id: string;
  email: string;
  name: string;
  role: Role;
  emailVerified: boolean;
  company: { id: string; name: string };
  csrfToken: string;
};

export type Page<T> = { items: T[]; total: number; page: number; pageSize: number };

export type RecordingState =
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

export type RecordingOut = {
  id: string;
  deviceId: string;
  deviceName: string;
  simulated: boolean;
  startedAt: string;
  endedAt: string;
  durationMs: number;
  sizeBytes: number;
  format: string;
  uploadedAt: string;
  part: number | null;
  state: RecordingState;
  retryCount: number;
  error: string | null;
  snippet: string | null;
  speakers: string[];
  placeholder: boolean;
};

export type SegmentOut = {
  id: string;
  startMs: number;
  endMs: number;
  speakerLabel: string;
  person: { id: string; name: string } | null;
  confidence: number | null;
  text: string;
  needsReview: boolean;
  corrected: boolean;
};

export type TranscriptOut = {
  reviewStatus: string;
  placeholder: boolean;
  language: string | null;
  segments: SegmentOut[];
};

export type RecordingDetailOut = RecordingOut & {
  transcript: TranscriptOut | null;
  audioUrl: string | null;
  failureDetail: string | null;
};

export type LocketMember = { id: string; name: string; email: string };

export type LocketOut = {
  productId: string;
  name: string;
  maxUsers: number;
  members: LocketMember[];
  firmwareVersion: string | null;
  batteryPercent: number | null;
  lastSeenAt: string | null;
};

export type LocketRegistered = { productId: string; name: string; secretHex: string };

export type CodeSent = { sentTo: string; expiresAt: string };

export type UploadAck = {
  recordingId: string;
  clientRecordingId: string;
  status: string;
  duplicate: boolean;
};

export type FirmwareLatest = { version: string; sizeBytes: number; sha256: string; downloadUrl: string };

export type MemberOut = { id: string; name: string; email: string | null; role: Role; isActive: boolean };

export type InvitationOut = {
  id: string;
  email: string;
  role: Role;
  expiresAt: string;
  createdAt: string;
  accepted: boolean;
  acceptUrl: string | null;
};

export type WorkspaceSummary = {
  devicesOnline: number;
  devicesTotal: number;
  recordingsToday: number;
  inProgress: number;
  needsReview: number;
  failed: number;
};
