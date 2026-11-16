"""Pitch: how high or low a voice is.

The fundamental frequency (F0) is measured every 10 ms with the YIN method (de Cheveigné and Kawahara,
2002), on the frames that are clearly voiced. From those frames three numbers describe a speaker:

* **base pitch**: the median F0, where the voice sits most of the time;
* **low pitch**: the 5th percentile, the bottom of the voice's range in this audio;
* **high pitch**: the 95th percentile, the top of it.

Percentiles, not the minimum and maximum, so one wrong frame cannot move them. Pitch is the first layer used
to tell speakers apart (a very high voice is never compared with a very low one); it does not name anyone on
its own, because many people share a pitch.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from app.voiceid.audio_io import ANALYSIS_RATE, Samples

FMIN_HZ = 60.0  # below the lowest adult speaking voice
FMAX_HZ = 500.0  # above all but the highest speaking voices
FRAME_SECONDS = 0.040  # long enough to hold two periods of the lowest pitch
HOP_SECONDS = 0.010
# The first dip of the normalised difference below this is taken as the period.
DIP_THRESHOLD = 0.15
# A frame counts as voiced when its best dip is at least this clean.
MAX_APERIODICITY = 0.25
# Frames quieter than this fraction of the loud ones, or than the absolute floor, are silence or background.
RELATIVE_ENERGY_FLOOR = 0.06
ABSOLUTE_ENERGY_FLOOR = 0.002  # about -54 dB below full scale
# A stretch of voiced frames shorter than this is a click or a wrong guess, not a vowel.
MIN_RUN_FRAMES = 5
# A frame whose pitch is further than this from its neighbours' median (in octaves) was an octave slip.
MAX_JUMP_OCTAVES = 0.35
# Need at least this much voiced speech to say anything about a voice.
MIN_VOICED_SECONDS = 0.5

_CHUNK_FRAMES = 1024

Array = NDArray[np.float64]


@dataclass(frozen=True)
class PitchTrack:
    """F0 for every hop. NaN where the frame is silent, noisy or unvoiced."""

    f0_hz: Array
    aperiodicity: Array
    rms: Array
    hop_seconds: float = HOP_SECONDS

    @property
    def voiced(self) -> NDArray[np.bool_]:
        return ~np.isnan(self.f0_hz)


@dataclass(frozen=True)
class PitchProfile:
    """What the voiced speech of one person (or one speaker in one recording) says about their pitch."""

    base_hz: float
    low_hz: float
    high_hz: float
    voiced_seconds: float
    # Share of the audio that was voiced speech. A low share means mostly silence, noise or whispering.
    voiced_ratio: float

    def __post_init__(self) -> None:
        if not (0 < self.low_hz <= self.base_hz <= self.high_hz):
            raise ValueError("Pitch must satisfy 0 < low <= base <= high.")


def _frames(samples: Samples, rate: int) -> tuple[NDArray[np.float64], int, int, int, int]:
    width = round(FRAME_SECONDS * rate)
    hop = round(HOP_SECONDS * rate)
    tau_min = int(np.floor(rate / FMAX_HZ))
    tau_max = int(np.ceil(rate / FMIN_HZ))
    return samples, width, hop, tau_min, tau_max


def _normalised_difference(block: Array, width: int, tau_max: int) -> Array:
    """YIN's cumulative-mean-normalised difference function for each row of `block`.

    Each row holds `width + tau_max` samples. d(tau) is how different the first `width` samples are from the
    same stretch `tau` samples later; it dips towards zero when `tau` is a whole number of periods.
    """
    nfft = 1 << int(np.ceil(np.log2(2 * width + tau_max)))
    head = block[:, :width]
    spectrum_head = np.fft.rfft(head, nfft, axis=1)
    spectrum_all = np.fft.rfft(block, nfft, axis=1)
    cross = np.fft.irfft(np.conj(spectrum_head) * spectrum_all, nfft, axis=1)[:, : tau_max + 1]
    running = np.concatenate([np.zeros((block.shape[0], 1)), np.cumsum(block * block, axis=1)], axis=1)
    energy_head = running[:, width] - running[:, 0]
    lags = np.arange(tau_max + 1)
    energy_lag = running[:, lags + width] - running[:, lags]
    diff = np.maximum(energy_head[:, None] + energy_lag - 2.0 * cross, 0.0)

    cumulative = np.cumsum(diff[:, 1:], axis=1)
    normalised: Array = np.ones_like(diff)
    with np.errstate(divide="ignore", invalid="ignore"):
        normalised[:, 1:] = np.where(cumulative > 0, diff[:, 1:] * lags[1:] / cumulative, 1.0)
    return normalised


def _pick_period(normalised: Array, tau_min: int, tau_max: int) -> tuple[Array, Array]:
    """The period (in samples, fractional) of each row and how clean the dip is (0 perfect, 1 none)."""
    rows = normalised.shape[0]
    index = np.arange(rows)
    window = normalised[:, tau_min : tau_max + 1]
    below = window < DIP_THRESHOLD
    has_dip = below.any(axis=1)
    tau = np.where(has_dip, below.argmax(axis=1), window.argmin(axis=1)) + tau_min

    # Walk down to the bottom of the first dip.
    for _ in range(tau_max - tau_min):
        step = (tau < tau_max) & (normalised[index, np.minimum(tau + 1, tau_max)] < normalised[index, tau])
        if not step.any():
            break
        tau = tau + step

    aperiodicity = normalised[index, tau]
    left = normalised[index, np.maximum(tau - 1, 0)]
    right = normalised[index, np.minimum(tau + 1, tau_max)]
    denominator = left - 2.0 * aperiodicity + right
    with np.errstate(divide="ignore", invalid="ignore"):
        shift = np.where(denominator > 1e-12, 0.5 * (left - right) / denominator, 0.0)
    return tau + np.clip(shift, -1.0, 1.0), aperiodicity


def _despike(f0: Array) -> Array:
    """Drop frames that jumped an octave from their neighbours, and runs too short to be a vowel."""
    out = f0.copy()
    voiced = ~np.isnan(out)
    if not voiced.any():
        return out
    edges = np.flatnonzero(np.diff(np.concatenate([[0], voiced.astype(int), [0]])))
    for start, stop in zip(edges[::2], edges[1::2], strict=True):
        run = np.log2(out[start:stop])
        if len(run) < MIN_RUN_FRAMES:
            out[start:stop] = np.nan
            continue
        half = 4
        padded = np.pad(run, half, mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)
        local = np.median(windows, axis=1)
        out[start:stop][np.abs(run - local) > MAX_JUMP_OCTAVES] = np.nan
    # Removing slips can leave new short fragments.
    voiced = ~np.isnan(out)
    edges = np.flatnonzero(np.diff(np.concatenate([[0], voiced.astype(int), [0]])))
    for start, stop in zip(edges[::2], edges[1::2], strict=True):
        if stop - start < MIN_RUN_FRAMES:
            out[start:stop] = np.nan
    return out


def _frame_rms(samples: Samples, count: int, hop: int, width: int, span: int) -> Array:
    """Loudness of the first `width` samples of every frame, with the frame's own average (over `span`) taken
    out, from running sums: the same number as measuring each frame directly, in a fraction of the time."""
    ones = np.concatenate([[0.0], np.cumsum(samples)])
    squares = np.concatenate([[0.0], np.cumsum(samples * samples)])
    starts = np.arange(count) * hop
    mean = (ones[starts + span] - ones[starts]) / span
    head_sum = ones[starts + width] - ones[starts]
    head_squares = squares[starts + width] - squares[starts]
    energy = (head_squares - 2.0 * mean * head_sum + width * mean * mean) / width
    loudness: Array = np.sqrt(np.maximum(energy, 0.0))
    return loudness


def track_pitch(samples: Samples, rate: int = ANALYSIS_RATE) -> PitchTrack:
    """F0 every 10 ms across `samples`."""
    samples, width, hop, tau_min, tau_max = _frames(samples, rate)
    span = width + tau_max
    empty = np.array([], dtype=np.float64)
    if len(samples) < span:
        return PitchTrack(empty, empty, empty)

    windows = np.lib.stride_tricks.sliding_window_view(samples, span)[::hop]
    count = windows.shape[0]
    f0 = np.full(count, np.nan)
    aperiodicity = np.ones(count)
    rms = _frame_rms(samples, count, hop, width, span)

    # Only frames that are loud enough can count as voiced, so only they get the (expensive) period search.
    loud = max(ABSOLUTE_ENERGY_FLOOR, RELATIVE_ENERGY_FLOOR * float(np.percentile(rms, 95)))
    candidates = np.flatnonzero(rms >= loud)

    for first in range(0, len(candidates), _CHUNK_FRAMES):
        chosen = candidates[first : first + _CHUNK_FRAMES]
        block = windows[chosen].astype(np.float64)
        # A constant offset is not pitch, and removing it from the whole recording at once would turn exact
        # silence into a steady offset that looks perfectly periodic. So it is removed frame by frame.
        block = block - block.mean(axis=1, keepdims=True)
        normalised = _normalised_difference(block, width, tau_max)
        period, clean = _pick_period(normalised, tau_min, tau_max)
        f0[chosen] = rate / period
        aperiodicity[chosen] = clean

    keep = (aperiodicity <= MAX_APERIODICITY) & (rms >= loud) & (f0 >= FMIN_HZ) & (f0 <= FMAX_HZ)
    f0 = np.where(keep, f0, np.nan)
    return PitchTrack(_despike(f0), aperiodicity, rms)


def profile_from_values(
    f0_hz: Sequence[float] | Array, *, total_seconds: float, hop_seconds: float = HOP_SECONDS
) -> PitchProfile | None:
    """Summarise voiced F0 values. None when there is too little voiced speech to say anything."""
    values = np.asarray(f0_hz, dtype=np.float64)
    values = values[~np.isnan(values)]
    voiced_seconds = len(values) * hop_seconds
    if voiced_seconds < MIN_VOICED_SECONDS:
        return None
    low, base, high = np.exp2(np.percentile(np.log2(values), [5, 50, 95]))
    ratio = min(1.0, voiced_seconds / total_seconds) if total_seconds > 0 else 0.0
    return PitchProfile(
        base_hz=float(base),
        low_hz=float(low),
        high_hz=float(high),
        voiced_seconds=float(voiced_seconds),
        voiced_ratio=float(ratio),
    )


def estimate_pitch(pieces: Sequence[Samples], rate: int = ANALYSIS_RATE) -> PitchProfile | None:
    """Pitch of one speaker from the pieces of audio they spoke (one piece for a whole clip, or one per
    turn). Each piece is tracked on its own, so the seam between two turns is never read as speech."""
    voiced: list[Array] = []
    total = 0.0
    for piece in pieces:
        total += len(piece) / rate
        track = track_pitch(piece, rate)
        voiced.append(track.f0_hz[track.voiced])
    if not voiced:
        return None
    return profile_from_values(np.concatenate(voiced), total_seconds=total)


def merge_profiles(profiles: Sequence[PitchProfile]) -> PitchProfile:
    """One profile from several samples of the same person, each counted by how much voiced speech it
    holds."""
    if not profiles:
        raise ValueError("No pitch profiles to merge.")
    weights = np.array([p.voiced_seconds for p in profiles])
    if weights.sum() <= 0:
        raise ValueError("Pitch profiles with no voiced speech cannot be merged.")

    def blend(values: list[float]) -> float:
        # Averaged in octaves, because pitch is heard as ratios.
        return float(np.exp2(np.average(np.log2(values), weights=weights)))

    base = blend([p.base_hz for p in profiles])
    low = min(blend([p.low_hz for p in profiles]), base)
    high = max(blend([p.high_hz for p in profiles]), base)
    seconds = float(weights.sum())
    ratio = float(np.average([p.voiced_ratio for p in profiles], weights=weights))
    return PitchProfile(base_hz=base, low_hz=low, high_hz=high, voiced_seconds=seconds, voiced_ratio=ratio)


def octaves_apart(a_hz: float, b_hz: float) -> float:
    """How far apart two pitches are, in octaves (12 semitones each)."""
    return abs(float(np.log2(a_hz / b_hz)))
