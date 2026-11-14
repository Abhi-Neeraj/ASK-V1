"""Made-up voices with a known pitch, for testing pitch and voice-signature code. Numbers only (no files, no
models, no network).

Each voice is a sum of harmonics of a slowly moving pitch, shaped by three formant bumps (the resonances of a
vocal tract) and cut into short "syllables" with pauses and a little breath noise. It is crude, but its pitch
is exactly known, and two voices can share a pitch and differ in formants (or the other way round), which is
what the tests need. It says nothing about accuracy on real people.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np

RATE = 16_000


@dataclass(frozen=True)
class Voice:
    f0: float  # the centre of the voice's pitch, in hertz
    formants: tuple[float, float, float] = (600.0, 1700.0, 2700.0)
    # Formant bandwidths: wider means a duller, breathier sound.
    bandwidth: tuple[float, float, float] = (90.0, 140.0, 200.0)
    wobble: float = 0.06  # how far the pitch wanders from f0, as a fraction of f0
    breath: float = 0.004  # level of the noise mixed in


LOW_MALE = Voice(f0=105.0, formants=(520.0, 1450.0, 2450.0))
MID_MALE = Voice(f0=135.0, formants=(560.0, 1550.0, 2550.0))
MID_FEMALE = Voice(f0=205.0, formants=(720.0, 1950.0, 2950.0))
HIGH_FEMALE = Voice(f0=265.0, formants=(780.0, 2150.0, 3150.0))


def _envelope(freqs: np.ndarray, voice: Voice) -> np.ndarray:
    gain = np.zeros_like(freqs)
    for centre, width in zip(voice.formants, voice.bandwidth, strict=True):
        gain += np.exp(-0.5 * ((freqs - centre) / (width * 2.0)) ** 2)
    return gain + 0.05  # a floor, so frequencies between formants are not silent


def speak(voice: Voice, seconds: float, *, seed: int = 0, syllables: bool = True) -> np.ndarray:
    """`seconds` of the voice, as floats between -1 and 1."""
    rng = np.random.default_rng(seed)
    count = int(seconds * RATE)
    t = np.arange(count) / RATE
    # Pitch drifts slowly (about a third of a hertz of "intonation") plus a little random wander.
    drift = np.sin(2 * np.pi * (0.35 + 0.1 * rng.random()) * t + rng.random() * 6.28)
    wander = np.convolve(rng.normal(0, 1, count), np.ones(800) / 800, mode="same") * 6
    f0 = voice.f0 * (1.0 + voice.wobble * drift + 0.01 * wander)
    phase = 2 * np.pi * np.cumsum(f0) / RATE

    signal = np.zeros(count)
    for k in range(1, int(7000 / voice.f0) + 1):
        freq = k * f0
        amplitude = _envelope(freq, voice) / k**1.3
        amplitude[freq > 7000] = 0.0
        signal += amplitude * np.sin(k * phase + rng.random() * 6.28)
    signal /= np.max(np.abs(signal)) + 1e-9

    if syllables:
        # Bursts of 0.25 to 0.5 s with 0.08 to 0.2 s of near-silence between them.
        gate = np.zeros(count)
        position = int(0.1 * RATE)
        while position < count:
            length = int((0.25 + 0.25 * rng.random()) * RATE)
            end = min(count, position + length)
            ramp = np.hanning(end - position) ** 0.5
            gate[position:end] = ramp
            position = end + int((0.08 + 0.12 * rng.random()) * RATE)
        signal = signal * gate
    signal = 0.6 * signal + voice.breath * rng.normal(0, 1, count)
    return np.clip(signal, -1.0, 1.0)


def to_wav_bytes(samples: np.ndarray, rate: int = RATE) -> bytes:
    pcm = np.round(np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return out.getvalue()


def conversation(
    turns: list[tuple[Voice, float, int]], *, gap_seconds: float = 0.6
) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """Voices taking turns. Each turn is (voice, seconds, seed). Returns the audio and, for each turn, its
    (start_ms, end_ms, index) so a test knows who spoke when."""
    pieces: list[np.ndarray] = []
    spans: list[tuple[int, int, int]] = []
    cursor = 0
    for index, (voice, seconds, seed) in enumerate(turns):
        piece = speak(voice, seconds, seed=seed)
        start = cursor
        pieces.append(piece)
        cursor += len(piece)
        spans.append((start * 1000 // RATE, cursor * 1000 // RATE, index))
        gap = np.zeros(int(gap_seconds * RATE))
        pieces.append(gap)
        cursor += len(gap)
    return np.concatenate(pieces), spans
