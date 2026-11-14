import { apiFetch } from "@/lib/api/client";
import type { AskApi } from "@/lib/api/contract";
import { openEventStream } from "@/lib/live/stream";
import type { Device, Invitation, Member, Page, Person, Recording, RecordingFilters } from "@/lib/api/types";

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const enc = encodeURIComponent;

/** HTTP implementation. Paths follow platform/docs/API.md. */
export const realApi: AskApi = {
  auth: {
    me: () => apiFetch("/auth/me"),
    login: (email, password) => apiFetch("/auth/login", { method: "POST", json: { email, password } }),
    logout: () => apiFetch("/auth/logout", { method: "POST" }),
    invitation: (token) => apiFetch(`/invitations/${enc(token)}`),
    acceptInvitation: (token, input) => apiFetch(`/invitations/${enc(token)}/accept`, { method: "POST", json: input }),
    requestPasswordReset: (email) => apiFetch("/auth/password-reset/request", { method: "POST", json: { email } }),
    resetPassword: (token, password) =>
      apiFetch("/auth/password-reset/confirm", { method: "POST", json: { token, password } }),
  },
  devices: {
    list: () => apiFetch<Device[]>("/devices"),
    connect: (id) => apiFetch(`/devices/${enc(id)}/connect`, { method: "POST" }),
    disconnect: (id) => apiFetch(`/devices/${enc(id)}/disconnect`, { method: "POST" }),
    createPairing: (name) => apiFetch("/devices", { method: "POST", json: { name } }),
    assign: (id, userId) => apiFetch(`/devices/${enc(id)}`, { method: "PATCH", json: { assignedUserId: userId } }),
    revoke: (id) => apiFetch(`/devices/${enc(id)}/revoke`, { method: "POST" }),
  },
  recordings: {
    list: (filters: RecordingFilters) =>
      apiFetch<Page<Recording>>(
        `/recordings${query({
          q: filters.q,
          deviceId: filters.deviceId,
          state: filters.state,
          page: filters.page,
          pageSize: filters.pageSize,
        })}`,
      ),
    get: (id) => apiFetch(`/recordings/${enc(id)}`),
    remove: (id) => apiFetch(`/recordings/${enc(id)}`, { method: "DELETE" }),
    updateTranscript: (id, edits) =>
      apiFetch(`/recordings/${enc(id)}/transcript`, { method: "PATCH", json: { edits } }),
    reprocess: (id) => apiFetch(`/recordings/${enc(id)}/reprocess`, { method: "POST" }),
  },
  people: {
    list: () => apiFetch<Person[]>("/people"),
    create: (displayName) => apiFetch("/people", { method: "POST", json: { displayName } }),
    recordConsent: (id, granted) => apiFetch(`/people/${enc(id)}/consent`, { method: "POST", json: { granted } }),
    remove: (id) => apiFetch(`/people/${enc(id)}`, { method: "DELETE" }),
    enrollSample: (id, wav) =>
      apiFetch(`/people/${enc(id)}/enroll`, { method: "POST", body: wav, headers: { "Content-Type": "audio/wav" } }),
    removeVoice: (id) => apiFetch(`/people/${enc(id)}/voice-profile`, { method: "DELETE" }),
    addSignatureSample: (id, wav) =>
      apiFetch(`/people/${enc(id)}/signature`, { method: "POST", body: wav, headers: { "Content-Type": "audio/wav" } }),
    matchSignature: (wav) =>
      apiFetch("/people/signature-matches", { method: "POST", body: wav, headers: { "Content-Type": "audio/wav" } }),
  },
  audit: {
    list: (filters) =>
      apiFetch(
        `/audit${query({
          category: filters.category,
          dateFrom: filters.dateFrom,
          dateTo: filters.dateTo,
          page: filters.page,
          pageSize: filters.pageSize,
        })}`,
      ),
  },
  processing: {
    status: () => apiFetch("/processing"),
  },
  team: {
    members: () => apiFetch<Member[]>("/members"),
    invitations: () => apiFetch<Invitation[]>("/invitations"),
    invite: (input) => apiFetch("/invitations", { method: "POST", json: input }),
    revokeInvitation: (id) => apiFetch(`/invitations/${enc(id)}`, { method: "DELETE" }),
    updateMember: (id, change) => apiFetch(`/members/${enc(id)}`, { method: "PATCH", json: change }),
    endMemberSessions: (id) => apiFetch(`/members/${enc(id)}/sessions`, { method: "DELETE" }),
  },
  live: {
    subscribe: (handlers) => openEventStream(handlers),
  },
  summary: () => apiFetch("/summary"),
  settings: {
    get: () => apiFetch("/settings"),
    update: (input) => apiFetch("/settings", { method: "PATCH", json: input }),
  },
};
