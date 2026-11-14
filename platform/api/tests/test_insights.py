"""The function-based summarizer: sentences to activities, tasks, owners, due dates, and a rule-built
summary."""

from __future__ import annotations

import re
import sys
import types
from collections.abc import Sequence
from typing import Any

import pytest

from app.config import Settings
from app.insights import embedding, extract, functions, summary, text
from app.insights.analyze import Utterance, analyze
from app.insights.context import ContextLink, effective_threshold, link_contexts
from app.insights.embedding import HashingEmbedder, Vector, build_embedder, cosine
from app.insights.functions import Classifier

CLASSIFIER = Classifier(HashingEmbedder())


def activity_of(sentence: str) -> str:
    return CLASSIFIER.classify_many([sentence])[0].activity


class ClassAlignedModel:
    """A stand-in for a sentence model whose space has one direction per activity. An example sentence of an
    activity points along it, anything mentioning a hall or booking points along the commitment direction (as
    a real model would for "take care of the booking"), and every other sentence points along the note
    direction."""

    engine = "semantic"
    name = "class-aligned stand-in"

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        order = [*functions.ACTIVITIES, functions.NOTE]
        examples = {t: a for a, ts in functions.PROTOTYPES.items() for t in ts}
        examples.update(dict.fromkeys(functions.NOTE_EXAMPLES, functions.NOTE))
        out: list[Vector] = []
        for sentence in texts:
            kind = examples.get(sentence)
            if kind is None:
                kind = (
                    functions.COMMITMENT if "reserve" in sentence or "booking" in sentence else functions.NOTE
                )
            out.append(tuple(1.0 if a == kind else 0.0 for a in order))
        return out


class TestText:
    def test_sentences_are_split_at_full_stops_but_not_at_abbreviations(self) -> None:
        got = text.split_sentences("Dr. Rao will call. Is it ready? Yes! We start at 9.30 on Monday.")
        assert got == ["Dr. Rao will call.", "Is it ready?", "Yes!", "We start at 9.30 on Monday."]

    def test_empty_and_wordless_text_has_no_sentences(self) -> None:
        assert text.split_sentences("") == [] and text.split_sentences("  ...  ") == []

    def test_topic_words_are_the_most_repeated_meaningful_words(self) -> None:
        got = text.topic_words(
            ["The budget needs review.", "Send the budget report.", "The report is late, the budget too."]
        )
        assert got[:2] == ["budget", "report"]

    def test_shortening_cuts_at_a_word_and_says_so(self) -> None:
        assert text.shorten("one two three four five", 12) == "one two…"
        assert text.shorten("short", 12) == "short"


class TestEmbeddings:
    def test_the_lexical_engine_is_deterministic_and_unit_length(self) -> None:
        engine = HashingEmbedder()
        a, b = engine.embed(["Send the report."])[0], engine.embed(["Send the report."])[0]
        assert a == b
        assert cosine(a, a) == pytest.approx(1.0)

    def test_sentences_sharing_words_are_closer_than_unrelated_ones(self) -> None:
        engine = HashingEmbedder()
        base, near, far = engine.embed(
            ["Please send me the budget report.", "Send the report to me please.", "The weather was lovely."]
        )
        assert cosine(base, near) > cosine(base, far) + 0.3

    def test_empty_text_gives_a_zero_vector_not_a_crash(self) -> None:
        assert set(HashingEmbedder().embed([""])[0]) == {0.0}

    def test_the_engine_can_be_chosen_and_falls_back_quietly(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert build_embedder(settings.model_copy(update={"analysis_engine": "lexical"})).engine == "lexical"
        monkeypatch.setattr(embedding, "semantic_installed", lambda: False)
        assert build_embedder(settings).engine == "lexical"  # auto, nothing installed
        with pytest.raises(embedding.EmbedderUnavailableError):
            build_embedder(settings.model_copy(update={"analysis_engine": "semantic"}))

    def test_a_semantic_model_that_cannot_load_falls_back_in_auto_and_fails_in_semantic(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(embedding, "semantic_installed", lambda: True)

        def cannot_load(self: Any) -> None:
            raise OSError("weights are not on this machine")

        monkeypatch.setattr(embedding.SentenceTransformerEmbedder, "load", cannot_load)
        assert build_embedder(settings).engine == "lexical"
        with pytest.raises(embedding.EmbedderUnavailableError, match="could not be loaded"):
            build_embedder(settings.model_copy(update={"analysis_engine": "semantic"}))

    def test_the_sentence_model_is_never_downloaded_unless_asked(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        class FakeModel:
            def __init__(self, name: str, **kwargs: Any) -> None:
                seen.update(name=name, **kwargs)

            def encode(self, texts: Sequence[str], **_: Any) -> list[list[float]]:
                return [[1.0, 0.0] for _ in texts]

        fake = types.ModuleType("sentence_transformers")
        fake.SentenceTransformer = FakeModel  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake)

        embedding.SentenceTransformerEmbedder(settings).load()
        assert seen["local_files_only"] is True  # the default: nothing is fetched

        embedding.SentenceTransformerEmbedder(
            settings.model_copy(update={"ai_text_allow_download": True})
        ).load()
        assert seen["local_files_only"] is False

        offline = settings.model_copy(update={"ai_text_allow_download": True, "ai_offline": True})
        embedding.SentenceTransformerEmbedder(offline).load()
        assert seen["local_files_only"] is True  # ASK_AI_OFFLINE wins

    def test_a_semantic_engine_is_used_through_the_same_interface(self) -> None:
        classifier = Classifier(ClassAlignedModel())  # type: ignore[arg-type]
        # No cue word at all, but it means the same as an example commitment, so meaning alone is enough...
        assert classifier.classify_many(["We can reserve the hall."])[0].activity == functions.COMMITMENT
        # ...whereas the words-only engine, which sees no shared words and no cue, calls it a plain note.
        assert activity_of("We can reserve the hall.") == functions.NOTE
        content = analyze([Utterance(0, 0, 1000, "SPEAKER_00", "Just a plain remark.")], [], classifier)
        assert content["engine"] == "semantic"

    def test_with_a_sentence_model_a_plain_statement_stays_a_note(self) -> None:
        classifier = Classifier(ClassAlignedModel())  # type: ignore[arg-type]
        assert classifier.classify_many(["The office has a lovely view."])[0].activity == functions.NOTE

    def test_with_a_sentence_model_the_rules_still_win_over_a_misleading_meaning(self) -> None:
        """A strong cue ("I will") decides it, even where the model's meaning points at a note."""
        classifier = Classifier(ClassAlignedModel())  # type: ignore[arg-type]
        assert (
            classifier.classify_many(["I will send the revised budget tomorrow."])[0].activity
            == functions.COMMITMENT
        )

    def test_with_a_sentence_model_a_question_about_an_approval_is_still_a_question(self) -> None:
        classifier = Classifier(ClassAlignedModel())  # type: ignore[arg-type]
        assert classifier.classify_many(["Is the budget approved?"])[0].activity == functions.QUESTION


class TestClassifying:
    @pytest.mark.parametrize(
        ("sentence", "expected"),
        [
            ("We decided to postpone the launch until the testing is finished.", functions.DECISION),
            ("It was agreed that we will go with the second supplier.", functions.DECISION),
            ("Please send me the updated numbers by Friday.", functions.ACTION),
            ("Could you book the meeting room for next week?", functions.ACTION),
            ("Let us meet at the front entrance at nine o'clock.", functions.ACTION),
            ("Someone should write up the notes.", functions.ACTION),
            ("I will call the vendor tomorrow and confirm the price.", functions.COMMITMENT),
            ("Leave it with me.", functions.COMMITMENT),
            ("When does the delivery arrive?", functions.QUESTION),
            ("Could you confirm whether the delivery arrives on Monday or Tuesday?", functions.QUESTION),
            ("Is the budget approved?", functions.QUESTION),
            ("The system must support exporting reports.", functions.REQUIREMENT),
            ("There was a problem with the server last night.", functions.ISSUE),
            ("The login page is not working for some customers.", functions.ISSUE),
            ("I am worried that the supplier might miss the deadline.", functions.RISK),
            ("There is a risk that the release could slip.", functions.RISK),
            ("The budget has been approved.", functions.APPROVAL),
            ("That sounds great.", functions.APPROVAL),
            ("The proposal was declined.", functions.REJECTION),
            ("I disagree, we should not do that.", functions.REJECTION),
            ("No, that will not work for us.", functions.REJECTION),
            ("The weather was lovely over the weekend.", functions.NOTE),
            ("It arrives on Tuesday.", functions.NOTE),
        ],
    )
    def test_each_kind_of_sentence_is_recognised(self, sentence: str, expected: str) -> None:
        assert activity_of(sentence) == expected

    def test_a_compound_sentence_keeps_its_second_meaning_as_secondary(self) -> None:
        result = CLASSIFIER.classify_many(["That sounds great, and I will bring the printed handouts."])[0]
        assert result.activity == functions.COMMITMENT
        assert functions.APPROVAL in result.secondary

    def test_cues_carry_the_most_weight_but_meaning_adds_to_them(self) -> None:
        # The weights are part of the contract: 70 percent rules, 30 percent meaning.
        assert pytest.approx(0.70) == functions.CUE_WEIGHT and pytest.approx(0.30) == functions.MEANING_WEIGHT
        assert pytest.approx(1.0) == functions.CUE_WEIGHT + functions.MEANING_WEIGHT

    def test_the_activities_are_exactly_the_apps_closed_registry(self) -> None:
        assert set(functions.REGISTRY_FUNCTION) == set(functions.ALL_ACTIVITIES)
        assert set(functions.REGISTRY_FUNCTION.values()) == {
            "CREATE_DECISION",
            "CREATE_ACTION",
            "CREATE_COMMITMENT",
            "CREATE_QUESTION",
            "CREATE_REQUIREMENT",
            "CREATE_ISSUE",
            "CREATE_RISK",
            "CREATE_APPROVAL",
            "CREATE_REJECTION",
            "CREATE_NOTE",
        }
        assert set(functions.LABELS) == set(functions.ALL_ACTIVITIES)

    def test_the_same_sentence_always_gets_the_same_answer(self) -> None:
        sentence = "Please remember to submit your expense reports before the end of the month."
        assert CLASSIFIER.classify_many([sentence]) == CLASSIFIER.classify_many([sentence])


class TestTasksOwnersAndDeadlines:
    @pytest.mark.parametrize(
        ("sentence", "activity", "task", "due"),
        [
            (
                "Please remember to submit your expense reports before the end of the month.",
                functions.ACTION,
                "submit your expense reports",
                "before the end of the month",
            ),
            (
                "I will send the revised budget spreadsheet to everyone before the end of the day tomorrow.",
                functions.COMMITMENT,
                "send the revised budget spreadsheet to everyone",
                "before the end of the day tomorrow",
            ),
            (
                "Ravi, please book the meeting room for next week's planning session.",
                functions.ACTION,
                "book the meeting room for next week's planning session",
                None,
            ),
            ("Can you update the roadmap by Friday?", functions.ACTION, "update the roadmap", "by Friday"),
            (
                "We need to finish the onboarding checklist within two weeks.",
                functions.ACTION,
                "finish the onboarding checklist",
                "within two weeks",
            ),
            ("OK, I'll take care of the booking.", functions.COMMITMENT, "take care of the booking", None),
            ("Let's meet on Monday morning.", functions.ACTION, "meet", "on Monday morning"),
            ("I will call the client at 3 pm.", functions.COMMITMENT, "call the client", "at 3 pm"),
        ],
    )
    def test_the_task_and_the_deadline_are_pulled_out(
        self, sentence: str, activity: str, task: str, due: str | None
    ) -> None:
        found_due = extract.find_due(sentence)
        got_task = extract.find_task(sentence, activity, found_due)
        assert found_due == due
        if task == "meet":  # "Let's meet" is too short to be called a task with its own words
            assert got_task in (None, "meet")
        else:
            assert got_task == task

    def test_a_deadline_inside_a_possessive_is_not_a_deadline(self) -> None:
        assert extract.find_due("Review next week's agenda.") is None

    def test_no_deadline_means_none(self) -> None:
        assert extract.find_due("Please send the report.") is None

    def test_a_task_of_one_word_is_not_a_task(self) -> None:
        assert extract.find_task("Please go.", functions.ACTION) is None

    def owner(
        self,
        sentence: str,
        activity: str,
        speaker: str = "SPEAKER_01",
        before: str | None = "SPEAKER_00",
        names: frozenset[str] = frozenset(),
    ) -> Any:
        return extract.find_owner(sentence, activity, speaker, before, names)

    def test_a_spoken_name_is_the_owner(self) -> None:
        assert self.owner("Ravi, please book the room.", functions.ACTION) == {"kind": "name", "text": "Ravi"}
        assert self.owner("Priya will prepare the slides.", functions.ACTION) == {
            "kind": "name",
            "text": "Priya",
        }
        assert self.owner("Let's assign it to Meera.", functions.ACTION) == {"kind": "name", "text": "Meera"}

    def test_i_will_makes_the_speaker_the_owner(self) -> None:
        assert self.owner("I will send it.", functions.COMMITMENT) == {
            "kind": "speaker",
            "label": "SPEAKER_01",
        }

    def test_you_makes_whoever_spoke_before_the_owner(self) -> None:
        assert self.owner("Could you send the file?", functions.ACTION) == {
            "kind": "speaker",
            "label": "SPEAKER_00",
        }
        assert self.owner("Could you send the file?", functions.ACTION, before=None) is None
        assert (
            self.owner("Could you send the file?", functions.ACTION, before="SPEAKER_01") is None
        )  # no one else spoke

    def test_we_makes_it_the_teams(self) -> None:
        assert self.owner("We should review the plan.", functions.ACTION) == {"kind": "team"}

    def test_someone_means_nobody_is_named(self) -> None:
        assert self.owner("Someone should write it up.", functions.ACTION) is None

    def test_ordinary_capitalised_words_are_not_names(self) -> None:
        assert self.owner("Thanks, please send it.", functions.ACTION) != {"kind": "name", "text": "Thanks"}
        assert self.owner("Monday we will review it.", functions.ACTION) != {"kind": "name", "text": "Monday"}

    def test_a_known_name_is_a_name_even_if_it_is_also_a_word(self) -> None:
        assert self.owner("Will, please call them.", functions.ACTION, names=frozenset({"Will"})) == {
            "kind": "name",
            "text": "Will",
        }

    def test_a_question_is_open_until_somebody_else_answers_it(self) -> None:
        q = "When does the delivery arrive?"
        assert extract.is_open_question(q, "A", []) is True
        assert (
            extract.is_open_question(q, "A", [("A", "I ask because it is urgent.")]) is True
        )  # same speaker
        assert extract.is_open_question(q, "A", [("B", "The weather is nice.")]) is True  # not an answer
        assert extract.is_open_question(q, "A", [("B", "It arrives on Tuesday.")]) is False
        assert extract.is_open_question(q, "A", [("B", "The delivery comes Tuesday.")]) is False
        assert extract.is_open_question(q, "A", [("B", "Why do you ask?"), ("B", "Yes, Tuesday.")]) is False


MEETING = [
    ("SPEAKER_00", "Good morning everyone, let us go through the agenda."),
    ("SPEAKER_01", "Could you confirm whether the delivery arrives on Monday or on Tuesday morning?"),
    (
        "SPEAKER_00",
        "It arrives on Tuesday. "
        "I will send the revised budget to everyone before the end of the day tomorrow.",
    ),
    ("SPEAKER_01", "Please remember to submit your expense reports before the end of the month."),
    ("SPEAKER_00", "We decided to postpone the launch until the testing is finished."),
    ("SPEAKER_01", "I am worried that the supplier might miss the deadline."),
    ("SPEAKER_00", "Ravi, please book the meeting room for next week."),
    ("SPEAKER_01", "Who is responsible for the documentation?"),
]


def meeting_content() -> dict[str, Any]:
    utterances = [Utterance(i, i * 5000, i * 5000 + 4500, s, t) for i, (s, t) in enumerate(MEETING)]
    voices = [{"label": "SPEAKER_00", "talkMs": 24000}, {"label": "SPEAKER_01", "talkMs": 18000}]
    return analyze(utterances, voices, CLASSIFIER, {"Ravi"})


class TestAnalysingARecording:
    def test_every_sentence_gets_what_context_activity_task_and_owner(self) -> None:
        content = meeting_content()
        assert content["engine"] == "lexical" and content["version"] == 1
        assert len(content["items"]) == 9  # the second utterance holds two sentences
        for item in content["items"]:
            assert set(item) == {
                "segmentIndex", "startMs", "endMs", "speaker", "what", "context", "activity", "secondary",
                "confidence", "task", "owner", "due", "dated", "verb", "keywords", "open",
            }  # fmt: skip
            assert set(item["context"]) == {
                "previous", "previousSpeaker", "topic", "group", "newContext", "similarity",
            }  # fmt: skip
            assert item["activity"] in functions.ALL_ACTIVITIES
            assert 0.0 <= item["confidence"] <= 1.0

    def test_the_findings_match_what_was_said(self) -> None:
        by_what = {i["what"]: i for i in meeting_content()["items"]}
        commitment = by_what["I will send the revised budget to everyone before the end of the day tomorrow."]
        assert commitment["activity"] == functions.COMMITMENT
        assert commitment["task"] == "send the revised budget to everyone"
        assert commitment["owner"] == {"kind": "speaker", "label": "SPEAKER_00"}
        assert commitment["due"] == "before the end of the day tomorrow"

        action = by_what["Please remember to submit your expense reports before the end of the month."]
        assert action["activity"] == functions.ACTION
        assert action["owner"] == {"kind": "speaker", "label": "SPEAKER_00"}  # asked of whoever spoke before

        booking = by_what["Ravi, please book the meeting room for next week."]
        assert booking["owner"] == {"kind": "name", "text": "Ravi"}

        assert (
            by_what["We decided to postpone the launch until the testing is finished."]["activity"]
            == functions.DECISION
        )
        assert (
            by_what["I am worried that the supplier might miss the deadline."]["activity"] == functions.RISK
        )

    def test_context_says_what_came_just_before(self) -> None:
        items = meeting_content()["items"]
        second = items[1]
        assert second["context"]["previous"] == "Good morning everyone, let us go through the agenda."
        assert second["context"]["previousSpeaker"] == "SPEAKER_00"
        assert items[0]["context"]["previous"] is None

    def test_questions_say_whether_they_were_answered(self) -> None:
        items = {i["what"]: i for i in meeting_content()["items"]}
        assert (
            items["Could you confirm whether the delivery arrives on Monday or on Tuesday morning?"]["open"]
            is False
        )
        assert items["Who is responsible for the documentation?"]["open"] is True  # nothing followed it

    def test_counts_add_up(self) -> None:
        content = meeting_content()
        assert sum(content["counts"].values()) == len(content["items"])
        assert content["counts"][functions.ACTION] >= 2

    def test_times_stay_inside_the_utterance_and_in_order(self) -> None:
        items = meeting_content()["items"]
        assert all(a["startMs"] <= a["endMs"] for a in items)
        assert [i["startMs"] for i in items] == sorted(i["startMs"] for i in items)

    def test_voices_are_passed_through_unchanged(self) -> None:
        assert meeting_content()["voices"][0] == {"label": "SPEAKER_00", "talkMs": 24000}

    def test_a_very_long_recording_is_cut_and_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.insights import analyze as module

        monkeypatch.setattr(module, "MAX_SENTENCES", 3)
        utterances = [
            Utterance(i, i * 1000, i * 1000 + 900, "SPEAKER_00", "Please send the file.") for i in range(10)
        ]
        content = analyze(utterances, [], CLASSIFIER)
        assert len(content["items"]) == 3 and content["truncated"] is True

    def test_nothing_said_gives_an_empty_analysis(self) -> None:
        content = analyze([], [], CLASSIFIER)
        assert content["items"] == [] and sum(content["counts"].values()) == 0

    def test_the_result_is_plain_json(self) -> None:
        import json

        assert json.loads(json.dumps(meeting_content())) == meeting_content()


class TestContexts:
    """A sentence is compared with the three before it: similar enough is the same context, else a new one."""

    def vectors(self, *ids: int) -> list[tuple[float, ...]]:
        return [tuple(1.0 if i == k else 0.0 for k in range(6)) for i in ids]

    def test_the_first_sentence_opens_the_first_context(self) -> None:
        links = link_contexts(self.vectors(0), threshold=0.5)
        assert links == [ContextLink(group=0, new=True, similarity=None)]

    def test_a_similar_sentence_stays_in_the_context_and_a_different_one_starts_a_new_context(self) -> None:
        links = link_contexts(self.vectors(0, 0, 1, 1), threshold=0.5)
        assert [(link.group, link.new) for link in links] == [(0, True), (0, False), (1, True), (1, False)]
        assert links[1].similarity == 1.0 and links[2].similarity == 0.0

    def test_it_looks_back_three_sentences_and_no_further(self) -> None:
        out_of_reach = link_contexts(self.vectors(0, 1, 2, 3, 0), threshold=0.5)  # 4 sentences back
        assert out_of_reach[4].new is True
        in_reach = link_contexts(self.vectors(0, 1, 2, 0), threshold=0.5)  # 3 sentences back
        assert (in_reach[3].new, in_reach[3].group) == (False, 0)

    def test_the_threshold_is_a_strict_greater_than(self) -> None:
        pair = [(1.0, 0.0), (0.6, 0.8)]  # cosine exactly 0.6
        assert link_contexts(pair, threshold=0.6)[1].new is True
        assert link_contexts(pair, threshold=0.59)[1].new is False

    def test_the_default_threshold_is_the_measured_one_and_the_words_engine_has_none(self) -> None:
        assert effective_threshold("semantic", None) == 0.30
        assert effective_threshold("lexical", None) is None
        assert effective_threshold("lexical", 0.2) == 0.2  # unless asked for on purpose
        assert effective_threshold("semantic", 0.6) == 0.6

    def test_with_a_sentence_model_every_sentence_gets_a_context_and_the_topics_are_listed(self) -> None:
        classifier = Classifier(ClassAlignedModel())  # type: ignore[arg-type]
        commit = functions.PROTOTYPES[functions.COMMITMENT]
        asking = functions.PROTOTYPES[functions.QUESTION]
        said = [commit[0], commit[1], asking[0], asking[1], commit[2]]
        utterances = [Utterance(i, i * 3000, i * 3000 + 2500, "SPEAKER_00", t) for i, t in enumerate(said)]

        content = analyze(utterances, [{"label": "SPEAKER_00", "talkMs": 12000}], classifier)

        groups = [item["context"]["group"] for item in content["items"]]
        assert groups == [0, 0, 1, 1, 0]  # the last one is within three sentences of the first context
        assert [item["context"]["newContext"] for item in content["items"]] == [
            True,
            False,
            True,
            False,
            False,
        ]
        assert content["contextThreshold"] == 0.30
        assert [(c["group"], c["sentences"]) for c in content["contexts"]] == [(0, 3), (1, 2)]
        story = " ".join(summary.build_summary(content, {}, {}, 15_000)["narrative"])
        assert "It moves through 2 topics:" in story

    def test_with_the_words_engine_no_topic_is_claimed(self) -> None:
        content = meeting_content()
        assert content["contexts"] == [] and content["contextThreshold"] is None
        assert all(item["context"]["group"] is None for item in content["items"])
        assert "topics" not in " ".join(summary.build_summary(content, {}, {}, 45_000)["narrative"])

    def test_the_words_engine_can_be_asked_to_work_contexts_out_anyway(self) -> None:
        utterances = [Utterance(i, i * 4000, i * 4000 + 3500, s, t) for i, (s, t) in enumerate(MEETING)]
        content = analyze(utterances, [], CLASSIFIER, context_similarity=0.2)
        assert content["contextThreshold"] == 0.2
        assert content["contexts"] and all(item["context"]["group"] is not None for item in content["items"])


class TestVerbsAndDeadlines:
    def test_the_verb_is_the_first_word_of_the_task(self) -> None:
        assert extract.find_verb("I will send the revised budget.", "send the revised budget") == "send"
        assert extract.find_verb("We need to finish the checklist.", "finish the checklist") == "finish"

    def test_without_a_task_the_verb_is_the_word_after_a_cue(self) -> None:
        assert extract.find_verb("Rahul will finish.", None) == "finish"
        assert extract.find_verb("We need to talk.", None) == "talk"

    def test_a_task_that_starts_with_a_pronoun_does_not_give_a_pronoun(self) -> None:
        assert extract.find_verb("Please do it.", "it again") != "it"

    def test_a_sentence_with_no_action_has_no_verb(self) -> None:
        assert extract.find_verb("The report is long.", None) is None

    def test_an_activity_with_a_date_is_a_dated_task_and_one_without_is_not(self) -> None:
        by_what = {i["what"]: i for i in meeting_content()["items"]}
        with_date = by_what["I will send the revised budget to everyone before the end of the day tomorrow."]
        assert with_date["dated"] is True and with_date["verb"] == "send"
        plain = by_what["Good morning everyone, let us go through the agenda."]
        assert plain["dated"] is False and plain["verb"] == "go"

    def test_notes_and_questions_are_never_dated_tasks(self) -> None:
        for item in meeting_content()["items"]:
            if item["activity"] in {functions.NOTE, functions.QUESTION, functions.RISK}:
                assert item["dated"] is False and item["verb"] is None

    def test_every_sentence_has_its_main_words(self) -> None:
        by_what = {i["what"]: i for i in meeting_content()["items"]}
        words = by_what["Please remember to submit your expense reports before the end of the month."][
            "keywords"
        ]
        assert len(words) <= 3 and "expense" in words and "submit" in words

    def test_the_summary_says_how_many_tasks_have_a_deadline(self) -> None:
        story = " ".join(summary.build_summary(meeting_content(), {}, {}, 45_000)["narrative"])
        assert re.search(r"\d of the \d tasks? (has|have) a deadline\.", story), story

    def test_a_single_task_without_a_date_is_said_plainly(self) -> None:
        utterances = [Utterance(0, 0, 3000, "SPEAKER_00", "Ravi, please review the draft.")]
        content = analyze(utterances, [], CLASSIFIER)
        assert content["items"][0]["dated"] is False
        story = " ".join(summary.build_summary(content, {}, {}, 3000)["narrative"])
        assert "The task has no deadline." in story

    def test_a_single_dated_task_is_said_plainly(self) -> None:
        utterances = [Utterance(0, 0, 3000, "SPEAKER_00", "Ravi, please review the draft by Friday.")]
        content = analyze(utterances, [], CLASSIFIER)
        story = " ".join(summary.build_summary(content, {}, {}, 3000)["narrative"])
        assert "The task has a deadline." in story


class TestThirdPersonPromises:
    """ "Rahul will finish the report by Friday" is a commitment, and it is Rahul's, not the speaker's."""

    def test_a_promise_about_someone_else_is_a_commitment_with_a_deadline_for_them(self) -> None:
        said = "Rahul will finish the report by Friday."
        item = analyze([Utterance(0, 0, 4000, "SPEAKER_01", said)], [], CLASSIFIER, {"Rahul"})["items"][0]
        assert item["activity"] == functions.COMMITMENT
        assert (item["task"], item["due"], item["verb"], item["dated"]) == (
            "finish the report",
            "by Friday",
            "finish",
            True,
        )
        assert item["owner"] == {"kind": "name", "text": "Rahul"}

    def test_he_or_she_is_not_the_speaker(self) -> None:
        item = analyze(
            [Utterance(0, 0, 4000, "SPEAKER_01", "She will send the invoice tomorrow.")], [], CLASSIFIER
        )["items"][0]
        assert item["activity"] == functions.COMMITMENT
        assert item["owner"] is None  # nobody named: never guessed to be the speaker

    def test_we_will_is_the_teams(self) -> None:
        item = analyze(
            [Utterance(0, 0, 4000, "SPEAKER_00", "We will deliver the first version on Monday.")],
            [],
            CLASSIFIER,
        )["items"][0]
        assert item["activity"] == functions.COMMITMENT and item["owner"] == {"kind": "team"}

    def test_a_forecast_is_not_a_promise(self) -> None:
        for sentence in ("It will rain tomorrow.", "Everything will be fine.", "The meeting will be long."):
            item = analyze([Utterance(0, 0, 3000, "SPEAKER_00", sentence)], [], CLASSIFIER)["items"][0]
            assert item["activity"] != functions.COMMITMENT, sentence


class TestWhoTheTaskIsAssignedTo:
    def test_when_the_speaker_says_their_own_name_it_is_the_same_person(self) -> None:
        utterances = [Utterance(0, 0, 4000, "SPEAKER_00", "Rahul will finish the report by Friday.")]
        content = analyze(utterances, [], CLASSIFIER, {"Rahul"})
        owner = content["items"][0]["owner"]
        assert owner == {"kind": "name", "text": "Rahul"}
        titled = summary.owner_title(owner, {"SPEAKER_00": "Rahul Kumar"}, {"rahul": "Rahul Kumar"})
        assert titled == summary.speaker_title("SPEAKER_00", {"SPEAKER_00": "Rahul Kumar"}) == "Rahul Kumar"

    def test_a_name_said_by_someone_else_goes_to_that_person_not_the_speaker(self) -> None:
        utterances = [Utterance(0, 0, 4000, "SPEAKER_01", "Rahul will finish the report by Friday.")]
        owner = analyze(utterances, [], CLASSIFIER, {"Rahul"})["items"][0]["owner"]
        titled = summary.owner_title(owner, {"SPEAKER_01": "Priya Nair"}, {"rahul": "Rahul Kumar"})
        assert titled == "Rahul Kumar"

    def test_i_will_goes_to_the_speaker_by_label_and_gets_their_name_when_read(self) -> None:
        utterances = [Utterance(0, 0, 4000, "SPEAKER_00", "I will finish the report by Friday.")]
        owner = analyze(utterances, [], CLASSIFIER)["items"][0]["owner"]
        assert owner == {"kind": "speaker", "label": "SPEAKER_00"}
        assert summary.owner_title(owner, {"SPEAKER_00": "Rahul Kumar"}, {}) == "Rahul Kumar"
        assert summary.owner_title(owner, {"SPEAKER_00": None}, {}) == "Speaker 1"  # not matched: never named


class TestTheSummary:
    def build(
        self, names: dict[str, str | None] | None = None, directory: dict[str, str] | None = None
    ) -> dict[str, Any]:
        return summary.build_summary(meeting_content(), names or {}, directory or {}, 45_000)

    def test_it_opens_with_who_and_how_long(self) -> None:
        first = self.build({"SPEAKER_00": "Ana"})["narrative"][0]
        assert first == "This recording is 45 seconds long with 2 speakers: Ana (57%) and Speaker 2 (43%)."

    def test_unmatched_speakers_are_numbered_never_named(self) -> None:
        text_ = " ".join(self.build()["narrative"])
        assert "Speaker 1" in text_ and "Speaker 2" in text_

    def test_actions_say_who_what_and_when(self) -> None:
        lines = " ".join(self.build({"SPEAKER_00": "Ana"})["narrative"])
        assert "Ana to send the revised budget to everyone (before the end of the day tomorrow)." in lines
        assert "Decided: postpone the launch until the testing is finished." in lines

    def test_a_spoken_first_name_becomes_the_full_name_only_when_it_is_unambiguous(self) -> None:
        with_directory = self.build(directory={"ravi": "Ravi Kumar"})
        owners = {line["owner"] for s in with_directory["sections"] for line in s["lines"]}
        assert "Ravi Kumar" in owners
        without = self.build()
        assert "Ravi" in {line["owner"] for s in without["sections"] for line in s["lines"]}

    def test_sections_hold_the_key_lines_with_where_they_were_said(self) -> None:
        sections = {s["key"]: s for s in self.build()["sections"]}
        assert {"decisions", "actions", "commitments", "questions", "concerns"} <= set(sections)
        action = sections["actions"]["lines"][0]
        assert {"text", "said", "speaker", "owner", "due", "atMs", "segmentIndex", "activityLabel"} <= set(
            action
        )

    def test_open_questions_are_listed_before_answered_ones(self) -> None:
        lines = {s["key"]: s for s in self.build()["sections"]}["questions"]["lines"]
        assert lines[0]["open"] is True and lines[-1]["open"] is False

    def test_the_same_analysis_always_gives_the_same_summary(self) -> None:
        assert self.build() == self.build()

    def test_nobody_is_named_unless_the_caller_says_so(self) -> None:
        """A voice that is no longer recognised (consent withdrawn) disappears from the summary at once,
        because names are looked up when it is read and never stored in the analysis."""
        import json

        assert "Ana" not in json.dumps(self.build())
        assert "Ana" not in json.dumps(meeting_content())

    def test_a_recording_with_nothing_in_it_says_so(self) -> None:
        empty = summary.build_summary(analyze([], [], CLASSIFIER), {}, {}, 1000)
        assert empty["narrative"] == ["There was not enough speech in this recording to summarise."]
        assert empty["sections"] == []

    def test_plain_text_export_lists_each_section(self) -> None:
        exported = summary.as_plain_text(self.build({"SPEAKER_00": "Ana"}))
        assert (
            "Action items (" in exported
            and "owner: Ana" in exported
            and "due: by" not in exported.splitlines()[0]
        )
