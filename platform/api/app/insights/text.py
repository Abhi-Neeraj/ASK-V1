"""Small text helpers: splitting a transcript into sentences, words, and the main topic words of a stretch."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence

_ABBREVIATIONS = frozenset({"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "no", "approx"})
_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")

STOPWORDS = frozenset(
    [
        "a",
        "about",
        "above",
        "after",
        "again",
        "all",
        "also",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "let",
        "lets",
        "like",
        "me",
        "more",
        "most",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
        "yes",
        "yeah",
        "okay",
        "ok",
        "well",
        "really",
        "actually",
        "maybe",
        "going",
        "get",
        "got",
        "need",
        "needs",
        "want",
        "thing",
        "things",
        "something",
        "anything",
        "everyone",
        "everybody",
        "someone",
        "somebody",
        "please",
        "thanks",
        "thank",
        "sure",
        "right",
        "good",
        "great",
        "one",
        "two",
        "three",
        "first",
    ]
)


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def tokens(text: str) -> list[str]:
    """Lower-case words, for comparing and counting."""
    return [w.lower() for w in words(text)]


def split_sentences(text: str) -> list[str]:
    """Sentences of one stretch of speech. Punctuation from the speech recognizer is trusted; an abbreviation
    such as "Dr." does not end a sentence."""
    flat = " ".join(text.split())
    if not flat:
        return []
    pieces = _BOUNDARY.split(flat)
    merged: list[str] = []
    for piece in pieces:
        if merged:
            last_word = merged[-1].rstrip(".").rsplit(" ", 1)[-1].lower()
            if merged[-1].endswith(".") and last_word in _ABBREVIATIONS:
                merged[-1] = f"{merged[-1]} {piece}"
                continue
        merged.append(piece)
    return [s.strip() for s in merged if words(s)]


def topic_words(texts: Sequence[str], limit: int = 3) -> list[str]:
    """The most repeated meaningful words across some sentences, most frequent first (ties: earliest
    first)."""
    counts: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    for text in texts:
        for token in tokens(text):
            if len(token) < 4 or token in STOPWORDS or token.isdigit():
                continue
            counts[token] += 1
            first_seen.setdefault(token, len(first_seen))
    ranked = sorted(counts, key=lambda t: (-counts[t], first_seen[t]))
    return ranked[:limit]


def shorten(text: str, limit: int) -> str:
    """`text` cut at a word boundary to at most `limit` characters, with an ellipsis if it was cut."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:")
    return f"{cut or flat[: limit - 1]}…"


def stem(word: str) -> str:
    """A rough stem, so "arrive", "arrives" and "arrived" count as one word. Only for comparing words."""
    w = word.lower()
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif (len(w) > 4 and w.endswith("ed")) or (
        len(w) > 4 and w.endswith(("sses", "ches", "shes", "xes", "zes"))
    ):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if len(w) > 4 and w.endswith("e"):
        w = w[:-1]
    return w
