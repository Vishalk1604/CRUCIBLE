"""The comparison that makes an empty cell legible.

Every competing approach to this brief fills all 252 columns. Ours leaves cells blank on
purpose, and to anyone who has not read the argument that looks like a worse product with
less coverage. Arguing the point does not fix that; measuring it does.

So run the same catalog three ways and put the numbers side by side:

    all         every grounded value the model proposed, nothing withheld
    grounded    values with a source span, uncertainty flagged
    certified   only values that passed the conformal threshold

Then report, for each: how many cells were populated, what share of them are wrong, and
what that implies for review effort. The interesting number is not our error rate - it is
the *difference*, because that difference is what abstention buys and it is expressible
without using the words "conformal", "nonconformity" or "coverage" even once.

Why this is not an ablation
---------------------------
An ablation asks whether a component helps the metric. This asks whether the *product
decision* is right, and the audience is a judge deciding between us and a team whose sheet
looks fuller. "Filling every cell gets you 3x the coverage and N% of it is wrong" is a
sentence anyone can act on. That is the point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from crucible.emit.rows import FillMode

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Sequence

#: Seconds a human takes to check one attribute value against a source. Deliberately
#: conservative: the point is an order of magnitude, not a payroll forecast, and a figure
#: that flatters the system is worth less than one nobody disputes.
SECONDS_PER_REVIEW = 20.0


@dataclass(frozen=True)
class ModeResult:
    """What one fill mode produced, and what it would cost."""

    mode: FillMode
    cells_populated: int
    cells_scored: int
    errors: int

    @property
    def error_rate(self) -> float:
        return self.errors / self.cells_scored if self.cells_scored else 0.0

    @property
    def review_hours(self) -> float:
        """Hours to check every populated cell by hand."""
        return self.cells_populated * SECONDS_PER_REVIEW / 3600.0

    @property
    def wrong_cells_shipped(self) -> float:
        """Expected wrong cells if nobody reviews. The number that costs trust."""
        return self.cells_populated * self.error_rate


@dataclass
class Comparison:
    """The three-row table, plus the sentences it supports."""

    results: list[ModeResult]
    n_products: int
    simulated: bool = True

    def by_mode(self, mode: FillMode) -> ModeResult | None:
        return next((r for r in self.results if r.mode is mode), None)

    def rows(self) -> list[tuple[str, str, str, str, str]]:
        """Header-less table rows, ready for a terminal or a web page."""
        return [
            (
                r.mode.value,
                f"{r.cells_populated:,}",
                f"{r.error_rate:.1%}",
                f"{r.wrong_cells_shipped:,.0f}",
                f"{r.review_hours:,.1f}",
            )
            for r in self.results
        ]

    def headline(self) -> str:
        """One sentence, no statistics vocabulary.

        Returns an empty string when the comparison has nothing to say - if `all` and
        `certified` populated the same cells, there is no trade to describe and inventing
        a headline would be spin.
        """
        loose = self.by_mode(FillMode.ALL)
        strict = self.by_mode(FillMode.CERTIFIED)
        if loose is None or strict is None:
            return ""
        if loose.cells_populated == strict.cells_populated:
            return ""
        extra = loose.cells_populated - strict.cells_populated
        wrong = loose.wrong_cells_shipped - strict.wrong_cells_shipped
        return (
            f"Filling every cell adds {extra:,} values across {self.n_products:,} products "
            f"and about {wrong:,.0f} of them are wrong. Withholding them is what the "
            f"blank cells are."
        )


def compare(
    scored: Sequence[tuple[str, float | None, bool]],
    n_products: int,
    threshold: float | None,
    simulated: bool = True,
) -> Comparison:
    """Build the three-mode comparison from one already-assayed catalog.

    `scored` is (attribute, nonconformity, is_error) per extracted value. Every mode is
    computed from the *same* extraction, so the comparison isolates the publishing policy
    and nothing else - re-extracting per mode would let model nondeterminism masquerade as
    a policy effect.

    Values whose nonconformity is unknown are counted as populated under `all` and
    `grounded` but never under `certified`, which is exactly how `EmitPolicy` treats them.
    """
    results: list[ModeResult] = []

    for mode in (FillMode.ALL, FillMode.GROUNDED, FillMode.CERTIFIED):
        admitted = [
            (nonconformity, is_error)
            for _, nonconformity, is_error in scored
            if _admits(mode, nonconformity, threshold)
        ]
        results.append(
            ModeResult(
                mode=mode,
                cells_populated=len(admitted),
                cells_scored=len(admitted),
                errors=sum(1 for _, is_error in admitted if is_error),
            )
        )

    return Comparison(results=results, n_products=n_products, simulated=simulated)


def _admits(mode: FillMode, nonconformity: float | None, threshold: float | None) -> bool:
    """Mirror of `EmitPolicy.admits`, over scored values rather than AttributeValues.

    Kept as a separate function on purpose rather than importing the policy: this one
    reasons about labelled data and has no `AttributeValue` to inspect for spans. The two
    must agree, and `tests/test_baseline.py` asserts that they do.
    """
    if mode in (FillMode.ALL, FillMode.GROUNDED):
        return True
    if threshold is None or nonconformity is None:
        return False
    return nonconformity <= threshold
