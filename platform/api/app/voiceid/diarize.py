"""Who spoke when, worked out from pitch first and voice colour second, with no model and no network.

    1. Find the voiced speech (the pitch tracker already says where the voice is).
    2. Cut it into windows of about 3 s and measure each: its base pitch and the average of its cepstral
       coefficients (the voice's colour).
    3. Group the windows. Two windows are never grouped if their pitches are far apart (the pitch gate);
       otherwise they are grouped when their colours are close enough.
    4. Join runs of windows with the same group into turns.

EXPERIMENTAL. It separates voices that differ clearly in pitch very well and voices of similar pitch only when
their colour differs enough; it does not handle two people talking at once; and its thresholds were only tried
on synthetic voices. The transcript says which separator produced it, and a person should check by listening.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from app.voiceid.audio_io import ANALYSIS_RATE, Samples
from app.voiceid.pitch import FRAME_SECONDS, HOP_SECONDS, track_pitch
from app.voiceid.signature import _WINDOW, _cepstra

Array = NDArray[np.float64]

WINDOW_SECONDS = 3.0
MIN_WINDOW_VOICED_SECONDS = 0.35  # a window with less voice than this has nothing to measure
MAX_GAP_SECONDS = 0.35  # a pause this short is breath or a consonant, not a change of speaker
MIN_TURN_SECONDS = 0.7  # a lone shorter stretch between two of the same voice is a wobble
# A voice kept to under this much speech in the whole recording is folded into the nearest other voice.
MIN_SPEAKER_SECONDS = 2.0
_FAR = 1e3  # "not the same speaker" in the distance matrix


@dataclass(frozen=True)
class Window:
    start_s: float
    end_s: float
    region: int
    pitch_hz: float
    colour: Array
    voiced_s: float


@dataclass(frozen=True)
class SpokenTurn:
    start_ms: int
    end_ms: int
    speaker: int  # 0, 1, 2 ... in order of first appearance


def measure_windows(
    samples: Samples, rate: int = ANALYSIS_RATE, window_seconds: float = WINDOW_SECONDS
) -> list[Window]:
    """The voiced stretches of `samples`, cut into measured windows."""
    track = track_pitch(samples, rate)
    voiced = track.voiced
    if not voiced.any():
        return []
    hop = round(HOP_SECONDS * rate)
    width = round(FRAME_SECONDS * rate)
    indexes = np.flatnonzero(voiced)
    starts = indexes * hop + (width - _WINDOW) // 2
    fits = starts + _WINDOW <= len(samples)
    indexes, starts = indexes[fits], starts[fits]
    if len(indexes) == 0:
        return []
    ceps = _cepstra(samples, starts.astype(np.intp), rate)
    f0 = track.f0_hz[indexes]
    times = indexes * HOP_SECONDS

    # Regions: runs of voiced frames separated by pauses longer than MAX_GAP_SECONDS.
    breaks = np.flatnonzero(np.diff(times) > MAX_GAP_SECONDS + HOP_SECONDS) + 1
    bounds = np.concatenate([[0], breaks, [len(times)]])
    windows: list[Window] = []
    for region, (first, last) in enumerate(itertools.pairwise(bounds)):
        t = times[first:last]
        if len(t) * HOP_SECONDS < MIN_WINDOW_VOICED_SECONDS:
            continue
        begin, finish = float(t[0]), float(t[-1]) + FRAME_SECONDS
        pieces = max(1, round((finish - begin) / window_seconds))
        edges = np.linspace(begin, finish, pieces + 1)
        for a, b in itertools.pairwise(edges):
            inside = (t >= a) & (t < b) if b < finish else (t >= a)
            if inside.sum() * HOP_SECONDS < MIN_WINDOW_VOICED_SECONDS:
                continue
            windows.append(
                Window(
                    start_s=float(a),
                    end_s=float(b),
                    region=region,
                    pitch_hz=float(np.exp2(np.median(np.log2(f0[first:last][inside])))),
                    colour=ceps[first:last][inside].mean(axis=0),
                    voiced_s=float(inside.sum() * HOP_SECONDS),
                )
            )
    return windows


def _distances(windows: list[Window], pitch_gate_octaves: float) -> Array:
    pitch = np.log2(np.array([w.pitch_hz for w in windows]))
    colour = np.array([w.colour for w in windows])
    gap = np.abs(pitch[:, None] - pitch[None, :])
    diff = colour[:, None, :] - colour[None, :, :]
    distance: Array = np.sqrt(np.mean(diff**2, axis=2))
    distance[gap > pitch_gate_octaves] = _FAR
    np.fill_diagonal(distance, 0.0)
    return distance


def _cluster(distance: Array, cutoff: float) -> NDArray[np.intp]:
    """Average-linkage grouping: keep merging the two closest groups until none is closer than `cutoff`."""
    n = len(distance)
    labels = np.arange(n)
    if n < 2:
        return labels
    work = distance.copy()
    np.fill_diagonal(work, np.inf)
    sizes = np.ones(n)
    alive = np.ones(n, dtype=bool)
    while alive.sum() > 1:
        flat = int(np.argmin(work))
        i, j = divmod(flat, n)
        if work[i, j] > cutoff:
            break
        # Lance-Williams update for average linkage; j is absorbed into i.
        merged = (sizes[i] * work[i] + sizes[j] * work[j]) / (sizes[i] + sizes[j])
        work[i, :] = merged
        work[:, i] = merged
        work[i, i] = np.inf
        work[j, :] = np.inf
        work[:, j] = np.inf
        sizes[i] += sizes[j]
        alive[j] = False
        labels[labels == j] = i
    return labels


def _fold_small_groups(labels: NDArray[np.intp], windows: list[Window], distance: Array) -> NDArray[np.intp]:
    """A group with very little speech is usually one voice's intonation, not a person: fold it into the
    closest group that is big enough (ignoring the pitch gate, which is how it came to be separate)."""
    labels = labels.copy()
    seconds = {
        int(g): sum(w.voiced_s for w, label in zip(windows, labels, strict=True) if label == g)
        for g in set(labels)
    }
    big = [g for g, s in seconds.items() if s >= MIN_SPEAKER_SECONDS]
    if not big or len(big) == len(seconds):
        return labels
    for small in [g for g in seconds if g not in big]:
        members = np.flatnonzero(labels == small)
        best, best_distance = big[0], np.inf
        for group in big:
            others = np.flatnonzero(labels == group)
            d = (
                float(
                    np.mean(
                        np.where(
                            distance[np.ix_(members, others)] >= _FAR,
                            np.nan,
                            distance[np.ix_(members, others)],
                        )
                    )
                )
                if np.any(distance[np.ix_(members, others)] < _FAR)
                else np.inf
            )
            if d < best_distance:
                best, best_distance = group, d
        labels[members] = best
    return labels


def _smooth(labels: NDArray[np.intp], windows: list[Window]) -> NDArray[np.intp]:
    """A single short window between two windows of one voice, in the same stretch of speech, takes their
    label."""
    out = labels.copy()
    for i in range(1, len(windows) - 1):
        same_region = windows[i - 1].region == windows[i].region == windows[i + 1].region
        short = windows[i].end_s - windows[i].start_s < MIN_TURN_SECONDS * 2
        if same_region and short and out[i - 1] == out[i + 1] != out[i]:
            out[i] = out[i - 1]
    return out


def diarize_samples(
    samples: Samples,
    rate: int = ANALYSIS_RATE,
    *,
    pitch_gate_octaves: float,
    cluster_distance: float,
    window_seconds: float = WINDOW_SECONDS,
    max_gap_seconds: float = 1.0,
) -> list[SpokenTurn]:
    """Turns for each voice in `samples`, numbered 0, 1, 2 ... in order of first appearance."""
    windows = measure_windows(samples, rate, window_seconds)
    if not windows:
        return []
    distance = _distances(windows, pitch_gate_octaves)
    labels = _cluster(distance, cluster_distance)
    labels = _fold_small_groups(labels, windows, distance)
    labels = _smooth(labels, windows)

    order: dict[int, int] = {}
    turns: list[SpokenTurn] = []
    for window, label in zip(windows, labels, strict=True):
        speaker = order.setdefault(int(label), len(order))
        start, end = round(window.start_s * 1000), round(window.end_s * 1000)
        if turns and turns[-1].speaker == speaker and start - turns[-1].end_ms <= max_gap_seconds * 1000:
            turns[-1] = SpokenTurn(turns[-1].start_ms, end, speaker)
        else:
            turns.append(SpokenTurn(start, end, speaker))
    return turns
