"""Pitch detection: base, low and high pitch of a voice, measured from audio."""

from __future__ import annotations

import numpy as np
import pytest

from app.voiceid import pitch
from app.voiceid.audio_io import Audio, resample
from tests.voice_synth import HIGH_FEMALE, LOW_MALE, MID_FEMALE, MID_MALE, RATE, Voice, speak


def cents(measured: float, truth: float) -> float:
    """Error in cents (hundredths of a semitone); 100 is a semitone."""
    return float(1200 * np.log2(measured / truth))


class TestMeasuringPitch:
    @pytest.mark.parametrize("voice", [LOW_MALE, MID_MALE, MID_FEMALE, HIGH_FEMALE])
    def test_base_pitch_is_found_within_a_semitone(self, voice: Voice) -> None:
        profile = pitch.estimate_pitch([speak(voice, 6.0, seed=3)])
        assert profile is not None
        assert abs(cents(profile.base_hz, voice.f0)) < 100, (profile, voice.f0)

    def test_low_is_below_base_and_high_above_it(self) -> None:
        profile = pitch.estimate_pitch([speak(MID_MALE, 8.0, seed=4)])
        assert profile is not None
        assert profile.low_hz < profile.base_hz < profile.high_hz
        # The made-up voice wanders about 6 percent either side, so the range is real but not huge.
        assert profile.high_hz / profile.low_hz < 1.4

    def test_a_wider_wander_gives_a_wider_range(self) -> None:
        calm = pitch.estimate_pitch([speak(Voice(f0=150.0, wobble=0.02), 8.0, seed=5)])
        lively = pitch.estimate_pitch([speak(Voice(f0=150.0, wobble=0.15), 8.0, seed=5)])
        assert calm is not None and lively is not None
        # Compared as ranges in octaves, because that is how a range of pitch is heard.
        calm_range = pitch.octaves_apart(calm.high_hz, calm.low_hz)
        lively_range = pitch.octaves_apart(lively.high_hz, lively.low_hz)
        assert lively_range > 2 * calm_range

    def test_a_higher_voice_has_a_higher_base_pitch(self) -> None:
        low = pitch.estimate_pitch([speak(LOW_MALE, 5.0, seed=1)])
        high = pitch.estimate_pitch([speak(HIGH_FEMALE, 5.0, seed=1)])
        assert low is not None and high is not None
        assert high.base_hz > 2 * low.base_hz * 0.9

    def test_the_same_voice_twice_gives_nearly_the_same_pitch(self) -> None:
        a = pitch.estimate_pitch([speak(MID_FEMALE, 6.0, seed=10)])
        b = pitch.estimate_pitch([speak(MID_FEMALE, 6.0, seed=11)])
        assert a is not None and b is not None
        assert abs(cents(a.base_hz, b.base_hz)) < 60

    def test_a_level_change_does_not_change_the_pitch(self) -> None:
        loud = speak(MID_MALE, 5.0, seed=2)
        quiet = pitch.estimate_pitch([loud * 0.05])
        normal = pitch.estimate_pitch([loud])
        assert quiet is not None and normal is not None
        assert abs(cents(quiet.base_hz, normal.base_hz)) < 30

    def test_a_little_background_noise_does_not_move_it(self) -> None:
        clean = speak(MID_MALE, 6.0, seed=6)
        noise = np.random.default_rng(0).normal(0, 0.02, len(clean))
        noisy = pitch.estimate_pitch([clean + noise])
        assert noisy is not None
        assert abs(cents(noisy.base_hz, MID_MALE.f0)) < 150


class TestWhenThereIsNothingToMeasure:
    def test_silence_has_no_pitch(self) -> None:
        assert pitch.estimate_pitch([np.zeros(RATE * 3)]) is None

    def test_noise_has_no_pitch(self) -> None:
        noise = np.random.default_rng(1).normal(0, 0.1, RATE * 3)
        assert pitch.estimate_pitch([noise]) is None

    def test_a_clip_shorter_than_one_frame_has_no_pitch(self) -> None:
        assert pitch.estimate_pitch([np.ones(100) * 0.1]) is None

    def test_too_little_speech_is_not_enough_to_judge_a_voice(self) -> None:
        # About a quarter of a second of voice: below the minimum.
        assert pitch.estimate_pitch([speak(MID_MALE, 0.35, seed=1, syllables=False)]) is None

    def test_no_pieces_at_all(self) -> None:
        assert pitch.estimate_pitch([]) is None


class TestMergingAndComparing:
    def test_pieces_are_tracked_separately_so_the_seam_is_never_read_as_speech(self) -> None:
        a, b = speak(MID_MALE, 3.0, seed=1), speak(MID_MALE, 3.0, seed=2)
        together = pitch.estimate_pitch([a, b])
        assert together is not None
        assert abs(cents(together.base_hz, MID_MALE.f0)) < 100

    def test_merging_samples_weights_each_by_its_voiced_speech(self) -> None:
        short = pitch.PitchProfile(base_hz=100, low_hz=90, high_hz=110, voiced_seconds=1, voiced_ratio=0.5)
        long = pitch.PitchProfile(base_hz=200, low_hz=180, high_hz=220, voiced_seconds=3, voiced_ratio=0.5)
        merged = pitch.merge_profiles([short, long])
        assert 100 < merged.base_hz < 200
        assert merged.base_hz > 150  # closer to the one with three times the speech
        assert merged.voiced_seconds == 4
        assert merged.low_hz <= merged.base_hz <= merged.high_hz

    def test_a_profile_must_be_in_order(self) -> None:
        with pytest.raises(ValueError, match="low <= base <= high"):
            pitch.PitchProfile(base_hz=100, low_hz=120, high_hz=130, voiced_seconds=1, voiced_ratio=1)

    def test_octaves_apart(self) -> None:
        assert pitch.octaves_apart(100, 200) == pytest.approx(1.0)
        assert pitch.octaves_apart(200, 100) == pytest.approx(1.0)
        assert pitch.octaves_apart(150, 150) == 0.0

    def test_merging_nothing_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="No pitch profiles"):
            pitch.merge_profiles([])


class TestSampleRates:
    def test_audio_at_another_rate_is_brought_to_the_analysis_rate(self) -> None:
        voice = speak(MID_MALE, 4.0, seed=9)
        # Pretend it was recorded at 32 kHz by repeating each sample, then bring it back down.
        doubled = Audio(np.repeat(voice, 2), 2 * RATE)
        back = resample(doubled)
        assert back.rate == RATE
        profile = pitch.estimate_pitch([back.samples])
        assert profile is not None
        assert abs(cents(profile.base_hz, MID_MALE.f0)) < 100


class TestSilenceIsNeverVoiced:
    def test_digital_silence_between_speech_is_not_counted_as_a_voice(self) -> None:
        """Regression: removing the average of a whole recording once turned exact silence into a steady
        offset that looked perfectly periodic, so every gap between turns was reported as a 64 Hz voice."""
        speech = speak(MID_MALE, 3.0, seed=1)
        quiet = np.zeros(RATE * 2)
        track = pitch.track_pitch(np.concatenate([speech, quiet, speech]))
        gap = slice(int(3.05 / pitch.HOP_SECONDS), int(4.95 / pitch.HOP_SECONDS))
        assert not track.voiced[gap].any()

    def test_a_constant_offset_in_the_signal_is_not_pitch(self) -> None:
        speech = speak(MID_MALE, 4.0, seed=2)
        shifted = pitch.estimate_pitch([speech + 0.2])
        plain = pitch.estimate_pitch([speech])
        assert shifted is not None and plain is not None
        assert abs(cents(shifted.base_hz, plain.base_hz)) < 30
