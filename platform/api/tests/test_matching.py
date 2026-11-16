"""Comparing voice prints, naming speakers, and choosing the threshold."""

from __future__ import annotations

import math
import random
import uuid

import pytest

from app.services import matching
from app.services.matching import Candidate, best_match, cosine


def unit(*components: float) -> tuple[float, ...]:
    return matching.normalize(components)


A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class TestVectors:
    def test_cosine_is_direction_not_length(self) -> None:
        assert cosine((1, 0), (5, 0)) == pytest.approx(1.0)
        assert cosine((1, 0), (0, 3)) == pytest.approx(0.0)
        assert cosine((1, 0), (-2, 0)) == pytest.approx(-1.0)

    def test_degenerate_input_is_never_similar(self) -> None:
        assert cosine((0, 0), (1, 1)) == 0.0
        assert cosine((1, 2), (1, 2, 3)) == 0.0  # different models, different sizes
        assert cosine((), ()) == 0.0

    def test_a_zero_voice_print_cannot_be_normalised(self) -> None:
        with pytest.raises(ValueError, match="zeros"):
            matching.normalize((0.0, 0.0))

    def test_the_mean_of_samples_points_between_them_and_has_unit_length(self) -> None:
        mean = matching.mean_voiceprint([(1, 0), (0, 1)])
        assert mean == pytest.approx((math.sqrt(0.5), math.sqrt(0.5)))
        assert matching.norm(mean) == pytest.approx(1.0)

    def test_samples_of_different_sizes_are_refused(self) -> None:
        with pytest.raises(ValueError, match="different models"):
            matching.mean_voiceprint([(1, 0), (0, 1, 0)])
        with pytest.raises(ValueError, match="No samples"):
            matching.mean_voiceprint([])

    def test_storage_round_trip_is_stable_and_checks_its_size(self) -> None:
        vector = unit(0.25, -0.5, 0.75, 1.0)
        restored = matching.decode_vector(matching.encode_vector(vector), 4)
        assert restored == pytest.approx(vector, abs=1e-6)  # stored as 32-bit floats
        with pytest.raises(ValueError, match="wrong size"):
            matching.decode_vector(matching.encode_vector(vector), 5)


class TestNamingSpeakers:
    people = (Candidate(A, unit(1, 0, 0)), Candidate(B, unit(0, 1, 0)), Candidate(C, unit(0, 0, 1)))

    def test_a_clear_match_gets_its_name_and_score(self) -> None:
        match = best_match(unit(0.95, 0.2, 0.1), self.people, threshold=0.5, margin=0.05)
        assert match is not None
        assert match.person_id == A
        assert match.score > 0.9
        assert match.runner_up is not None and match.runner_up < match.score

    def test_below_the_threshold_nobody_is_named(self) -> None:
        # Closest to A, but only 0.45 similar: not enough to put a name to.
        assert best_match(unit(0.45, 0.6, 0.66), self.people, threshold=0.7, margin=0.0) is None

    def test_two_similar_candidates_are_not_told_apart_by_a_coin_toss(self) -> None:
        similar = (Candidate(A, unit(1, 0.05)), Candidate(B, unit(1, -0.05)))
        # Both score ~0.99 against this voice; the gap is far below the margin.
        assert best_match(unit(1, 0), similar, threshold=0.5, margin=0.05) is None

    def test_the_margin_is_irrelevant_with_one_candidate(self) -> None:
        only = (Candidate(A, unit(1, 0)),)
        assert best_match(unit(1, 0.1), only, threshold=0.5, margin=0.9) is not None

    def test_no_candidates_means_unknown(self) -> None:
        assert best_match(unit(1, 0), (), threshold=0.0, margin=0.0) is None

    def test_a_print_from_a_different_model_is_ignored_not_compared(self) -> None:
        other_model = (Candidate(A, unit(1, 0, 0, 0)),)  # 4 dimensions against a 3-dimension query
        assert best_match(unit(1, 0, 0), other_model, threshold=-1.0, margin=0.0) is None

    def test_the_threshold_is_inclusive(self) -> None:
        exact = best_match(unit(1, 0), (Candidate(A, unit(1, 0)),), threshold=1.0, margin=0.0)
        assert exact is not None


class TestChoosingAThreshold:
    def test_it_picks_the_lowest_threshold_that_meets_the_target(self) -> None:
        same = [0.9, 0.8, 0.75, 0.7, 0.6]
        different = [0.5, 0.4, 0.3, 0.2, 0.1, 0.55]
        result = matching.suggest_threshold(same, different, max_false_accept_rate=0.0)
        assert 0.55 < result.threshold <= 0.6  # just above the highest wrong pair, below the lowest right one
        assert result.false_accept_rate == 0.0
        assert result.false_reject_rate == 0.0
        assert (result.same_pairs, result.different_pairs) == (5, 6)

    def test_overlapping_distributions_trade_misses_for_safety(self) -> None:
        same = [0.9, 0.8, 0.7, 0.45, 0.4]
        different = [0.5, 0.42, 0.3, 0.2, 0.1]
        strict = matching.suggest_threshold(same, different, max_false_accept_rate=0.0)
        lenient = matching.suggest_threshold(same, different, max_false_accept_rate=0.4)
        assert strict.false_accept_rate == 0.0
        assert strict.threshold > lenient.threshold  # asking for safety costs a higher bar...
        assert strict.false_reject_rate >= lenient.false_reject_rate  # ...and more missed names
        assert lenient.false_accept_rate <= 0.4

    def test_a_target_is_never_exceeded(self) -> None:
        rng = random.Random(7)
        same = [rng.gauss(0.7, 0.12) for _ in range(300)]
        different = [rng.gauss(0.25, 0.12) for _ in range(300)]
        for target in (0.0, 0.001, 0.01, 0.05, 0.2):
            result = matching.suggest_threshold(same, different, max_false_accept_rate=target)
            real_far = sum(1 for d in different if d >= result.threshold) / len(different)
            assert real_far <= target
            assert result.false_accept_rate == pytest.approx(real_far)

    def test_it_needs_both_kinds_of_pair_and_a_sane_target(self) -> None:
        with pytest.raises(ValueError, match="Need scores"):
            matching.suggest_threshold([], [0.1])
        with pytest.raises(ValueError, match="Need scores"):
            matching.suggest_threshold([0.9], [])
        with pytest.raises(ValueError, match="fraction"):
            matching.suggest_threshold([0.9], [0.1], max_false_accept_rate=2)

    def test_pairs_are_counted_within_and_across_people(self) -> None:
        by_person = {"ana": [unit(1, 0), unit(1, 0.1), unit(1, -0.1)], "bo": [unit(0, 1), unit(0.1, 1)]}
        same, different = matching.pair_scores(by_person)
        assert len(same) == 3 + 1  # ana: 3 pairs, bo: 1 pair
        assert len(different) == 3 * 2
        assert min(same) > max(different)

    def test_calibrating_from_real_looking_samples_gives_a_working_threshold(self) -> None:
        rng = random.Random(3)

        def voice(centre: tuple[float, ...]) -> tuple[float, ...]:
            return matching.normalize([c + rng.gauss(0, 0.15) for c in centre])

        by_person = {
            "ana": [voice((1, 0, 0, 0)) for _ in range(6)],
            "bo": [voice((0, 1, 0, 0)) for _ in range(6)],
            "cy": [voice((0, 0, 1, 0)) for _ in range(6)],
        }
        result = matching.calibrate(by_person, max_false_accept_rate=0.0)
        assert 0 < result.threshold < 1
        assert result.false_accept_rate == 0.0
        # The suggested threshold really separates a new sample of ana from bo and cy.
        probe = voice((1, 0, 0, 0))
        people = tuple(Candidate(uuid.uuid4(), matching.mean_voiceprint(v)) for v in by_person.values())
        match = best_match(probe, people, threshold=result.threshold, margin=0.0)
        assert match is not None and match.person_id == people[0].person_id

    def test_calibration_needs_enough_people_and_samples(self) -> None:
        with pytest.raises(ValueError, match="two different people"):
            matching.calibrate({"ana": [(1, 0), (1, 0.1)]})
        with pytest.raises(ValueError, match="two samples"):
            matching.calibrate({"ana": [(1, 0)], "bo": [(0, 1), (0, 1)]})
