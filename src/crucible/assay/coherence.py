"""Catalog coherence: judging a value against its peers.

The other verifiers examine a value in isolation. Dimensional algebra asks whether the
unit makes sense; the constraint solver asks whether the value contradicts its siblings
within one product. Neither can see that a 6205 bearing has been given a 520 mm outside
diameter when every other bearing in the category sits between 20 and 90.

That is what this adds. A catalog is a population, and a value far outside its
population is suspect regardless of whether it parses, carries a plausible unit, or
satisfies every within-product constraint. It is the signal that catches the quiet
errors: transposed digits, decimal shifts, and values lifted from the wrong column.

Fitted on the extraction, not on the answer key
-----------------------------------------------
The reference distribution comes from the extracted catalog itself, errors included.
Fitting on gold data would be leakage - it would import knowledge the system does not
have at deployment, and inflate every number downstream.

This works because the statistics are robust. Median and MAD are unmoved by a minority
of outliers, so with an error rate around thirty percent the centre still lands on the
truth, and the errors are exactly what stands out against it. A mean and standard
deviation would be dragged toward the errors and would then fail to flag them - the
classic way an outlier detector is defeated by the outliers it was built to find.

Deliberate limits
-----------------
It abstains below a minimum sample count. A distribution fitted on four values describes
those four values, and flagging against it would generate confident nonsense for every
sparsely populated attribute.

It abstains on attributes whose spread is genuinely wide. Length on a fastener catalog
legitimately ranges over two orders of magnitude, and a verifier that flags the tails of
a real distribution is producing false positives that cost human review time. Where MAD
is large relative to the median, this says nothing rather than something unreliable.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from crucible.assay.base import Verifier
from crucible.schema import AttributeSpec, AttributeValue, ProductRecord, ValueKind
from crucible.units import UnitParseError, parse_quantity
from crucible.verdict import VerifierSignal

#: Below this many observations a distribution describes its samples, not a population.
MIN_SAMPLES = 12

#: Robust z-score beyond which a numeric value is treated as an outlier. 3.5 is the
#: conventional threshold for modified z-scores and sits far enough out that ordinary
#: catalog variation does not trip it.
OUTLIER_Z = 3.5

#: Scale factor making MAD a consistent estimator of standard deviation for normal data.
MAD_TO_SIGMA = 1.4826

#: Above this ratio of MAD to median, the attribute is too dispersed to judge. Fastener
#: length spans orders of magnitude legitimately; flagging its tails would be noise.
MAX_RELATIVE_SPREAD = 0.75

#: A nominal value seen this rarely is suspicious rather than merely uncommon.
RARE_FREQUENCY = 0.02

#: Floor on the trust this verifier will report. Trust of exactly zero means "hard
#: contradiction" to the fusion stage, which bypasses the calibrated threshold entirely.
#: Being far from the median is never a contradiction - large parts exist - so however
#: extreme the deviation, this verifier stays strictly above the floor and lets the other
#: signals weigh in. A 300-sigma outlier previously reached zero and was silently
#: promoted to a hard rejection.
MIN_TRUST = 0.05


@dataclass
class NumericProfile:
    """Robust location and scale for one numeric attribute in one category."""

    median: float
    mad: float
    count: int

    @property
    def usable(self) -> bool:
        if self.count < MIN_SAMPLES or self.mad <= 0:
            return False
        return abs(self.mad / self.median) <= MAX_RELATIVE_SPREAD if self.median else False

    def z(self, value: float) -> float:
        scale = self.mad * MAD_TO_SIGMA
        return abs(value - self.median) / scale if scale > 0 else 0.0


@dataclass
class NominalProfile:
    """Observed value frequencies for one nominal attribute in one category."""

    counts: Counter = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def usable(self) -> bool:
        return self.total >= MIN_SAMPLES

    def frequency(self, value: str) -> float:
        return self.counts[value.strip().lower()] / self.total if self.total else 0.0


@dataclass
class CatalogProfile:
    """Fitted distributions, keyed by (category, attribute)."""

    numeric: dict[tuple[str, str], NumericProfile] = field(default_factory=dict)
    nominal: dict[tuple[str, str], NominalProfile] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.numeric) + len(self.nominal)


def _magnitude(text: str) -> float | None:
    """Comparable magnitude for a value, or None if it has none.

    Values carrying different units are not made comparable here. Converting would
    require trusting the unit, and a wrong unit is one of the errors this is meant to
    surface; a converted outlier would be normalised into looking ordinary.
    """
    try:
        quantity = parse_quantity(text)
    except UnitParseError:
        return None
    return quantity.magnitude


def fit(records: Sequence[ProductRecord], schemas: dict) -> CatalogProfile:
    """Fit distributions from an extracted catalog, errors and all."""
    numeric_samples: dict[tuple[str, str], list[float]] = defaultdict(list)
    nominal_samples: dict[tuple[str, str], Counter] = defaultdict(Counter)

    for record in records:
        schema = schemas.get(record.category_id or "")
        if schema is None:
            continue
        for value in record.values:
            spec = schema.get(value.attribute)
            if spec is None:
                continue
            key = (record.category_id, value.attribute)

            if spec.kind in (ValueKind.QUANTITY, ValueKind.RANGE):
                magnitude = _magnitude(value.raw)
                if magnitude is not None:
                    numeric_samples[key].append(magnitude)
            elif spec.kind is ValueKind.NOMINAL:
                nominal_samples[key][value.raw.strip().lower()] += 1

    profile = CatalogProfile()
    for key, samples in numeric_samples.items():
        ordered = sorted(samples)
        median = _median(ordered)
        mad = _median(sorted(abs(s - median) for s in samples))
        profile.numeric[key] = NumericProfile(median=median, mad=mad, count=len(samples))

    for key, counts in nominal_samples.items():
        profile.nominal[key] = NominalProfile(counts=counts)

    return profile


def _median(ordered: Sequence[float]) -> float:
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


class CoherenceVerifier(Verifier):
    """Flags values that sit far outside their category's distribution."""

    name = "coherence"

    def __init__(self, profile: CatalogProfile) -> None:
        self.profile = profile

    def _check(
        self, value: AttributeValue, spec: AttributeSpec, record: ProductRecord
    ) -> VerifierSignal:
        key = (record.category_id or "", value.attribute)

        if spec.kind in (ValueKind.QUANTITY, ValueKind.RANGE):
            return self._check_numeric(key, value)
        if spec.kind is ValueKind.NOMINAL:
            return self._check_nominal(key, value)
        return self.abstain(f"no profile for {spec.kind}")

    def _check_numeric(self, key: tuple[str, str], value: AttributeValue) -> VerifierSignal:
        profile = self.profile.numeric.get(key)
        if profile is None or not profile.usable:
            return self.abstain("no usable distribution for this attribute")

        magnitude = _magnitude(value.raw)
        if magnitude is None:
            return self.abstain("value has no comparable magnitude")

        z = profile.z(magnitude)
        if z <= OUTLIER_Z:
            return self.ok(f"within {z:.1f} robust sigma of category median")

        # Not a hard failure. A genuine outlier exists - a 2 metre valve in a catalog of
        # small ones is unusual and correct - so this lowers trust and lets the fusion
        # weigh it against the other signals rather than rejecting outright.
        trust = max(MIN_TRUST, 1.0 - (z - OUTLIER_Z) / (OUTLIER_Z * 2))
        return self.doubt(
            trust,
            f"{magnitude:g} is {z:.1f} robust sigma from the category median of {profile.median:g}",
        )

    def _check_nominal(self, key: tuple[str, str], value: AttributeValue) -> VerifierSignal:
        profile = self.profile.nominal.get(key)
        if profile is None or not profile.usable:
            return self.abstain("no usable distribution for this attribute")

        frequency = profile.frequency(value.raw)
        if frequency == 0.0:
            return self.doubt(0.15, f"{value.raw!r} never appears elsewhere in this category")
        if frequency < RARE_FREQUENCY:
            return self.doubt(
                0.5, f"{value.raw!r} appears in only {frequency:.1%} of this category"
            )
        return self.ok(f"{value.raw!r} appears in {frequency:.0%} of this category")


def spread_report(profile: CatalogProfile) -> list[str]:
    """Which attributes were too dispersed to judge, for the ablation write-up."""
    lines = []
    for (category, attribute), numeric in sorted(profile.numeric.items()):
        if not numeric.usable:
            reason = (
                "too few samples"
                if numeric.count < MIN_SAMPLES
                else "spread too wide"
                if numeric.median and abs(numeric.mad / numeric.median) > MAX_RELATIVE_SPREAD
                else "no variation"
            )
            lines.append(f"{category}.{attribute}: abstaining ({reason}, n={numeric.count})")
    return lines
