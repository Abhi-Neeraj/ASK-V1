"""Measuring each speaker of one recording: how long they spoke, their pitch, and their voice signature.

Input is the recording's audio and, for each speaker label, the stretches (start and end in milliseconds) that
speaker spoke. Output is what the pipeline needs both to name speakers (signature matching) and to describe
them (pitch and talk time on the recording page).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.voiceid.audio_io import Audio, slice_spans
from app.voiceid.pitch import PitchProfile, estimate_pitch
from app.voiceid.signature import VoiceSignature, compute_signature

# The most speech measured per speaker. More does not make the measurement better and costs time.
MAX_MEASURED_SECONDS = 60


@dataclass(frozen=True)
class SpeakerVoice:
    label: str
    talk_ms: int
    turns: int
    pitch: PitchProfile | None
    # Only made when the speaker spoke long enough to judge a voice by; None otherwise. Never leaves the
    # server.
    signature: VoiceSignature | None


def longest_spans(spans: Sequence[tuple[int, int]], budget_ms: int) -> list[tuple[int, int]]:
    """The longest stretches first until the budget is used, put back in time order."""
    chosen: list[tuple[int, int]] = []
    used = 0
    for start, end in sorted(spans, key=lambda s: s[1] - s[0], reverse=True):
        if used >= budget_ms:
            break
        take = min(end - start, budget_ms - used)
        chosen.append((start, start + take))
        used += take
    return sorted(chosen)


def measure_speakers(
    audio: Audio,
    spans_by_speaker: Mapping[str, Sequence[tuple[int, int]]],
    *,
    min_speech_seconds: float,
) -> dict[str, SpeakerVoice]:
    """Pitch for every speaker with enough voiced speech, and a signature for those who spoke at least
    `min_speech_seconds`. Speakers with nothing to measure still appear, with no pitch."""
    voices: dict[str, SpeakerVoice] = {}
    for label, spans in spans_by_speaker.items():
        talk_ms = sum(max(0, end - start) for start, end in spans)
        pieces = slice_spans(audio, longest_spans(spans, MAX_MEASURED_SECONDS * 1000))
        signature = (
            compute_signature(pieces, audio.rate) if talk_ms >= min_speech_seconds * 1000 and pieces else None
        )
        pitch = (
            signature.pitch
            if signature is not None
            else (estimate_pitch(pieces, audio.rate) if pieces else None)
        )
        voices[label] = SpeakerVoice(
            label=label, talk_ms=talk_ms, turns=len(spans), pitch=pitch, signature=signature
        )
    return voices
