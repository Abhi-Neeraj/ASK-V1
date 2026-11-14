import { describe, expect, it } from "vitest";
import { encodeFrame, AskFrame } from "@/domain/device/framing";
import { AskSession } from "@/domain/device/transport";
import { DeviceIngestionService } from "@/application/device/DeviceIngestionService";
import { InMemoryDeviceTransport } from "@/application/device/InMemoryDeviceTransport";

function frame(over: Partial<AskFrame>): AskFrame {
  return {
    type: "AUDIO",
    flags: 0,
    sessionId: "C-92831",
    sequence: 1,
    timestampMs: 1_726_578_123_000,
    payload: new Uint8Array(0),
    crc32: 0,
    ...over,
  };
}

function sessionFrames(): AskFrame[] {
  return [
    frame({ type: "START", sequence: 0 }),
    frame({ type: "AUDIO", sequence: 1, payload: new Uint8Array([1, 2]) }),
    frame({ type: "AUDIO", sequence: 2, payload: new Uint8Array([3]) }),
    frame({ type: "END", sequence: 3 }),
  ];
}

describe("DeviceIngestionService", () => {
  it("emits one session with ordered audio when END arrives", () => {
    const sessions: AskSession[] = [];
    const service = new DeviceIngestionService((s) => sessions.push(s));

    for (const f of sessionFrames()) {
      expect(service.ingestFrame(encodeFrame(f))).toBeUndefined();
    }

    expect(sessions).toHaveLength(1);
    expect(sessions[0].sessionId).toBe("C-92831");
    expect([...sessions[0].audio]).toEqual([1, 2, 3]);
    expect(sessions[0].audioBytesTotal).toBe(3);
  });

  it("emits every chunk when one arrives early and is retransmitted", () => {
    const sessions: AskSession[] = [];
    const service = new DeviceIngestionService((s) => sessions.push(s));
    const audio = (sequence: number) => encodeFrame(frame({ sequence, payload: new Uint8Array([sequence]) }));

    service.ingestFrame(encodeFrame(frame({ type: "START", sequence: 0 })));
    service.ingestFrame(audio(1));
    expect(service.ingestFrame(audio(3))?.code).toBe("OUT_OF_ORDER");
    service.ingestFrame(audio(2));
    service.ingestFrame(audio(3));
    service.ingestFrame(encodeFrame(frame({ type: "END", sequence: 4 })));

    expect(sessions).toHaveLength(1);
    expect([...sessions[0].audio]).toEqual([1, 2, 3]);
    expect(sessions[0].audioBytesTotal).toBe(3);
  });

  it("does not emit a session when chunks are missing at END, and emits it after recovery", () => {
    const sessions: AskSession[] = [];
    const service = new DeviceIngestionService((s) => sessions.push(s));
    const audio = (sequence: number) => encodeFrame(frame({ sequence, payload: new Uint8Array([sequence]) }));
    const end = () => encodeFrame(frame({ type: "END", sequence: 4 }));

    service.ingestFrame(encodeFrame(frame({ type: "START", sequence: 0 })));
    service.ingestFrame(audio(1));
    service.ingestFrame(audio(3)); // seq 2 missing
    expect(service.ingestFrame(end())?.code).toBe("OUT_OF_ORDER");
    expect(sessions).toHaveLength(0);

    service.ingestFrame(audio(2));
    service.ingestFrame(audio(3));
    expect(service.ingestFrame(end())).toBeUndefined();
    expect(sessions).toHaveLength(1);
    expect([...sessions[0].audio]).toEqual([1, 2, 3]);
  });

  it("does not emit a session that never reaches END", () => {
    const sessions: AskSession[] = [];
    const service = new DeviceIngestionService((s) => sessions.push(s));

    for (const f of sessionFrames().slice(0, 3)) service.ingestFrame(encodeFrame(f));

    expect(sessions).toHaveLength(0);
  });

  it("rejects a corrupt frame before it reaches processing", () => {
    const sessions: AskSession[] = [];
    const service = new DeviceIngestionService((s) => sessions.push(s));

    const bytes = encodeFrame(frame({ payload: new Uint8Array([1, 2]) }));
    bytes[bytes.length - 1] ^= 0xff;

    expect(service.ingestFrame(bytes)?.code).toBe("CHECKSUM");
    expect(sessions).toHaveLength(0);
  });

  it("delivers sessions through the in-memory transport seam", async () => {
    const transport = new InMemoryDeviceTransport();
    const service = new DeviceIngestionService((s) => transport._emit(s));
    transport._setFrameHandler((bytes) => service.ingestFrame(bytes));

    const received: AskSession[] = [];
    transport.onSession((s) => received.push(s));

    for (const f of sessionFrames()) await transport.send(f);

    expect(received).toHaveLength(1);
    expect([...received[0].audio]).toEqual([1, 2, 3]);
  });
});
