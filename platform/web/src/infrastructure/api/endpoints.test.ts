import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { createClient } from "@/infrastructure/api/client";
import { endpoints } from "@/infrastructure/api/endpoints";
import { memoryTokenStore } from "@/infrastructure/api/tokenStore";

type Call = { method: string; path: string; init: RequestInit };

function setup(body: unknown = {}) {
  const calls: Call[] = [];
  const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
    const u = new URL(String(url), "http://x");
    calls.push({ method: init?.method ?? "GET", path: u.pathname + u.search, init: init ?? {} });
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  const tokens = memoryTokenStore();
  const api = endpoints(createClient({ base: "", fetchImpl, tokens }), tokens);
  return { api, calls, tokens };
}

describe("endpoints", () => {
  it("login_stores_the_csrf_token_from_the_body", async () => {
    const { api, tokens } = setup({ id: "u1", email: "a@b.c", name: "A", role: "admin", csrfToken: "csrf-1" });
    const user = await api.login("a@b.c", "pw");
    expect(user.role).toBe("admin");
    expect(tokens.get()).toBe("csrf-1");
  });

  it("upload_sends_wav_with_sha256_and_recorded_at", async () => {
    const { api, calls } = setup({ recordingId: "r1", duplicate: false });
    const wav = new Uint8Array([82, 73, 70, 70, 1, 2, 3]);
    const at = new Date("2026-11-14T10:00:00Z");
    await api.uploadRecording("ASK-7KQ2MX", "SESSION000000001", wav, at);
    const [call] = calls;
    expect(call.method).toBe("PUT");
    expect(call.path).toBe("/api/lockets/ASK-7KQ2MX/recordings/SESSION000000001");
    const headers = call.init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("audio/wav");
    expect(headers["X-Content-SHA256"]).toBe(createHash("sha256").update(wav).digest("hex"));
    expect(headers["X-Recorded-At"]).toBe("2026-11-14T10:00:00.000Z");
    expect(headers["X-Session-Ref"]).toBeUndefined();
  });

  it("upload_sends_the_meeting_and_part_number", async () => {
    const { api, calls } = setup({ recordingId: "r1", duplicate: false });
    await api.uploadRecording("ASK-1", "SESSION000000001-2", new Uint8Array([1]), new Date(), {
      sessionRef: "SESSION000000001",
      sequence: 2,
    });
    const headers = calls[0].init.headers as Record<string, string>;
    expect(headers["X-Session-Ref"]).toBe("SESSION000000001");
    expect(headers["X-Sequence"]).toBe("2");
  });

  it("paths_match_the_api_reference", async () => {
    const { api, calls } = setup([]);
    await api.me();
    await api.logout();
    await api.requestPasswordReset("a@b.c");
    await api.changePassword("old", "new-password-123");
    await api.listRecordings({ q: "panel", page: 2 });
    await api.getRecording("r1");
    await api.deleteRecording("r1");
    await api.deleteMeeting("r1");
    await api.reprocess("r1");
    await api.listLockets();
    await api.registerLocket("Desk");
    await api.requestPairingCode("ASK-1", "00".repeat(16));
    await api.confirmPaired("ASK-1", "00".repeat(16), "ab".repeat(32));
    await api.deleteLocket("ASK-1");
    await api.removeMember("ASK-1", "u2");
    await api.setLimit("ASK-1", 2);
    await api.latestFirmware();
    await api.listMembers();
    await api.invite("b@c.d", "employee");
    await api.listInvitations();
    await api.cancelInvitation("i1");
    await api.updateMember("u2", { role: "manager" });
    await api.workspaceSummary();
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([
      "GET /api/auth/me",
      "POST /api/auth/logout",
      "POST /api/auth/password-reset/request",
      "POST /api/auth/change-password",
      "GET /api/recordings?q=panel&page=2&pageSize=20",
      "GET /api/recordings/r1",
      "DELETE /api/recordings/r1",
      "DELETE /api/recordings/r1?meeting=true",
      "POST /api/recordings/r1/reprocess",
      "GET /api/lockets",
      "POST /api/lockets",
      "POST /api/lockets/ASK-1/pairing-code",
      "POST /api/lockets/ASK-1/paired",
      "DELETE /api/lockets/ASK-1",
      "DELETE /api/lockets/ASK-1/members/u2",
      "PATCH /api/lockets/ASK-1/limit",
      "GET /api/firmware/latest",
      "GET /api/members",
      "POST /api/invitations",
      "GET /api/invitations",
      "DELETE /api/invitations/i1",
      "PATCH /api/members/u2",
      "GET /api/summary",
    ]);
    expect(JSON.parse(String(calls[12].init.body))).toEqual({ nonce: "00".repeat(16), proof: "ab".repeat(32) });
    expect(JSON.parse(String(calls[15].init.body))).toEqual({ maxUsers: 2 });
  });

  it("text_files_and_audio_urls", async () => {
    const { api } = setup();
    expect(api.audioUrl("r1")).toBe("/api/recordings/r1/audio");
    expect(api.textUrl("r1", "summary")).toBe("/api/recordings/r1/summary.txt");
  });
});
