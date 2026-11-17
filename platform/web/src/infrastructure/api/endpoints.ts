// Every server call the app makes, with paths exactly as in platform/docs/API.md.

import { sha256Hex } from "@/domain/crypto/sha256";
import type { ApiClient } from "@/infrastructure/api/client";
import type { TokenStore } from "@/infrastructure/api/tokenStore";
import type {
  CodeSent,
  FirmwareLatest,
  InvitationOut,
  LocketOut,
  LocketRegistered,
  MemberOut,
  Page,
  RecordingDetailOut,
  RecordingOut,
  Role,
  UploadAck,
  UserOut,
  WorkspaceSummary,
} from "@/infrastructure/api/types";

const enc = encodeURIComponent;

export function endpoints(client: ApiClient, tokens: TokenStore) {
  const keep = async (user: UserOut): Promise<UserOut> => {
    await tokens.set(user.csrfToken);
    return user;
  };

  return {
    // --- account -------------------------------------------------------------------------------
    login: async (email: string, password: string) =>
      keep(await client.request<UserOut>("POST", "/api/auth/login", { json: { email, password } })),
    me: async () => keep(await client.request<UserOut>("GET", "/api/auth/me")),
    logout: async () => {
      try {
        await client.request("POST", "/api/auth/logout");
      } finally {
        await tokens.set(null);
      }
    },
    requestPasswordReset: (email: string) =>
      client.request<void>("POST", "/api/auth/password-reset/request", { json: { email } }),
    confirmPasswordReset: (token: string, password: string) =>
      client.request<void>("POST", "/api/auth/password-reset/confirm", { json: { token, password } }),
    changePassword: (currentPassword: string, newPassword: string) =>
      client.request<void>("POST", "/api/auth/change-password", { json: { currentPassword, newPassword } }),

    // --- recordings ----------------------------------------------------------------------------
    listRecordings: ({ q, page = 1, pageSize = 20 }: { q?: string; page?: number; pageSize?: number } = {}) => {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      params.set("page", String(page));
      params.set("pageSize", String(pageSize));
      return client.request<Page<RecordingOut>>("GET", `/api/recordings?${params.toString()}`);
    },
    getRecording: (id: string) => client.request<RecordingDetailOut>("GET", `/api/recordings/${enc(id)}`),
    deleteRecording: (id: string) => client.request<void>("DELETE", `/api/recordings/${enc(id)}`),
    /** Deletes every part of the meeting this recording belongs to (a long meeting is many one-minute parts). */
    deleteMeeting: (id: string) => client.request<void>("DELETE", `/api/recordings/${enc(id)}?meeting=true`),
    reprocess: (id: string) => client.request<RecordingOut>("POST", `/api/recordings/${enc(id)}/reprocess`),
    audioUrl: (id: string) => `${client.base}/api/recordings/${enc(id)}/audio`,
    audioBytes: (id: string) => client.bytes(`/api/recordings/${enc(id)}/audio`),
    textUrl: (id: string, which: "transcription" | "summary") =>
      `${client.base}/api/recordings/${enc(id)}/${which}.txt`,
    textFile: (id: string, which: "transcription" | "summary") =>
      client.text(`/api/recordings/${enc(id)}/${which}.txt`),

    // --- lockets -------------------------------------------------------------------------------
    listLockets: () => client.request<LocketOut[]>("GET", "/api/lockets"),
    registerLocket: (name: string) => client.request<LocketRegistered>("POST", "/api/lockets", { json: { name } }),
    requestPairingCode: (productId: string, nonce: string) =>
      client.request<CodeSent>("POST", `/api/lockets/${enc(productId)}/pairing-code`, { json: { nonce } }),
    confirmPaired: (productId: string, nonce: string, proof: string) =>
      client.request<void>("POST", `/api/lockets/${enc(productId)}/paired`, { json: { nonce, proof } }),
    /** Administrators only: the locket is revoked for good and disappears from every list. Its recordings stay. */
    deleteLocket: (productId: string) => client.request<void>("DELETE", `/api/lockets/${enc(productId)}`),
    removeMember: (productId: string, userId: string) =>
      client.request<void>("DELETE", `/api/lockets/${enc(productId)}/members/${enc(userId)}`),
    setLimit: (productId: string, maxUsers: number) =>
      client.request<LocketOut>("PATCH", `/api/lockets/${enc(productId)}/limit`, { json: { maxUsers } }),
    uploadRecording: async (
      productId: string,
      clientId: string,
      wav: Uint8Array,
      recordedAt: Date,
      group?: { sessionRef: string; sequence: number },
    ) =>
      client.request<UploadAck>("PUT", `/api/lockets/${enc(productId)}/recordings/${enc(clientId)}`, {
        body: wav as BodyInit,
        headers: {
          "Content-Type": "audio/wav",
          "X-Content-SHA256": sha256Hex(wav),
          "X-Recorded-At": recordedAt.toISOString(),
          ...(group ? { "X-Session-Ref": group.sessionRef, "X-Sequence": String(group.sequence) } : {}),
        },
      }),

    // --- firmware ------------------------------------------------------------------------------
    latestFirmware: () => client.request<FirmwareLatest>("GET", "/api/firmware/latest"),

    // --- team ----------------------------------------------------------------------------------
    listMembers: () => client.request<MemberOut[]>("GET", "/api/members"),
    invite: (email: string, role: Role) =>
      client.request<InvitationOut>("POST", "/api/invitations", { json: { email, role } }),
    listInvitations: () => client.request<InvitationOut[]>("GET", "/api/invitations"),
    cancelInvitation: (id: string) => client.request<void>("DELETE", `/api/invitations/${enc(id)}`),
    updateMember: (userId: string, patch: { role?: Role; isActive?: boolean }) =>
      client.request<MemberOut>("PATCH", `/api/members/${enc(userId)}`, { json: patch }),

    workspaceSummary: () => client.request<WorkspaceSummary>("GET", "/api/summary"),
  };
}

export type Api = ReturnType<typeof endpoints>;
