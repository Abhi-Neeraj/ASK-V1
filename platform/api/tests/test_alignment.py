"""Who said what: joining recognized words to speaker turns."""

from __future__ import annotations

from app.ai.types import AsrSegment, Turn, Word
from app.services.alignment import DEFAULT_SPEAKER, MAX_GAP_MS, align


def words(*items: tuple[str, int, int], probability: float | None = 0.9) -> tuple[Word, ...]:
    """(" Hello", start, end) triples, spaced like faster-whisper writes them."""
    return tuple(Word(start_ms=s, end_ms=e, text=t, probability=probability) for t, s, e in items)


def segment_of(parts: tuple[Word, ...], confidence: float | None = None) -> AsrSegment:
    return AsrSegment(
        start_ms=parts[0].start_ms,
        end_ms=parts[-1].end_ms,
        text="".join(w.text for w in parts).strip(),
        words=parts,
        confidence=confidence,
    )


class TestWithoutSpeakerSeparation:
    def test_everything_is_one_speaker_and_nothing_is_called_uncertain(self) -> None:
        result = align(
            [AsrSegment(0, 2000, " Hello there. "), AsrSegment(2500, 4000, "General Kenobi.")], turns=[]
        )
        assert [(s.speaker, s.text, s.uncertain) for s in result] == [
            (DEFAULT_SPEAKER, "Hello there.", False),
            (DEFAULT_SPEAKER, "General Kenobi.", False),
        ]

    def test_empty_segments_and_disorder_are_handled(self) -> None:
        result = align(
            [AsrSegment(5000, 6000, "second"), AsrSegment(0, 1000, "first"), AsrSegment(2000, 3000, "   ")],
            [],
        )
        assert [s.text for s in result] == ["first", "second"]

    def test_nothing_in_gives_nothing_out(self) -> None:
        assert align([], []) == []
        assert align([], [Turn(0, 1000, "A")]) == []


class TestWholeSegments:
    turns = [Turn(0, 5000, "SPEAKER_00"), Turn(5000, 10000, "SPEAKER_01")]

    def test_a_segment_goes_to_the_speaker_who_covers_most_of_it(self) -> None:
        result = align([AsrSegment(500, 4500, "all mine"), AsrSegment(5200, 9000, "all yours")], self.turns)
        assert [(s.speaker, s.uncertain) for s in result] == [("SPEAKER_00", False), ("SPEAKER_01", False)]

    def test_a_segment_split_close_to_evenly_is_marked_uncertain(self) -> None:
        # 2.5 s of one speaker and 2.0 s of the other: neither clearly owns it.
        result = align([AsrSegment(2500, 7000, "across the turn")], self.turns)
        assert len(result) == 1
        assert result[0].uncertain

    def test_a_mostly_one_sided_segment_is_trusted(self) -> None:
        result = align([AsrSegment(500, 5300, "nearly all one")], self.turns)  # 4.5 s vs 0.3 s
        assert result[0].speaker == "SPEAKER_00"
        assert not result[0].uncertain

    def test_a_segment_nobody_was_speaking_during_is_marked_uncertain(self) -> None:
        result = align([AsrSegment(20_000, 21_000, "from nowhere")], self.turns)
        assert result[0].speaker == "SPEAKER_01"  # the nearest turn
        assert result[0].uncertain

    def test_the_recognizers_confidence_is_kept(self) -> None:
        assert align([AsrSegment(0, 1000, "x", confidence=0.42)], self.turns)[0].confidence == 0.42


class TestWordByWord:
    turns = [Turn(0, 3000, "SPEAKER_00"), Turn(3000, 6000, "SPEAKER_01")]

    def test_a_speaker_change_in_the_middle_of_a_segment_splits_it(self) -> None:
        sentence = words(
            (" How", 1000, 1300), (" are", 1300, 1600), (" you?", 1600, 2000),
            (" Fine,", 3200, 3600), (" thanks", 3600, 4000), (" and", 4000, 4300),
        )  # fmt: skip
        result = align([segment_of(sentence)], self.turns)
        assert [(s.speaker, s.text) for s in result] == [
            ("SPEAKER_00", "How are you?"),
            ("SPEAKER_01", "Fine, thanks and"),
        ]
        assert (result[0].start_ms, result[0].end_ms) == (1000, 2000)
        assert (result[1].start_ms, result[1].end_ms) == (3200, 4300)
        assert not any(s.uncertain for s in result)

    def test_a_segment_by_one_speaker_stays_whole(self) -> None:
        result = align(
            [segment_of(words((" One", 100, 400), (" two", 400, 700), (" three", 700, 1000)))], self.turns
        )
        assert [(s.speaker, s.text) for s in result] == [("SPEAKER_00", "One two three")]

    def test_confidence_is_the_mean_of_the_words(self) -> None:
        parts = (Word(0, 400, " a", 0.8), Word(400, 800, " b", 0.6))
        assert align([segment_of(parts)], self.turns)[0].confidence == 0.7

    def test_models_that_do_not_put_spaces_in_words_still_read_properly(self) -> None:
        parts = words(("Hello", 0, 400), ("world", 400, 800))
        assert align([segment_of(parts)], self.turns)[0].text == "Hello world"

    def test_a_single_stray_word_between_the_same_speaker_is_folded_in_and_flagged(self) -> None:
        # SPEAKER_01 "speaks" for one word in the middle of SPEAKER_00's sentence: a ragged turn edge.
        turns = [Turn(0, 1000, "SPEAKER_00"), Turn(1000, 1400, "SPEAKER_01"), Turn(1400, 4000, "SPEAKER_00")]
        sentence = words(
            (" I", 100, 400),
            (" really", 500, 900),
            (" think", 1000, 1400),
            (" we", 1500, 1800),
            (" should", 1800, 2200),
        )
        result = align([segment_of(sentence)], turns)
        assert [(s.speaker, s.text) for s in result] == [("SPEAKER_00", "I really think we should")]
        assert result[0].uncertain  # a person should confirm it

    def test_a_short_real_reply_from_someone_else_is_not_swallowed(self) -> None:
        # "Yes." is a genuine one-word answer from the second speaker at the end: it stays theirs.
        turns = [Turn(0, 2000, "SPEAKER_00"), Turn(2000, 3000, "SPEAKER_01")]
        parts = words((" Shall", 100, 600), (" we", 600, 900), (" start?", 900, 1500), (" Yes.", 2200, 2700))
        result = align([segment_of(parts)], turns)
        assert [(s.speaker, s.text) for s in result] == [
            ("SPEAKER_00", "Shall we start?"),
            ("SPEAKER_01", "Yes."),
        ]
        assert not any(s.uncertain for s in result)

    def test_a_word_far_from_any_speaker_is_flagged(self) -> None:
        turns = [Turn(0, 1000, "SPEAKER_00")]
        gap_start = 1000 + MAX_GAP_MS * 3
        parts = words(
            (" near", 100, 500),
            (" far", gap_start, gap_start + 300),
            (" farther", gap_start + 300, gap_start + 600),
        )
        result = align([segment_of(parts)], turns)
        assert len(result) == 1  # same nearest speaker throughout
        assert result[0].uncertain  # but two of the three words were nowhere near anyone

    def test_overlapping_speech_is_flagged_not_guessed_silently(self) -> None:
        turns = [Turn(0, 4000, "SPEAKER_00"), Turn(1000, 3000, "SPEAKER_01")]
        parts = words((" both", 1500, 1900), (" talking", 1900, 2400), (" here", 2400, 2800))
        result = align([segment_of(parts)], turns)
        assert result[0].uncertain

    def test_words_that_are_only_whitespace_are_ignored(self) -> None:
        parts = (Word(0, 100, " ", None), Word(100, 500, " hi", 0.9))
        result = align([AsrSegment(0, 500, "hi", words=parts)], self.turns)
        assert [s.text for s in result] == ["hi"]
