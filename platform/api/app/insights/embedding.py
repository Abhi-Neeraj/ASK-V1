"""Turning sentences into vectors whose closeness means closeness of meaning, with two interchangeable
engines.

* **semantic**: a sentence-embedding model (a "sentence transformer") run on this machine. It understands
  that "I'll take care of it" and "Leave it with me" mean much the same thing. It needs the
  `sentence-transformers` package and the model's weights. The weights are NEVER downloaded unless
  `ASK_AI_TEXT_ALLOW_DOWNLOAD=1` (or they were put in the model folder by hand): nothing is fetched from
  the internet without being asked.
* **lexical**: a fingerprint of the words and letter groups in a sentence. It needs nothing installed, runs
  anywhere and gives the same answer every time, but it only notices shared words, not shared meaning.

Everything downstream is written against `TextEmbedder`, so the engine can change without touching the rules.
The engine that was used is stored with every analysis, and the screens say which one it was (without naming a
product).
"""

from __future__ import annotations

import importlib.util
import itertools
import logging
import math
import threading
import zlib
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from app.config import Settings
from app.insights.text import tokens

logger = logging.getLogger("ask.insights")

Vector = tuple[float, ...]
Engine = Literal["semantic", "lexical"]


class TextEmbedder(Protocol):
    engine: Engine
    # What it is, for developers and logs. Never shown to visitors.
    name: str

    def embed(self, texts: Sequence[str]) -> list[Vector]: ...


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    length = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return max(-1.0, min(1.0, dot / length)) if length else 0.0


class HashingEmbedder:
    """The words-and-letters fingerprint. Words (lightly trimmed so "sending" and "send" agree), pairs of
    words, and groups of three letters are each hashed to a place in a 768-number vector, then the vector is
    scaled to length 1. Deterministic, no data files, no downloads."""

    engine: Engine = "lexical"
    name = "word and letter-group fingerprint"
    DIMENSIONS = 768

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        return [self._one(text) for text in texts]

    @staticmethod
    def _trim(word: str) -> str:
        for suffix in ("ing", "ed", "es", "s"):
            if len(word) > len(suffix) + 3 and word.endswith(suffix):
                return word[: -len(suffix)]
        return word

    def _one(self, text: str) -> Vector:
        trimmed = [self._trim(t) for t in tokens(text)]
        features: list[tuple[str, float]] = [(f"w:{t}", 1.0) for t in trimmed]
        features += [(f"b:{a}_{b}", 0.7) for a, b in itertools.pairwise(trimmed)]
        for t in trimmed:
            padded = f"^{t}$"
            features += [(f"c:{padded[i : i + 3]}", 0.25) for i in range(len(padded) - 2)]
        vector = [0.0] * self.DIMENSIONS
        for feature, weight in features:
            h = zlib.crc32(feature.encode("utf-8"))
            vector[h % self.DIMENSIONS] += weight if (h >> 31) & 1 else -weight
        length = math.sqrt(sum(v * v for v in vector))
        return tuple(v / length for v in vector) if length else tuple(vector)


class SentenceTransformerEmbedder:
    """A sentence-embedding model run on this machine (CPU). See the module note on downloads."""

    engine: Engine = "semantic"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.name = settings.ai_text_model
        self._model: Any | None = None
        self._lock = threading.Lock()

    def load(self) -> None:
        from sentence_transformers import SentenceTransformer

        only_local = self._settings.ai_offline or not self._settings.ai_text_allow_download
        directory = str(self._settings.ai_model_dir) if self._settings.ai_model_dir else None
        self._model = SentenceTransformer(
            self._settings.ai_text_model,
            device="cpu",
            cache_folder=directory,
            local_files_only=only_local,
        )

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        if self._model is None:
            self.load()
        model = self._model
        if model is None:  # load() sets it, so this cannot happen
            raise EmbedderUnavailableError("The sentence model is not loaded.")
        with self._lock:  # models are not safe to share between threads
            vectors = model.encode(
                list(texts), normalize_embeddings=True, show_progress_bar=False, batch_size=32
            )
        return [tuple(float(x) for x in row) for row in vectors]


class EmbedderUnavailableError(Exception):
    pass


def semantic_installed() -> bool:
    try:
        return importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):
        return False


def describe_engine(settings: Settings) -> tuple[Engine, str]:
    """Which engine would run, and why, without loading anything."""
    if settings.analysis_engine == "lexical":
        return "lexical", "The lexical engine was chosen (ASK_ANALYSIS_ENGINE=lexical)."
    if not semantic_installed():
        return (
            "lexical",
            "The sentence model is not installed (pip install sentence-transformers), so sentences are "
            "matched by words, not by meaning.",
        )
    return "semantic", "The sentence model is installed; it runs if its weights are present."


def build_embedder(settings: Settings) -> TextEmbedder:
    """The embedder to use. `auto` tries the semantic engine and quietly falls back to the lexical one if it
    cannot run; `semantic` insists and raises instead."""
    if settings.analysis_engine == "lexical":
        return HashingEmbedder()
    if not semantic_installed():
        if settings.analysis_engine == "semantic":
            raise EmbedderUnavailableError(
                "ASK_ANALYSIS_ENGINE=semantic needs the sentence-transformers package."
            )
        return HashingEmbedder()
    try:
        embedder = SentenceTransformerEmbedder(settings)
        embedder.load()
    except Exception as error:
        if settings.analysis_engine == "semantic":
            raise EmbedderUnavailableError(
                "The sentence model could not be loaded. If its weights are not on this machine, "
                "put them in the model folder or allow the download (ASK_AI_TEXT_ALLOW_DOWNLOAD=1)."
            ) from error
        logger.warning(
            "the sentence model could not be loaded (%s); using the lexical engine", type(error).__name__
        )
        return HashingEmbedder()
    return embedder
