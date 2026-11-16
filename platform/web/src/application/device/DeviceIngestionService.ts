import { decodeFrame } from "@/domain/device/framing";
import { AskSessionAssembler, FrameError, mapDecodeError } from "@/domain/device/sessionAssembler";
import { AskSession } from "@/domain/device/transport";

// The gateway ingestion service: routes raw device frames to per-session
// assemblers and emits a ready-to-process AskSession on END. Corrupt or
// out-of-order frames are rejected before any payload is passed downstream
// (the "missing/corrupt chunks detected before ASR" gate).
export type SessionConsumer = (session: AskSession) => void;

export class DeviceIngestionService {
  private readonly sessions = new Map<string, AskSessionAssembler>();
  private readonly consumer: SessionConsumer;

  constructor(consumer: SessionConsumer) {
    this.consumer = consumer;
  }

  // Returns the rejection reason, or undefined when the frame was accepted.
  ingestFrame(bytes: Uint8Array): FrameError | undefined {
    // Decode (and CRC-check) first so the session id is never read from a corrupt frame.
    const decoded = decodeFrame(bytes);
    if (!decoded.ok) return mapDecodeError(decoded.error);

    const { sessionId } = decoded.frame;
    let assembler = this.sessions.get(sessionId);
    if (!assembler) {
      assembler = new AskSessionAssembler(sessionId);
      this.sessions.set(sessionId, assembler);
    }

    const result = assembler.ingest(bytes);
    if (!result.accepted) return result.error;

    if (result.isComplete) {
      this.consumer({
        sessionId,
        audio: assembler.audio(),
        audioBytesTotal: result.isComplete.totalBytes,
      });
      this.sessions.delete(sessionId);
    }
    return undefined;
  }
}
