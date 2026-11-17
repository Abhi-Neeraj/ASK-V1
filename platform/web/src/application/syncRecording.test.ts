import { describe, expect, it } from "vitest";
import { discardPending, pendingCount, retryPending, savePart, saveTextFiles } from "@/application/syncRecording";
import { firmwareOffer, startFirmwareUpdate } from "@/application/firmwareUpdate";
import type { CapturedPart } from "@/application/captureRecording";
import type { LocketLink } from "@/domain/locket/link";
import { ApiError } from "@/infrastructure/api/client";
import type { LocalFiles } from "@/infrastructure/files/localFiles";

function memoryFiles(): LocalFiles & { store: Map<string, Uint8Array | string> } {
  const store = new Map<string, Uint8Array | string>();
  return {
    store,
    write: async (path, data) => {
      store.set(path, data);
    },
    read: async (path) => {
      const value = store.get(path);
      if (value === undefined) return null;
      return typeof value === "string" ? new TextEncoder().encode(value) : value;
    },
    list: async (dir) => [...store.keys()].filter((k) => k.startsWith(`${dir}/`)).map((k) => k.slice(dir.length + 1)),
    remove: async (path) => {
      store.delete(path);
    },
  };
}

const part = (n: number, final = true): CapturedPart => ({
  sessionId: "SESSION000000001",
  part: n,
  final,
  startedAt: new Date(Date.UTC(2026, 10, 14, 10, n * 9, 0)),
  wav: new Uint8Array([82, 73, 70, 70, n]),
  durationMs: 1000,
});

type Upload = { productId: string; clientId: string; at: string; sessionRef?: string; sequence?: number };

function api(behaviour: (u: Upload) => "ok" | ApiError) {
  const uploads: Upload[] = [];
  return {
    uploads,
    uploadRecording: async (
      productId: string,
      clientId: string,
      _wav: Uint8Array,
      at: Date,
      group?: { sessionRef: string; sequence: number },
    ) => {
      const u = { productId, clientId, at: at.toISOString(), ...group };
      uploads.push(u);
      const result = behaviour(u);
      if (result instanceof ApiError) throw result;
      return { recordingId: `rec-${clientId}`, clientRecordingId: clientId, status: "stored", duplicate: false };
    },
  };
}

describe("savePart and retryPending", () => {
  it("uploads_and_keeps_audio_under_the_recording_id", async () => {
    const files = memoryFiles();
    await savePart({ files, productId: "ASK-7KQ2MX", part: part(0) });
    expect(await pendingCount(files)).toBe(1);
    const server = api(() => "ok");
    expect(await retryPending({ api: server, files })).toEqual({
      uploaded: ["rec-SESSION000000001-0"],
      rejected: [],
      reasons: [],
      waiting: [],
    });
    expect(server.uploads).toEqual([
      {
        productId: "ASK-7KQ2MX",
        clientId: "SESSION000000001-0",
        at: "2026-11-14T10:00:00.000Z",
        sessionRef: "SESSION000000001",
        sequence: 0,
      },
    ]);
    expect(files.store.get("ASK/rec-SESSION000000001-0/audio.wav")).toEqual(part(0).wav);
    expect(await pendingCount(files)).toBe(0);
  });

  it("parts_of_one_meeting_share_the_session_and_count_up", async () => {
    const files = memoryFiles();
    await savePart({ files, productId: "ASK-7KQ2MX", part: part(0, false) });
    await savePart({ files, productId: "ASK-7KQ2MX", part: part(1, true) });
    const server = api(() => "ok");
    await retryPending({ api: server, files });
    expect(server.uploads.map((u) => [u.clientId, u.sessionRef, u.sequence])).toEqual([
      ["SESSION000000001-0", "SESSION000000001", 0],
      ["SESSION000000001-1", "SESSION000000001", 1],
    ]);
  });

  it("failed_upload_is_kept_and_retried_once_online", async () => {
    const files = memoryFiles();
    await savePart({ files, productId: "ASK-7KQ2MX", part: part(0) });
    let online = false;
    const server = api(() => (online ? "ok" : new ApiError(0, "network", "offline")));
    expect(await retryPending({ api: server, files })).toEqual({ uploaded: [], rejected: [], reasons: [], waiting: [] });
    expect(await pendingCount(files)).toBe(1);
    online = true;
    expect((await retryPending({ api: server, files })).uploaded).toHaveLength(1);
    expect((await retryPending({ api: server, files })).uploaded).toHaveLength(0);
    expect(server.uploads).toHaveLength(2);
  });

  it("a_file_the_server_refuses_is_set_aside_and_does_not_block_the_rest", async () => {
    const files = memoryFiles();
    await savePart({ files, productId: "ASK-7KQ2MX", part: part(0) });
    await savePart({ files, productId: "ASK-7KQ2MX", part: { ...part(1), sessionId: "SESSION000000002" } });
    const server = api((u) =>
      u.clientId.startsWith("SESSION000000001") ? new ApiError(413, "audio_too_long", "Too long.") : "ok",
    );
    const result = await retryPending({ api: server, files });
    expect(result).toEqual({
      uploaded: ["rec-SESSION000000002-1"],
      rejected: ["SESSION000000001-0"],
      reasons: ["audio_too_long: Too long."],
      waiting: [],
    });
    expect(files.store.has("ASK/rejected/SESSION000000001-0.wav")).toBe(true);
    expect(await pendingCount(files)).toBe(0);
  });

  it("a_locket_the_account_is_not_paired_with_keeps_its_recording_waiting", async () => {
    const files = memoryFiles();
    await savePart({ files, productId: "ASK-NOPAIR", part: part(0) });
    await savePart({ files, productId: "ASK-7KQ2MX", part: { ...part(1), sessionId: "SESSION000000002" } });
    const server = api((u) =>
      u.productId === "ASK-NOPAIR" ? new ApiError(404, "not_found", "That locket was not found.") : "ok",
    );
    const result = await retryPending({ api: server, files });
    // The other locket's part still uploads, and the unpaired locket's part is kept, never set aside.
    expect(result).toEqual({
      uploaded: ["rec-SESSION000000002-1"],
      rejected: [],
      reasons: [],
      waiting: ["ASK-NOPAIR"],
    });
    expect(files.store.has("ASK/pending/SESSION000000001-0.wav")).toBe(true);
    expect([...files.store.keys()].some((k) => k.startsWith("ASK/rejected"))).toBe(false);
    expect(await pendingCount(files)).toBe(1);
  });

  it("once_paired_the_waiting_recording_uploads", async () => {
    const files = memoryFiles();
    await savePart({ files, productId: "ASK-NOPAIR", part: part(0) });
    let paired = false;
    const server = api(() => (paired ? "ok" : new ApiError(404, "not_found", "That locket was not found.")));
    expect((await retryPending({ api: server, files })).waiting).toEqual(["ASK-NOPAIR"]);
    paired = true;
    const after = await retryPending({ api: server, files });
    expect(after.uploaded).toEqual(["rec-SESSION000000001-0"]);
    expect(after.waiting).toEqual([]);
    expect(await pendingCount(files)).toBe(0);
  });

  it("a_recording_an_older_version_set_aside_for_not_being_paired_is_put_back", async () => {
    const files = memoryFiles();
    const meta = { productId: "ASK-NOPAIR", sessionId: "SESSION000000001", part: 0, startedAt: part(0).startedAt.toISOString() };
    files.store.set("ASK/rejected/SESSION000000001-0.wav", part(0).wav);
    files.store.set("ASK/rejected/SESSION000000001-0.json", JSON.stringify({ ...meta, reason: "not_found" }));
    // A part the server refused as too large (a server mistake since fixed) is put back too.
    files.store.set("ASK/rejected/SESSION000000003-0.wav", new Uint8Array([3]));
    files.store.set("ASK/rejected/SESSION000000003-0.json", JSON.stringify({ ...meta, sessionId: "SESSION000000003", reason: "body_too_large" }));
    // Another set-aside part, refused for a real reason, must stay where it is.
    files.store.set("ASK/rejected/SESSION000000009-0.wav", new Uint8Array([1]));
    files.store.set("ASK/rejected/SESSION000000009-0.json", JSON.stringify({ ...meta, sessionId: "SESSION000000009", reason: "audio_too_long" }));
    const server = api(() => "ok");
    const result = await retryPending({ api: server, files });
    expect(result.uploaded).toEqual(["rec-SESSION000000001-0", "rec-SESSION000000003-0"]);
    expect(files.store.has("ASK/rejected/SESSION000000001-0.wav")).toBe(false);
    expect(files.store.has("ASK/rejected/SESSION000000003-0.wav")).toBe(false);
    expect(files.store.has("ASK/rejected/SESSION000000009-0.wav")).toBe(true);
  });

  it("discarding_removes_everything_waiting_and_everything_set_aside", async () => {
    const files = memoryFiles();
    await savePart({ files, productId: "ASK-NOPAIR", part: part(0) });
    await savePart({ files, productId: "ASK-NOPAIR", part: { ...part(1), sessionId: "SESSION000000002" } });
    files.store.set("ASK/rejected/OLD-0.wav", new Uint8Array([1]));
    files.store.set("ASK/rejected/OLD-0.json", "{}");
    files.store.set("ASK/rec-1/audio.wav", new Uint8Array([2])); // an uploaded recording is not touched
    expect(await discardPending(files)).toBe(3);
    expect(await pendingCount(files)).toBe(0);
    expect([...files.store.keys()]).toEqual(["ASK/rec-1/audio.wav"]);
  });

  it("signed_out_or_busy_server_waits_instead_of_discarding", async () => {
    for (const status of [401, 408, 429, 503]) {
      const files = memoryFiles();
      await savePart({ files, productId: "ASK-7KQ2MX", part: part(0) });
      const server = api(() => new ApiError(status, "x", "later"));
      expect(await retryPending({ api: server, files })).toEqual({ uploaded: [], rejected: [], reasons: [], waiting: [] });
      expect(await pendingCount(files)).toBe(1);
    }
  });
});

describe("saveTextFiles", () => {
  it("saves_both_files", async () => {
    const files = memoryFiles();
    const textApi = { textFile: async (_id: string, which: string) => `${which} text\n` };
    expect(await saveTextFiles({ api: textApi, files, recordingId: "rec-1" })).toEqual({ transcript: true, summary: true });
    expect(files.store.get("ASK/rec-1/transcription.txt")).toBe("transcription text\n");
    expect(files.store.get("ASK/rec-1/summary.txt")).toBe("summary text\n");
  });

  it("saves_the_transcript_even_when_the_summary_is_not_ready", async () => {
    const files = memoryFiles();
    const textApi = {
      textFile: async (_id: string, which: string) => {
        if (which === "summary") throw new ApiError(404, "summary_not_ready", "No summary yet.");
        return "[00:00:01] Speaker 1: Hi.\n";
      },
    };
    expect(await saveTextFiles({ api: textApi, files, recordingId: "rec-1" })).toEqual({
      transcript: true,
      summary: "not_ready",
    });
    expect(files.store.has("ASK/rec-1/summary.txt")).toBe(false);
  });

  it("no_transcript_yet_saves_nothing", async () => {
    const files = memoryFiles();
    const textApi = {
      textFile: async () => {
        throw new ApiError(404, "transcript_not_ready", "Not yet.");
      },
    };
    expect(await saveTextFiles({ api: textApi, files, recordingId: "rec-1" })).toEqual({
      transcript: false,
      summary: "not_ready",
    });
    expect(files.store.size).toBe(0);
  });
});

describe("firmware", () => {
  const latest = { version: "1.2.0", sizeBytes: 1000, sha256: "ab".repeat(32), downloadUrl: "https://x/fw?token=1" };

  it("offers_only_newer_firmware", async () => {
    const fw = { latestFirmware: async () => latest };
    expect(await firmwareOffer(fw, { firmwareVersion: "1.1.9" })).toEqual(latest);
    expect(await firmwareOffer(fw, { firmwareVersion: "1.2.0" })).toBeNull();
    const none = { latestFirmware: async () => Promise.reject(new ApiError(404, "no_firmware", "None.")) };
    expect(await firmwareOffer(none, { firmwareVersion: "1.0.0" })).toBeNull();
  });

  it("sends_wifi_then_ota", async () => {
    const sent: string[] = [];
    const link = { send: async (c: string) => void sent.push(c) } as unknown as LocketLink;
    await startFirmwareUpdate({ link, latest, wifi: { ssid: "Home", password: "pw" } });
    expect(sent[0]).toBe("WIFI 486f6d65 7077");
    expect(sent[1].startsWith(`OTA 1.2.0 1000 ${"ab".repeat(32)} `)).toBe(true);
    sent.length = 0;
    await startFirmwareUpdate({ link, latest });
    expect(sent).toHaveLength(1);
  });
});
