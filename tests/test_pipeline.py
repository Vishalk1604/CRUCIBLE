"""Tests for the end-to-end run.

The comparison logic gets the most attention here, because it decides what counts as an
error and therefore what the guarantee is calibrated against. If `0.5 in` were scored as
disagreeing with `12.7 mm`, the pipeline would manufacture errors and then dutifully
certify itself against them.
"""

import pytest

from crucible.pipeline import run, values_agree
from crucible.schema import AttributeSpec, ValueKind

LENGTH = AttributeSpec(
    name="bore", kind=ValueKind.QUANTITY, dimension="[length]", canonical_unit="millimeter"
)
NOMINAL = AttributeSpec(
    name="body_material", kind=ValueKind.NOMINAL, vocabulary=["316 stainless steel", "brass"]
)


class TestValueComparison:
    def test_identical_strings_agree(self):
        assert values_agree("25 mm", "25 mm", LENGTH)

    def test_case_and_whitespace_are_ignored(self):
        assert values_agree(" 316 Stainless Steel ", "316 stainless steel", NOMINAL)

    def test_equivalent_units_agree(self):
        # Scoring these as different would invent errors the system then gets certified
        # against.
        assert values_agree('0.5"', "12.7 mm", LENGTH)
        assert values_agree("1 in", "25.4 mm", LENGTH)

    def test_different_magnitudes_disagree(self):
        assert not values_agree("25 mm", "52 mm", LENGTH)

    def test_wrong_dimension_disagrees(self):
        assert not values_agree("25 kg", "25 mm", LENGTH)

    def test_dropped_unit_does_not_silently_agree(self):
        # Treating "12.7" as matching "12.7 mm" would hide the unit-dropped fault
        # entirely, and that fault is precisely one the verifiers must be shown catching.
        assert not values_agree("12.7", "12.7 mm", LENGTH)

    def test_both_unitless_compare_numerically(self):
        assert values_agree("12.7", "12.7", LENGTH)

    def test_nominal_values_do_not_get_numeric_treatment(self):
        assert not values_agree("brass", "316 stainless steel", NOMINAL)

    def test_unparseable_numeric_disagrees_rather_than_raising(self):
        assert not values_agree("see chart", "25 mm", LENGTH)

    def test_tolerance_absorbs_conversion_rounding_but_not_digit_errors(self):
        assert values_agree("25.0001 mm", "25 mm", LENGTH)
        assert not values_agree("28 mm", "25 mm", LENGTH)


# Module-scoped so the run happens once. The parameters are chosen because they
# actually yield a certificate: at a stricter alpha the guarantee assertions below would
# skip, and a test that skips is not testing the thing it was written for.
@pytest.fixture(scope="module")
def result():
    return run(alpha=0.07, n_per_category=400)


class TestEndToEnd:
    def test_produces_scored_values(self, result):
        assert result.n_test > 0
        assert result.certified

    def test_baseline_error_is_near_the_injected_rate(self, result):
        # Sanity check that faults are reaching the scored set at roughly the rate asked
        # for. A large gap would mean corruptions are landing on values that are then
        # excluded from scoring.
        assert 0.05 <= result.baseline_error <= 0.20

    def test_scorer_carries_ordering_information(self, result):
        # Below about 0.6 there is not enough separation for any threshold to certify,
        # and a failure to certify would be a scorer problem rather than a bound problem.
        assert result.auroc > 0.6

    def test_result_is_marked_as_simulated(self, result):
        # Every number from this path inherits the fault-injection caveat.
        assert result.simulated is True

    def test_guarantee_holds(self, result):
        assert result.certificate is not None, (
            "these parameters are chosen to produce a certificate; if none was issued "
            "the guarantee assertion is silently not running"
        )
        assert result.realized_error <= result.certificate.calibration.alpha
        assert result.certificate.calibration.error_upper_bound <= 0.07

    def test_verification_beats_publishing_everything(self, result):
        assert result.certificate is not None
        assert result.realized_error < result.baseline_error, (
            "auto-published values are no cleaner than the raw extraction, so "
            "verification is adding nothing"
        )


class TestRefusal:
    def test_declines_alpha_the_current_verifiers_cannot_support(self):
        # With only the dimensional and constraint verifiers wired, separation is not
        # sufficient for a 1% guarantee. Refusing is correct; inventing a threshold
        # would not be.
        result = run(alpha=0.01, n_per_category=250)
        assert result.certificate is None
        assert result.selection.reason
        assert "no guarantee available" in result.summary()


class TestDeterminism:
    def test_same_seed_reproduces_the_run(self):
        a = run(alpha=0.07, n_per_category=150, seed=5)
        b = run(alpha=0.07, n_per_category=150, seed=5)
        assert a.baseline_error == b.baseline_error
        assert a.realized_error == b.realized_error
        assert a.n_test == b.n_test
