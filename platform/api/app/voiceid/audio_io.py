"""Reading the samples of a WAV file the API has already validated (16-bit PCM, mono or stereo)."""

from __future__ import annotations

import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

# The measurements below are made at this rate: 16 kHz covers every part of a voice that matters here, and
# a fixed rate means one set of frame sizes for every recording.
ANALYSIS_RATE = 16_000

Samples = NDArray[np.float64]


@dataclass(frozen=True)
class Audio:
    """Mono audio as numbers between -1 and 1, at `rate` samples a second."""

    samples: Samples
    rate: int

    @property
    def seconds(self) -> float:
        return len(self.samples) / self.rate if self.rate else 0.0


def read_wav(path: Path) -> Audio:
    """The audio of a 16-bit PCM WAV file, mixed down to mono. Stereo channels are averaged."""
    with wave.open(str(path), "rb") as handle:
        channels, width, rate = handle.getnchannels(), handle.getsampwidth(), handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError("Only 16-bit audio can be analysed.")
    data = np.frombuffer(frames, dtype="<i2").astype(np.float64) / 32768.0
    if channels > 1:
        data = data[: len(data) - len(data) % channels].reshape(-1, channels).mean(axis=1)
    return Audio(samples=data, rate=rate)


def resample(audio: Audio, rate: int = ANALYSIS_RATE) -> Audio:
    """The same audio at another sample rate (smoothed first when going down, so nothing folds back)."""
    if audio.rate == rate or len(audio.samples) == 0:
        return Audio(audio.samples, audio.rate if audio.rate == rate else rate)
    samples = audio.samples
    if rate < audio.rate:
        # A moving average across one new-rate sample is a crude but safe low-pass for speech.
        width = max(1, round(audio.rate / rate))
        if width > 1:
            kernel = np.ones(width) / width
            samples = np.convolve(samples, kernel, mode="same")
    count = max(1, round(len(samples) * rate / audio.rate))
    positions = np.linspace(0, len(samples) - 1, count)
    return Audio(np.interp(positions, np.arange(len(samples)), samples), rate)


def load_for_analysis(path: Path) -> Audio:
    return resample(read_wav(path))


def slice_spans(audio: Audio, spans: Sequence[tuple[int, int]]) -> list[Samples]:
    """The parts of `audio` inside each (start_ms, end_ms) span. Spans outside the audio are dropped."""
    pieces: list[Samples] = []
    for start_ms, end_ms in spans:
        first = max(0, int(start_ms * audio.rate / 1000))
        last = min(len(audio.samples), int(end_ms * audio.rate / 1000))
        if last > first:
            pieces.append(audio.samples[first:last])
    return pieces
