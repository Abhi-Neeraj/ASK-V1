"""Deciding whose voice a signature is, or that it is nobody in particular.

Two layers, in this order:

1. **Pitch gate.** A signature is only compared with people whose base pitch is within `pitch_gate_octaves`
   of its own. A low voice is never compared with a high one, however alike their spectra look.
2. **Timbre similarity.** Among the people who pass the gate, the closest one is named only if its similarity
   reaches `threshold` AND beats the next closest by at least `margin`. Otherwise the answer is "nobody",
   because a wrong name is worse than no name.

Pure functions: no database, no models. Consent and tenancy are the caller's business: it decides who is
passed in as a candidate.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from app.voiceid.pitch import octaves_apart
from app.voiceid.signature import VoiceSignature, similarity, timbre_distance


@dataclass(frozen=True)
class SignatureCandidate:
    person_id: uuid.UUID
    signature: VoiceSignature


@dataclass(frozen=True)
class Scored:
    person_id: uuid.UUID
    score: float  # timbre similarity, 0 to 1
    distance: float
    pitch_gap_octaves: float
    passed_gate: bool


@dataclass(frozen=True)
class SignatureMatch:
    person_id: uuid.UUID
    score: float
    runner_up: float | None
    pitch_gap_octaves: float


def score_candidates(
    signature: VoiceSignature, candidates: Sequence[SignatureCandidate], *, pitch_gate_octaves: float
) -> list[Scored]:
    """Everyone ranked: those who pass the pitch gate first (best similarity first), then those who do not."""
    scored: list[Scored] = []
    for candidate in candidates:
        if candidate.signature.version != signature.version:
            continue  # made by another version of the measurement: not comparable
        gap = octaves_apart(signature.pitch.base_hz, candidate.signature.pitch.base_hz)
        distance = timbre_distance(signature.timbre, candidate.signature.timbre)
        scored.append(
            Scored(
                person_id=candidate.person_id,
                score=similarity(distance),
                distance=distance,
                pitch_gap_octaves=gap,
                passed_gate=gap <= pitch_gate_octaves,
            )
        )
    scored.sort(key=lambda s: (not s.passed_gate, -s.score))
    return scored


def best_signature_match(
    signature: VoiceSignature,
    candidates: Sequence[SignatureCandidate],
    *,
    threshold: float,
    margin: float,
    pitch_gate_octaves: float,
) -> SignatureMatch | None:
    """Who this voice is, or None if it is not clearly anyone."""
    passing = [
        s
        for s in score_candidates(signature, candidates, pitch_gate_octaves=pitch_gate_octaves)
        if s.passed_gate
    ]
    if not passing:
        return None
    best = passing[0]
    runner_up = passing[1].score if len(passing) > 1 else None
    if best.score < threshold:
        return None
    if runner_up is not None and best.score - runner_up < margin:
        return None
    return SignatureMatch(
        person_id=best.person_id,
        score=best.score,
        runner_up=runner_up,
        pitch_gap_octaves=best.pitch_gap_octaves,
    )
