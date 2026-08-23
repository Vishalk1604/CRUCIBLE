"""Tests for the fill-mode comparison.

The most important test here is `test_admits_agrees_with_emit_policy`. `baseline._admits`
and `EmitPolicy.admits` are two implementations of one rule, kept separate because one
reasons about labelled scores and the other about AttributeValues. If they drift, the
comparison stops describing the exporter it claims to describe - and it would keep
producing plausible numbers while doing so.
"""

from __future__ import annotations

import pytest

from crucible.baseline import SECONDS_PER_REVIEW, Comparison, ModeResult, _admits, compare
from crucible.emit.rows import EmitPolicy, FillMode
from crucible.schema import AttributeValue, SourceSpan


def scored(n_clean: int, n_dirty: int, threshold_at: float = 0.5):
    """Values where the clean ones score below the threshold and the dirty ones above."""
    rows = [(f"a{i}", threshold_at - 0.2, False) for i in range(n_clean)]
    rows += [(f"b{i}", threshold_at + 0.2, True) for i in range(n_dirty)]
    return rows


class TestPolicyAgreement:
    @pytest.mark.parametrize("mode", list(FillMode))
    @pytest.mark.parametrize("nonconformity", [None, 0.0, 0.3, 0.5, 0.7, 1.0])
    @pytest.mark.parametrize("threshold", [None, 0.5])
    def test_admits_agrees_with_emit_policy(self, mode, nonconformity, threshold):
        value = AttributeValue(
            attribute="x",
            raw="5",
            spans=[SourceSpan(doc_id="erp", quote="5", start=0, end=1)],
        )
        policy = EmitPolicy(fill_mode=mode, threshold=threshold)
        assert _admits(mode, nonconformity, threshold) == policy.admits(value, nonconformity)


class TestCounting:
    def test_all_and_grounded_publish_everything(self):
        comparison = compare(scored(80, 20), n_products=25, threshold=0.5)
        for mode in (FillMode.ALL, FillMode.GROUNDED):
            result = comparison.by_mode(mode)
            assert result.cells_populated == 100
            assert result.errors == 20
            assert result.error_rate == pytest.approx(0.20)

    def test_certified_withholds_the_values_above_the_threshold(self):
        comparison = compare(scored(80, 20), n_products=25, threshold=0.5)
        result = comparison.by_mode(FillMode.CERTIFIED)
        assert result.cells_populated == 80
        assert result.errors == 0
        assert result.error_rate == 0.0

    def test_certified_publishes_nothing_without_a_threshold(self):
        # Same refusal as EmitPolicy: no calibration, no certified claim.
        comparison = compare(scored(80, 20), n_products=25, threshold=None)
        assert comparison.by_mode(FillMode.CERTIFIED).cells_populated == 0
        assert comparison.by_mode(FillMode.ALL).cells_populated == 100

    def test_unscored_values_never_reach_certified(self):
        rows = [("a", None, False), ("b", None, True)]
        comparison = compare(rows, n_products=1, threshold=0.5)
        assert comparison.by_mode(FillMode.CERTIFIED).cells_populated == 0
        assert comparison.by_mode(FillMode.ALL).cells_populated == 2


class TestDerivedNumbers:
    def test_review_hours_follow_the_stated_rate(self):
        result = ModeResult(FillMode.ALL, cells_populated=180, cells_scored=180, errors=18)
        assert result.review_hours == pytest.approx(180 * SECONDS_PER_REVIEW / 3600)

    def test_wrong_cells_shipped_is_populated_times_error_rate(self):
        result = ModeResult(FillMode.ALL, cells_populated=100, cells_scored=100, errors=20)
        assert result.wrong_cells_shipped == pytest.approx(20.0)

    def test_empty_input_does_not_divide_by_zero(self):
        comparison = compare([], n_products=0, threshold=0.5)
        for result in comparison.results:
            assert result.error_rate == 0.0
            assert result.review_hours == 0.0


class TestHeadline:
    def test_states_the_trade_without_statistics_vocabulary(self):
        comparison = compare(scored(80, 20), n_products=25, threshold=0.5)
        headline = comparison.headline()
        assert "20" in headline  # the withheld values
        for jargon in ("conformal", "nonconformity", "coverage", "AUROC", "alpha"):
            assert jargon.lower() not in headline.lower()

    def test_is_silent_when_there_is_no_trade_to_describe(self):
        # Every value certified: inventing a headline here would be spin.
        comparison = compare(scored(80, 0), n_products=25, threshold=0.5)
        assert comparison.headline() == ""

    def test_is_silent_when_modes_are_missing(self):
        assert Comparison(results=[], n_products=0).headline() == ""


class TestTable:
    def test_three_rows_in_a_fixed_order(self):
        rows = compare(scored(80, 20), n_products=25, threshold=0.5).rows()
        assert [r[0] for r in rows] == ["all", "grounded", "certified"]
        assert len(rows) == 3

    def test_simulated_defaults_to_true(self):
        # Non-negotiable #6 again: label it until proven otherwise.
        assert compare([], n_products=0, threshold=None).simulated is True
