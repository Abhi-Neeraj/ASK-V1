"""Context: is this sentence still about what was just being talked about, or has it moved on?

The check is a function, not a model. The sentence's vector is compared with the vectors of the three
sentences before it; if the closest of them is more similar than the threshold, the sentence is in the
same context, otherwise a new context starts. The sentence model only supplies the vectors.

**The threshold was measured, not assumed.** A figure of 0.6 was suggested for this check. On 37 steps
through four hand-labelled meetings (8 real changes of topic), the sentence model's similarities between
sentences about the same subject mostly sit between 0.35 and 0.5, and between different subjects below
0.25. So at 0.6 almost every sentence was called a new topic (24 percent right), while between 0.25 and
0.35 it was 84 to 92 percent right. 0.30 is the default. A small, single-author set: an indication, not
an accuracy figure. It is one setting (`ASK_CONTEXT_SIMILARITY`) if you want another value.

The words-and-letters engine cannot do this: its similarities between sentences on one subject are as
low as between sentences on different subjects (best F1 0.43). With that engine no contexts are worked
out unless a threshold is chosen on purpose, and no topic is claimed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.insights.embedding import Engine, Vector, cosine

# Each sentence is compared with this many sentences before it.
CONTEXT_WINDOW = 3
# Measured on the sentence model; see the module note. None: no contexts are worked out.
DEFAULT_THRESHOLD: dict[str, float | None] = {"semantic": 0.30, "lexical": None}


@dataclass(frozen=True)
class ContextLink:
    """Where one sentence sits: its context's number (0, 1, 2 ...), whether it opens a new context, and how
    similar it was to the closest of the sentences just before it (None for the very first)."""

    group: int
    new: bool
    similarity: float | None


def effective_threshold(engine: Engine, override: float | None) -> float | None:
    """The threshold to use: the one asked for, otherwise the measured default for this engine (or None)."""
    return override if override is not None else DEFAULT_THRESHOLD.get(engine)


def link_contexts(
    vectors: Sequence[Vector], threshold: float, window: int = CONTEXT_WINDOW
) -> list[ContextLink]:
    """For every sentence, compare it with the previous `window` sentences. Similarity above `threshold` to
    any
    of them keeps it in the context of the closest one; otherwise it starts a new context."""
    links: list[ContextLink] = []
    groups: list[int] = []
    for i, vector in enumerate(vectors):
        if i == 0:
            links.append(ContextLink(group=0, new=True, similarity=None))
            groups.append(0)
            continue
        start = max(0, i - window)
        scores = [cosine(vector, vectors[j]) for j in range(start, i)]
        best = max(range(len(scores)), key=scores.__getitem__)
        if scores[best] > threshold:
            group = groups[start + best]
            links.append(ContextLink(group=group, new=False, similarity=round(scores[best], 3)))
        else:
            group = max(groups) + 1
            links.append(ContextLink(group=group, new=True, similarity=round(scores[best], 3)))
        groups.append(group)
    return links
