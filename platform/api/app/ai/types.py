"""What the models hand back, and what the pipeline asks of them.

The pipeline depends only on these small interfaces, never on a particular library, so the real
models, the development stub and the test doubles are interchangeable. All times are milliseconds
from the start of the recording.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Word:
    start_ms: int
    end_ms: int
    # As the model wrote it, so punctuation stays attached ("Hello,") and a leading space may be present.
    text: str
    probability: float | None = None


@dataclass(frozen=True)
class AsrSegment:
    start_ms: int
    end_ms: int
    text: str
    words: tuple[Word, ...] = ()
    # 0..1 when the model reports one; None when it does not.
    confidence: float | None = None


@dataclass(frozen=True)
class Transcription:
    segments: tuple[AsrSegment, ...]
    model: str
    model_version: str
    language: str | None = None


@dataclass(frozen=True)
class Turn:
    """One stretch of one speaker. The label says "same voice", never "who"."""

    start_ms: int
    end_ms: int
    speaker: str


@dataclass(frozen=True)
class Diarization:
    turns: tuple[Turn, ...]
    model: str
    model_version: str


@dataclass(frozen=True)
class Embedding:
    vector: tuple[float, ...]
    # The weights that produced it. Vectors from different models cannot be compared.
    model: str
    model_version: str


class Transcriber(Protocol):
    def transcribe(self, audio: Path) -> Transcription: ...


class Diarizer(Protocol):
    def diarize(self, audio: Path) -> Diarization: ...


class SpeakerEmbedder(Protocol):
    @property
    def model(self) -> str:
        """The weights this embedder uses. Known without loading anything."""
        ...

    def embed(self, audio: Path, spans: Sequence[tuple[int, int]] | None = None) -> Embedding:
        """A voice print of the given stretches of audio joined together, or of all of it."""
        ...


@dataclass(frozen=True)
class Capabilities:
    """What a provider can really do, decided without loading any model.

    Each flag is independent: a missing speaker-separation token must not stop transcription, and a
    missing speaker model must not stop either. Every `False` has a reason in `notes`.
    """

    provider: str
    placeholder: bool
    transcription: bool
    diarization: bool
    speaker_matching: bool
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Provider:
    """The models a worker (or the enrollment endpoint) uses."""

    name: str
    placeholder: bool
    transcriber: Transcriber
    diarizer: Diarizer | None = None
    embedder: SpeakerEmbedder | None = None
    notes: tuple[str, ...] = field(default=())

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            provider=self.name,
            placeholder=self.placeholder,
            transcription=True,
            diarization=self.diarizer is not None,
            speaker_matching=self.embedder is not None,
            notes=self.notes,
        )


class ProviderUnavailableError(Exception):
    """The configured provider cannot start. The message says what to install or set."""


class PermanentError(Exception):
    """This input cannot be processed, and trying again will not change that (unusable audio, say).
    Anything else that goes wrong is assumed to be temporary and is retried."""
