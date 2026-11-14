"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { microphoneSupported, startRecording, type Recording } from "@/lib/audio/recorder";

export type RecorderStatus = "idle" | "recording" | "converting";

export type FinishedSample = { wav: Blob; seconds: number };

/**
 * Records one spoken sample from the microphone. It stops by itself after `maxSeconds`, always turns
 * the microphone off (also if the screen is left mid-recording), and tells the screen why when the
 * microphone cannot be used, in words a person can act on.
 */
export function useVoiceRecorder({ maxSeconds, onFinish }: { maxSeconds: number; onFinish: (sample: FinishedSample) => void }) {
  const [status, setStatus] = useState<RecorderStatus>("idle");
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | undefined>();
  const [supported] = useState(microphoneSupported);

  const recording = useRef<Recording | null>(null);
  const startedAt = useRef(0);
  const ticker = useRef<number | undefined>(undefined);
  const finish = useRef(onFinish);
  useEffect(() => {
    finish.current = onFinish;
  }, [onFinish]);

  const stopTicker = () => {
    window.clearInterval(ticker.current);
    ticker.current = undefined;
  };

  const stop = useCallback(async () => {
    const active = recording.current;
    if (!active) return;
    recording.current = null;
    stopTicker();
    const elapsed = (performance.now() - startedAt.current) / 1000;
    setStatus("converting");
    try {
      finish.current({ wav: await active.stop(), seconds: elapsed });
    } catch {
      setError("The recording could not be read. Please try again.");
    } finally {
      setStatus("idle");
      setSeconds(0);
    }
  }, []);

  const start = useCallback(async () => {
    setError(undefined);
    try {
      recording.current = await startRecording(setLevel);
    } catch (failure) {
      const denied = failure instanceof DOMException && failure.name === "NotAllowedError";
      const missing = failure instanceof DOMException && failure.name === "NotFoundError";
      setError(
        denied
          ? "The browser was not allowed to use the microphone. Allow it for this site, or choose a WAV file instead."
          : missing
            ? "No microphone was found. Connect one, or choose a WAV file instead."
            : "The microphone could not be started.",
      );
      return;
    }
    startedAt.current = performance.now();
    setSeconds(0);
    setStatus("recording");
    ticker.current = window.setInterval(() => {
      const elapsed = (performance.now() - startedAt.current) / 1000;
      setSeconds(elapsed);
      if (elapsed >= maxSeconds) void stop();
    }, 250);
  }, [maxSeconds, stop]);

  // Leaving the screen mid-recording must not leave the microphone on.
  useEffect(
    () => () => {
      stopTicker();
      recording.current?.cancel();
      recording.current = null;
    },
    [],
  );

  return { status, seconds, level, error, supported, start, stop };
}
