// Receiving a recording from the locket over Bluetooth (spec 5.3). Frames arrive one per notification:
// START, AUDIO 1..n (IMA ADPCM blocks), END.
//
// - The locket sends while it records (2026-11-21), so the wait after Stop is only the last part. Audio is handed on
//   in parts of 30 seconds (about 1 MB), each decoded to a 16 kHz WAV and uploaded as soon as it is complete, so the
//   server is already transcribing while the meeting is still arriving and only the last half minute is left when
//   it ends. The server ties the parts of one recording together and shows them as ONE item, with one transcript,
//   one summary and one audio.  (Sending only after Stop took 8 minutes for a 5-minute recording.)
// - Progress is acknowledged every 32 frames. The END frame is acknowledged (which lets the locket delete the
//   session from its card) only after every part was saved: a failed save never loses audio.
// - A gap, a corrupt frame or a wrong-sized block asks the locket to resend from the first missing frame, once
//   until progress is made. After a reconnect, `reconnected()` asks again. Routine STATUS never rewinds.

import { BLOCK_BYTES, SAMPLES_PER_BLOCK, decodeAdpcmBlock } from "@/domain/locket/adpcm";
import { ack, resend } from "@/domain/locket/commands";
import type { LocketLink } from "@/domain/locket/link";
import { int16ToWav } from "@/domain/audio/wav";
import { decodeFrame } from "@/domain/device/framing";

export type CapturedPart = {
  sessionId: string;
  part: number;
  final: boolean;
  startedAt: Date;
  wav: Uint8Array;
  durationMs: number;
};

const ACK_EVERY = 32;
// A repeated frame (the locket went back because a confirmation never reached it) is answered with the last
// confirmation again, at most this often, or the locket would resend the same frames forever.
const REACK_EVERY_MS = 500;
const RETRY_ACK_MS = 400; // a confirmation whose write failed is tried again this soon, up to twice
const SAMPLE_RATE = 16000;
const BLOCK_MS = (SAMPLES_PER_BLOCK / SAMPLE_RATE) * 1000;

type Active = {
  sessionId: string;
  startedAt: number;
  next: number;
  blocks: Int16Array[];
  part: number;
  askedFrom: number | null;
  sinceAck: number;
  lastAckAt: number;
  saving: Promise<void>;
  failed: boolean;
};

export class RecordingCapture {
  /** Half a minute of 16 ms blocks. */
  static readonly DEFAULT_BLOCKS_PER_PART = Math.floor((30 * 1000) / BLOCK_MS);

  private active: Active | null = null;
  private readonly askedUnknown = new Set<string>();
  private readonly blocksPerPart: number;
  private readonly now: () => number;

  constructor(
    private readonly link: LocketLink,
    private readonly onPart: (part: CapturedPart) => Promise<void>,
    opts: { blocksPerPart?: number; onError?: (error: unknown) => void; now?: () => number } = {},
  ) {
    this.blocksPerPart = opts.blocksPerPart ?? RecordingCapture.DEFAULT_BLOCKS_PER_PART;
    this.onError = opts.onError ?? (() => undefined);
    this.now = opts.now ?? Date.now;
  }

  private readonly onError: (error: unknown) => void;

  start(): () => void {
    const stopAudio = this.link.onAudio((bytes) => this.receive(bytes));
    return () => stopAudio();
  }

  /** The link came back: ask the locket for everything after what we have. */
  reconnected(): void {
    if (!this.active) return;
    this.active.askedFrom = null;
    this.askFrom(this.active);
  }

  resumeFrom(): { sessionId: string; seq: number } | null {
    return this.active ? { sessionId: this.active.sessionId, seq: this.active.next } : null;
  }

  private send(command: string, retries = 0): void {
    this.link.send(command).catch(() => {
      // A write can fail while the radio is busy ("operation already in progress"). A confirmation is cumulative, so
      // trying it again a moment later is always safe; a RESEND is never repeated late (it could rewind the locket
      // after it moved on), the next gap or reconnect asks again.
      if (retries > 0) setTimeout(() => this.send(command, retries - 1), RETRY_ACK_MS);
    });
  }

  private askFrom(active: Active): void {
    if (active.askedFrom === active.next) return;
    active.askedFrom = active.next;
    this.send(resend(active.sessionId, active.next));
  }

  private receive(bytes: Uint8Array): void {
    const decoded = decodeFrame(bytes);
    if (!decoded.ok) {
      if (this.active) this.askFrom(this.active);
      return;
    }
    const frame = decoded.frame;
    if (frame.type === "START" && frame.sequence === 0) {
      this.active = {
        sessionId: frame.sessionId,
        startedAt: frame.timestampMs,
        next: 1,
        blocks: [],
        part: 0,
        askedFrom: null,
        sinceAck: 0,
        lastAckAt: 0,
        saving: Promise.resolve(),
        failed: false,
      };
      return;
    }
    const active = this.active;
    if (!active || active.sessionId !== frame.sessionId) {
      if (!this.askedUnknown.has(frame.sessionId)) {
        this.askedUnknown.add(frame.sessionId);
        this.send(resend(frame.sessionId, 0)); // a session we never saw start: ask for all of it
      }
      return;
    }
    if (frame.type === "AUDIO") this.audio(active, frame.sequence, frame.payload);
    else if (frame.type === "END") this.end(active, frame.sequence);
  }

  private audio(active: Active, sequence: number, payload: Uint8Array): void {
    if (sequence < active.next) {
      // A repeat of something we have. The locket only repeats frames when it did not get our confirmation (a lost
      // write, a busy radio), so say again how far we are. Without this, one lost confirmation made it resend the
      // same frames for ever: every one of them is "something we have", and a confirmation is only sent for new ones.
      this.repeatAck(active);
      return;
    }
    if (sequence > active.next || payload.length !== BLOCK_BYTES) {
      this.askFrom(active);
      return;
    }
    active.blocks.push(decodeAdpcmBlock(payload));
    active.next += 1;
    active.askedFrom = null;
    active.sinceAck += 1;
    if (active.sinceAck >= ACK_EVERY) {
      active.sinceAck = 0;
      active.lastAckAt = this.now();
      this.send(ack(active.sessionId, active.next - 1), 2);
    }
    if (active.blocks.length >= this.blocksPerPart) this.emit(active, false);
  }

  private repeatAck(active: Active): void {
    const now = this.now();
    if (active.next <= 1 || now - active.lastAckAt < REACK_EVERY_MS) return;
    active.lastAckAt = now;
    this.send(ack(active.sessionId, active.next - 1), 2);
  }

  private end(active: Active, sequence: number): void {
    if (sequence !== active.next) {
      this.askFrom(active);
      return;
    }
    if (active.blocks.length || active.part === 0) this.emit(active, true);
    this.active = null;
    active.saving
      .then(() => {
        if (!active.failed) this.send(ack(active.sessionId, sequence), 2);
      })
      .catch(() => undefined);
  }

  private emit(active: Active, final: boolean): void {
    const blocks = active.blocks;
    active.blocks = [];
    const samples = new Int16Array(blocks.length * SAMPLES_PER_BLOCK);
    blocks.forEach((block, i) => samples.set(block, i * SAMPLES_PER_BLOCK));
    const part: CapturedPart = {
      sessionId: active.sessionId,
      part: active.part,
      final,
      startedAt: new Date(active.startedAt + active.part * this.blocksPerPart * BLOCK_MS),
      wav: int16ToWav(samples, SAMPLE_RATE),
      durationMs: Math.round(blocks.length * BLOCK_MS),
    };
    active.part += 1;
    active.saving = active.saving.then(() =>
      this.onPart(part).catch((error: unknown) => {
        active.failed = true;
        this.onError(error);
      }),
    );
  }
}
