"""Tests for conformal risk control.

The central test in this file is `TestGuaranteeHolds`, which is the project's entire
claim stated as an assertion: over many independent trials, the error rate among
auto-published values on *held-out* data must come in at or below the promised alpha.

If that test passes, the certificate means something. If it fails, everything else in
the repository is decoration.
"""

import numpy as np
import pytest

from crucible.certify.conformal import (
    clopper_pearson_upper,
    required_sample_size,
    risk_coverage_curve,
    select_threshold,
)


def synthetic(n: int, rng: np.random.Generator, sharpness: float = 12.0, midpoint: float = 0.8):
    """A scorer with realistic behaviour: reliable at low scores, unreliable at high ones.

    Errors are drawn from a logistic in the score. The defaults put the overall error
    rate near 10%, which is roughly where frontier models sit on product attribute
    extraction, while leaving the low-scoring bulk genuinely trustworthy. That is the
    regime the whole system is designed for: a model that is mostly right, with the
    errors concentrated somewhere a verifier can find them.

    A perfect scorer would make the guarantee trivial and prove nothing; a scorer whose
    best values are still 10% wrong cannot support a 2% guarantee at all, and correctly
    causes `select_threshold` to refuse.
    """
    scores = rng.uniform(0.0, 1.0, size=n)
    error_prob = 1.0 / (1.0 + np.exp(-sharpness * (scores - midpoint)))
    is_error = rng.random(n) < error_prob
    return scores, is_error


class TestClopperPearson:
    def test_zero_errors_still_bounds_above_zero(self):
        # Observing no errors in 100 trials does not prove the error rate is 0.
        bound = clopper_pearson_upper(0, 100, 0.05)
        assert 0.0 < bound < 0.05

    def test_bound_tightens_with_more_data(self):
        assert clopper_pearson_upper(0, 1000, 0.05) < clopper_pearson_upper(0, 100, 0.05)

    def test_bound_loosens_with_more_errors(self):
        assert clopper_pearson_upper(10, 100, 0.05) > clopper_pearson_upper(1, 100, 0.05)

    def test_degenerate_inputs_return_the_vacuous_bound(self):
        assert clopper_pearson_upper(0, 0, 0.05) == 1.0
        assert clopper_pearson_upper(5, 5, 0.05) == 1.0

    def test_matches_known_value(self):
        # 0 successes in 10 trials, 95% one-sided: 1 - 0.05^(1/10) = 0.2589
        assert clopper_pearson_upper(0, 10, 0.05) == pytest.approx(0.2589, abs=1e-3)


class TestRequiredSampleSize:
    def test_stricter_alpha_needs_more_data(self):
        assert required_sample_size(0.01, 0.05) > required_sample_size(0.05, 0.05)

    def test_two_percent_at_95_confidence_needs_about_150(self):
        # The number worth quoting: this is why calibration set size is a real constraint
        # and not a detail.
        assert 140 <= required_sample_size(0.02, 0.05) <= 160

    def test_rejects_nonsense_parameters(self):
        with pytest.raises(ValueError):
            required_sample_size(0.0, 0.05)
        with pytest.raises(ValueError):
            required_sample_size(0.5, 1.5)


class TestGuaranteeHolds:
    """The claim, tested. Calibrate on one sample, measure on another."""

    @pytest.mark.parametrize("alpha", [0.02, 0.05, 0.10])
    def test_realized_error_respects_the_promise_across_trials(self, alpha):
        rng = np.random.default_rng(20260820)
        delta = 0.05
        trials = 100
        violations = 0
        coverages = []

        for _ in range(trials):
            cal_scores, cal_errors = synthetic(1500, rng)
            test_scores, test_errors = synthetic(1500, rng)

            selection = select_threshold(cal_scores, cal_errors, alpha, delta)
            if not selection.feasible:
                continue

            accepted = test_scores <= selection.threshold
            if accepted.sum() == 0:
                continue

            realized = test_errors[accepted].mean()
            coverages.append(accepted.mean())
            if realized > alpha:
                violations += 1

        assert coverages, "no trial produced a usable threshold"
        violation_rate = violations / trials

        # The bound permits violations at rate delta. Allowing 3x delta keeps the test
        # from flaking on sampling noise while still failing loudly if the procedure is
        # actually broken - a naive best-threshold scan violates at roughly 50%.
        assert violation_rate <= 3 * delta, (
            f"promised <= {alpha:.0%} error at {1 - delta:.0%} confidence, but "
            f"{violation_rate:.1%} of trials exceeded it on held-out data"
        )

    def test_relaxing_alpha_buys_coverage(self):
        # The dial has to actually do something, monotonically.
        rng = np.random.default_rng(7)
        scores, errors = synthetic(5000, rng)

        coverages = []
        for alpha in [0.02, 0.05, 0.10, 0.20]:
            selection = select_threshold(scores, errors, alpha, 0.05)
            assert selection.feasible, f"expected alpha={alpha} to be certifiable at n=5000"
            coverages.append(selection.stats.coverage)

        assert coverages == sorted(coverages), f"coverage not monotone in alpha: {coverages}"

    def test_certified_bound_never_exceeds_the_request(self):
        rng = np.random.default_rng(11)
        scores, errors = synthetic(5000, rng)
        selection = select_threshold(scores, errors, 0.05, 0.05)
        assert selection.feasible
        assert selection.stats.error_upper_bound <= 0.05
        assert selection.stats.is_valid


class TestRefusesWhatItCannotBack:
    def test_declines_when_calibration_set_is_too_small(self):
        # 40 points cannot support a 2% claim at 95% confidence no matter how clean they
        # look. Saying so is the correct behaviour.
        rng = np.random.default_rng(3)
        scores, errors = synthetic(40, rng)
        selection = select_threshold(scores, errors, 0.02, 0.05)
        assert not selection.feasible
        assert "at least" in selection.reason
        assert selection.threshold == float("-inf")

    def test_declines_when_the_scorer_is_uninformative(self):
        # Random scores carry no signal, so no threshold can be certified at a strict
        # alpha. The system must not invent one.
        rng = np.random.default_rng(5)
        scores = rng.uniform(size=3000)
        errors = rng.random(3000) < 0.30
        selection = select_threshold(scores, errors, 0.02, 0.05)
        assert not selection.feasible

    def test_empty_calibration_set(self):
        selection = select_threshold([], [], 0.05, 0.05)
        assert not selection.feasible
        assert "empty" in selection.reason

    def test_rejects_mismatched_inputs(self):
        with pytest.raises(ValueError, match="differ in length"):
            select_threshold([0.1, 0.2], [True], 0.05)

    @pytest.mark.parametrize(
        ("alpha", "delta"), [(0.0, 0.05), (1.0, 0.05), (0.05, 0.0), (0.05, 1.0)]
    )
    def test_rejects_out_of_range_parameters(self, alpha, delta):
        with pytest.raises(ValueError):
            select_threshold([0.1] * 100, [False] * 100, alpha, delta)


class TestRiskCoverageCurve:
    def test_produces_a_point_per_requested_level(self):
        rng = np.random.default_rng(13)
        scores, errors = synthetic(4000, rng)
        curve = risk_coverage_curve(scores, errors, [0.01, 0.02, 0.05, 0.10])
        assert len(curve) == 4
        assert all(0.0 <= coverage <= 1.0 for _, coverage, _ in curve)

    def test_reports_zero_coverage_for_uncertifiable_levels(self):
        rng = np.random.default_rng(17)
        scores, errors = synthetic(200, rng)
        curve = risk_coverage_curve(scores, errors, [0.001])
        assert curve[0][1] == 0.0
