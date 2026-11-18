import { describe, expect, it, vi } from "vitest";
import { type CapturedPart, RecordingCapture } from "@/application/captureRecording";
import { BLOCK_BYTES, SAMPLES_PER_BLOCK, encodeAdpcmBlock } from "@/domain/locket/adpcm";
import type { LocketLink } from "@/domain/locket/link";
import { type FrameType, encodeFrame } from "@/domain/device/framing";

const SID = "SESSION000000001";
const START_MS = Date.UTC(2026, 10, 14, 10, 0, 0);

function fakeLink() {
  let audio: ((frame: Uint8Array) => void) | null = null;
  let status: ((text: string) => void) | null = null;
  const sent: string[] = [];
  const link: LocketLink = {
    scan: () => () => undefined,
    connect: async () => undefined,
    disconnect: async () => undefined,
    onDisconnected: () => () => undefined,
    readInfo: async () => "",
    readNonce: async () => new Uint8Array(16),
    bond: async () => undefined,
    readProof: async () => "",
    send: async (command) => {
      sent.push(command);
    },
    onStatus: (listener) => {
      status = listener;
      return () => {
        status = null;
      };
    },
    onAudio: (listener) => {
      audio = listener;
      return () => {
        audio = null;
      };
    },
  };
  return { link, sent, deliver: (f: Uint8Array) => audio?.(f), status: (t: string) => status?.(t) };
}

function frame(type: FrameType, sequence: number, payload: Uint8Array = new Uint8Array(0)): Uint8Array {
  return encodeFrame({ type, flags: 0, sessionId: SID, sequence, timestampMs: START_MS, payload, crc32: 0 });
}

function audioBlocks(count: number): Uint8Array[] {
  const state = { predictor: 0, index: 0 };
  const blocks: Uint8Array[] = [];
  for (let b = 0; b < count; b += 1) {
    const samples = new Int16Array(SAMPLES_PER_BLOCK);
    for (let i = 0; i < samples.length; i += 1) samples[i] = Math.round(6000 * Math.sin((b * SAMPLES_PER_BLOCK + i) / 9));
    blocks.push(encodeAdpcmBlock(samples, state));
  }
  return blocks;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const samplesIn = (wav: Uint8Array) => (wav.length - 44) / 2;

function capture(
  opts: { blocksPerPart?: number; save?: (part: CapturedPart) => Promise<void>; now?: () => number } = {},
) {
  const fake = fakeLink();
  const parts: CapturedPart[] = [];
  const recorder = new RecordingCapture(
    fake.link,
    async (part) => {
      parts.push(part);
      await opts.save?.(part);
    },
    { blocksPerPart: opts.blocksPerPart, now: opts.now },
  );
  recorder.start();
  return { ...fake, parts, recorder };
}

function play(deliver: (f: Uint8Array) => void, blocks: Uint8Array[]) {
  deliver(frame("START", 0));
  blocks.forEach((b, i) => deliver(frame("AUDIO", i + 1, b)));
  deliver(frame("END", blocks.length + 1));
}

describe("RecordingCapture", () => {
  it("assembles_a_session_into_a_wav", async () => {
    const { deliver, parts } = capture();
    play(deliver, audioBlocks(10));
    await flush();
    expect(parts).toHaveLength(1);
    const [part] = parts;
    expect(part).toMatchObject({ sessionId: SID, part: 0, final: true, durationMs: 160 });
    expect(part.startedAt.getTime()).toBe(START_MS);
    expect(samplesIn(part.wav)).toBe(10 * SAMPLES_PER_BLOCK);
    const header = new DataView(part.wav.buffer, part.wav.byteOffset);
    expect(String.fromCharCode(...part.wav.subarray(0, 4))).toBe("RIFF");
    expect(header.getUint16(22, true)).toBe(1);
    expect(header.getUint32(24, true)).toBe(16000);
  });

  it("splits_long_sessions_into_parts", async () => {
    const { deliver, parts } = capture({ blocksPerPart: 4 });
    play(deliver, audioBlocks(10));
    await flush();
    expect(parts.map((p) => [p.part, p.final, samplesIn(p.wav) / SAMPLES_PER_BLOCK])).toEqual([
      [0, false, 4],
      [1, false, 4],
      [2, true, 2],
    ]);
    expect(parts[1].startedAt.getTime()).toBe(START_MS + 4 * 16);
  });

  it("the_default_part_is_at_most_nine_minutes", () => {
    expect(RecordingCapture.DEFAULT_BLOCKS_PER_PART * SAMPLES_PER_BLOCK).toBeLessThanOrEqual(9 * 60 * 16000);
  });

  it("the_default_part_is_five_minutes_like_a_part_on_the_locket_so_a_short_recording_is_one_item", () => {
    const seconds = (RecordingCapture.DEFAULT_BLOCKS_PER_PART * SAMPLES_PER_BLOCK) / 16000;
    expect(seconds).toBeGreaterThanOrEqual(295);
    expect(seconds).toBeLessThanOrEqual(305);
  });

  it("frames_it_already_has_are_answered_with_the_last_confirmation_again_but_not_every_time", () => {
    let clock = 10_000;
    const { deliver, sent } = capture({ now: () => clock });
    const blocks = audioBlocks(70);
    deliver(frame("START", 0));
    blocks.forEach((b, i) => deliver(frame("AUDIO", i + 1, b)));
    expect(sent).toEqual([`ACK ${SID} 32`, `ACK ${SID} 64`]); // 6 more frames since: not yet a confirmation
    // The locket never got our confirmation, waits five seconds, and sends frames 65.. again: say where we are, once.
    clock += 5000;
    for (let seq = 65; seq <= 70; seq += 1) deliver(frame("AUDIO", seq, blocks[seq - 1]));
    expect(sent).toEqual([`ACK ${SID} 32`, `ACK ${SID} 64`, `ACK ${SID} 70`]);
    // Still repeating a moment later: no flood of confirmations...
    clock += 200;
    deliver(frame("AUDIO", 65, blocks[64]));
    expect(sent).toHaveLength(3);
    // ...but if it goes on, we say it again.
    clock += 400;
    deliver(frame("AUDIO", 65, blocks[64]));
    expect(sent).toEqual([`ACK ${SID} 32`, `ACK ${SID} 64`, `ACK ${SID} 70`, `ACK ${SID} 70`]);
  });

  it("acks_every_32_frames_and_the_end_only_after_every_part_is_saved", async () => {
    let release: () => void = () => undefined;
    const saved = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { deliver, sent } = capture({ save: () => saved });
    play(deliver, audioBlocks(40));
    await flush();
    expect(sent).toEqual([`ACK ${SID} 32`]);
    release();
    await flush();
    expect(sent).toEqual([`ACK ${SID} 32`, `ACK ${SID} 41`]);
  });

  it("a_failed_save_never_acks_the_end", async () => {
    const { deliver, sent } = capture({ save: async () => Promise.reject(new Error("disk full")) });
    play(deliver, audioBlocks(3));
    await flush();
    await flush();
    expect(sent.filter((c) => c.endsWith(" 4"))).toEqual([]);
  });

  it("resumes_after_a_disconnect_without_gaps_or_duplicates", async () => {
    const { deliver, sent, parts, recorder } = capture();
    const blocks = audioBlocks(12);
    deliver(frame("START", 0));
    blocks.slice(0, 5).forEach((b, i) => deliver(frame("AUDIO", i + 1, b)));
    recorder.reconnected();
    expect(sent.at(-1)).toBe(`RESEND ${SID} 6`);
    deliver(frame("AUDIO", 5, blocks[4])); // a repeat by mistake
    blocks.slice(5).forEach((b, i) => deliver(frame("AUDIO", i + 6, b)));
    deliver(frame("END", 13));
    await flush();
    expect(parts).toHaveLength(1);
    expect(samplesIn(parts[0].wav)).toBe(12 * SAMPLES_PER_BLOCK);
  });

  it("routine_status_does_not_rewind_the_locket", () => {
    const { deliver, sent, status } = capture();
    deliver(frame("START", 0));
    deliver(frame("AUDIO", 1, audioBlocks(1)[0]));
    status(`ASKSTAT/1 recording 80 ${SID} 1 0`);
    status(`ASKSTAT/1 recording 80 ${SID} 1 0`);
    expect(sent.filter((c) => c.startsWith("RESEND"))).toEqual([]);
  });

  it("ignores_corrupt_frames_and_wrong_length_payloads_and_asks_again", async () => {
    const { deliver, sent, parts } = capture();
    const blocks = audioBlocks(3);
    deliver(frame("START", 0));
    deliver(frame("AUDIO", 1, blocks[0]));
    const broken = frame("AUDIO", 2, blocks[1]);
    broken[50] ^= 0xff;
    deliver(broken);
    expect(sent.at(-1)).toBe(`RESEND ${SID} 2`);
    deliver(frame("AUDIO", 3, blocks[2])); // early: still waiting for 2
    deliver(frame("AUDIO", 2, blocks[1].subarray(0, BLOCK_BYTES - 1))); // valid CRC, wrong size
    expect(sent.filter((c) => c.startsWith("RESEND"))).toHaveLength(1);
    deliver(frame("AUDIO", 2, blocks[1]));
    deliver(frame("AUDIO", 3, blocks[2]));
    deliver(frame("END", 4));
    await flush();
    expect(samplesIn(parts[0].wav)).toBe(3 * SAMPLES_PER_BLOCK);
  });

  it("a_confirmation_whose_write_failed_is_tried_again_but_a_resend_is_not", async () => {
    vi.useFakeTimers();
    try {
      const { deliver, sent, link } = capture();
      const original = link.send;
      let failures = 2; // the radio is busy for the first two writes
      link.send = async (command) => {
        if (failures-- > 0) throw new Error("GATT operation already in progress");
        return original(command);
      };
      const blocks = audioBlocks(34);
      deliver(frame("START", 0));
      blocks.slice(0, 32).forEach((b, i) => deliver(frame("AUDIO", i + 1, b)));
      expect(sent).toEqual([]); // the first write failed
      await vi.advanceTimersByTimeAsync(400); // the second try fails too
      expect(sent).toEqual([]);
      await vi.advanceTimersByTimeAsync(400); // the third works
      expect(sent).toEqual([`ACK ${SID} 32`]);

      failures = 1;
      deliver(frame("AUDIO", 36, blocks[0])); // a gap: asks for a resend, whose write fails
      await vi.advanceTimersByTimeAsync(5000);
      expect(sent.filter((c) => c.startsWith("RESEND"))).toEqual([]); // never repeated late
    } finally {
      vi.useRealTimers();
    }
  });

  it("asks_from_the_start_for_a_session_it_never_saw", () => {
    const { deliver, sent } = capture();
    deliver(frame("AUDIO", 7, audioBlocks(1)[0]));
    deliver(frame("AUDIO", 8, audioBlocks(1)[0]));
    expect(sent).toEqual([`RESEND ${SID} 0`]);
  });
});
