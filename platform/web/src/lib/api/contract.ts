import type {
  AuditEvent,
  AuditFilters,
  Device,
  Invitation,
  InvitationInfo,
  Member,
  MemberChange,
  Page,
  PairingCode,
  Person,
  ProcessingStatus,
  ProcessingSummary,
  Recording,
  RecordingDetail,
  RecordingFilters,
  Role,
  SegmentEdit,
  SignatureMatch,
  User,
  WorkspaceSettings,
} from "@/lib/api/types";
import type { LiveHandlers } from "@/lib/live/events";

/** Everything the UI needs from a backend. Implemented by the real HTTP client and by demo mode. */
export interface AskApi {
  auth: {
    me(): Promise<User>;
    login(email: string, password: string): Promise<User>;
    logout(): Promise<void>;
    invitation(token: string): Promise<InvitationInfo>;
    acceptInvitation(token: string, input: { name: string; password: string }): Promise<User>;
    requestPasswordReset(email: string): Promise<void>;
    resetPassword(token: string, password: string): Promise<void>;
  };
  devices: {
    list(): Promise<Device[]>;
    connect(id: string): Promise<Device>;
    disconnect(id: string): Promise<Device>;
    createPairing(name: string): Promise<PairingCode>;
    /** Managers only. `null` removes the assignment. */
    assign(id: string, userId: string | null): Promise<Device>;
    /** Managers only. Invalidates the locket's credential immediately. */
    revoke(id: string): Promise<Device>;
  };
  recordings: {
    list(filters: RecordingFilters): Promise<Page<Recording>>;
    get(id: string): Promise<RecordingDetail>;
    remove(id: string): Promise<void>;
    updateTranscript(id: string, edits: SegmentEdit[]): Promise<RecordingDetail>;
    /** Managers only. Replaces the transcript, so corrections made to it are lost. */
    reprocess(id: string): Promise<Recording>;
  };
  people: {
    list(): Promise<Person[]>;
    create(displayName: string): Promise<Person>;
    /** Managers only. Withdrawing consent also deletes the person's voice profile. */
    recordConsent(id: string, granted: boolean): Promise<Person>;
    /** Managers only. Transcript text stays, with those lines shown as "Unknown speaker". */
    remove(id: string): Promise<void>;
    /** Managers only. One spoken sample as a WAV recording. The audio is not kept; only a voice print is. */
    enrollSample(id: string, wav: Blob): Promise<Person>;
    /** Managers only. Deletes the voice print and voice signature and un-names past transcript lines. */
    removeVoice(id: string): Promise<void>;
    /** Managers only. One WAV file of the person speaking, added to their voice signature. The audio is not kept. */
    addSignatureSample(id: string, wav: Blob): Promise<Person>;
    /** Managers only. A WAV file of someone speaking, compared with the people who have a voice signature. */
    matchSignature(wav: Blob): Promise<SignatureMatch>;
  };
  audit: {
    /** Administrators only. Newest first. */
    list(filters: AuditFilters): Promise<Page<AuditEvent>>;
  };
  processing: {
    status(): Promise<ProcessingStatus>;
  };
  team: {
    members(): Promise<Member[]>;
    /** Managers only. Pending invitations that have not expired or been revoked. */
    invitations(): Promise<Invitation[]>;
    /** Managers may invite employees only; administrators may invite any role. */
    invite(input: { email: string; role: Role }): Promise<Invitation>;
    revokeInvitation(id: string): Promise<void>;
    /** Administrators only, never for themselves. Switching someone off signs them out everywhere. */
    updateMember(id: string, change: MemberChange): Promise<Member>;
    /** Administrators only. Signs someone out everywhere without switching them off. */
    endMemberSessions(id: string): Promise<void>;
  };
  /** The live feed. Real: server-sent events. Demo: a labelled simulation. */
  live: {
    /** Starts following the feed. Returns a function that stops it. */
    subscribe(handlers: LiveHandlers): () => void;
  };
  summary(): Promise<ProcessingSummary>;
  settings: {
    get(): Promise<WorkspaceSettings>;
    update(input: Partial<WorkspaceSettings>): Promise<WorkspaceSettings>;
  };
}
