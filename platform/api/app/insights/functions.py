"""The activities a sentence can be, and how a sentence is matched to one.

The set is closed: it is the same set of functions the ASK app's guarded memory registry allows (decision,
action, commitment, question, requirement, issue, risk, approval, rejection, note), so what is found here can be
handed to that registry unchanged (`REGISTRY_FUNCTION`). A sentence that is none of them is a plain "note".

A sentence's score for an activity is 70 percent cue words (the rules) and 30 percent closeness in meaning to
example sentences of the activity (the embedding). The rules carry the weight on purpose: they are exact,
explainable, and behave the same whichever embedding engine runs. The embedding catches paraphrases the rules
miss. The best activity wins if it reaches `MIN_SCORE`; any other activity that scores well is kept as secondary.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np

from app.insights.embedding import TextEmbedder, Vector

DECISION: Final = "DECISION"
ACTION: Final = "ACTION"
COMMITMENT: Final = "COMMITMENT"
QUESTION: Final = "QUESTION"
REQUIREMENT: Final = "REQUIREMENT"
ISSUE: Final = "ISSUE"
RISK: Final = "RISK"
APPROVAL: Final = "APPROVAL"
REJECTION: Final = "REJECTION"
NOTE: Final = "NOTE"

ACTIVITIES: Final = (DECISION, ACTION, COMMITMENT, QUESTION, REQUIREMENT, ISSUE, RISK, APPROVAL, REJECTION)
ALL_ACTIVITIES: Final = (*ACTIVITIES, NOTE)

# The function of the ASK app's closed registry each activity corresponds to.
REGISTRY_FUNCTION: Final = {
    DECISION: "CREATE_DECISION",
    ACTION: "CREATE_ACTION",
    COMMITMENT: "CREATE_COMMITMENT",
    QUESTION: "CREATE_QUESTION",
    REQUIREMENT: "CREATE_REQUIREMENT",
    ISSUE: "CREATE_ISSUE",
    RISK: "CREATE_RISK",
    APPROVAL: "CREATE_APPROVAL",
    REJECTION: "CREATE_REJECTION",
    NOTE: "CREATE_NOTE",
}

# What each activity is called on screen, and the plural for headings.
LABELS: Final = {
    DECISION: ("Decision", "Decisions"),
    ACTION: ("Action item", "Action items"),
    COMMITMENT: ("Commitment", "Commitments"),
    QUESTION: ("Question", "Questions"),
    REQUIREMENT: ("Requirement", "Requirements"),
    ISSUE: ("Issue", "Issues"),
    RISK: ("Risk", "Risks"),
    APPROVAL: ("Approval", "Approvals"),
    REJECTION: ("Rejection", "Rejections"),
    NOTE: ("Note", "Notes"),
}

MIN_SCORE = 0.30
# The sentence-model way of weighing meaning (see Classifier). Chosen on a split of 100 labelled sentences and
# checked on the other half: 40 of 50 right on the held-out half, against 31 of 50 for the words engine. A small
# set written by the author, so treat the figures as an indication, not a measurement.
SEMANTIC_TEMPERATURE = 0.03
SEMANTIC_NOTE_PRIOR = 0.18
SECONDARY_SCORE = 0.40
# 70 percent functions, 30 percent meaning. Measured against 65/35 on 100 labelled sentences (both engines):
# identical results, so the split follows the design: the model supplies meaning, the rules decide.
CUE_WEIGHT = 0.70
MEANING_WEIGHT = 0.30

# Example sentences. They are the "meaning" side of the match, so they are generic and plain.
PROTOTYPES: Final[dict[str, tuple[str, ...]]] = {
    DECISION: (
        "We decided to go with the second option.",
        "It is agreed, we will postpone the launch until next month.",
        "The decision is to move the meeting to Thursday.",
        "Let's go with the original plan.",
        "We have settled on a fixed price for the contract.",
    ),
    ACTION: (
        "Please send me the updated report by Friday.",
        "Could you book the meeting room for next week?",
        "Someone needs to follow up with the supplier.",
        "Make sure the team reviews the document before the call.",
        "Let's schedule a follow up meeting.",
    ),
    COMMITMENT: (
        "I will send the revised budget to everyone tomorrow.",
        "I'll take care of the booking this afternoon.",
        "Leave it with me, I will handle it.",
        "We will deliver the first version by the end of the month.",
        "I can call the vendor and confirm the price.",
    ),
    QUESTION: (
        "When does the delivery arrive?",
        "Who is responsible for the documentation?",
        "Could you confirm whether the price includes tax?",
        "What is the status of the release?",
        "Do we have enough budget for this?",
    ),
    REQUIREMENT: (
        "The system must support exporting reports.",
        "We need a simpler way for customers to sign in.",
        "It is required that every invoice has a purchase order number.",
        "The new version has to work on older phones.",
        "Customers are asking for a feature to share their results.",
    ),
    ISSUE: (
        "There is a problem with the server and it keeps failing.",
        "The login page is broken for some customers.",
        "We have a blocker, the data export is not working.",
        "The shipment was delayed and the customer complained.",
        "An error appears every time we try to save.",
    ),
    RISK: (
        "I am worried that the supplier might miss the deadline.",
        "There is a risk that the release could slip.",
        "If we do not hire soon we may fall behind.",
        "My concern is that the budget will not be enough.",
        "This could cause a problem later if we ignore it.",
    ),
    APPROVAL: (
        "The budget has been approved.",
        "That sounds good, go ahead.",
        "I am happy with that, you have my sign off.",
        "Yes, that works for me.",
        "Approved, please proceed.",
    ),
    REJECTION: (
        "I cannot approve that, it is rejected.",
        "No, that will not work for us.",
        "I disagree, we should not do that.",
        "The proposal was declined.",
        "We are not going ahead with this.",
    ),
}


# Plain statements that are none of the activities. Only used with the sentence model, where "note" is one of the
# ten shares a sentence's meaning is divided into.
NOTE_EXAMPLES: Final = (
    "The office opens at eight.",
    "Last quarter's revenue was around two million.",
    "He lives near the station.",
    "The file is saved in the shared folder.",
    "There are about forty people on the team.",
    "The conference is held every spring.",
    "The server is located in the data center downtown.",
    "It rained for most of the afternoon.",
)


@dataclass(frozen=True)
class Cue:
    pattern: re.Pattern[str]
    weight: float


def _cues(*specs: tuple[str, float]) -> tuple[Cue, ...]:
    return tuple(Cue(re.compile(p, re.IGNORECASE), w) for p, w in specs)


_INFO_REQUEST = r"(?:confirm|tell|explain|clarify|let me know|say|remind|check (?:whether|if)|show)"
# Verbs that, after "will", promise a piece of work: "Rahul will finish the report".
_WORK_VERBS = (
    r"(?:send|finish|prepare|review|complete|update|call|email|share|submit|deliver|handle|fix|write|"
    r"check|book|arrange|follow up|get|bring|make|set up|create|organi[sz]e|confirm|draft|post|upload|"
    r"publish|schedule|run|test|deploy|ship|take care|take over|look into|sort|pick up|forward|collect)"
)

CUES: Final[dict[str, tuple[Cue, ...]]] = {
    QUESTION: _cues(
        (r"\?\s*$", 0.90),
        (rf"^(?:could|can|would|will) you (?:please )?{_INFO_REQUEST}\b", 0.95),
        (r"^(?:who|what|when|where|why|how|which)\b", 0.55),
        (r"^(?:is|are|was|were|do|does|did|have|has|shall|should)\b.+\b(?:or|yet)\b", 0.35),
    ),
    ACTION: _cues(
        (rf"^(?:could|can|would|will) you (?:please )?(?!{_INFO_REQUEST})\w+", 0.90),
        (r"\b(?:please|kindly)\b", 0.60),
        (
            r"\b(?:remember to|don't forget to|do not forget to|make sure (?:to|that|we|you|the)|be sure to)\b",
            0.75,
        ),
        (r"\b(?:let's|let us)\b", 0.70),
        (r"\b(?:need to|needs to|have to|has to|got to|ought to|should|must)\b", 0.55),
        (r"\b(?:follow up|action item|to-do|to do|take care of|responsible for|in charge of)\b", 0.70),
        (r"\b(?:assign(?:ed)?|delegate[ds]?) (?:it |this |that )?to\b", 0.85),
        (
            r"^(?:send|prepare|update|review|check|call|email|schedule|book|submit|share|finish|complete|create|fix|"
            r"confirm|write|draft|arrange|organi[sz]e|contact|order|set up|plan|ask|bring|collect|upload|reply)\b",
            0.70,
        ),
        (r"\b(?:someone|somebody|anyone|anybody) (?:needs? to|should|can|could|has to)\b", 0.80),
    ),
    COMMITMENT: _cues(
        (
            rf"\b[\w']+ (?:will|shall|is going to|are going to|'ll) (?:also |then |definitely |just )?{_WORK_VERBS}\b",
            0.80,
        ),
        (r"\b(?:i will|i'll|i shall|i am going to|i'm going to|i can|i could)\b", 0.90),
        (r"\b(?:we will|we'll|we are going to|we're going to|we shall)\b", 0.75),
        (
            r"\b(?:leave it with me|i've got it|i have got it|i'll handle|i can handle|count on me|i promise)\b",
            0.95,
        ),
    ),
    DECISION: _cues(
        (
            r"\b(?:we (?:have |had )?decided|we've decided|it was decided|the decision (?:is|was)|decision made)\b",
            0.95,
        ),
        (r"\b(?:agreed|we agree[d]?|we all agree|settled on|final (?:call|choice|decision))\b", 0.80),
        (
            r"\b(?:let's go with|we'll go with|we will go with|we are going with|we're going with|the plan is)\b",
            0.85,
        ),
        (r"\b(?:postpone|reschedule|move|push)(?:d)? (?:it |this |the \w+ )?(?:to|until|back)\b", 0.45),
    ),
    REQUIREMENT: _cues(
        (r"\b(?:requirement|required|requires?|mandatory|must (?:support|have|be|include|work))\b", 0.80),
        (r"\b(?:we|you|they|it) (?:need|needs|needed) (?:a|an|the|to be|some) \w+", 0.55),
        (r"\b(?:has to|have to) (?:be|work|support|include|have)\b", 0.65),
        (
            r"\b(?:customers?|users?|clients?) (?:keep )?(?:ask(?:ing)? for|want(?:s)?|expect(?:s)?|need(?:s)?)\b",
            0.65,
        ),
        (r"\b(?:should support|should be able to|must be able to)\b", 0.80),
    ),
    ISSUE: _cues(
        (
            r"\b(?:problem|issue|bug|broken|failing|failed|failure|error|outage|incident|complain(?:t|ed)|blocker|blocked)\b",
            0.75,
        ),
        (r"\b(?:not working|doesn't work|does not work|stopped working|keeps? (?:failing|crashing))\b", 0.90),
        (r"\b(?:delay(?:ed)?|late|overdue|missing|crash(?:ed|es)?)\b", 0.45),
    ),
    RISK: _cues(
        (r"\b(?:worried|worry|worrying|concern(?:ed)?|afraid|nervous|anxious)\b", 0.85),
        (r"\b(?:risk|risky|danger|at risk|uncertain|unsure)\b", 0.85),
        (r"\b(?:might|may|could) (?:miss|fail|slip|delay|be late|fall behind|cause|go wrong)\b", 0.80),
        (r"\b(?:if we (?:don't|do not|fail to)|unless we)\b", 0.60),
    ),
    APPROVAL: _cues(
        (r"\b(?:approved|approve|signed off|sign[- ]off|green ?light)\b", 0.90),
        (r"\b(?:go ahead|you have my|gets? my (?:approval|vote))\b", 0.80),
        (
            r"\b(?:that works|that sounds (?:good|great|fine|perfect)|sounds (?:good|great|fine|perfect)|i'm (?:happy|fine|ok|okay) with)\b",
            0.65,
        ),
        (r"^(?:yes|yeah|yep|sure|absolutely|agreed|perfect)\b[ ,.!]*(?:that|please|go|let)?", 0.40),
    ),
    REJECTION: _cues(
        (r"\b(?:rejected|declined|vetoed|turned down|not approved|cannot approve|can't approve)\b", 0.95),
        (r"\b(?:i disagree|i don't agree|i do not agree|we disagree|i object)\b", 0.85),
        (
            r"\b(?:that won't work|that will not work|won't work for|not going ahead|call (?:it )?off|scrap (?:it|that|this))\b",
            0.80,
        ),
        (r"^(?:no|nope|nah)\b[ ,.!]", 0.55),
    ),
}


@dataclass
class Classification:
    activity: str
    score: float
    secondary: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


def cue_score(sentence: str, activity: str) -> float:
    """The strongest cue of `activity` found in `sentence`, 0 to 1."""
    best = 0.0
    for cue in CUES.get(activity, ()):
        if cue.weight > best and cue.pattern.search(sentence):
            best = cue.weight
    return best


class Classifier:
    """Matches sentences to activities. Create one per embedder: it embeds the example sentences once.

    Two ways of weighing meaning, chosen by the engine, because the two engines' similarities mean different
    things (measured on 100 sentences written for the purpose, tuned on one half and checked on the other):

    * lexical (words and letter groups): closeness to the nearest example of each activity. Rules decide most
      sentences; meaning only nudges. Kept as it was, because a looser rule gave more wrong labels.
    * semantic (a sentence model): similarity to the centre of each activity's examples, turned into shares
      that add up to one across all ten activities including "note". A sentence with no cue word at all can
      then still be recognised by what it means.
    """

    def __init__(self, embedder: TextEmbedder) -> None:
        self.embedder = embedder
        self._semantic = embedder.engine == "semantic"
        examples = [(activity, text) for activity, texts in PROTOTYPES.items() for text in texts]
        if self._semantic:
            examples += [(NOTE, text) for text in NOTE_EXAMPLES]
        vectors = embedder.embed([text for _, text in examples])
        self._prototypes: dict[str, list[Vector]] = {a: [] for a in ALL_ACTIVITIES}
        for (activity, _), vector in zip(examples, vectors, strict=True):
            self._prototypes[activity].append(vector)
        self._unit_prototypes: dict[str, Any] = {
            activity: np.array([self._unit(v) for v in vecs]) for activity, vecs in self._prototypes.items()
        }
        self._centres: Any = None
        if self._semantic:
            centres = []
            for activity in ALL_ACTIVITIES:
                mean = np.mean(np.array(self._prototypes[activity], dtype=np.float64), axis=0)
                centres.append(mean / (np.linalg.norm(mean) or 1.0))
            self._centres = np.array(centres)

    def meaning(self, vector: Vector, activity: str) -> float:
        """How close `vector` is to the nearest example of `activity`, 0 to 1."""
        return self._meaning_of_unit(self._unit(vector), activity)

    @staticmethod
    def _unit(vector: Vector) -> Any:
        array = np.asarray(vector, dtype=np.float64)
        length = float(np.linalg.norm(array))
        return array / length if length else array

    def _meaning_of_unit(self, unit: Any, activity: str) -> float:
        matrix = self._unit_prototypes[activity]
        return max(0.0, float((matrix @ unit).max())) if len(matrix) else 0.0

    def classify_many(
        self, sentences: Sequence[str], vectors: Sequence[Vector] | None = None
    ) -> list[Classification]:
        """Each sentence's activity. Pass `vectors` (from `embedder.embed`) when the caller has them already, so
        the sentences are embedded once and the same vectors can also serve the context check."""
        if vectors is None:
            vectors = self.embedder.embed(list(sentences)) if sentences else []
        if self._semantic and len(vectors):
            shares = self._shares(vectors)
            return [
                self._classify_semantic(sentence, dict(zip(ALL_ACTIVITIES, row, strict=True)))
                for sentence, row in zip(sentences, shares, strict=True)
            ]
        return [self._classify(sentence, vector) for sentence, vector in zip(sentences, vectors, strict=True)]

    # --- the sentence-model way --------------------------------------------------------------------------

    def _shares(self, vectors: Sequence[Vector]) -> Any:
        """For each sentence, how its meaning is shared out across the ten activities (each row adds to 1)."""
        matrix = np.array(vectors, dtype=np.float64)
        matrix = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)
        scaled = (matrix @ self._centres.T) / SEMANTIC_TEMPERATURE
        scaled = scaled - scaled.max(axis=1, keepdims=True)
        shares = np.exp(scaled)
        return shares / shares.sum(axis=1, keepdims=True)

    def _classify_semantic(self, sentence: str, shares: dict[str, float]) -> Classification:
        scores = {
            a: round(CUE_WEIGHT * cue_score(sentence, a) + MEANING_WEIGHT * float(shares[a]), 4)
            for a in ACTIVITIES
        }
        note = round(SEMANTIC_NOTE_PRIOR + MEANING_WEIGHT * float(shares[NOTE]), 4)
        if sentence.rstrip().endswith("?"):
            for activity in ACTIVITIES:
                if activity not in (QUESTION, ACTION):
                    scores[activity] = round(0.6 * scores[activity], 4)
        ranked = sorted(ACTIVITIES, key=lambda a: -scores[a])
        best = ranked[0]
        if scores[best] <= note:
            return Classification(NOTE, max(0.0, min(1.0, 1.0 - scores[best])), scores=scores)
        secondary = [
            a for a in ranked[1:] if scores[a] >= SECONDARY_SCORE and scores[a] >= 0.6 * scores[best]
        ]
        return Classification(best, scores[best], secondary[:2], scores)

    # --- the words way -----------------------------------------------------------------------------------

    def _classify(self, sentence: str, vector: Vector) -> Classification:
        unit = self._unit(vector)
        scores = {
            a: round(CUE_WEIGHT * cue_score(sentence, a) + MEANING_WEIGHT * self._meaning_of_unit(unit, a), 4)
            for a in ACTIVITIES
        }
        if sentence.rstrip().endswith("?"):
            # "Is the budget approved?" asks about an approval; it is not one. A question stays a question
            # unless it is a request to do something ("Could you book the room?").
            for activity in ACTIVITIES:
                if activity not in (QUESTION, ACTION):
                    scores[activity] = round(0.6 * scores[activity], 4)
        ranked = sorted(ACTIVITIES, key=lambda a: -scores[a])
        best = ranked[0]
        if scores[best] < MIN_SCORE:
            return Classification(NOTE, 1.0 - scores[best], scores=scores)
        secondary = [
            a for a in ranked[1:] if scores[a] >= SECONDARY_SCORE and scores[a] >= 0.6 * scores[best]
        ]
        return Classification(best, scores[best], secondary[:2], scores)
