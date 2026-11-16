import { describe, expect, it } from "vitest";
import { LocketSession, type SessionState } from "@/application/locketSession";
import { SAMPLES_PER_BLOCK, encodeAdpcmBlock } from "@/domain/locket/adpcm";
import type { FoundLocket, LocketLink } from "@/domain/locket/link";
import { encodeFrame, type FrameType } from "@/domain/device/framing";
import { ApiError } from "@/infrastructure/api/client";
import type { LocalFiles } from "@/infrastructure/files/localFiles";

const SID = "SESSION000000001";

function fakeLink(opts: { found?: FoundLocket[]; failConnects?: number } = {}) {
  const log: string[] = [];
  const audio = new Set<(f: Uint8Array) => void>();
  const status = new Set<(t: string) => void>();
  const drops = new Set<() => void>();
  let failConnects = opts.failConnects ?? 0;
  const link: LocketLink = {
    scan: (onFound) => {
      log.push("scan");
      for (const f of opts.found ?? []) onFound(f);
      return () => log.push("stopScan");
    },
    connect: async (id) => {
      log.push(`connect ${id}`);
      if (failConnects > 0) {
        failConnects -= 1;
        throw new Error("out of range");
      }
    },
    disconnect: async () => {
      log.push("disconnect");
    },
    onDisconnected: (l) => {
      drops.add(l);
      return () => drops.delete(l);
    },
    readInfo: async () => "ASKINFO/1 ASK-7KQ2MX 1.0.0 80 1",
    readNonce: async () => new Uint8Array(16),
    bond: async () => undefined,
    readProof: async () => "",
    send: async (c) => {
      log.push(c.startsWith("TIME") ? "TIME" : c);
    },
    onStatus: (l) => {
      status.add(l);
      return () => status.delete(l);
    },
    onAudio: (l) => {
      audio.add(l);
      return () => audio.delete(l);
    },
  };
  return {
    link,
    log,
    listeners: () => ({ audio: audio.size, status: status.size, drops: drops.size }),
    deliver: (f: Uint8Array) => audio.forEach((l) => l(f)),
    drop: () => [...drops].forEach((l) => l()),
  };
}

function memoryFiles(opts: { failWrites?: boolean } = {}): LocalFiles & { store: Map<string, Uint8Array | string> } {
  const store = new Map<string, Uint8Array | string>();
  return {
    store,
    write: async (path, data) => {
      if (opts.failWrites) throw new Error("disk full");
      store.set(path, data);
    },
    read: async (path) => {
      const v = store.get(path);
      return v === undefined ? null : typeof v === "string" ? new TextEncoder().encode(v) : v;
    },
    list: async (dir) => [...store.keys()].filter((k) => k.startsWith(`${dir}/`)).map((k) => k.slice(dir.length + 1)),
    remove: async (path) => {
      store.delete(path);
    },
  };
}

const frame = (type: FrameType, sequence: number, payload: Uint8Array = new Uint8Array(0)) =>
  encodeFrame({ type, flags: 0, sessionId: SID, sequence, timestampMs: Date.UTC(2026, 10, 14), payload, crc32: 0 });

function blocks(n: number): Uint8Array[] {
  const state = { predictor: 0, index: 0 };
  return Array.from({ length: n }, (_, b) => {
    const s = new Int16Array(SAMPLES_PER_BLOCK);
    for (let i = 0; i < s.length; i += 1) s[i] = Math.round(5000 * Math.sin((b * SAMPLES_PER_BLOCK + i) / 7));
    return encodeAdpcmBlock(s, state);
  });
}

const settle = async () => {
  for (let i = 0; i < 5; i += 1) await new Promise((r) => setTimeout(r, 0));
};

function make(opts: { found?: FoundLocket[]; failConnects?: number; failWrites?: boolean; uploadError?: ApiError } = {}) {
  const fake = fakeLink(opts);
  const files = memoryFiles(opts);
  const uploads: string[] = [];
  const states: SessionState[] = [];
  const api = {
    uploadRecording: async (_p: string, clientId: string) => {
      if (opts.uploadError) throw opts.uploadError;
      uploads.push(clientId);
      return { recordingId: `rec-${clientId}`, clientRecordingId: clientId, status: "stored", duplicate: false };
    },
  };
  const session = new LocketSession({
    link: fake.link,
    api,
    files,
    onChange: (s) => states.push(s),
    scanTimeoutMs: 20,
    sleep: async () => undefined,
  });
  return { ...fake, files, uploads, states, session, last: () => states.at(-1)! };
}

const LOCKET = { id: "dev-1", name: "ASK-7KQ2MX", rssi: -50 };

describe("LocketSession", () => {
  it("connects_to_an_already_paired_locket_by_product_id", async () => {
    const t = make({ found: [{ id: "other", name: "ASK-OTHER1", rssi: -40 }, LOCKET] });
    await t.session.connectTo("ASK-7KQ2MX");
    expect(t.log).toContain("connect dev-1");
    expect(t.log).not.toContain("connect other");
    expect(t.last()).toMatchObject({ productId: "ASK-7KQ2MX", connected: true, firmwareVersion: "1.0.0" });
  });

  it("says_so_when_the_locket_is_not_nearby", async () => {
    const t = make({ found: [] });
    await t.session.connectTo("ASK-7KQ2MX");
    expect(t.last()).toMatchObject({ connected: false });
    expect(t.last().message).toMatch(/not found/i);
  });

  it("adopts_the_connection_pairing_opened_without_connecting_twice", async () => {
    const t = make();
    await t.session.attach(LOCKET);
    expect(t.log.filter((l) => l.startsWith("connect"))).toEqual([]);
    expect(t.last()).toMatchObject({ productId: "ASK-7KQ2MX", connected: true });
  });

  it("one_receiver_across_reconnects_and_asks_for_what_it_missed", async () => {
    const t = make({ found: [LOCKET], failConnects: 0 });
    await t.session.attach(LOCKET);
    const b = blocks(4);
    t.deliver(frame("START", 0));
    t.deliver(frame("AUDIO", 1, b[0]));
    t.deliver(frame("AUDIO", 2, b[1]));
    t.drop();
    await settle();
    expect(t.log).toContain("connect dev-1");
    expect(t.log).toContain(`RESEND ${SID} 3`);
    expect(t.listeners()).toEqual({ audio: 1, status: 1, drops: 1 });
    t.deliver(frame("AUDIO", 3, b[2]));
    t.deliver(frame("AUDIO", 4, b[3]));
    t.deliver(frame("END", 5));
    await settle();
    expect(t.uploads).toEqual([`${SID}-0`]);
    expect(t.log).toContain(`ACK ${SID} 5`);
  });

  it("keeps_trying_to_reconnect_and_reports_it", async () => {
    const t = make({ found: [LOCKET], failConnects: 2 });
    await t.session.attach(LOCKET);
    t.drop();
    await settle();
    expect(t.log.filter((l) => l === "connect dev-1")).toHaveLength(3);
    expect(t.last().connected).toBe(true);
  });

  it("does_not_reconnect_after_the_person_disconnects", async () => {
    const t = make({ found: [LOCKET] });
    await t.session.attach(LOCKET);
    await t.session.disconnect();
    t.drop();
    await settle();
    expect(t.log.filter((l) => l.startsWith("connect"))).toEqual([]);
    expect(t.listeners()).toEqual({ audio: 0, status: 0, drops: 0 });
  });

  it("uploads_waiting_recordings_after_connecting", async () => {
    const t = make({ found: [LOCKET] });
    await t.files.write("ASK/pending/OLDSESSION000001-0.wav", new Uint8Array([1]));
    await t.files.write(
      "ASK/pending/OLDSESSION000001-0.json",
      JSON.stringify({ productId: "ASK-7KQ2MX", sessionId: "OLDSESSION000001", part: 0, startedAt: "2026-11-14T09:00:00Z" }),
    );
    await t.session.connectTo("ASK-7KQ2MX");
    await settle();
    expect(t.uploads).toEqual(["OLDSESSION000001-0"]);
    expect(t.last().pending).toBe(0);
  });

  it("a_failed_save_and_a_failed_upload_say_different_things", async () => {
    const saveFails = make({ failWrites: true });
    await saveFails.session.attach(LOCKET);
    saveFails.deliver(frame("START", 0));
    saveFails.deliver(frame("AUDIO", 1, blocks(1)[0]));
    saveFails.deliver(frame("END", 2));
    await settle();
    expect(saveFails.last().message).toMatch(/could not be saved/i);
    expect(saveFails.log).not.toContain(`ACK ${SID} 2`);

    const offline = make({ uploadError: new ApiError(0, "network", "offline") });
    await offline.session.attach(LOCKET);
    offline.deliver(frame("START", 0));
    offline.deliver(frame("AUDIO", 1, blocks(1)[0]));
    offline.deliver(frame("END", 2));
    await settle();
    expect(offline.last().message).toMatch(/saved on this phone/i);
    expect(offline.last().pending).toBe(1);
    expect(offline.log).toContain(`ACK ${SID} 2`);
  });
});
