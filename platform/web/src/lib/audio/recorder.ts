// Recording from the microphone in the browser, and converting what was recorded for upload.
// Browser audio APIs only: nothing here runs on the server, and nothing is sent until a person
// chooses to save the sample.

import { SAMPLE_RATE, encodeWav16, rms } from "@/lib/audio/wav";

export type Recording = {
  /** Stops recording and returns the sample as 16 kHz mono WAV. */
  stop(): Promise<Blob>;
  /** Stops and throws the sample away. */
  cancel(): void;
};

export function microphoneSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    typeof MediaRecorder !== "undefined" &&
    typeof window.OfflineAudioContext !== "undefined"
  );
}

type AudioContextClass = typeof AudioContext;

function audioContext(): AudioContext {
  const Ctor: AudioContextClass =
    window.AudioContext ?? (window as unknown as { webkitAudioContext: AudioContextClass }).webkitAudioContext;
  return new Ctor();
}

/** Browsers record in their own format (Opus, AAC); the server wants plain WAV, so convert here. */
export async function toWav16k(recorded: Blob): Promise<Blob> {
  const context = audioContext();
  try {
    const decoded = await context.decodeAudioData(await recorded.arrayBuffer());
    const length = Math.max(1, Math.ceil(decoded.duration * SAMPLE_RATE));
    // Rendering into a one-channel context at 16 kHz resamples and mixes down to mono in one step.
    const offline = new OfflineAudioContext(1, length, SAMPLE_RATE);
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start();
    const rendered = await offline.startRendering();
    return encodeWav16(rendered.getChannelData(0));
  } finally {
    void context.close();
  }
}

/**
 * Asks for the microphone and starts recording. `onLevel` is told how loud it is, about 15 times a
 * second, so the screen can show that the microphone is really picking something up.
 * Rejects if permission is refused or there is no microphone.
 */
export async function startRecording(onLevel: (level: number) => void): Promise<Recording> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });

  const chunks: Blob[] = [];
  const recorder = new MediaRecorder(stream);
  recorder.ondataavailable = (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  };

  const meterContext = audioContext();
  const analyser = meterContext.createAnalyser();
  analyser.fftSize = 1024;
  meterContext.createMediaStreamSource(stream).connect(analyser);
  const frame = new Float32Array(analyser.fftSize);
  const meter = window.setInterval(() => {
    analyser.getFloatTimeDomainData(frame);
    onLevel(Math.min(1, rms(frame) * 6)); // speech is quiet next to full scale; scale it up to be visible
  }, 66);

  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    window.clearInterval(meter);
    stream.getTracks().forEach((track) => track.stop()); // turns the microphone light off
    void meterContext.close();
    onLevel(0);
  };

  recorder.start();
  return {
    stop: () =>
      new Promise<Blob>((resolve, reject) => {
        recorder.onstop = () => {
          release();
          toWav16k(new Blob(chunks, { type: recorder.mimeType })).then(resolve, reject);
        };
        if (recorder.state === "inactive") recorder.onstop(new Event("stop"));
        else recorder.stop();
      }),
    cancel: () => {
      recorder.onstop = null;
      if (recorder.state !== "inactive") recorder.stop();
      release();
    },
  };
}
