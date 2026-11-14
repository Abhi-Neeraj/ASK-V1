import { ApiError } from "@/lib/api/client";
import type { AskApi } from "@/lib/api/contract";
import type {
  AuditEvent,
  Device,
  Invitation,
  Member,
  Person,
  Recording,
  RecordingDetail,
  TranscriptInfo,
  WorkspaceSettings,
} from "@/lib/api/types";
import {
  DEMO_PITCH,
  demoAnalysis,
  demoAuditEvents,
  demoDevices,
  demoMembers,
  demoPeople,
  demoRecordings,
  demoTranscript,
  demoUser,
} from "@/lib/demo/data";

// DEMO MODE ONLY. In-memory state so connect, delete and edit actions visibly work.
// Everything resets on reload. Never used unless NEXT_PUBLIC_DEMO_MODE=true.

const SESSION_KEY = "ask_demo_session";
const DEMO_ONLINE_AFTER_MS = 8_000;

const devices: Device[] = structuredClone(demoDevices);
const people: Person[] = structuredClone(demoPeople);
const members: Member[] = structuredClone(demoMembers);
const invitations: Invitation[] = [];
let recordings: Recording[] = structuredClone(demoRecordings);
const transcripts = new Map<string, TranscriptInfo>();
let settings: WorkspaceSettings = { retentionDays: null, purgeGraceDays: 7 };

const delay = <T>(value: T, ms = 220) => new Promise<T>((resolve) => setTimeout(() => resolve(value), ms));

function signedIn(): boolean {
  try {
    return window.localStorage.getItem(SESSION_KEY) === "1";
  } catch {
    return false;
  }
}

function setSignedIn(value: boolean) {
  try {
    if (value) window.localStorage.setItem(SESSION_KEY, "1");
    else window.localStorage.removeItem(SESSION_KEY);
  } catch {
    // storage unavailable: the demo session just will not persist
  }
}

function requireSession() {
  if (!signedIn()) throw new ApiError(401, "unauthenticated", "Sign in to continue.");
}

function detail(recording: Recording): RecordingDetail {
  if (!transcripts.has(recording.id)) {
    const created = demoTranscript(recording);
    if (created) transcripts.set(recording.id, created);
  }
  const transcript = transcripts.get(recording.id) ?? null;
  return {
    ...recording,
    transcript,
    audioUrl: null,
    failureDetail: null,
    analysis: demoAnalysis(recording, transcript),
  };
}

export const demoApi: AskApi = {
  auth: {
    async me() {
      requireSession();
      return delay(demoUser, 120);
    },
    async login() {
      setSignedIn(true);
      return delay(demoUser);
    },
    async logout() {
      setSignedIn(false);
      return delay(undefined, 80);
    },
    async invitation(token) {
      if (token !== "demo") throw new ApiError(404, "invitation_not_found", "This invitation link is not valid.");
      return delay({
        email: "new.teammate@example.com",
        companyName: demoUser.company.name,
        role: "employee" as const,
        expiresAt: new Date(Date.now() + 3 * 24 * 3600_000).toISOString(),
      });
    },
    async acceptInvitation() {
      setSignedIn(true);
      return delay(demoUser);
    },
    async requestPasswordReset() {
      return delay(undefined);
    },
    async resetPassword() {
      return delay(undefined);
    },
  },
  devices: {
    async list() {
      requireSession();
      return delay(structuredClone(devices));
    },
    async connect(id) {
      requireSession();
      const device = devices.find((d) => d.id === id);
      if (!device) throw new ApiError(404, "device_not_found", "Device not found.");
      if (device.status !== "online") throw new ApiError(409, "device_offline", "This locket is offline.");
      device.connected = true;
      return delay(structuredClone(device));
    },
    async disconnect(id) {
      requireSession();
      const device = devices.find((d) => d.id === id);
      if (!device) throw new ApiError(404, "device_not_found", "Device not found.");
      device.connected = false;
      return delay(structuredClone(device));
    },
    async createPairing(name) {
      requireSession();
      return delay({
        deviceId: "ASK-DEMO",
        deviceName: name,
        code: "DEMO-0000",
        expiresAt: new Date(Date.now() + 10 * 60_000).toISOString(),
      });
    },
    async assign(id, userId) {
      requireSession();
      const device = devices.find((d) => d.id === id);
      if (!device) throw new ApiError(404, "not_found", "That locket was not found.");
      const member = userId === null ? null : members.find((m) => m.id === userId);
      if (member === undefined || (member !== null && !member.isActive)) {
        throw new ApiError(422, "validation_error", "That person is not in your company.");
      }
      device.assignedTo = member ? { id: member.id, name: member.name } : null;
      return delay(structuredClone(device));
    },
    async revoke(id) {
      requireSession();
      const device = devices.find((d) => d.id === id);
      if (!device) throw new ApiError(404, "not_found", "That locket was not found.");
      device.status = "revoked";
      device.connected = false;
      return delay(structuredClone(device));
    },
  },
  recordings: {
    async list(filters) {
      requireSession();
      const q = filters.q?.trim().toLowerCase();
      const filtered = recordings.filter(
        (r) =>
          (!filters.deviceId || r.deviceId === filters.deviceId) &&
          (!filters.state || r.state === filters.state) &&
          (!q || (r.snippet ?? "").toLowerCase().includes(q) || r.speakers.some((s) => s.toLowerCase().includes(q))),
      );
      const pageSize = filters.pageSize ?? 10;
      const page = filters.page ?? 1;
      return delay({
        items: filtered.slice((page - 1) * pageSize, page * pageSize),
        total: filtered.length,
        page,
        pageSize,
      });
    },
    async get(id) {
      requireSession();
      const recording = recordings.find((r) => r.id === id);
      if (!recording) throw new ApiError(404, "recording_not_found", "Recording not found.");
      return delay(detail(recording));
    },
    async remove(id) {
      requireSession();
      recordings = recordings.filter((r) => r.id !== id);
      return delay(undefined);
    },
    async updateTranscript(id, edits) {
      requireSession();
      const recording = recordings.find((r) => r.id === id);
      if (!recording) throw new ApiError(404, "recording_not_found", "Recording not found.");
      const current = detail(recording);
      if (!current.transcript) throw new ApiError(409, "no_transcript", "This recording has no transcript yet.");
      for (const edit of edits) {
        const segment = current.transcript.segments.find((s) => s.id === edit.segmentId);
        if (segment) {
          segment.text = edit.text;
          segment.corrected = true;
          segment.needsReview = false;
        }
      }
      if (current.transcript.segments.every((s) => !s.needsReview)) current.transcript.reviewStatus = "reviewed";
      return delay(structuredClone(detail(recording)));
    },
    async reprocess(id) {
      requireSession();
      const recording = recordings.find((r) => r.id === id);
      if (!recording) throw new ApiError(404, "recording_not_found", "Recording not found.");
      if (["queued", "validating", "transcribing", "diarizing", "identifying_speakers", "aligning"].includes(recording.state)) {
        throw new ApiError(409, "already_processing", "This recording is already being processed.");
      }
      recording.state = "queued";
      recording.error = null;
      recording.retryCount += 1;
      transcripts.delete(id); // a new transcript replaces the old one, and corrections with it
      return delay(structuredClone(recording));
    },
  },
  people: {
    async list() {
      requireSession();
      return delay(structuredClone(people));
    },
    async create(displayName) {
      requireSession();
      const person: Person = {
        id: `per_${people.length + 1}`,
        displayName,
        consentStatus: "pending",
        consentRecordedAt: null,
        enrolled: false,
        enrolledAt: null,
        sampleCount: 0,
        samplesRequired: 3,
        voiceOutdated: false,
        signature: { ready: false, sampleCount: 0, samplesRequired: 3, outdated: false, updatedAt: null, pitch: null },
      };
      people.push(person);
      return delay(structuredClone(person));
    },
    async recordConsent(id, granted) {
      requireSession();
      const person = people.find((p) => p.id === id);
      if (!person) throw new ApiError(404, "not_found", "That person was not found.");
      person.consentStatus = granted ? "granted" : "revoked";
      person.consentRecordedAt = new Date().toISOString();
      if (!granted) {
        // Withdrawing consent removes the voice profile, as the real backend does.
        person.enrolled = false;
        person.enrolledAt = null;
        person.sampleCount = 0;
        if (person.signature) person.signature = { ...person.signature, ready: false, sampleCount: 0, updatedAt: null, pitch: null };
      }
      return delay(structuredClone(person));
    },
    async remove(id) {
      requireSession();
      const index = people.findIndex((p) => p.id === id);
      if (index === -1) throw new ApiError(404, "not_found", "That person was not found.");
      people.splice(index, 1);
      return delay(undefined);
    },
    async enrollSample(id) {
      requireSession();
      const person = people.find((p) => p.id === id);
      if (!person) throw new ApiError(404, "not_found", "That person was not found.");
      if (person.consentStatus !== "granted") {
        throw new ApiError(409, "consent_required", "Record this person's consent before enrolling a voice.");
      }
      person.sampleCount += 1;
      person.enrolled = person.sampleCount >= person.samplesRequired;
      person.enrolledAt = new Date().toISOString();
      return delay(structuredClone(person));
    },
    async removeVoice(id) {
      requireSession();
      const person = people.find((p) => p.id === id);
      if (!person) throw new ApiError(404, "not_found", "That person was not found.");
      person.sampleCount = 0;
      person.enrolled = false;
      person.enrolledAt = null;
      if (person.signature) person.signature = { ...person.signature, ready: false, sampleCount: 0, updatedAt: null, pitch: null };
      return delay(undefined);
    },
    async addSignatureSample(id) {
      requireSession();
      const person = people.find((p) => p.id === id);
      if (!person) throw new ApiError(404, "not_found", "That person was not found.");
      if (person.consentStatus !== "granted") {
        throw new ApiError(409, "consent_required", "Record this person's consent before adding a voice signature.");
      }
      const current = person.signature ?? { ready: false, sampleCount: 0, samplesRequired: 3, outdated: false, updatedAt: null, pitch: null };
      const sampleCount = current.sampleCount + 1;
      // The demo cannot measure audio, so the pitch is an invented one and the demo banner says so.
      person.signature = {
        ...current,
        sampleCount,
        ready: sampleCount >= current.samplesRequired,
        updatedAt: new Date().toISOString(),
        pitch: DEMO_PITCH[person.displayName] ?? { baseHz: 160, lowHz: 130, highHz: 200 },
      };
      return delay(structuredClone(person));
    },
    async matchSignature() {
      requireSession();
      throw new ApiError(
        501,
        "not_available",
        "Matching a voice is not part of the demo: it needs a real server to measure the audio.",
      );
    },
  },
  audit: {
    async list(filters) {
      requireSession();
      const from = filters.dateFrom ? new Date(filters.dateFrom).getTime() : -Infinity;
      const to = filters.dateTo ? new Date(filters.dateTo).getTime() : Infinity;
      const matching: AuditEvent[] = demoAuditEvents
        .filter(
          (event) =>
            (!filters.category || event.category === filters.category) &&
            new Date(event.at).getTime() >= from &&
            new Date(event.at).getTime() <= to,
        )
        .sort((a, b) => b.at.localeCompare(a.at));
      const pageSize = filters.pageSize ?? 25;
      const page = filters.page ?? 1;
      return delay({
        items: structuredClone(matching.slice((page - 1) * pageSize, page * pageSize)),
        total: matching.length,
        page,
        pageSize,
      });
    },
  },
  processing: {
    // Demo mode pretends a full worker is running, like the rest of its invented data.
    async status() {
      requireSession();
      const active = (state: string) => recordings.filter((r) => r.state === state).length;
      return delay({
        available: true,
        workers: 1,
        placeholder: false,
        transcription: true,
        diarization: true,
        speakerMatching: true,
        queued: active("queued"),
        running: recordings.filter((r) => ["validating", "transcribing", "diarizing", "identifying_speakers", "aligning"].includes(r.state)).length,
        notes: [],
        enrollmentAvailable: true,
      });
    },
  },
  team: {
    async members() {
      requireSession();
      return delay(structuredClone(members));
    },
    async invitations() {
      requireSession();
      return delay(structuredClone(invitations));
    },
    async invite({ email, role }) {
      requireSession();
      const address = email.trim().toLowerCase();
      if (members.some((m) => m.email === address)) {
        throw new ApiError(409, "account_exists", "That person already has an account.");
      }
      const invitation: Invitation = {
        id: `inv_${invitations.length + 1}`,
        email: address,
        role,
        createdAt: new Date().toISOString(),
        expiresAt: new Date(Date.now() + 72 * 3600_000).toISOString(),
        accepted: false,
        acceptUrl: null,
      };
      invitations.unshift(invitation);
      // Like the real API, the link is returned once and is not kept with the stored invitation.
      return delay({ ...invitation, acceptUrl: `${window.location.origin}/register?token=demo` });
    },
    async revokeInvitation(id) {
      requireSession();
      const index = invitations.findIndex((i) => i.id === id);
      if (index === -1) throw new ApiError(404, "not_found", "That invitation was not found.");
      invitations.splice(index, 1);
      return delay(undefined);
    },
    async updateMember(id, change) {
      requireSession();
      const member = members.find((m) => m.id === id);
      if (!member) throw new ApiError(404, "not_found", "That person was not found.");
      if (member.id === demoUser.id) {
        throw new ApiError(409, "cannot_change_self", "You cannot change your own role or access. Ask another administrator.");
      }
      if (change.role) member.role = change.role;
      if (change.isActive !== undefined) member.isActive = change.isActive;
      return delay(structuredClone(member));
    },
    async endMemberSessions(id) {
      requireSession();
      if (!members.some((m) => m.id === id)) throw new ApiError(404, "not_found", "That person was not found.");
      if (id === demoUser.id) throw new ApiError(409, "cannot_change_self", "Use Sign out to end your own sessions.");
      return delay(undefined);
    },
  },
  live: {
    // A scripted stand-in for the live feed so the connection prompt can be reviewed without a
    // backend: one locket "comes online" a few seconds after the dashboard opens. Demo mode only.
    subscribe({ onStatus, onEvent }) {
      onStatus("live");
      const timer = setTimeout(() => {
        const device = devices.find((d) => d.id === "dev_2");
        if (!device || device.status === "online") return;
        device.status = "online";
        device.lastHeartbeatAt = new Date().toISOString();
        device.batteryPercent = 64;
        onEvent({
          type: "device.online",
          at: new Date().toISOString(),
          id: null,
          data: { deviceId: device.id, name: device.name },
        });
      }, DEMO_ONLINE_AFTER_MS);
      return () => clearTimeout(timer);
    },
  },
  async summary() {
    requireSession();
    const startOfDay = new Date();
    startOfDay.setHours(0, 0, 0, 0);
    const active = ["uploaded", "queued", "validating", "transcribing", "diarizing", "identifying_speakers", "aligning", "retrying"];
    return delay({
      devicesOnline: devices.filter((d) => d.status === "online").length,
      devicesTotal: devices.length,
      recordingsToday: recordings.filter((r) => new Date(r.uploadedAt) >= startOfDay).length,
      inProgress: recordings.filter((r) => active.includes(r.state)).length,
      needsReview: recordings.filter((r) => r.state === "needs_review").length,
      failed: recordings.filter((r) => r.state === "failed").length,
    });
  },
  settings: {
    async get() {
      requireSession();
      return delay({ ...settings });
    },
    async update(input) {
      requireSession();
      settings = { ...settings, ...input };
      return delay({ ...settings });
    },
  },
};
