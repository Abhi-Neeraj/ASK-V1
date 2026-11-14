// Turning recorded audio into the format the server accepts: plain 16-bit mono WAV at 16 kHz.
// Pure functions only, so they are tested without a microphone or a browser audio engine.

/** The sample rate voice samples are sent at. It is also the only rate the server accepts by default. */
export const SAMPLE_RATE = 16_000;

function writeText(view: DataView, offset: number, text: string) {
  for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
}

/** A complete WAV file (header and 16-bit samples) for mono audio in the range -1..1. */
export function wavBytes(samples: Float32Array, sampleRate: number = SAMPLE_RATE): Uint8Array<ArrayBuffer> {
  const dataLength = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataLength);
  const view = new DataView(buffer);

  writeText(view, 0, "RIFF");
  view.setUint32(4, 36 + dataLength, true);
  writeText(view, 8, "WAVE");
  writeText(view, 12, "fmt ");
  view.setUint32(16, 16, true); // size of this chunk
  view.setUint16(20, 1, true); // uncompressed PCM
  view.setUint16(22, 1, true); // one channel
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // bytes per second
  view.setUint16(32, 2, true); // bytes per frame
  view.setUint16(34, 16, true); // bits per sample
  writeText(view, 36, "data");
  view.setUint32(40, dataLength, true);

  for (let i = 0; i < samples.length; i++) {
    const clipped = Math.max(-1, Math.min(1, samples[i] ?? 0));
    view.setInt16(44 + i * 2, clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff, true);
  }
  return new Uint8Array(buffer);
}

export function encodeWav16(samples: Float32Array, sampleRate: number = SAMPLE_RATE): Blob {
  return new Blob([wavBytes(samples, sampleRate)], { type: "audio/wav" });
}

/** How loud a stretch of audio is, 0 (silence) to 1 (full scale). */
export function rms(samples: ArrayLike<number>): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += (samples[i] ?? 0) ** 2;
  return Math.sqrt(sum / samples.length);
}

/** Seconds of audio in a mono WAV made by `wavBytes`. */
export function wavSeconds(bytes: Uint8Array): number {
  if (bytes.length < 44) return 0;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const rate = view.getUint32(24, true);
  return rate === 0 ? 0 : view.getUint32(40, true) / 2 / rate;
}
