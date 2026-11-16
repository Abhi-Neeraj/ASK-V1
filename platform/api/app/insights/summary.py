"""The summary, written by templates from the analysis. No language model: the same analysis always gives the
same summary, and every line is built from a sentence that was said.

It runs when a recording is read (not when it is processed), because it names people: who a speaker label is
comes from the transcript as it stands now, so a person whose voice data was deleted is no longer named, and a
correction to a name shows up at once.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.insights.functions import (
    ACTION,
    APPROVAL,
    COMMITMENT,
    DECISION,
    ISSUE,
    LABELS,
    QUESTION,
    REJECTION,
    REQUIREMENT,
    RISK,
)
from app.insights.text import shorten

# How many lines each section shows. The full list is in the analysis; this is a summary.
_PER_SECTION = 8
_NARRATIVE_EXAMPLES = 2
# How many topics the overview lists by name.
_TOPICS_SHOWN = 4

# The sections in the order they are told, with the activities each one gathers.
SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("decisions", "Decisions", (DECISION,)),
    ("actions", "Action items", (ACTION,)),
    ("commitments", "Commitments", (COMMITMENT,)),
    ("questions", "Questions", (QUESTION,)),
    ("requirements", "Requirements", (REQUIREMENT,)),
    ("concerns", "Issues and risks", (ISSUE, RISK)),
    ("approvals", "Approvals and rejections", (APPROVAL, REJECTION)),
)


def speaker_title(label: str, names: Mapping[str, str | None]) -> str:
    """ "Ana" for a matched voice, otherwise the label as "Speaker 1" (SPEAKER_00 is the first one)."""
    name = names.get(label)
    if name:
        return name
    digits = "".join(ch for ch in label if ch.isdigit())
    return f"Speaker {int(digits) + 1}" if digits else "Unknown speaker"


def owner_title(
    owner: Mapping[str, Any] | None, names: Mapping[str, str | None], directory: Mapping[str, str]
) -> str | None:
    """Who a task falls to, in words. `directory` maps a given name (lower case) to the full name of the one
    registered person who has it, so "Ravi" becomes "Ravi Kumar" only where that is unambiguous."""
    if not owner:
        return None
    kind = owner.get("kind")
    if kind == "speaker":
        return speaker_title(str(owner.get("label", "")), names)
    if kind == "name":
        spoken = str(owner.get("text", ""))
        return directory.get(spoken.lower(), spoken)
    if kind == "team":
        return "The team"
    return None


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _clock(ms: int) -> str:
    seconds = max(0, ms) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def _duration_words(ms: int) -> str:
    seconds = max(0, round(ms / 1000))
    if seconds < 60:
        return f"{seconds} second" + ("" if seconds == 1 else "s")
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min {rest:02d} s" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d} min"


def _line(
    item: Mapping[str, Any], names: Mapping[str, str | None], directory: Mapping[str, str]
) -> dict[str, Any]:
    activity = item["activity"]
    owner = owner_title(item.get("owner"), names, directory)
    return {
        "activity": activity,
        "activityLabel": LABELS[activity][0],
        "text": item.get("task") or item["what"],
        "said": item["what"],
        "speaker": speaker_title(item["speaker"], names),
        "owner": owner,
        "due": item.get("due"),
        "dated": bool(item.get("dated")),
        "open": item.get("open"),
        "atMs": item["startMs"],
        "segmentIndex": item["segmentIndex"],
        "confidence": item.get("confidence"),
    }


def build_summary(
    content: Mapping[str, Any],
    names: Mapping[str, str | None],
    directory: Mapping[str, str],
    duration_ms: int,
) -> dict[str, Any]:
    """The summary of one analysis: a few sentences of overview, and the key lines by section."""
    items: list[Mapping[str, Any]] = list(content.get("items", []))
    voices: list[Mapping[str, Any]] = list(content.get("voices", []))
    by_activity: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_activity.setdefault(item["activity"], []).append(_line(item, names, directory))

    sections: list[dict[str, Any]] = []
    for key, title, activities in SECTIONS:
        lines = [line for a in activities for line in by_activity.get(a, [])]
        if key == "questions":
            lines.sort(key=lambda line: (line["open"] is False, line["atMs"]))  # unanswered first
        else:
            lines.sort(key=lambda line: line["atMs"])
        if lines:
            sections.append({"key": key, "title": title, "total": len(lines), "lines": lines[:_PER_SECTION]})

    return {
        "narrative": _narrative(
            items, voices, by_activity, names, duration_ms, list(content.get("contexts", []))
        ),
        "sections": sections,
        "sentences": len(items),
        "truncated": bool(content.get("truncated")),
    }


def _narrative(
    items: list[Mapping[str, Any]],
    voices: list[Mapping[str, Any]],
    by_activity: Mapping[str, list[dict[str, Any]]],
    names: Mapping[str, str | None],
    duration_ms: int,
    contexts: list[Mapping[str, Any]],
) -> list[str]:
    paragraphs: list[str] = []

    if not items:
        return ["There was not enough speech in this recording to summarise."]

    # 1. Who and how long.
    talkers = sorted(voices, key=lambda v: -int(v.get("talkMs", 0)))
    total_talk = sum(int(v.get("talkMs", 0)) for v in talkers) or 1
    head = f"This recording is {_duration_words(duration_ms)} long"
    if len(talkers) > 1:
        who = _join(
            [
                f"{speaker_title(v['label'], names)} ({round(100 * int(v.get('talkMs', 0)) / total_talk)}%)"
                for v in talkers
            ]
        )
        head += f" with {len(talkers)} speakers: {who}."
    elif talkers:
        head += f" with one speaker ({speaker_title(talkers[0]['label'], names)})."
    else:
        head += "."
    paragraphs.append(head)

    # 2. What it contained.
    found = []
    for activity in (DECISION, ACTION, COMMITMENT, QUESTION, REQUIREMENT, ISSUE, RISK, APPROVAL, REJECTION):
        count = len(by_activity.get(activity, []))
        if count:
            found.append(_plural(count, LABELS[activity][0].lower(), LABELS[activity][1].lower()))
    open_questions = sum(1 for line in by_activity.get(QUESTION, []) if line["open"] is not False)
    if found:
        sentence = f"It contains {_join(found)}."
        if open_questions:
            sentence += f" {_plural(open_questions, 'question is', 'questions are')} still unanswered."
        paragraphs.append(sentence)
    else:
        paragraphs.append(
            "No decisions, action items or questions were picked out of it: it reads as discussion."
        )

    # 2b. How many of the tasks have a deadline: an activity together with a date or time.
    tasks = [line for a in (ACTION, COMMITMENT) for line in by_activity.get(a, [])]
    if tasks:
        dated = sum(1 for line in tasks if line["dated"])
        if len(tasks) > 1:
            has = "has" if dated == 1 else "have"
            how_many = _plural(len(tasks), "task", "tasks")
            paragraphs.append(f"{dated} of the {how_many} {has} a deadline.")
        else:
            paragraphs.append("The task has a deadline." if dated else "The task has no deadline.")

    # 2c. What it talked about, in order, where the contexts were worked out.
    topics = [", ".join(c["keywords"]) for c in contexts if c.get("keywords")]
    if len(contexts) > 1 and topics:
        shown = topics[:_TOPICS_SHOWN]
        more = f" and {len(topics) - len(shown)} more" if len(topics) > len(shown) else ""
        paragraphs.append(f"It moves through {len(contexts)} topics: {'; '.join(shown)}{more}.")
    elif len(contexts) == 1 and topics:
        paragraphs.append(f"It stays on one topic: {topics[0]}.")

    # 3. The main points, in plain templates.
    for activity in (DECISION, ACTION, COMMITMENT, REQUIREMENT, ISSUE, RISK):
        for line in by_activity.get(activity, [])[:_NARRATIVE_EXAMPLES]:
            text = shorten(str(line["text"]), 140).rstrip(".")
            due = f" ({line['due']})" if line["due"] else ""
            if activity == DECISION:
                paragraphs.append(f"Decided: {text}.")
            elif activity in (ACTION, COMMITMENT):
                lead = f"{line['owner']} to" if line["owner"] else "To do:"
                paragraphs.append(f"{lead} {text}{due}.")
            elif activity == REQUIREMENT:
                paragraphs.append(f"Needed: {text}.")
            elif activity == ISSUE:
                paragraphs.append(f"Issue raised by {line['speaker']}: {text}.")
            else:
                paragraphs.append(f"Concern raised by {line['speaker']}: {text}.")
    return paragraphs


def as_plain_text(summary: Mapping[str, Any]) -> str:
    """The summary as text for an export."""
    out: list[str] = list(summary.get("narrative", []))
    for section in summary.get("sections", []):
        out.append("")
        out.append(f"{section['title']} ({section['total']})")
        for line in section["lines"]:
            parts = [f"- [{_clock(line['atMs'])}] {line['text']}"]
            if line.get("owner"):
                parts.append(f"owner: {line['owner']}")
            if line.get("due"):
                parts.append(f"due: {line['due']}")
            out.append("  ".join(parts))
    return "\n".join(out)
