// IMA ADPCM, the 4:1 audio compression the locket uses over Bluetooth (spec 5.3).
//
// One AUDIO payload = one block of 132 bytes: predictor (int16 little-endian), step index (uint8), a reserved
// zero byte, then 128 bytes of 4-bit codes, low nibble first = 256 samples at 16 kHz. The header holds the
// decoder state before the first sample, so every block decodes on its own (a resent frame needs nothing else).

export const SAMPLES_PER_BLOCK = 256;
export const BLOCK_BYTES = 4 + SAMPLES_PER_BLOCK / 2;

const STEPS = [
  7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107,
  118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
  1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894,
  6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
  32767,
];
const INDEX_CHANGE = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8];

export type AdpcmState = { predictor: number; index: number };

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

function step(state: AdpcmState, code: number): number {
  const size = STEPS[state.index];
  let delta = size >> 3;
  if (code & 4) delta += size;
  if (code & 2) delta += size >> 1;
  if (code & 1) delta += size >> 2;
  state.predictor = clamp(code & 8 ? state.predictor - delta : state.predictor + delta, -32768, 32767);
  state.index = clamp(state.index + INDEX_CHANGE[code], 0, STEPS.length - 1);
  return state.predictor;
}

export function decodeAdpcmBlock(payload: Uint8Array): Int16Array {
  if (payload.length !== BLOCK_BYTES) {
    throw new Error(`An audio block is ${BLOCK_BYTES} bytes, got ${payload.length}.`);
  }
  const view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
  const state = { predictor: view.getInt16(0, true), index: clamp(payload[2], 0, STEPS.length - 1) };
  const out = new Int16Array(SAMPLES_PER_BLOCK);
  for (let i = 0; i < SAMPLES_PER_BLOCK / 2; i += 1) {
    const byte = payload[4 + i];
    out[2 * i] = step(state, byte & 0x0f);
    out[2 * i + 1] = step(state, byte >> 4);
  }
  return out;
}

function encodeOne(state: AdpcmState, sample: number): number {
  const size = STEPS[state.index];
  let diff = sample - state.predictor;
  let code = 0;
  if (diff < 0) {
    code = 8;
    diff = -diff;
  }
  if (diff >= size) {
    code |= 4;
    diff -= size;
  }
  if (diff >= size >> 1) {
    code |= 2;
    diff -= size >> 1;
  }
  if (diff >= size >> 2) code |= 1;
  step(state, code); // advance exactly as the decoder will
  return code;
}

/** Reference encoder (the firmware's job; used here for tests and test vectors). Advances `state`. */
export function encodeAdpcmBlock(samples: Int16Array, state: AdpcmState): Uint8Array {
  if (samples.length !== SAMPLES_PER_BLOCK) throw new Error(`A block holds ${SAMPLES_PER_BLOCK} samples.`);
  const block = new Uint8Array(BLOCK_BYTES);
  const view = new DataView(block.buffer);
  view.setInt16(0, state.predictor, true);
  block[2] = state.index;
  for (let i = 0; i < SAMPLES_PER_BLOCK / 2; i += 1) {
    const low = encodeOne(state, samples[2 * i]);
    const high = encodeOne(state, samples[2 * i + 1]);
    block[4 + i] = low | (high << 4);
  }
  return block;
}
