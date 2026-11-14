"""THE DEVELOPMENT STUB. It does not listen to anything.

It exists so the whole pipeline (queue, states, live updates, transcript screens, export) can be run
on a machine with no speech models. Everything it writes is labelled as a placeholder, in the text
itself and in the database, and the screens repeat it.

It deliberately cannot do two things:
  * It produces no real words. Segments say that nothing was transcribed.
  * It has no speaker model, so no one is ever identified by name, and enrollment is unavailable.
    A fake "match" would be a fake identity, which is worse than a fake sentence.
"""

from __future__ import annotations

import wave
from pathlib import Path

from app.ai.types import AsrSegment, Provider, Transcription

STUB_MODEL = "placeholder (no speech model)"
STUB_VERSION = "1"
SEGMENT_MS = 4_000

NOTES = (
    "Placeholder mode: transcripts are not real. No speech model is configured.",
    "Speaker separation is unavailable, so every recording is treated as one speaker.",
    "Speaker matching is unavailable, so nobody is identified by voice.",
)


def _clock(milliseconds: int) -> str:
    seconds = milliseconds // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


class StubTranscriber:
    def transcribe(self, audio: Path) -> Transcription:
        with wave.open(str(audio), "rb") as handle:
            frames, rate = handle.getnframes(), handle.getframerate()
        duration_ms = int(frames * 1000 / rate) if rate else 0

        segments: list[AsrSegment] = []
        for number, start in enumerate(range(0, max(duration_ms, 1), SEGMENT_MS), start=1):
            end = min(start + SEGMENT_MS, duration_ms)
            if end <= start:
                break
            label = f"Placeholder {number}, {_clock(start)} to {_clock(end)}"
            segments.append(
                AsrSegment(start_ms=start, end_ms=end, text=f"[{label}: nothing was transcribed.]")
            )
        return Transcription(
            segments=tuple(segments), model=STUB_MODEL, model_version=STUB_VERSION, language=None
        )


def build_stub() -> Provider:
    return Provider(
        name="stub",
        placeholder=True,
        transcriber=StubTranscriber(),
        diarizer=None,
        embedder=None,
        notes=NOTES,
    )
