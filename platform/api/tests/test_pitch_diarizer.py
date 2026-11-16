"""Telling speakers apart by pitch first and voice colour second (experimental, no model)."""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pytest

from app.ai.local import PitchDiarizer, build_local, local_capabilities
from app.config import Settings
from app.voiceid.diarize import SpokenTurn, diarize_samples
from tests.voice_synth import (
    HIGH_FEMALE,
    LOW_MALE,
    MID_FEMALE,
    RATE,
    Voice,
    conversation,
    speak,
    to_wav_bytes,
)

GATE, CUT = 0.45, 1.2


def who_spoke_at(turns: list[SpokenTurn], ms: int) -> int | None:
    for turn in turns:
        if turn.start_ms <= ms < turn.end_ms:
            return turn.speaker
    return None


def label_of_each_true_turn(turns: list[SpokenTurn], truth: list[tuple[int, int, int]]) -> list[int | None]:
    """The separator's speaker number at the middle of each true turn."""
    return [who_spoke_at(turns, (start + end) // 2) for start, end, _ in truth]


class TestSeparatingVoices:
    def test_a_low_voice_and_a_high_voice_are_two_speakers(self) -> None:
        audio, truth = conversation(
            [(LOW_MALE, 5, 1), (HIGH_FEMALE, 5, 2), (LOW_MALE, 5, 3), (HIGH_FEMALE, 5, 4)]
        )
        turns = diarize_samples(audio, pitch_gate_octaves=GATE, cluster_distance=CUT)
        labels = label_of_each_true_turn(turns, truth)
        assert labels == [0, 1, 0, 1]

    def test_three_voices_are_three_speakers_numbered_by_first_appearance(self) -> None:
        audio, truth = conversation(
            [
                (HIGH_FEMALE, 5, 1),
                (LOW_MALE, 5, 2),
                (MID_FEMALE, 5, 3),
                (HIGH_FEMALE, 5, 4),
                (LOW_MALE, 5, 5),
                (MID_FEMALE, 5, 6),
            ]
        )
        turns = diarize_samples(audio, pitch_gate_octaves=GATE, cluster_distance=CUT)
        assert label_of_each_true_turn(turns, truth) == [0, 1, 2, 0, 1, 2]

    def test_voices_of_the_same_pitch_are_separated_by_their_colour(self) -> None:
        bright = Voice(f0=150.0, formants=(750.0, 2100.0, 3100.0))
        dark = Voice(f0=150.0, formants=(450.0, 1250.0, 2300.0))
        audio, truth = conversation([(bright, 6, 1), (dark, 6, 2), (bright, 6, 3), (dark, 6, 4)])
        turns = diarize_samples(audio, pitch_gate_octaves=GATE, cluster_distance=CUT)
        assert label_of_each_true_turn(turns, truth) == [0, 1, 0, 1]

    def test_one_voice_talking_stays_one_speaker(self) -> None:
        audio, _ = conversation([(MID_FEMALE, 6, 1), (MID_FEMALE, 6, 2), (MID_FEMALE, 6, 3)])
        turns = diarize_samples(audio, pitch_gate_octaves=GATE, cluster_distance=CUT)
        assert {t.speaker for t in turns} == {0}

    def test_turns_do_not_overlap_and_stay_inside_the_audio(self) -> None:
        audio, _ = conversation([(LOW_MALE, 4, 1), (HIGH_FEMALE, 4, 2), (LOW_MALE, 4, 3)])
        turns = diarize_samples(audio, pitch_gate_octaves=GATE, cluster_distance=CUT)
        assert turns
        for before, after in itertools.pairwise(turns):
            assert before.end_ms <= after.start_ms
        assert all(0 <= t.start_ms < t.end_ms <= len(audio) * 1000 // RATE + 50 for t in turns)

    def test_a_tighter_pitch_gate_never_merges_what_a_looser_one_keeps_apart(self) -> None:
        audio, _ = conversation([(LOW_MALE, 5, 1), (HIGH_FEMALE, 5, 2)])
        loose = {t.speaker for t in diarize_samples(audio, pitch_gate_octaves=2.0, cluster_distance=0.1)}
        tight = {t.speaker for t in diarize_samples(audio, pitch_gate_octaves=0.2, cluster_distance=0.1)}
        assert len(tight) >= len(loose)


class TestWhenThereIsNoSpeech:
    def test_silence_gives_no_turns(self) -> None:
        assert diarize_samples(np.zeros(RATE * 5), pitch_gate_octaves=GATE, cluster_distance=CUT) == []

    def test_noise_gives_no_turns(self) -> None:
        noise = np.random.default_rng(3).normal(0, 0.1, RATE * 5)
        assert diarize_samples(noise, pitch_gate_octaves=GATE, cluster_distance=CUT) == []

    def test_a_blip_of_voice_is_not_a_speaker(self) -> None:
        blip = speak(MID_FEMALE, 0.3, syllables=False)
        assert diarize_samples(blip, pitch_gate_octaves=GATE, cluster_distance=CUT) == []


class TestAsAProvider:
    @pytest.fixture
    def pitch_settings(self, settings: Settings) -> Settings:
        return settings.model_copy(update={"ai_diarizer": "pitch"})

    def test_it_is_available_without_any_model_and_says_it_is_experimental(
        self, pitch_settings: Settings
    ) -> None:
        capabilities = local_capabilities(pitch_settings)
        assert capabilities.diarization is True
        assert any("EXPERIMENTAL pitch method" in note for note in capabilities.notes)

    def test_it_turns_a_wav_file_into_labelled_turns(self, pitch_settings: Settings, tmp_path: Path) -> None:
        audio, truth = conversation([(LOW_MALE, 5, 1), (HIGH_FEMALE, 5, 2), (LOW_MALE, 5, 3)])
        path = tmp_path / "talk.wav"
        path.write_bytes(to_wav_bytes(audio))

        result = PitchDiarizer(pitch_settings).diarize(path)

        assert "experimental" in result.model.lower()  # the transcript and the screen both rely on this word
        assert {t.speaker for t in result.turns} == {"SPEAKER_00", "SPEAKER_01"}
        first, second, third = (
            next(t.speaker for t in result.turns if t.start_ms <= (s + e) // 2 < t.end_ms)
            for s, e, _ in truth
        )
        assert first == third != second

    def test_a_file_that_is_not_a_wav_is_a_permanent_failure(
        self, pitch_settings: Settings, tmp_path: Path
    ) -> None:
        from app.ai.types import PermanentError

        path = tmp_path / "nothing.wav"
        path.write_bytes(b"this is not audio")
        with pytest.raises(PermanentError):
            PitchDiarizer(pitch_settings).diarize(path)

    def test_the_default_setting_does_not_turn_it_on(self, settings: Settings) -> None:
        """Separation by pitch is opt-in (ASK_AI_DIARIZER=pitch): an existing deployment is not changed."""
        assert settings.ai_diarizer == "auto"
        assert not any("pitch method" in note for note in local_capabilities(settings).notes)

    def test_the_local_provider_uses_it_when_asked(self, pitch_settings: Settings) -> None:
        if not local_capabilities(pitch_settings).transcription:
            pytest.skip("the local provider needs the speech model installed")
        assert isinstance(build_local(pitch_settings).diarizer, PitchDiarizer)
