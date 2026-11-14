"""Stand-ins for the speech models, so the pipeline's own behaviour can be tested exactly.

These are test doubles, not the development stub: they return whatever the test says, so a test can
ask "what if the recognizer fails twice", "what if two people are talking", or "what if this voice is
nearly identical to two enrolled ones".
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from app.ai.types import (
    AsrSegment,
    Diarization,
    Embedding,
    Provider,
    Transcription,
    Turn,
    Word,
)

TEST_ASR = "test-asr"
TEST_VOICE_MODEL = "test-voice"


class FakeTranscriber:
    def __init__(
        self,
        segments: Sequence[AsrSegment] | None = None,
        *,
        language: str | None = "en",
        before: Callable[[Path], None] | None = None,
    ) -> None:
        self.segments = tuple(segments) if segments is not None else default_segments()
        self.language = language
        self.before = before
        self.calls: list[Path] = []

    def transcribe(self, audio: Path) -> Transcription:
        self.calls.append(audio)
        if self.before:
            self.before(audio)
        return Transcription(self.segments, model=TEST_ASR, model_version="1.0", language=self.language)


class FakeDiarizer:
    def __init__(self, turns: Sequence[Turn]) -> None:
        self.turns = tuple(turns)
        self.calls = 0

    def diarize(self, audio: Path) -> Diarization:
        self.calls += 1
        return Diarization(self.turns, model="test-diarizer", model_version="2.0")


class FakeEmbedder:
    """Returns a chosen voice print for each speaker, picked by where their speech starts."""

    def __init__(self, by_start_ms: dict[int, Sequence[float]], *, model: str = TEST_VOICE_MODEL) -> None:
        self._by_start = {start: tuple(vector) for start, vector in by_start_ms.items()}
        self._model = model
        self.calls: list[tuple[tuple[int, int], ...]] = []

    @property
    def model(self) -> str:
        return self._model

    def embed(self, audio: Path, spans: Sequence[tuple[int, int]] | None = None) -> Embedding:
        chosen = tuple(spans or ())
        self.calls.append(chosen)
        key = chosen[0][0] if chosen else 0
        return Embedding(vector=self._by_start[key], model=self._model, model_version="1.0")


class SequenceEmbedder:
    """Hands out a chosen voice print for each sample in turn, or fails, for enrollment tests."""

    def __init__(
        self,
        vectors: Sequence[Sequence[float]] = ((1.0, 0.0, 0.0),),
        *,
        model: str = TEST_VOICE_MODEL,
        error: Exception | None = None,
    ) -> None:
        self._vectors = [tuple(v) for v in vectors]
        self._model = model
        self._error = error
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    def embed(self, audio: Path, spans: Sequence[tuple[int, int]] | None = None) -> Embedding:
        self.calls += 1
        if self._error is not None:
            raise self._error
        vector = self._vectors[min(self.calls - 1, len(self._vectors) - 1)]
        return Embedding(vector=vector, model=self._model, model_version="1.0")


class FileEmbedder:
    """A voice print per recording, chosen by file name, for calibration tests."""

    def __init__(self, by_stem: dict[str, Sequence[float]], model: str = TEST_VOICE_MODEL) -> None:
        self._by_stem = {k: tuple(v) for k, v in by_stem.items()}
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def embed(self, audio: Path, spans: Sequence[tuple[int, int]] | None = None) -> Embedding:
        return Embedding(vector=self._by_stem[audio.stem], model=self._model, model_version="1.0")


def default_segments() -> tuple[AsrSegment, ...]:
    """Two people taking turns, with word times, in a clip at least 8 s long."""
    first = (
        Word(500, 1000, " Good", 0.95),
        Word(1000, 1500, " morning", 0.95),
        Word(1500, 2200, " everyone.", 0.9),
    )
    second = (
        Word(4200, 4600, " Morning,", 0.9),
        Word(4600, 5000, " shall", 0.9),
        Word(5000, 5400, " we", 0.92),
        Word(5400, 6000, " begin?", 0.9),
    )
    return (
        AsrSegment(500, 2200, "Good morning everyone.", first, 0.93),
        AsrSegment(4200, 6000, "Morning, shall we begin?", second, 0.91),
    )


def two_speaker_turns() -> tuple[Turn, ...]:
    return (Turn(0, 3500, "SPEAKER_00"), Turn(3500, 8000, "SPEAKER_01"))


def make_provider(
    *,
    transcriber: FakeTranscriber | None = None,
    diarizer: FakeDiarizer | None = None,
    embedder: FakeEmbedder | None = None,
    placeholder: bool = False,
) -> Provider:
    return Provider(
        name="test",
        placeholder=placeholder,
        transcriber=transcriber or FakeTranscriber(),
        diarizer=diarizer,
        embedder=embedder,
    )
