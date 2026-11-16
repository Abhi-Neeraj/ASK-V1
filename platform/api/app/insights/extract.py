"""Pulling the parts of a sentence that matter: the task, who owns it, when it is due.

All fixed patterns, so every result can be explained by pointing at the words it came from. Owners are
recorded as one of:

* `{"kind": "speaker", "label": "SPEAKER_01"}`: the person who said it ("I will send the report") or the person
  who was spoken to ("Could you send the report?" answered by whoever spoke before);
* `{"kind": "name", "text": "Ravi"}`: a name that was spoken ("Ravi, please book the room");
* `{"kind": "team"}`: everyone present ("let's meet at nine");
* nothing: nobody was named ("someone should write it up").

A speaker is recorded by label, never by name: who a label is comes from the transcript when it is read, so a
voice that stops being recognised (withdrawn consent) stops being named here too.
"""

from __future__ import annotations

import re
from typing import Any

from app.insights.functions import ACTION, COMMITMENT, DECISION, REQUIREMENT
from app.insights.text import STOPWORDS, shorten, split_sentences, stem, tokens

Owner = dict[str, Any]

# Capitalised words that are not names when they open a sentence or sit before "will".
_NOT_NAMES = frozenset(
    [
        "a",
        "all",
        "also",
        "and",
        "any",
        "anyone",
        "anybody",
        "are",
        "because",
        "but",
        "can",
        "could",
        "do",
        "does",
        "each",
        "everyone",
        "everybody",
        "for",
        "from",
        "good",
        "great",
        "he",
        "she",
        "him",
        "her",
        "his",
        "hello",
        "hey",
        "hi",
        "how",
        "i",
        "if",
        "it",
        "its",
        "let",
        "lets",
        "look",
        "maybe",
        "no",
        "nobody",
        "none",
        "not",
        "now",
        "ok",
        "okay",
        "once",
        "only",
        "or",
        "our",
        "please",
        "right",
        "see",
        "so",
        "some",
        "someone",
        "somebody",
        "sorry",
        "sure",
        "thanks",
        "thank",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "today",
        "tomorrow",
        "tonight",
        "we",
        "well",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "would",
        "yes",
        "yeah",
        "yep",
        "you",
        "your",
        "everything",
        "nothing",
        "something",
        "anything",
        "everyone's",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ]
)
_NAME = r"[A-Z][a-z]{1,14}"

_VOCATIVE = re.compile(rf"^(?:(?:ok(?:ay)?|so|well|right|hey|and)[, ]+)?({_NAME})[,:]\s+\S")
_NAME_THEN_MODAL = re.compile(
    rf"\b({_NAME})\s+(?:will|can|could|should|needs? to|has to|is going to|is to|to (?:take|handle|send|prepare|check|call|book))\b"
)
_ASSIGN_TO = re.compile(rf"\b(?:assign(?:ed)?|delegate[ds]?|give|hand)\b[^.?!]{{0,25}}?\bto\s+({_NAME})\b")
_UNASSIGNED = re.compile(r"\b(?:someone|somebody|anyone|anybody|nobody has)\b", re.IGNORECASE)
_I_WILL = re.compile(
    r"\b(?:i will|i'll|i shall|i am going to|i'm going to|i can|i could|leave it with me|i've got it|i have got it)\b",
    re.IGNORECASE,
)
_WE_WILL = re.compile(r"\b(?:we will|we'll|we are going to|we're going to|we shall)\b", re.IGNORECASE)
_YOU = re.compile(
    r"\b(?:could you|can you|would you|will you|you (?:should|need to|must|can|could)|please)\b",
    re.IGNORECASE,
)
_TEAM = re.compile(r"\b(?:we|let's|let us|our team|everyone|everybody|all of us|team)\b", re.IGNORECASE)

_FILLER_START = re.compile(
    r"^(?:(?:ok(?:ay)?|so|well|right|yes|yeah|yep|and|but|then|also|thanks|thank you|sure)[,.!]?\s+)+",
    re.IGNORECASE,
)

_DAY = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_MONTH = r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
_COUNT = r"(?:\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten|couple of|few)"
_WHEN = (
    rf"(?:end of (?:the )?(?:day|week|month|quarter|year)|{_DAY}(?: (?:morning|afternoon|evening))?|tomorrow(?: (?:morning|afternoon|evening))?"
    rf"|today|tonight|next (?:week|month|quarter|year|{_DAY})|this (?:week|month|afternoon|evening|morning|quarter)"
    rf"|{_COUNT} (?:minutes?|hours?|days?|weeks?|months?)(?: from now)?"
    rf"|{_MONTH}(?: \d{{1,2}}(?:st|nd|rd|th)?)?|\d{{1,2}}(?:st|nd|rd|th)?(?: of)? {_MONTH}"
    rf"|(?:the )?(?:first|second|third|last) (?:of the month|week of \w+)|noon|midnight|(?:\d{{1,2}}(?::\d{{2}})? ?(?:am|pm)))"
)
_DUE = re.compile(
    rf"\b((?:(?:by|before|until|on|within|in|at|no later than|from)\s+(?:the\s+)?)?{_WHEN}(?:\s+(?:morning|afternoon|evening))?(?:\s+(?:tomorrow|today))?"
    r"|asap|as soon as possible|right away|immediately|first thing)\b(?!['’]s)",
    re.IGNORECASE,
)
_DUE_NEEDS_ANCHOR = re.compile(r"^(?:today|tonight|tomorrow|noon|midnight)", re.IGNORECASE)

_TASK_LEAD: dict[str, re.Pattern[str]] = {
    COMMITMENT: re.compile(
        r"\b(?:i will|i'll|i shall|i am going to|i'm going to|i can|i could|we will|we'll|we are going to|we're going to|we shall"
        r"|[\w']+ (?:will|shall|is going to|are going to))\s+(?:also |just |then |go ahead and |definitely )?",
        re.IGNORECASE,
    ),
    ACTION: re.compile(
        r"(?:\b(?:please|kindly)\s+(?:remember to |make sure (?:to |that )?|don't forget to |do not forget to )?"
        r"|\b(?:could|can|would|will) you (?:please )?(?:also )?"
        r"|\b(?:remember to|don't forget to|do not forget to|be sure to|make sure (?:to|that)?)\s+"
        r"|\b(?:let's|let us)\s+(?:also )?"
        r"|\b(?:need to|needs to|have to|has to|got to|ought to|should|must)\s+(?:also )?"
        r"|\b(?:someone|somebody|anyone|anybody)\s+(?:needs? to|should|can|could|has to)\s+"
        r"|\b(?:to take care of|responsible for|in charge of)\s+)",
        re.IGNORECASE,
    ),
    REQUIREMENT: re.compile(
        r"\b(?:must|should|has to|have to|needs? to|need(?:s)? (?:a|an|the)|required to|requires?)\s+",
        re.IGNORECASE,
    ),
    DECISION: re.compile(
        r"\b(?:we (?:have |had )?decided (?:to |that )?|we've decided (?:to |that )?|it was decided (?:to |that )?|the decision (?:is|was) (?:to |that )?|(?:let's|let us|we'll|we will|we are going to) go with )",
        re.IGNORECASE,
    ),
}

_QUESTION_WORDS = re.compile(
    r"^(?:who|what|when|where|why|how|which|is|are|do|does|did|can|could|would|will|should)\b", re.IGNORECASE
)


def _clean(task: str) -> str:
    task = task.strip(" \t,;:-–—.!?\"'")
    task = re.sub(r"\s+", " ", task)
    task = re.sub(r"^(?:and|then|also|to)\s+", "", task, flags=re.IGNORECASE)
    return task


def find_due(sentence: str) -> str | None:
    """The deadline or time phrase in `sentence`, as spoken ("before the end of the month", "by Friday")."""
    best: str | None = None
    for match in _DUE.finditer(sentence):
        phrase = match.group(1).strip()
        # A bare "today" inside an ordinary clause ("what happened today") is weak, but still the best clue.
        if best is None or len(phrase) > len(best):
            best = phrase
    return best


def find_task(sentence: str, activity: str, due: str | None = None) -> str | None:
    """What is to be done, in the words of the sentence with the request wrapped around it taken off. The
    deadline, if given, is taken out of the task: it is reported on its own."""
    text = sentence.strip()
    text = re.sub(rf"^{_NAME}[,:]\s+", "", text)  # "Ravi, please ..." -> "please ..."
    text = _FILLER_START.sub("", text).strip()
    lead = _TASK_LEAD.get(activity)
    if lead is not None:
        match = lead.search(text)
        if match:
            text = text[match.end() :]
    # Cut a trailing clause that only agrees or comments.
    text = re.split(r",\s+(?:and|but|so)\s+(?:i|we)\s+(?:will|'ll|can)\b", text, maxsplit=1)[0]
    if due:
        text = re.sub(rf"\s*,?\s*{re.escape(due)}\b", "", text, count=1, flags=re.IGNORECASE)
    task = _clean(text)
    if len(task.split()) < 2:
        return None
    task = task[0].lower() + task[1:] if task[:2] != task[:2].upper() else task
    return shorten(task, 160)


def find_owner(
    sentence: str, activity: str, speaker: str, previous_speaker: str | None, known_names: frozenset[str]
) -> Owner | None:
    """Who the task falls to, if the sentence says or implies it."""
    text = sentence.strip()

    def valid(name: str) -> bool:
        return name.lower() not in _NOT_NAMES or name in known_names

    vocative = _VOCATIVE.match(text)
    if vocative and valid(vocative.group(1)):
        return {"kind": "name", "text": vocative.group(1)}
    assigned = _ASSIGN_TO.search(text)
    if assigned and valid(assigned.group(1)):
        return {"kind": "name", "text": assigned.group(1)}
    if activity in (ACTION, COMMITMENT, REQUIREMENT):
        for named in _NAME_THEN_MODAL.finditer(text):
            if valid(named.group(1)):
                return {"kind": "name", "text": named.group(1)}
    if _I_WILL.search(text):
        return {"kind": "speaker", "label": speaker}
    if activity == COMMITMENT and _WE_WILL.search(text):
        return {"kind": "team"}
    if _UNASSIGNED.search(text):
        return None
    if _YOU.search(text) and activity == ACTION:
        if previous_speaker is not None and previous_speaker != speaker:
            return {"kind": "speaker", "label": previous_speaker}
        return None
    if activity == ACTION and _TEAM.search(text):
        return {"kind": "team"}
    return None


_DIRECT_ANSWER = re.compile(
    r"^(?:yes|yeah|yep|no|nope|sure|it is|it's|it was|that is|that's|they are|they're|he is|she is|we are|"
    r"we have|i think|i believe|i don't know|i do not know|not sure|about|around|probably|maybe|because|"
    r"on |at |by |next |tomorrow|today|\d)",
    re.IGNORECASE,
)


def is_open_question(question: str, speaker: str, replies: list[tuple[str, str]]) -> bool:
    """A question is still open if nobody else answered it. `replies` is what followed it, as (speaker label,
    sentence) pairs, nearest first. Only the next few sentences count, and an answer is a sentence from someone
    else that is not another question and either shares a meaningful word with the question or is a plain direct
    answer ("Yes", "On Tuesday", "It is ready")."""
    asked = {stem(t) for t in tokens(question) if len(t) >= 4 and t not in STOPWORDS}
    for who, text in replies[:3]:
        if who == speaker or text.rstrip().endswith("?"):
            continue
        shared = asked & {stem(t) for t in tokens(text) if len(t) >= 4 and t not in STOPWORDS}
        if shared or _DIRECT_ANSWER.match(text.strip()):
            return False
    return True


def sentences_of(text: str) -> list[str]:
    return split_sentences(text)


# Words that open a task but are not the verb of it ("the report", "your expense reports", "it").
_NOT_VERBS = frozenset(
    [
        "a",
        "an",
        "the",
        "this",
        "that",
        "these",
        "those",
        "my",
        "your",
        "our",
        "their",
        "his",
        "her",
        "its",
        "it",
        "them",
        "us",
        "me",
        "you",
        "we",
        "they",
        "he",
        "she",
        "i",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "and",
        "or",
        "but",
        "not",
        "no",
        "so",
        "up",
        "out",
    ]
)
_CUE_THEN_VERB = re.compile(
    r"\b(?:will|'ll|shall|can|could|should|must|need to|needs to|have to|has to|going to|let's|let us|"
    r"please|remember to|make sure to|decided to)\s+(?:also\s+|just\s+|then\s+)?([a-z][a-z-]+)",
    re.IGNORECASE,
)


def find_verb(sentence: str, task: str | None) -> str | None:
    """The action verb of a sentence: the first word of its task, or the word right after a cue such as "will" or
    "need to". Keyword and verb extraction, nothing smarter: None when there is no such word."""
    if task:
        first = task.split(" ", 1)[0].strip(",.;:").lower()
        if first.isalpha() and first not in _NOT_VERBS:
            return first
    match = _CUE_THEN_VERB.search(sentence)
    if match:
        word = match.group(1).lower()
        if word not in _NOT_VERBS and word.isalpha():
            return word
    return None
