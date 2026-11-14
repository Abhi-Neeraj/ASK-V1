import { describe, expect, it } from "vitest";
import { SAMPLE_RATE, encodeWav16, rms, wavBytes, wavSeconds } from "@/lib/audio/wav";

const text = (bytes: Uint8Array, from: number, length: number) =>
  String.fromCharCode(...bytes.slice(from, from + length));

describe("wavBytes", () => {
  const tone = Float32Array.from({ length: 1600 }, (_, n) => Math.sin((2 * Math.PI * 440 * n) / SAMPLE_RATE));
  const bytes = wavBytes(tone);
  const view = new DataView(bytes.buffer);

  it("writes a header the server will accept: plain 16-bit mono PCM at 16 kHz", () => {
    expect(text(bytes, 0, 4)).toBe("RIFF");
    expect(text(bytes, 8, 4)).toBe("WAVE");
    expect(text(bytes, 12, 4)).toBe("fmt ");
    expect(view.getUint16(20, true)).toBe(1); // PCM, not compressed
    expect(view.getUint16(22, true)).toBe(1); // mono
    expect(view.getUint32(24, true)).toBe(16_000);
    expect(view.getUint32(28, true)).toBe(32_000); // bytes per second
    expect(view.getUint16(32, true)).toBe(2); // bytes per frame
    expect(view.getUint16(34, true)).toBe(16);
    expect(text(bytes, 36, 4)).toBe("data");
  });

  it("has sizes that agree with the data that follows, which the server checks", () => {
    expect(bytes.length).toBe(44 + 1600 * 2);
    expect(view.getUint32(4, true) + 8).toBe(bytes.length); // RIFF size covers the whole file
    expect(view.getUint32(40, true)).toBe(1600 * 2);
  });

  it("measures its own length", () => {
    expect(wavSeconds(bytes)).toBeCloseTo(0.1, 6);
    expect(wavSeconds(wavBytes(new Float32Array(SAMPLE_RATE * 4)))).toBe(4);
    expect(wavSeconds(new Uint8Array(10))).toBe(0);
  });

  it("scales full-scale samples to the 16-bit range and never wraps around", () => {
    const loud = new DataView(wavBytes(Float32Array.of(1, -1, 2, -2, 0.5)).buffer);
    expect([0, 1, 2, 3, 4].map((i) => loud.getInt16(44 + i * 2, true))).toEqual([32767, -32768, 32767, -32768, 16383]);
  });

  it("keeps silence silent", () => {
    const quiet = wavBytes(new Float32Array(100));
    expect(quiet.slice(44).every((byte) => byte === 0)).toBe(true);
  });

  it("uses the rate it is given", () => {
    const rate8k = new DataView(wavBytes(new Float32Array(8), 8000).buffer);
    expect(rate8k.getUint32(24, true)).toBe(8000);
    expect(rate8k.getUint32(28, true)).toBe(16_000);
  });

  it("is an empty but valid file for no audio", () => {
    const empty = wavBytes(new Float32Array(0));
    expect(empty.length).toBe(44);
    expect(wavSeconds(empty)).toBe(0);
  });
});

describe("encodeWav16", () => {
  it("wraps the same bytes in a blob the browser can upload as audio/wav", async () => {
    const blob = encodeWav16(new Float32Array(160));
    expect(blob.type).toBe("audio/wav");
    expect(blob.size).toBe(44 + 320);
    expect(text(new Uint8Array(await blob.arrayBuffer()), 0, 4)).toBe("RIFF");
  });
});

describe("rms", () => {
  it("is zero for silence and for nothing", () => {
    expect(rms([])).toBe(0);
    expect(rms(new Float32Array(50))).toBe(0);
  });

  it("is the root of the mean square", () => {
    expect(rms([1, -1, 1, -1])).toBe(1);
    expect(rms([0.5, 0.5])).toBe(0.5);
    expect(rms([3, 4])).toBeCloseTo(Math.sqrt(12.5), 10);
  });
});
