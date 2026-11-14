"""Voice signatures: how they are made, compared and matched. Made-up voices with known pitch, plus the
Windows
text-to-speech samples when they have been generated (skipped otherwise)."""

from __future__ import annotations

import dataclasses
import uuid
from pathlib import Path

import numpy as np
import pytest

from app.voiceid import signature as sig
from app.voiceid.audio_io import load_for_analysis
from app.voiceid.matching import SignatureCandidate, best_signature_match, score_candidates
from app.voiceid.pitch import PitchProfile
from tests.voice_synth import HIGH_FEMALE, LOW_MALE, MID_FEMALE, MID_MALE, RATE, Voice, speak

THRESHOLD, MARGIN, GATE = 0.5, 0.1, 0.4


def make(voice: Voice, seed: int = 1, seconds: float = 6.0) -> sig.VoiceSignature:
    made = sig.compute_signature([speak(voice, seconds, seed=seed)])
    assert made is not None
    return made


def candidate(voice: Voice, seeds: tuple[int, ...] = (1, 2, 3)) -> SignatureCandidate:
    """A person enrolled from a few samples, as the server builds them."""
    current = make(voice, seeds[0])
    for count, seed in enumerate(seeds[1:], start=1):
        current = sig.merge_signatures(current, count, make(voice, seed))
    return SignatureCandidate(uuid.uuid4(), current)


class TestMakingASignature:
    def test_it_holds_the_pitch_and_a_timbre_of_fixed_size(self) -> None:
        made = make(MID_MALE)
        assert len(made.timbre) == sig.TIMBRE_DIMENSIONS == 24
        assert made.pitch.low_hz < made.pitch.base_hz < made.pitch.high_hz
        assert made.version == sig.SIGNATURE_VERSION
        assert made.frames >= sig.MIN_VOICED_FRAMES

    def test_silence_noise_and_too_little_speech_make_no_signature(self) -> None:
        assert sig.compute_signature([np.zeros(RATE * 3)]) is None
        assert sig.compute_signature([np.random.default_rng(2).normal(0, 0.1, RATE * 3)]) is None
        assert sig.compute_signature([speak(MID_MALE, 0.35, syllables=False)]) is None
        assert sig.compute_signature([]) is None

    def test_what_is_said_does_not_matter_only_who_says_it(self) -> None:
        a, b = make(MID_FEMALE, seed=1), make(MID_FEMALE, seed=2)
        other = make(MID_MALE, seed=1)
        assert sig.timbre_distance(a.timbre, b.timbre) < sig.timbre_distance(a.timbre, other.timbre)

    def test_loudness_does_not_change_the_signature(self) -> None:
        voice = speak(MID_MALE, 6.0, seed=4)
        loud = sig.compute_signature([voice])
        quiet = sig.compute_signature([voice * 0.1])
        assert loud is not None and quiet is not None
        assert sig.timbre_distance(loud.timbre, quiet.timbre) < 0.05

    def test_the_timbre_survives_storage_unchanged(self) -> None:
        made = make(MID_FEMALE)
        again = sig.decode_timbre(sig.encode_timbre(made.timbre))
        assert sig.timbre_distance(made.timbre, again) < 1e-5

    def test_a_stored_timbre_of_the_wrong_size_is_refused(self) -> None:
        with pytest.raises(ValueError, match="wrong size"):
            sig.decode_timbre(b"\x00" * 10)

    def test_a_signature_with_the_wrong_number_of_values_is_refused(self) -> None:
        profile = PitchProfile(base_hz=150, low_hz=130, high_hz=170, voiced_seconds=3, voiced_ratio=0.7)
        with pytest.raises(ValueError, match="wrong number"):
            sig.VoiceSignature(pitch=profile, timbre=(0.0, 1.0), frames=100)


class TestMergingSamples:
    def test_every_sample_counts_equally(self) -> None:
        a, b, c = make(MID_MALE, 1), make(MID_MALE, 2), make(MID_MALE, 3)
        one_by_one = sig.merge_signatures(sig.merge_signatures(a, 1, b), 2, c)
        mean = np.mean([a.timbre, b.timbre, c.timbre], axis=0)
        assert sig.timbre_distance(one_by_one.timbre, tuple(mean)) < 1e-9
        assert one_by_one.frames == a.frames + b.frames + c.frames

    def test_signatures_from_different_versions_are_never_combined(self) -> None:
        a = make(MID_MALE)
        old = dataclasses.replace(a, version="dsp-0")
        with pytest.raises(ValueError, match="different versions"):
            sig.merge_signatures(a, 1, old)

    def test_a_signature_needs_a_sample_behind_it(self) -> None:
        a = make(MID_MALE)
        with pytest.raises(ValueError, match="at least one sample"):
            sig.merge_signatures(a, 0, a)


class TestSimilarity:
    def test_identical_voices_score_one_and_the_score_falls_with_distance(self) -> None:
        assert sig.similarity(0.0) == 1.0
        assert sig.similarity(0.4) > sig.similarity(0.8) > sig.similarity(1.6) > 0

    def test_timbres_of_different_shapes_cannot_be_compared(self) -> None:
        with pytest.raises(ValueError, match="different shapes"):
            sig.timbre_distance((1.0, 2.0), (1.0,))


class TestPitchIsTheFirstLayer:
    def test_a_low_voice_is_never_compared_with_a_high_one(self) -> None:
        low, high = candidate(LOW_MALE), candidate(HIGH_FEMALE)
        scored = score_candidates(make(LOW_MALE, seed=9), [high, low], pitch_gate_octaves=GATE)
        assert [s.passed_gate for s in scored] == [True, False]
        assert scored[0].person_id == low.person_id  # those that pass the gate are listed first

    def test_even_an_identical_spectrum_is_not_matched_across_the_gate(self) -> None:
        mine = make(MID_FEMALE, seed=5)
        # The same timbre as mine, but belonging to a voice an octave lower.
        octave_lower = dataclasses.replace(
            mine,
            pitch=PitchProfile(mine.pitch.base_hz / 2, mine.pitch.low_hz / 2, mine.pitch.high_hz / 2, 3, 0.7),
        )
        twin = SignatureCandidate(uuid.uuid4(), octave_lower)
        assert best_signature_match(mine, [twin], threshold=0.1, margin=0.0, pitch_gate_octaves=GATE) is None

    def test_voices_of_the_same_pitch_are_told_apart_by_their_timbre(self) -> None:
        bright = Voice(f0=150.0, formants=(750.0, 2100.0, 3100.0))
        dark = Voice(f0=150.0, formants=(450.0, 1250.0, 2300.0))
        ana, ben = candidate(bright), candidate(dark)
        for probe, truth in ((bright, ana), (dark, ben)):
            match = best_signature_match(
                make(probe, seed=20), [ana, ben], threshold=THRESHOLD, margin=MARGIN, pitch_gate_octaves=GATE
            )
            assert match is not None and match.person_id == truth.person_id


class TestChoosingAName:
    def test_each_voice_is_matched_to_its_own_enrolled_person(self) -> None:
        people = {v: candidate(v) for v in (LOW_MALE, MID_MALE, MID_FEMALE, HIGH_FEMALE)}
        for voice, person in people.items():
            match = best_signature_match(
                make(voice, seed=30),
                list(people.values()),
                threshold=THRESHOLD,
                margin=MARGIN,
                pitch_gate_octaves=GATE,
            )
            assert match is not None, voice
            assert match.person_id == person.person_id
            assert match.score >= THRESHOLD

    def test_a_stranger_gets_no_name(self) -> None:
        enrolled = [candidate(MID_MALE), candidate(MID_FEMALE)]
        stranger = Voice(f0=170.0, formants=(430.0, 2300.0, 3400.0), bandwidth=(160.0, 220.0, 300.0))
        assert (
            best_signature_match(
                make(stranger), enrolled, threshold=THRESHOLD, margin=MARGIN, pitch_gate_octaves=GATE
            )
            is None
        )

    def test_two_people_who_sound_the_same_are_not_told_apart_by_a_coin_toss(self) -> None:
        a = candidate(MID_MALE, seeds=(1, 2, 3))
        b = SignatureCandidate(uuid.uuid4(), a.signature)  # the same voice enrolled twice
        match = best_signature_match(
            make(MID_MALE, seed=40), [a, b], threshold=THRESHOLD, margin=MARGIN, pitch_gate_octaves=GATE
        )
        assert match is None

    def test_nobody_enrolled_means_no_match(self) -> None:
        assert (
            best_signature_match(make(MID_MALE), [], threshold=0.1, margin=0.0, pitch_gate_octaves=GATE)
            is None
        )

    def test_a_signature_from_another_version_is_not_a_candidate(self) -> None:
        mine = make(MID_MALE)
        old = SignatureCandidate(uuid.uuid4(), dataclasses.replace(mine, version="dsp-0"))
        assert score_candidates(mine, [old], pitch_gate_octaves=GATE) == []

    def test_one_candidate_needs_no_margin_but_still_the_threshold(self) -> None:
        only = candidate(MID_MALE)
        same = best_signature_match(
            make(MID_MALE, seed=50), [only], threshold=THRESHOLD, margin=MARGIN, pitch_gate_octaves=GATE
        )
        assert same is not None and same.runner_up is None
        too_strict = best_signature_match(
            make(MID_MALE, seed=50), [only], threshold=0.9999, margin=MARGIN, pitch_gate_octaves=GATE
        )
        assert too_strict is None


REAL = Path(__file__).resolve().parents[2] / "data/samples/real-audio"


@pytest.mark.skipif(not (REAL / "single-zira.wav").exists(), reason="run tools/make_sample_audio.py first")
class TestWindowsSpeechVoices:
    """Two text-to-speech voices of similar pitch. Synthetic, so this shows the method works on real speech
    audio, and says nothing about accuracy on real people."""

    def enrolled(self, name: str) -> SignatureCandidate:
        made = [
            sig.compute_signature([load_for_analysis(REAL / "voices" / name / f"enroll-{n}.wav").samples])
            for n in (1, 2, 3)
        ]
        assert all(m is not None for m in made)
        current = made[0]
        assert current is not None
        for count, other in enumerate(made[1:], start=1):
            assert other is not None
            current = sig.merge_signatures(current, count, other)
        return SignatureCandidate(uuid.uuid4(), current)

    def test_a_sentence_that_was_never_enrolled_is_matched_to_the_right_voice(self) -> None:
        hazel, zira = self.enrolled("hazel"), self.enrolled("zira")
        for file, truth in (("single-hazel.wav", hazel), ("single-zira.wav", zira)):
            probe = sig.compute_signature([load_for_analysis(REAL / file).samples])
            assert probe is not None
            match = best_signature_match(
                probe, [hazel, zira], threshold=THRESHOLD, margin=MARGIN, pitch_gate_octaves=GATE
            )
            assert match is not None, file
            assert match.person_id == truth.person_id, file
