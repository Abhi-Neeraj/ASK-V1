"""Comparing voice prints, and choosing the threshold that decides when a voice gets a name.

Pure functions with no models and no database, so every rule here can be tested exactly.

The one rule that matters: a wrong name is worse than no name. A voice is named only when it is
clearly closer to one enrolled person than to the threshold AND than to everyone else. Otherwise it is
"Unknown speaker".
"""

from __future__ import annotations

import itertools
import math
import struct
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

Vector = tuple[float, ...]


def norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


def normalize(vector: Sequence[float]) -> Vector:
    length = norm(vector)
    if length == 0:
        raise ValueError("A voice print cannot be all zeros.")
    return tuple(x / length for x in vector)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Similarity of direction, -1 to 1. Different lengths or a zero vector are never similar."""
    if len(a) != len(b) or not a:
        return 0.0
    length = norm(a) * norm(b)
    if length == 0:
        return 0.0
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b, strict=True)) / length))


def mean_voiceprint(vectors: Sequence[Sequence[float]]) -> Vector:
    """The average direction of several samples of one voice, as a unit vector."""
    if not vectors:
        raise ValueError("No samples.")
    length = len(vectors[0])
    if any(len(v) != length for v in vectors):
        raise ValueError("Samples come from different models.")
    return normalize([sum(normalize(v)[i] for v in vectors) / len(vectors) for i in range(length)])


def merge_sample(current: Sequence[float], samples: int, new: Sequence[float]) -> Vector:
    """The voice print after one more sample: every sample counts equally, however many came before."""
    if samples < 1 or len(current) != len(new):
        raise ValueError("Cannot merge a sample into a voice print of a different shape.")
    fresh = normalize(new)
    return normalize([c * samples + n for c, n in zip(normalize(current), fresh, strict=True)])


def encode_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def decode_vector(data: bytes, dimension: int) -> Vector:
    if len(data) != dimension * 4:
        raise ValueError("Stored voice print has the wrong size.")
    return struct.unpack(f"<{dimension}f", data)


@dataclass(frozen=True)
class Candidate:
    person_id: uuid.UUID
    vector: Vector


@dataclass(frozen=True)
class Match:
    person_id: uuid.UUID
    score: float
    runner_up: float | None


def best_match(
    vector: Sequence[float], candidates: Sequence[Candidate], *, threshold: float, margin: float
) -> Match | None:
    """Who this voice is, or None if it is not clearly anyone.

    The best score must reach `threshold`, and beat the second best by at least `margin`, so two
    people who sound alike are never told apart by a coin toss.
    """
    scored = sorted(
        ((cosine(vector, c.vector), c) for c in candidates if len(c.vector) == len(vector)),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored:
        return None
    top, candidate = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else None
    if top < threshold:
        return None
    if runner_up is not None and top - runner_up < margin:
        return None
    return Match(person_id=candidate.person_id, score=top, runner_up=runner_up)


# --- choosing a threshold ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Calibration:
    threshold: float
    # Of the pairs of DIFFERENT people, how many would be wrongly called the same (the dangerous error).
    false_accept_rate: float
    # Of the pairs of the SAME person, how many would be wrongly called different (a missed name).
    false_reject_rate: float
    same_pairs: int
    different_pairs: int


def pair_scores(by_person: Mapping[str, Sequence[Sequence[float]]]) -> tuple[list[float], list[float]]:
    """Similarity of every pair of samples: within a person (should be high), across people (low)."""
    same: list[float] = []
    different: list[float] = []
    people = list(by_person.items())
    for i, (_, samples) in enumerate(people):
        for a in range(len(samples)):
            for b in range(a + 1, len(samples)):
                same.append(cosine(samples[a], samples[b]))
        for _, others in people[i + 1 :]:
            different.extend(cosine(x, y) for x in samples for y in others)
    return same, different


def suggest_threshold(
    same: Sequence[float], different: Sequence[float], *, max_false_accept_rate: float = 0.01
) -> Calibration:
    """The lowest threshold that keeps wrongly-matched pairs of different people at or under the target.

    Lowest, because that misses the fewest real matches for the safety asked for.
    """
    if not same or not different:
        raise ValueError("Need scores from the same person and from different people.")
    if not 0 <= max_false_accept_rate <= 1:
        raise ValueError("The false-accept target is a fraction between 0 and 1.")

    scores = sorted({*same, *different})
    midpoints = [(low + high) / 2 for low, high in itertools.pairwise(scores)]
    candidates = [scores[0] - 1e-6, *midpoints, scores[-1] + 1e-6]

    for threshold in candidates:  # ascending: false accepts only fall as the threshold rises
        accepted = sum(1 for score in different if score >= threshold) / len(different)
        if accepted <= max_false_accept_rate:
            rejected = sum(1 for score in same if score < threshold) / len(same)
            return Calibration(threshold, accepted, rejected, len(same), len(different))
    raise AssertionError("A threshold above every score always accepts nothing.")  # pragma: no cover


def calibrate(
    by_person: Mapping[str, Sequence[Sequence[float]]], *, max_false_accept_rate: float = 0.01
) -> Calibration:
    if len(by_person) < 2:
        raise ValueError("Need samples from at least two different people.")
    if any(len(samples) < 2 for samples in by_person.values()):
        raise ValueError("Need at least two samples from each person.")
    same, different = pair_scores(by_person)
    return suggest_threshold(same, different, max_false_accept_rate=max_false_accept_rate)
