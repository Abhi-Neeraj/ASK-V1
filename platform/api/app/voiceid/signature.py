"""The voice signature: pitch, plus the shape of the voice's spectrum.

A signature is a small set of numbers measured from the voiced stretches of one speaker's audio:

* the speaker's **pitch** profile (base, low and high; see `pitch`), and
* the speaker's **timbre**: the average and the spread of 12 cepstral coefficients, which describe the broad
  shape of the spectrum (the resonances of the vocal tract) independent of how loud the audio is or how high
  the voice is pitched.

It is text-independent (it does not matter what was said) and needs a few seconds of speech. It is a coarse
fingerprint: it separates voices that sound clearly different and is NOT a reliable biometric. Its comparison
rules are uncalibrated for real people until they are measured on consented recordings, and it is never
presented as identity verification.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from numpy.typing import NDArray

from app.voiceid.audio_io import ANALYSIS_RATE, Samples
from app.voiceid.pitch import (
    FRAME_SECONDS,
    HOP_SECONDS,
    PitchProfile,
    merge_profiles,
    profile_from_values,
    track_pitch,
)

# Bump when the measurement changes, so an old stored signature is never compared with a new one.
SIGNATURE_VERSION = "dsp-1"

_MEL_FILTERS = 26
_CEPSTRA = 12  # c1..c12; c0 is loudness, which says nothing about whose voice it is
_WINDOW = 400  # 25 ms at 16 kHz
_NFFT = 512
_PRE_EMPHASIS = 0.97
_LOW_EDGE_HZ = 100.0
_HIGH_EDGE_HZ = 7000.0
# Voiced frames needed before the spectrum's shape means anything (0.6 s).
MIN_VOICED_FRAMES = 60

TIMBRE_DIMENSIONS = 2 * _CEPSTRA

Array = NDArray[np.float64]


@dataclass(frozen=True)
class VoiceSignature:
    pitch: PitchProfile
    # Mean of c1..c12 followed by their standard deviations: TIMBRE_DIMENSIONS numbers.
    timbre: tuple[float, ...]
    frames: int
    version: str = SIGNATURE_VERSION

    def __post_init__(self) -> None:
        if len(self.timbre) != TIMBRE_DIMENSIONS:
            raise ValueError("A signature's timbre has the wrong number of values.")


def _hz_to_mel(hz: Array | float) -> Array | float:
    return 2595.0 * np.log10(1.0 + np.asarray(hz) / 700.0)


def _mel_to_hz(mel: Array | float) -> Array | float:
    return 700.0 * (10.0 ** (np.asarray(mel) / 2595.0) - 1.0)


@lru_cache(maxsize=4)
def _filterbank(rate: int) -> tuple[Array, Array]:
    """Triangular mel filters over the power spectrum, and the cosine transform to cepstral coefficients."""
    bins = _NFFT // 2 + 1
    edges_hz = _mel_to_hz(np.linspace(_hz_to_mel(_LOW_EDGE_HZ), _hz_to_mel(_HIGH_EDGE_HZ), _MEL_FILTERS + 2))
    edges = np.floor((_NFFT + 1) * np.asarray(edges_hz) / rate).astype(int)
    bank = np.zeros((_MEL_FILTERS, bins))
    for m in range(1, _MEL_FILTERS + 1):
        left, centre, right = edges[m - 1], edges[m], edges[m + 1]
        centre = max(centre, left + 1)
        right = max(right, centre + 1)
        for k in range(left, min(centre, bins)):
            bank[m - 1, k] = (k - left) / (centre - left)
        for k in range(centre, min(right, bins)):
            bank[m - 1, k] = (right - k) / (right - centre)
    n = np.arange(_MEL_FILTERS)
    # Orthonormal DCT-II, rows 1..12.
    dct = np.sqrt(2.0 / _MEL_FILTERS) * np.cos(
        np.pi * np.outer(np.arange(1, _CEPSTRA + 1), 2 * n + 1) / (2 * _MEL_FILTERS)
    )
    return bank, dct


def _cepstra(samples: Samples, starts: NDArray[np.intp], rate: int) -> Array:
    """c1..c12 for the 25 ms frame beginning at each of `starts`."""
    bank, dct = _filterbank(rate)
    emphasised = np.append(samples[0], samples[1:] - _PRE_EMPHASIS * samples[:-1])
    window = np.hamming(_WINDOW)
    out = np.empty((len(starts), _CEPSTRA))
    for first in range(0, len(starts), 2048):
        chunk = starts[first : first + 2048]
        frames = emphasised[chunk[:, None] + np.arange(_WINDOW)] * window
        power = np.abs(np.fft.rfft(frames, _NFFT, axis=1)) ** 2
        energies = np.maximum(power @ bank.T, 1e-12)
        out[first : first + len(chunk)] = np.log(energies) @ dct.T
    return out


def _voiced_cepstra(samples: Samples, rate: int) -> tuple[Array, Array]:
    """(cepstra of the voiced frames, their pitch values) for one piece of audio."""
    track = track_pitch(samples, rate)
    if not track.voiced.any():
        return np.empty((0, _CEPSTRA)), np.empty(0)
    hop = round(HOP_SECONDS * rate)
    width = round(FRAME_SECONDS * rate)
    # Centre each 25 ms frame on the middle of the 40 ms frame the pitch was measured in.
    starts = np.flatnonzero(track.voiced) * hop + (width - _WINDOW) // 2
    starts = starts[starts + _WINDOW <= len(samples)]
    keep = np.flatnonzero(track.voiced)[: len(starts)]
    return _cepstra(samples, starts.astype(np.intp), rate), track.f0_hz[keep]


def compute_signature(pieces: Sequence[Samples], rate: int = ANALYSIS_RATE) -> VoiceSignature | None:
    """The signature of one speaker from the pieces of audio they spoke. None when there is too little voiced
    speech to measure (silence, noise, a clip that is too short)."""
    ceps: list[Array] = []
    f0: list[Array] = []
    total = 0.0
    for piece in pieces:
        total += len(piece) / rate
        c, p = _voiced_cepstra(piece, rate)
        if len(c):
            ceps.append(c)
            f0.append(p)
    if not ceps:
        return None
    pooled = np.concatenate(ceps)
    profile = profile_from_values(np.concatenate(f0), total_seconds=total)
    if profile is None or len(pooled) < MIN_VOICED_FRAMES:
        return None
    timbre = np.concatenate([pooled.mean(axis=0), pooled.std(axis=0)])
    return VoiceSignature(pitch=profile, timbre=tuple(float(v) for v in timbre), frames=len(pooled))


def merge_signatures(current: VoiceSignature, samples: int, new: VoiceSignature) -> VoiceSignature:
    """The signature after one more sample: every sample counts equally, however many came before."""
    if current.version != new.version:
        raise ValueError("Signatures made by different versions cannot be combined.")
    if samples < 1:
        raise ValueError("A signature needs at least one sample behind it.")
    blended = [(c * samples + n) / (samples + 1) for c, n in zip(current.timbre, new.timbre, strict=True)]
    return VoiceSignature(
        pitch=merge_profiles([current.pitch, new.pitch]),
        timbre=tuple(blended),
        frames=current.frames + new.frames,
        version=current.version,
    )


# --- comparing two signatures ------------------------------------------------------------------------------
#
# The cepstral coefficients are all in the same units (log energy), so they are compared directly: the
# distance
# is the root-mean-square difference over the 24 timbre values. Measured on synthetic voices (see
# platform/docs/VOICE_SIGNATURE.md) two utterances of one voice were at most about 0.6 apart and different
# voices at least about 0.8 apart. Real people vary more than synthetic voices, and microphones and rooms add
# their own colour, so these numbers are a starting point, not a calibration.

# The distance at which the similarity score has fallen to about 37 percent.
SIMILARITY_SCALE = 0.8


def timbre_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Root-mean-square difference between two timbre vectors; 0 means identical."""
    if len(a) != len(b) or not a:
        raise ValueError("Timbre vectors of different shapes cannot be compared.")
    difference = np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64)
    return float(np.sqrt(np.mean(difference**2)))


def similarity(distance: float) -> float:
    """1 for identical voices, falling smoothly towards 0 as they differ. Not a probability."""
    return float(np.exp(-((distance / SIMILARITY_SCALE) ** 2)))


def encode_timbre(timbre: Sequence[float]) -> bytes:
    return np.asarray(timbre, dtype="<f4").tobytes()


def decode_timbre(data: bytes) -> tuple[float, ...]:
    if len(data) != TIMBRE_DIMENSIONS * 4:
        raise ValueError("Stored voice signature has the wrong size.")
    return tuple(float(v) for v in np.frombuffer(data, dtype="<f4"))
