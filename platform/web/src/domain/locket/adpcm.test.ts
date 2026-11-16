import { describe, expect, it } from "vitest";
import { BLOCK_BYTES, SAMPLES_PER_BLOCK, decodeAdpcmBlock, encodeAdpcmBlock } from "@/domain/locket/adpcm";

describe("IMA ADPCM blocks", () => {
  it("decodes_a_known_block", () => {
    // Start state predictor 0, step index 0; every nibble 7 (+, all three magnitude bits). Worked by hand from
    // the standard IMA step table: 11, 41, 104, 240, 533, 1164, 2521, 5431.
    const block = new Uint8Array(BLOCK_BYTES);
    block.fill(0x77, 4);
    const samples = decodeAdpcmBlock(block);
    expect(samples.length).toBe(SAMPLES_PER_BLOCK);
    expect(Array.from(samples.slice(0, 8))).toEqual([11, 41, 104, 240, 533, 1164, 2521, 5431]);
  });

  it("reads_the_starting_state_from_the_block_header", () => {
    const block = new Uint8Array(BLOCK_BYTES);
    new DataView(block.buffer).setInt16(0, -1000, true);
    block[2] = 0;
    // nibble 0: +0 change except step>>3 = 0, so the first sample stays at the predictor
    expect(decodeAdpcmBlock(block)[0]).toBe(-1000);
  });

  it("round_trips_a_sine_within_tolerance", () => {
    const sine = new Int16Array(SAMPLES_PER_BLOCK * 4);
    for (let i = 0; i < sine.length; i += 1) sine[i] = Math.round(12000 * Math.sin((2 * Math.PI * 1000 * i) / 16000));
    const state = { predictor: 0, index: 0 };
    const decoded: number[] = [];
    for (let b = 0; b < 4; b += 1) {
      const block = encodeAdpcmBlock(sine.subarray(b * SAMPLES_PER_BLOCK, (b + 1) * SAMPLES_PER_BLOCK), state);
      expect(block.length).toBe(BLOCK_BYTES);
      decoded.push(...decodeAdpcmBlock(block));
    }
    // Skip the first block while the step size adapts.
    let worst = 0;
    for (let i = SAMPLES_PER_BLOCK; i < sine.length; i += 1) worst = Math.max(worst, Math.abs(decoded[i] - sine[i]));
    expect(worst).toBeLessThan(0.02 * 32768);
  });

  it("each_block_decodes_on_its_own", () => {
    const tone = new Int16Array(SAMPLES_PER_BLOCK * 2);
    for (let i = 0; i < tone.length; i += 1) tone[i] = Math.round(8000 * Math.sin(i / 5));
    const state = { predictor: 0, index: 0 };
    encodeAdpcmBlock(tone.subarray(0, SAMPLES_PER_BLOCK), state);
    const second = encodeAdpcmBlock(tone.subarray(SAMPLES_PER_BLOCK), state);
    const alone = decodeAdpcmBlock(second);
    expect(Math.abs(alone[0] - tone[SAMPLES_PER_BLOCK])).toBeLessThan(2000);
  });

  it("rejects_wrong_block_length", () => {
    expect(() => decodeAdpcmBlock(new Uint8Array(131))).toThrow();
  });
});
