"""A quick end-to-end check that each model in a provider actually loads and runs.

Used by `python -m app.cli ai-check --load`, so the first run of a new installation does not have to
be a real customer recording. It feeds a generated tone through each component. A tone is not speech,
so what comes back is not meaningful; what matters is that every model loads, runs and answers.
"""

from __future__ import annotations

import math
import struct
import tempfile
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.ai.types import Provider
from app.services.jobs import sanitize_error
from app.services.matching import cosine

TONE_SECONDS = 4
RATE = 16_000


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    seconds: float


def _write_tone(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        frames = bytearray()
        for n in range(RATE * TONE_SECONDS):
            frames += struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * n / RATE)))
        handle.writeframes(bytes(frames))


def _run(name: str, action: Callable[[], str]) -> Check:
    started = time.monotonic()
    try:
        detail = action()
        return Check(name, True, detail, time.monotonic() - started)
    except Exception as error:
        return Check(
            name, False, sanitize_error(f"{type(error).__name__}: {error}"), time.monotonic() - started
        )


def smoke_test(provider: Provider) -> list[Check]:
    """Run every component the provider has. Each is reported on its own; one failing stops nothing else."""
    with tempfile.TemporaryDirectory() as folder:
        tone = Path(folder) / "tone.wav"
        _write_tone(tone)
        checks: list[Check] = []

        def transcribe() -> str:
            result = provider.transcriber.transcribe(tone)
            return f"{result.model} {result.model_version}, {len(result.segments)} segment(s)"

        checks.append(_run("transcription", transcribe))

        if provider.diarizer is not None:
            diarizer = provider.diarizer

            def diarize() -> str:
                result = diarizer.diarize(tone)
                return f"{result.model} {result.model_version}, {len(result.turns)} turn(s)"

            checks.append(_run("speaker separation", diarize))

        if provider.embedder is not None:
            embedder = provider.embedder

            def embed() -> str:
                first = embedder.embed(tone)
                second = embedder.embed(tone, [(0, 2000), (2000, 4000)])
                same = cosine(first.vector, second.vector)
                if same < 0.9:
                    raise RuntimeError(
                        f"the same audio gave two different voice prints (similarity {same:.2f})"
                    )
                size = len(first.vector)
                return f"{first.model}, {size} numbers per voice print, repeatable (similarity {same:.2f})"

            checks.append(_run("speaker matching", embed))
        return checks
