"""Analysing a whole recording: for every sentence, what was said, its context, the key activity, the
task that follows from it and who it falls to. The result is plain data (JSON-friendly) that is stored
with the transcript and turned into a written summary when read (see `summary`).

The split of work: the sentence model, when there is one, only turns sentences into vectors (meaning).
Everything else is a function: the context check (`context`), the activity (`functions`: cue words and
verbs, plus the meaning), the task, owner and deadline (`extract`), and the summary text (`summary`)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from app.insights import extract
from app.insights.context import ContextLink, effective_threshold, link_contexts
from app.insights.functions import (
    ACTION,
    ACTIVITIES,
    COMMITMENT,
    DECISION,
    NOTE,
    QUESTION,
    REQUIREMENT,
    Classification,
    Classifier,
)
from app.insights.text import shorten, split_sentences, topic_words

ANALYSIS_VERSION = 1
# More than this many sentences in one recording are not analysed (the rest are left out, and it says so).
MAX_SENTENCES = 2000
_CONTEXT_CHARS = 160
_TASK_ACTIVITIES = (ACTION, COMMITMENT, REQUIREMENT, DECISION)


@dataclass(frozen=True)
class Utterance:
    """One transcript segment: who said it, when, and the words."""

    index: int
    start_ms: int
    end_ms: int
    speaker: str
    text: str


@dataclass(frozen=True)
class _Sentence:
    segment: int
    start_ms: int
    end_ms: int
    speaker: str
    text: str


def _sentences(utterances: Sequence[Utterance]) -> tuple[list[_Sentence], bool]:
    out: list[_Sentence] = []
    for u in utterances:
        parts = split_sentences(u.text)
        for position, part in enumerate(parts):
            # A segment's time is shared out between its sentences by where each one starts in the text.
            span = max(0, u.end_ms - u.start_ms)
            before = sum(len(p) for p in parts[:position])
            total = max(1, sum(len(p) for p in parts))
            start = u.start_ms + span * before // total
            end = u.start_ms + span * (before + len(part)) // total
            out.append(_Sentence(u.index, start, end, u.speaker, part))
            if len(out) >= MAX_SENTENCES:
                return out, True
    return out, False


def analyze(
    utterances: Sequence[Utterance],
    voices: Sequence[dict[str, Any]],
    classifier: Classifier,
    known_names: Collection[str] = (),
    context_similarity: float | None = None,
) -> dict[str, Any]:
    """The stored analysis of one transcript.

    `voices` are the per-speaker measurements made from the audio (pitch, talk time, how they were matched);
    they are passed through unchanged. `known_names` are given names of people in the company, so a spoken
    name is recognised as a name even where it could be an ordinary word. `context_similarity` overrides the
    engine's measured threshold for the context check (see `context`).
    """
    sentences, truncated = _sentences(utterances)
    texts = [s.text for s in sentences]
    # One pass through the model serves both the activity and the context check.
    vectors = classifier.embedder.embed(texts) if texts else []
    classes = classifier.classify_many(texts, vectors)
    names = frozenset(n for n in known_names if n)

    engine = classifier.embedder.engine
    threshold = effective_threshold(engine, context_similarity)
    links: list[ContextLink | None] = (
        list(link_contexts(vectors, threshold))
        if threshold is not None and vectors
        else [None] * len(sentences)
    )

    items: list[dict[str, Any]] = []
    for position, (sentence, cls, link) in enumerate(zip(sentences, classes, links, strict=True)):
        items.append(_item(position, sentence, cls, link, sentences, names))

    counts = dict.fromkeys((*ACTIVITIES, NOTE), 0)
    for item in items:
        counts[item["activity"]] += 1
    return {
        "version": ANALYSIS_VERSION,
        "engine": engine,
        "voices": list(voices),
        "items": items,
        "counts": counts,
        # Empty when no contexts were worked out (the words engine, unless a threshold was chosen).
        "contexts": _contexts(sentences, links),
        "contextThreshold": threshold,
        "truncated": truncated,
    }


def _contexts(sentences: Sequence[_Sentence], links: Sequence[ContextLink | None]) -> list[dict[str, Any]]:
    """One entry per context: where it began and ended, how many sentences, and its main words."""
    grouped: dict[int, list[_Sentence]] = {}
    for sentence, link in zip(sentences, links, strict=True):
        if link is not None:
            grouped.setdefault(link.group, []).append(sentence)
    return [
        {
            "group": group,
            "startMs": members[0].start_ms,
            "endMs": members[-1].end_ms,
            "sentences": len(members),
            "keywords": topic_words([m.text for m in members], 3),
        }
        for group, members in sorted(grouped.items())
    ]


def _item(
    position: int,
    sentence: _Sentence,
    cls: Classification,
    link: ContextLink | None,
    all_sentences: Sequence[_Sentence],
    names: frozenset[str],
) -> dict[str, Any]:
    before = all_sentences[max(0, position - 4) : position]
    after = all_sentences[position + 1 : position + 4]
    previous = before[-1] if before else None
    previous_other = next((s.speaker for s in reversed(before) if s.speaker != sentence.speaker), None)

    window = [s.text for s in before[-3:]] + [sentence.text]
    context = {
        "previous": shorten(previous.text, _CONTEXT_CHARS) if previous else None,
        "previousSpeaker": previous.speaker if previous else None,
        "topic": topic_words(window),
        # Which context this sentence is in, whether it opens a new one, and how close it was to the sentences
        # just before it. None when contexts were not worked out.
        "group": link.group if link else None,
        "newContext": link.new if link else None,
        "similarity": link.similarity if link else None,
    }

    activity = cls.activity
    task = owner = due = None
    if activity in _TASK_ACTIVITIES:
        due = extract.find_due(sentence.text)
        task = extract.find_task(sentence.text, activity, due)
        owner = extract.find_owner(sentence.text, activity, sentence.speaker, previous_other, names)

    open_question: bool | None = None
    if activity == QUESTION:
        open_question = extract.is_open_question(
            sentence.text, sentence.speaker, [(s.speaker, s.text) for s in after]
        )

    return {
        "segmentIndex": sentence.segment,
        "startMs": sentence.start_ms,
        "endMs": sentence.end_ms,
        "speaker": sentence.speaker,
        "what": sentence.text,
        "context": context,
        "activity": activity,
        "secondary": list(cls.secondary),
        "confidence": round(max(0.0, min(1.0, cls.score)), 3),
        "task": task,
        "owner": owner,
        "due": due,
        # An activity together with a date or time is a task with a deadline.
        "dated": activity in _TASK_ACTIVITIES and due is not None,
        # The action verb and the main words of the sentence.
        "verb": extract.find_verb(sentence.text, task) if activity in _TASK_ACTIVITIES else None,
        "keywords": topic_words([sentence.text], 3),
        "open": open_question,
    }
