"""Choosing the speaker-matching threshold from recordings of real people, not from a guess.

The threshold decides when a voice is given a name. Too low and strangers are called by an employee's
name; too high and employees are called "Unknown speaker". The right value depends on the speaker
model, the microphones and the rooms, so it has to be measured on this deployment's own audio.

Layout: a folder with one sub-folder per person, each holding several short .wav recordings of that
person speaking (different days and rooms are better than back-to-back takes).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.ai.types import SpeakerEmbedder
from app.services.matching import Calibration, Vector, calibrate

# Below this many comparisons a 1% error target cannot even be measured.
MIN_DIFFERENT_PAIRS = 100
MIN_SAMPLES_PER_PERSON = 5


@dataclass(frozen=True)
class CalibrationReport:
    result: Calibration
    people: int
    samples: int
    warnings: tuple[str, ...]


def embed_directory(embedder: SpeakerEmbedder, directory: Path) -> dict[str, list[Vector]]:
    """A voice print for every .wav file, grouped by the person whose folder it is in."""
    if not directory.is_dir():
        raise ValueError(f"{directory} is not a folder.")
    by_person: dict[str, list[Vector]] = {}
    for person in sorted(p for p in directory.iterdir() if p.is_dir()):
        files = sorted(person.glob("*.wav"))
        if files:
            by_person[person.name] = [embedder.embed(file).vector for file in files]
    if not by_person:
        raise ValueError("No folders of .wav files found. Use one folder per person.")
    return by_person


def calibrate_directory(
    embedder: SpeakerEmbedder, directory: Path, max_false_accept_rate: float
) -> CalibrationReport:
    by_person = embed_directory(embedder, directory)
    result = calibrate(by_person, max_false_accept_rate=max_false_accept_rate)

    warnings: list[str] = []
    thin = sorted(name for name, samples in by_person.items() if len(samples) < MIN_SAMPLES_PER_PERSON)
    if thin:
        warnings.append(
            f"Fewer than {MIN_SAMPLES_PER_PERSON} samples for {', '.join(thin)}. "
            "More samples per person make the threshold more trustworthy."
        )
    if result.different_pairs < MIN_DIFFERENT_PAIRS:
        floor = 1 / result.different_pairs
        warnings.append(
            f"Only {result.different_pairs} comparisons between different people, so the smallest error rate "
            f"this can measure is {floor:.1%}. Add more people or more samples before trusting a low target."
        )
    if len(by_person) < 3:
        warnings.append("Only a few people were compared. A real workplace has more voices that sound alike.")
    return CalibrationReport(
        result=result,
        people=len(by_person),
        samples=sum(len(v) for v in by_person.values()),
        warnings=tuple(warnings),
    )
