"""The recording summary as a stored thing: building it, keeping it, and reading it back with names.

What is stored (`RecordingAnalysis.content`) names speakers only by label. Who a label is comes from the
transcript's own segments at the moment the recording is read, so a person who withdraws consent, or whose
voice
data is deleted, disappears from the summary immediately without the summary being rewritten.
"""

from __future__ import annotations

import threading
import uuid
from collections import Counter
from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import timeutil
from app.config import Settings
from app.insights import summary as summary_module
from app.insights.analyze import Utterance, analyze
from app.insights.embedding import build_embedder
from app.insights.functions import LABELS, Classifier
from app.models import Person, RecordingAnalysis, Transcript, TranscriptSegment
from app.schemas.people import PitchOut
from app.schemas.recordings import (
    AnalysisItemOut,
    AnalysisOut,
    ContextOut,
    ItemContextOut,
    SummaryLineOut,
    SummaryOut,
    SummarySectionOut,
    VoiceOut,
)

_CLASSIFIERS: dict[tuple[str, str, bool, bool], Classifier] = {}
_LOCK = threading.Lock()


def classifier_for(settings: Settings) -> Classifier:
    """The classifier for these settings, built once per process (loading a model takes seconds)."""
    key = (
        settings.analysis_engine,
        settings.ai_text_model,
        settings.ai_text_allow_download,
        settings.ai_offline,
    )
    with _LOCK:
        if key not in _CLASSIFIERS:
            _CLASSIFIERS[key] = Classifier(build_embedder(settings))
        return _CLASSIFIERS[key]


def reset_classifiers() -> None:
    """For tests."""
    with _LOCK:
        _CLASSIFIERS.clear()


# --- people's names ---------------------------------------------------------------------------------


def given_names(db: Session, company_id: uuid.UUID) -> frozenset[str]:
    """The first names of the company's registered people, so a spoken "Ravi" is known to be a name."""
    names = db.scalars(select(Person.display_name).where(Person.company_id == company_id))
    return frozenset(n.split()[0] for n in names if n.strip())


def name_directory(db: Session, company_id: uuid.UUID) -> dict[str, str]:
    """First name (lower case) -> full display name, only where exactly one registered person has that first
    name. A spoken "Ravi" is never turned into the wrong Ravi."""
    seen: dict[str, list[str]] = {}
    for display in db.scalars(select(Person.display_name).where(Person.company_id == company_id)):
        if display.strip():
            seen.setdefault(display.split()[0].lower(), []).append(display)
    return {first: names[0] for first, names in seen.items() if len(names) == 1}


# --- storing ----------------------------------------------------------------------------------------


def utterances_of(db: Session, transcript: Transcript) -> list[Utterance]:
    rows = db.scalars(
        select(TranscriptSegment)
        .where(TranscriptSegment.transcript_id == transcript.id)
        .order_by(TranscriptSegment.idx)
    )
    return [Utterance(s.idx, s.start_ms, s.end_ms, s.speaker_label, s.text) for s in rows]


def store(db: Session, transcript: Transcript, content: dict[str, Any]) -> RecordingAnalysis:
    """Make `content` the analysis of `transcript`, replacing any earlier one."""
    db.execute(delete(RecordingAnalysis).where(RecordingAnalysis.transcript_id == transcript.id))
    row = RecordingAnalysis(
        transcript_id=transcript.id,
        company_id=transcript.company_id,
        engine=content["engine"],
        version=content["version"],
        content=content,
        created_at=timeutil.utcnow(),
    )
    db.add(row)
    return row


def find(db: Session, transcript: Transcript) -> RecordingAnalysis | None:
    return db.scalar(select(RecordingAnalysis).where(RecordingAnalysis.transcript_id == transcript.id))


def refresh(db: Session, settings: Settings, transcript: Transcript) -> bool:
    """Redo the sentence analysis after the transcript's text was corrected. The voice measurements are kept
    (the audio did not change). Does nothing if there was no analysis. Returns whether it ran."""
    existing = find(db, transcript)
    if existing is None:
        return False
    voices = list(existing.content.get("voices", []))
    content = analyze(
        utterances_of(db, transcript),
        voices,
        classifier_for(settings),
        given_names(db, transcript.company_id),
        settings.context_similarity,
    )
    store(db, transcript, content)
    return True


# --- reading ----------------------------------------------------------------------------------------


def _speaker_names(db: Session, transcript: Transcript) -> dict[str, str | None]:
    """For each speaker label, the person it is (the one most of its segments are attributed to) or None."""
    rows = db.execute(
        select(TranscriptSegment.speaker_label, TranscriptSegment.person_id, Person.display_name)
        .outerjoin(Person, Person.id == TranscriptSegment.person_id)
        .where(TranscriptSegment.transcript_id == transcript.id)
    ).all()
    votes: dict[str, Counter[str]] = {}
    for label, person_id, name in rows:
        if person_id is not None and name:
            votes.setdefault(label, Counter())[name] += 1
    names: dict[str, str | None] = {label: None for label, _, _ in rows}
    for label, counted in votes.items():
        names[label] = counted.most_common(1)[0][0]
    return names


def _pitch(raw: Mapping[str, Any] | None) -> PitchOut | None:
    if not raw:
        return None
    return PitchOut(base_hz=raw["baseHz"], low_hz=raw["lowHz"], high_hz=raw["highHz"])


def analysis_out(
    db: Session, transcript: Transcript, duration_ms: int, *, can_manage: bool
) -> AnalysisOut | None:
    row = find(db, transcript)
    if row is None:
        return None
    content: dict[str, Any] = row.content
    names = _speaker_names(db, transcript)
    directory = name_directory(db, transcript.company_id)

    raw_voices = list(content.get("voices", []))
    total = sum(int(v.get("talkMs", 0)) for v in raw_voices) or 1
    voices: list[VoiceOut] = []
    for v in sorted(raw_voices, key=lambda v: -int(v.get("talkMs", 0))):
        label = v["label"]
        name = names.get(label)
        match = (
            v.get("match") if name else None
        )  # a match only counts while the name is still on the transcript
        voices.append(
            VoiceOut(
                label=label,
                title=summary_module.speaker_title(label, names),
                name=name,
                talk_ms=int(v.get("talkMs", 0)),
                share=round(int(v.get("talkMs", 0)) / total, 3),
                turns=int(v.get("turns", 0)),
                pitch=_pitch(v.get("pitch")),
                matched_by=match["method"] if match else None,
                match_score=match["score"] if match and can_manage else None,
            )
        )

    built = summary_module.build_summary(content, names, directory, duration_ms)
    items = [
        AnalysisItemOut(
            segment_index=i["segmentIndex"],
            start_ms=i["startMs"],
            end_ms=i["endMs"],
            speaker=summary_module.speaker_title(i["speaker"], names),
            what=i["what"],
            context=ItemContextOut(
                previous=i["context"]["previous"],
                previous_speaker=(
                    summary_module.speaker_title(i["context"]["previousSpeaker"], names)
                    if i["context"]["previousSpeaker"]
                    else None
                ),
                topic=list(i["context"]["topic"]),
                group=i["context"].get("group"),
                new_context=i["context"].get("newContext"),
                similarity=i["context"].get("similarity"),
            ),
            activity=i["activity"],
            activity_label=LABELS[i["activity"]][0],
            secondary=list(i["secondary"]),
            confidence=i["confidence"],
            task=i["task"],
            owner=summary_module.owner_title(i["owner"], names, directory),
            due=i["due"],
            dated=bool(i.get("dated")),
            verb=i.get("verb"),
            keywords=list(i.get("keywords", [])),
            open=i["open"],
        )
        for i in content.get("items", [])
    ]
    return AnalysisOut(
        engine=row.engine,
        voices=voices,
        summary=SummaryOut(
            narrative=built["narrative"],
            sections=[
                SummarySectionOut(
                    key=s["key"],
                    title=s["title"],
                    total=s["total"],
                    lines=[SummaryLineOut(**_camel_line(line)) for line in s["lines"]],
                )
                for s in built["sections"]
            ],
            sentences=built["sentences"],
            truncated=built["truncated"],
        ),
        items=items,
        counts={k: int(v) for k, v in content.get("counts", {}).items()},
        contexts=[
            ContextOut(
                group=c["group"],
                start_ms=c["startMs"],
                end_ms=c["endMs"],
                sentences=c["sentences"],
                keywords=list(c["keywords"]),
            )
            for c in content.get("contexts", [])
        ],
        context_threshold=content.get("contextThreshold"),
        created_at=row.created_at,
    )


def _camel_line(line: Mapping[str, Any]) -> dict[str, Any]:
    """Summary lines come out of the pure summary module in camelCase; the response model takes snake_case."""
    return {
        "activity": line["activity"],
        "activity_label": line["activityLabel"],
        "text": line["text"],
        "said": line["said"],
        "speaker": line["speaker"],
        "owner": line["owner"],
        "due": line["due"],
        "dated": line.get("dated", False),
        "open": line["open"],
        "at_ms": line["atMs"],
        "segment_index": line["segmentIndex"],
        "confidence": line["confidence"],
    }
