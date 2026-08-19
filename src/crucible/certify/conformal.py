"""Conformal risk control: turning scores into a guarantee.

Every other product in this space stops at a confidence score. A score tells you that
one value is probably fine. It cannot tell you what fraction of a catalog is safe to
publish, because a miscalibrated 0.9 and a well-calibrated 0.9 look identical.

This module answers the question a distributor actually has: *if I auto-publish
everything the system is confident about, how wrong will my catalog be?* The answer
comes with a finite-sample statistical bound rather than a vibe.

The procedure
-------------
Given a calibration set of values with known-correct labels and computed nonconformity
scores, we choose a threshold tau such that among values scoring at or below tau, the
error rate is at most alpha with probability at least 1 - delta.

Two properties are non-negotiable:

**The bound must be honest about multiplicity.** Scanning every candidate threshold and
keeping whichever looked best on the calibration set is the classic way to produce a
guarantee that does not hold out of sample. We use fixed-sequence testing: candidate
thresholds are ordered in advance from most conservative to least, each is tested at
level delta, and the sequence stops at the first threshold that fails. Everything
rejected before the stop is rejected with family-wise error control, so no correction
term is needed and none is being quietly skipped.

**The system must refuse to promise what it cannot back.** Certifying a 2% error rate at
95% confidence requires roughly 150 accepted calibration points with zero observed
errors - that is just what the Clopper-Pearson bound costs. With a small calibration set
the honest answer is "I cannot certify this", not a threshold that happens to look good
on forty examples. `select_threshold` returns an infeasible result in that case, and the
caller is expected to surface it rather than paper over it.

The guarantee holds for values exchangeable with the calibration set. That assumption is
recorded on every Certificate, because a new product category or an unseen document
format breaks it and the number stops meaning what it says.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.stats import beta

from crucible.verdict import Assay, CalibrationStats, CertifiedValue, Decision

#: Minimum accepted calibration points before a threshold is even considered. Below this
#: the Clopper-Pearson interval is so wide that any "guarantee" is noise.
DEFAULT_MIN_ACCEPTED = 30

#: Number of candidate thresholds in the fixed testing sequence. Fixed in advance,
#: because the sequence must not depend on the data it is tested against.
DEFAULT_GRID_SIZE = 50


def clopper_pearson_upper(errors: int, n: int, delta: float) -> float:
    """Exact upper confidence bound on a binomial error rate.

    Returns the value p such that, having observed `errors` failures in `n` trials, the
    true error rate is at most p with confidence 1 - delta. Exact rather than normal-
    approximate, because the interesting regime is a handful of errors in a few hundred
    trials, where the normal approximation is worthless.
    """
    if n <= 0:
        return 1.0
    if errors >= n:
        return 1.0
    return float(beta.ppf(1.0 - delta, errors + 1, n - errors))


def required_sample_size(alpha: float, delta: float) -> int:
    """Accepted points needed to certify `alpha` at confidence 1 - delta, assuming no
    observed errors.

    Solves (delta)^(1/n) >= 1 - alpha. Useful for telling an operator *why* their
    calibration set is too small, rather than only that it is.
    """
    if not 0 < alpha < 1 or not 0 < delta < 1:
        raise ValueError("alpha and delta must lie strictly between 0 and 1")
    return int(np.ceil(np.log(delta) / np.log(1 - alpha)))


@dataclass(frozen=True)
class ThresholdSelection:
    """Outcome of calibration, including the case where nothing could be certified."""

    stats: CalibrationStats | None
    feasible: bool
    reason: str = ""

    @property
    def threshold(self) -> float:
        """The selected tau, or -inf when nothing may be auto-published."""
        return self.stats.threshold if self.stats else float("-inf")


def select_threshold(
    scores: Sequence[float],
    is_error: Sequence[bool],
    alpha: float,
    delta: float = 0.05,
    *,
    grid_size: int = DEFAULT_GRID_SIZE,
    min_accepted: int = DEFAULT_MIN_ACCEPTED,
) -> ThresholdSelection:
    """Choose the largest threshold whose selective error rate is provably at most alpha.

    Args:
        scores: nonconformity per calibration value; higher means less trustworthy.
        is_error: whether each calibration value was in fact wrong.
        alpha: the error rate the operator is willing to accept among auto-published values.
        delta: 1 - delta is the confidence in the bound.
        grid_size: number of candidate thresholds in the fixed sequence.
        min_accepted: smallest acceptance set worth testing.

    Returns:
        A ThresholdSelection. When infeasible, `reason` explains what would fix it.
    """
    if len(scores) != len(is_error):
        raise ValueError(f"scores and labels differ in length: {len(scores)} vs {len(is_error)}")
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must lie strictly between 0 and 1, got {alpha}")
    if not 0 < delta < 1:
        raise ValueError(f"delta must lie strictly between 0 and 1, got {delta}")

    score_array = np.asarray(scores, dtype=float)
    error_array = np.asarray(is_error, dtype=bool)
    n_total = score_array.size

    if n_total == 0:
        return ThresholdSelection(None, False, "calibration set is empty")

    needed = required_sample_size(alpha, delta)
    if n_total < needed:
        return ThresholdSelection(
            None,
            False,
            f"certifying {alpha:.1%} error at {1 - delta:.0%} confidence needs at least "
            f"{needed} calibration points even with zero observed errors; only "
            f"{n_total} were supplied",
        )

    # The sequence must not begin below the point where certification is arithmetically
    # possible. With 30 accepted values the Clopper-Pearson bound is about 9.5% however
    # clean the data is, so starting there would fail the first test and end the sequence
    # before it began. `needed` depends only on alpha and delta - never on the labels -
    # so raising the floor to it costs nothing in validity.
    effective_min = max(min_accepted, needed)
    if effective_min > n_total:
        return ThresholdSelection(
            None, False, f"calibration set of {n_total} cannot support alpha={alpha:.1%}"
        )

    # Candidate thresholds, ordered most conservative first. Defined as score quantiles
    # over a grid fixed in advance so the testing sequence does not depend on the labels.
    coverage_grid = np.linspace(effective_min / n_total, 1.0, grid_size)
    candidates = np.quantile(score_array, coverage_grid, method="higher")

    best: CalibrationStats | None = None
    order = np.argsort(score_array)
    sorted_scores = score_array[order]
    sorted_errors = error_array[order]

    for tau in candidates:
        # searchsorted on the sorted scores is what keeps this O(log n) per candidate
        # rather than O(n); on a million-value run the difference is the whole runtime.
        n_accepted = int(np.searchsorted(sorted_scores, tau, side="right"))
        if n_accepted < effective_min:
            continue

        n_errors = int(sorted_errors[:n_accepted].sum())
        upper = clopper_pearson_upper(n_errors, n_accepted, delta)

        if upper > alpha:
            # Fixed-sequence testing: the first failure ends the sequence. Continuing to
            # scan for a luckier threshold further along is exactly the multiplicity
            # error that invalidates the guarantee.
            break

        best = CalibrationStats(
            n_calibration=n_total,
            alpha=alpha,
            delta=delta,
            threshold=float(tau),
            empirical_error=n_errors / n_accepted,
            error_upper_bound=upper,
            coverage=n_accepted / n_total,
        )

    if best is None:
        return ThresholdSelection(
            None,
            False,
            f"no threshold accepting at least {effective_min} values could be certified at "
            f"{alpha:.1%}; the scorer does not separate correct from incorrect values well "
            "enough at this risk level",
        )

    return ThresholdSelection(best, True)


def apply_threshold(
    assays: Sequence[Assay],
    threshold: float,
    impacts: Sequence[float] | None = None,
    values: Sequence | None = None,
) -> list[Decision]:
    """Turn scores into decisions.

    A hard verifier failure is rejected regardless of threshold. Dimensional analysis and
    physical constraints are not opinions to be outvoted by a calibrated score: there is
    no confidence level at which a mass may be published into a length column.
    """
    decisions: list[Decision] = []
    for assay in assays:
        if assay.has_hard_failure:
            decisions.append(Decision.REJECT)
        elif assay.nonconformity is None:
            decisions.append(Decision.REVIEW)
        elif assay.nonconformity <= threshold:
            decisions.append(Decision.AUTO_PUBLISH)
        else:
            decisions.append(Decision.REVIEW)
    return decisions


def risk_coverage_curve(
    scores: Sequence[float],
    is_error: Sequence[bool],
    alphas: Sequence[float],
    delta: float = 0.05,
    **kwargs,
) -> list[tuple[float, float, float]]:
    """Automation rate achievable at each requested risk level.

    This is the data behind the dial: turning alpha up buys coverage, turning it down
    buys safety. Returns (alpha, coverage, certified_upper_bound) per level, with
    coverage 0 where the risk level could not be certified at all.
    """
    curve: list[tuple[float, float, float]] = []
    for alpha in alphas:
        selection = select_threshold(scores, is_error, alpha, delta, **kwargs)
        if selection.feasible and selection.stats:
            curve.append((alpha, selection.stats.coverage, selection.stats.error_upper_bound))
        else:
            curve.append((alpha, 0.0, float("nan")))
    return curve


def realized_error_rate(certified: Sequence[CertifiedValue], is_error: Sequence[bool]) -> float:
    """Error rate among auto-published values.

    The number that must come in at or below the promised alpha on held-out data. If it
    does not, the guarantee is decoration and the project has failed at its one job.
    """
    published = [
        err
        for cv, err in zip(certified, is_error, strict=True)
        if cv.decision is Decision.AUTO_PUBLISH
    ]
    if not published:
        return 0.0
    return sum(published) / len(published)
