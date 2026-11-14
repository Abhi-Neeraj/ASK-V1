"""Putting words and speakers together: who said each stretch of the transcript.

Speech recognition knows what was said and when; speaker separation knows who was talking and when.
Neither knows the other's result. This joins them by time, word by word where the recognizer gives
word times (so a speaker change in the middle of a sentence is caught), and otherwise per segment.

Anything it cannot place with confidence is marked `uncertain`, so a person reviews it instead of
trusting a guess. It never quietly changes who said something.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.ai.types import AsrSegment, Turn, Word

DEFAULT_SPEAKER = "SPEAKER_00"
# A word this far from any speaker turn has probably been put with the wrong one.
MAX_GAP_MS = 500
# A segment whose best speaker covers less than this share of it is a judgment call.
MIN_DOMINANT_SHARE = 0.7
# More than this share of shaky words makes a whole line shaky.
MAX_SHAKY_SHARE = 0.3

_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class AlignedSegment:
    start_ms: int
    end_ms: int
    speaker: str
    text: str
    confidence: float | None
    uncertain: bool


@dataclass(frozen=True)
class _Placed:
    word: Word
    speaker: str
    shaky: bool
    # Moved to the surrounding speaker to tidy a ragged turn edge. A person should confirm it.
    folded: bool = False


def _clean(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def _join_words(words: Sequence[Word]) -> str:
    """Models differ: some put the spacing inside each word (" Hello"), some do not."""
    if len(words) > 1 and not any(word.text[:1].isspace() for word in words[1:]):
        return _clean(" ".join(word.text for word in words))
    return _clean("".join(word.text for word in words))


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _speaker_at(turns: Sequence[Turn], moment: int) -> tuple[str, bool]:
    """The speaker at this moment, and whether that is a shaky call (overlap, or nobody was speaking)."""
    covering = [t for t in turns if t.start_ms <= moment < t.end_ms]
    if covering:
        speakers = {t.speaker for t in covering}
        # When people talk over each other, the one who started most recently is the likelier source.
        chosen = max(covering, key=lambda t: (t.start_ms, t.end_ms))
        return chosen.speaker, len(speakers) > 1
    nearest = min(turns, key=lambda t: min(abs(moment - t.start_ms), abs(moment - t.end_ms)))
    gap = min(abs(moment - nearest.start_ms), abs(moment - nearest.end_ms))
    return nearest.speaker, gap > MAX_GAP_MS


def _runs(placed: Sequence[_Placed]) -> list[list[_Placed]]:
    runs: list[list[_Placed]] = []
    for item in placed:
        if runs and runs[-1][-1].speaker == item.speaker:
            runs[-1].append(item)
        else:
            runs.append([item])
    return runs


def _fold_boundary_wobble(runs: list[list[_Placed]]) -> list[list[_Placed]]:
    """A single word between two stretches by the same speaker is almost always a ragged turn edge.

    It is folded into them, but every word moved is marked shaky, so the line gets reviewed. Short runs
    anywhere else are left alone: "Yes." from someone else is a real reply and must stay theirs.
    """
    if len(runs) < 3:
        return runs
    folded: list[list[_Placed]] = [runs[0]]
    index = 1
    while index < len(runs):
        current = runs[index]
        following = runs[index + 1] if index + 1 < len(runs) else None
        if following is not None and len(current) == 1 and folded[-1][0].speaker == following[0].speaker:
            speaker = folded[-1][0].speaker
            moved = _Placed(current[0].word, speaker, shaky=True, folded=True)
            folded[-1] = [*folded[-1], moved, *following]
            index += 2
        else:
            folded.append(current)
            index += 1
    return folded


def _align_by_words(segment: AsrSegment, turns: Sequence[Turn]) -> list[AlignedSegment]:
    placed = [
        _Placed(word, *_speaker_at(turns, (word.start_ms + word.end_ms) // 2))
        for word in segment.words
        if word.text.strip()
    ]
    aligned: list[AlignedSegment] = []
    for run in _fold_boundary_wobble(_runs(placed)):
        words = [item.word for item in run]
        shaky_share = sum(1 for item in run if item.shaky) / len(run)
        aligned.append(
            AlignedSegment(
                start_ms=words[0].start_ms,
                end_ms=max(words[-1].end_ms, words[0].start_ms),
                speaker=run[0].speaker,
                text=_join_words(words),
                confidence=_mean([w.probability for w in words if w.probability is not None]),
                uncertain=shaky_share > MAX_SHAKY_SHARE or any(item.folded for item in run),
            )
        )
    return aligned


def _align_whole(segment: AsrSegment, turns: Sequence[Turn]) -> AlignedSegment:
    overlap: dict[str, int] = {}
    for turn in turns:
        shared = min(segment.end_ms, turn.end_ms) - max(segment.start_ms, turn.start_ms)
        if shared > 0:
            overlap[turn.speaker] = overlap.get(turn.speaker, 0) + shared
    length = max(segment.end_ms - segment.start_ms, 1)
    if overlap:
        speaker = max(overlap, key=lambda name: overlap[name])
        covered = sum(overlap.values())
        uncertain = overlap[speaker] / covered < MIN_DOMINANT_SHARE or covered < length * 0.5
    else:
        speaker, uncertain = _speaker_at(turns, (segment.start_ms + segment.end_ms) // 2)[0], True
    return AlignedSegment(
        start_ms=segment.start_ms,
        end_ms=max(segment.end_ms, segment.start_ms),
        speaker=speaker,
        text=_clean(segment.text),
        confidence=segment.confidence,
        uncertain=uncertain,
    )


def align(segments: Sequence[AsrSegment], turns: Sequence[Turn]) -> list[AlignedSegment]:
    """One transcript line per stretch of one speaker, in time order.

    With no speaker turns at all (separation unavailable) everything is one speaker and nothing is
    called uncertain: the transcript says it was not separated, which is the honest statement.
    """
    result: list[AlignedSegment] = []
    for segment in sorted(segments, key=lambda s: (s.start_ms, s.end_ms)):
        if not _clean(segment.text):
            continue
        if not turns:
            result.append(
                AlignedSegment(
                    start_ms=segment.start_ms,
                    end_ms=max(segment.end_ms, segment.start_ms),
                    speaker=DEFAULT_SPEAKER,
                    text=_clean(segment.text),
                    confidence=segment.confidence,
                    uncertain=False,
                )
            )
        elif any(w.text.strip() for w in segment.words):
            result.extend(_align_by_words(segment, turns))
        else:
            result.append(_align_whole(segment, turns))
    return result
