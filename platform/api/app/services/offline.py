"""Run the processing pipeline on one WAV file by hand, outside the database.

    python -m app.cli process-file recording.wav [--voices folder] [--json]

This is the quickest honest answer to "do the real models work here, and how long do they take?": it runs
exactly the stages a worker runs (validate, transcribe, separate speakers, match voices, align) with the
configured provider, and shows what came out and how long each stage took. It writes nothing to the
database or to storage, and the audio never leaves this machine.

`--voices` is a folder with one sub-folder of WAV samples per person. Their voices are enrolled in memory
for the run only (nothing is saved), so naming a speaker can be tried before anyone is enrolled for real.
That is a demonstration of the matching rules, not a person's consent or enrollment.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from app.ai.types import Provider, SpeakerEmbedder, Turn
from app.config import Settings
from app.services.alignment import align
from app.services.audio import inspect_wav
from app.services.matching import Candidate, mean_voiceprint
from app.services.pipeline import Identity, identify_speakers

T = TypeVar("T")
_NAMESPACE = uuid.UUID("5f0c6e0e-0000-4000-8000-00000000a5c1")


@dataclass(frozen=True)
class Stage:
    name: str
    seconds: float


@dataclass(frozen=True)
class Line:
    start_ms: int
    end_ms: int
    speaker: str
    person: str | None  # a name only if a voice match cleared the threshold and the margin
    score: float | None
    text: str
    uncertain: bool


@dataclass
class FileResult:
    audio_seconds: float
    provider: str
    placeholder: bool
    language: str | None
    transcription_model: str
    diarization_model: str | None
    lines: list[Line] = field(default_factory=list)
    stages: list[Stage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return sum(stage.seconds for stage in self.stages)


def _enroll(embedder: SpeakerEmbedder, folder: Path) -> tuple[list[Candidate], dict[uuid.UUID, str]]:
    """A voice print for every person-folder, held in memory for this run only."""
    candidates: list[Candidate] = []
    names: dict[uuid.UUID, str] = {}
    for person in sorted(p for p in folder.iterdir() if p.is_dir()):
        samples = sorted(person.glob("*.wav"))
        if not samples:
            continue
        vectors = [embedder.embed(sample).vector for sample in samples]
        person_id = uuid.uuid5(_NAMESPACE, person.name)
        candidates.append(Candidate(person_id, mean_voiceprint(vectors)))
        names[person_id] = person.name
    return candidates, names


def process_file(
    provider: Provider, settings: Settings, audio: Path, voices: Path | None = None
) -> FileResult:
    """The stages of a worker, on one file. Raises what the pipeline would: a file that is not usable audio
    raises ApiError, a model that cannot run raises the provider's own error."""
    stages: list[Stage] = []

    def timed(name: str, work: Callable[[], T]) -> T:
        started = time.monotonic()
        value = work()
        stages.append(Stage(name, time.monotonic() - started))
        return value

    info = timed("validating", lambda: inspect_wav(audio, settings))
    candidates: list[Candidate] = []
    names: dict[uuid.UUID, str] = {}
    if voices is not None and provider.embedder is not None:
        enrolling = provider.embedder
        candidates, names = timed(
            "enrolling samples (not part of a normal run)", lambda: _enroll(enrolling, voices)
        )

    transcription = timed("transcribing", lambda: provider.transcriber.transcribe(audio))
    turns: list[Turn] = []
    diarization = None
    if provider.diarizer is not None:
        diarizer = provider.diarizer
        diarization = timed("separating speakers", lambda: diarizer.diarize(audio))
        turns = list(diarization.turns)

    identities: dict[str, Identity] = {}
    if provider.embedder is not None and turns and candidates:
        embedder = provider.embedder
        identities = timed(
            "matching voices", lambda: identify_speakers(settings, embedder, audio, turns, candidates)
        )
    aligned = timed("aligning", lambda: align(transcription.segments, turns))

    result = FileResult(
        audio_seconds=info.duration_ms / 1000,
        provider=provider.name,
        placeholder=provider.placeholder,
        language=transcription.language,
        transcription_model=f"{transcription.model} {transcription.model_version}",
        diarization_model=(f"{diarization.model} {diarization.model_version}" if diarization else None),
        stages=stages,
        notes=list(provider.notes),
    )
    if diarization is None:
        result.notes.append("Speakers were not separated: every line is one speaker.")
    for segment in aligned:
        who = identities.get(segment.speaker)
        result.lines.append(
            Line(
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                speaker=segment.speaker,
                person=names.get(who.person_id) if who else None,
                score=who.score if who else None,
                text=segment.text,
                uncertain=segment.uncertain,
            )
        )
    return result


def clock(ms: int) -> str:
    seconds, millis = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def render(result: FileResult, lines: Sequence[Line] | None = None) -> str:
    """The result as text for a terminal."""
    out = [
        f"provider: {result.provider}"
        + ("  (PLACEHOLDER: this text is not a real transcript)" if result.placeholder else ""),
        f"audio:    {result.audio_seconds:.1f} s   language: {result.language or 'unknown'}",
        f"models:   {result.transcription_model}"
        + (f"; {result.diarization_model}" if result.diarization_model else ""),
        "",
    ]
    for line in lines if lines is not None else result.lines:
        who = f"{line.person} ({line.score:.2f})" if line.person else "Unknown speaker"
        flag = "  [check]" if line.uncertain else ""
        span = f"{clock(line.start_ms)}-{clock(line.end_ms)}"
        out.append(f"{span} | {who:<22} | {line.speaker:<10} | {line.text}{flag}")
    if not result.lines:
        out.append("(no speech was found)")
    out.append("")
    out.extend(f"  {stage.name:<44} {stage.seconds:6.2f} s" for stage in result.stages)
    out.append(f"  {'total':<44} {result.total_seconds:6.2f} s")
    out.extend(f"note: {note}" for note in result.notes)
    out.append("A voice match is not proof of identity. Names appear only above the configured threshold.")
    return "\n".join(out)
