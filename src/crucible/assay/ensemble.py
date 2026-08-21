"""Ensemble disagreement: does the model say the same thing twice.

The other three verifiers all ask whether a value is *consistent* - with its unit, its
siblings, its category. None of them can tell the difference between a value the model
read straight off the text and one it effectively guessed. Both look equally well-formed.

Resampling separates them. Extract the same product again under sampling and see what
survives: a value the source genuinely states comes back unchanged, while a value the
model invented to fill a required field comes back different each time. This is the one
signal that measures the model's own stability rather than the value's plausibility.

Why this one is built for the frontier, not just for accuracy
-------------------------------------------------------------
The measured problem with the existing suite is resolution. Three verifiers each emitting
about three discrete trust levels produce thirteen distinct signal patterns across 2627
values, and one pattern alone covers 43.5% of them. A threshold has roughly twelve places
it can sit, so the risk-coverage frontier is a coarse staircase and most of the dial
selects the same threshold.

The fix has to be a verifier that is *continuous* and applies to *most* values, and the
existing three are neither. Coherence is applicable almost everywhere but returns full
trust on 97.6% of values, so it is close to a constant; the other two abstain on more
than half.

Hence agreement is scored by mean pairwise string similarity rather than by exact match.
Exact match over three samples yields four possible values and would barely help. A
similarity ratio is continuous, so it breaks ties even between values that no other
verifier can separate - which is the whole point.

Comparison happens after normalisation, so `Z` and `Z metal shielded one side` count as
agreement. Without that this would mostly measure whether the model happened to expand an
abbreviation the same way twice, which is a fact about its formatting rather than its
confidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from crucible.assay.base import Verifier
from crucible.normalize import normalise_value
from crucible.schema import AttributeSpec, AttributeValue, CategorySchema, ProductRecord
from crucible.verdict import VerifierSignal

#: Below this mean similarity the samples are treated as materially disagreeing.
DISAGREEMENT_FLOOR = 0.55

#: Trust floor. Disagreement across samples is evidence of guessing, never proof of
#: error - a model can be unstable about a value that happens to be right - so this
#: verifier lowers confidence but never issues the hard failure that would bypass the
#: calibrated threshold entirely.
MIN_TRUST = 0.08


@dataclass
class EnsembleIndex:
    """Values proposed by each resampling pass, keyed by (sku, attribute)."""

    samples: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    n_passes: int = 0

    def get(self, sku: str, attribute: str) -> list[str]:
        return self.samples.get((sku, attribute), [])

    def __len__(self) -> int:
        return len(self.samples)


def build_index(
    sample_records: Sequence[Sequence[ProductRecord]],
    schemas: dict[str, CategorySchema],
) -> EnsembleIndex:
    """Collect resampled values, normalised, into a lookup.

    Normalising here rather than at comparison time means each value is canonicalised
    once instead of once per pairwise comparison.
    """
    index = EnsembleIndex(n_passes=len(sample_records))

    for records in sample_records:
        for record in records:
            schema = schemas.get(record.category_id or "")
            if schema is None:
                continue
            for value in record.values:
                spec = schema.get(value.attribute)
                if spec is None:
                    continue
                canonical = normalise_value(value.raw, spec).normalised
                index.samples.setdefault((record.sku, value.attribute), []).append(canonical)

    return index


def mean_pairwise_similarity(values: Sequence[str]) -> float:
    """Average similarity across every pair. Continuous by construction."""
    if len(values) < 2:
        return 1.0

    lowered = [v.strip().lower() for v in values]
    ratios = [
        SequenceMatcher(None, lowered[i], lowered[j]).ratio()
        for i in range(len(lowered))
        for j in range(i + 1, len(lowered))
    ]
    return sum(ratios) / len(ratios) if ratios else 1.0


class EnsembleVerifier(Verifier):
    """Scores a value by how stably the model reproduces it under resampling."""

    name = "ensemble"

    def __init__(self, index: EnsembleIndex) -> None:
        self.index = index

    def _check(
        self, value: AttributeValue, spec: AttributeSpec, record: ProductRecord
    ) -> VerifierSignal:
        others = self.index.get(record.sku, value.attribute)
        if not others:
            # The resampling passes never proposed this attribute at all. That is itself
            # weak evidence - the primary pass produced something the others did not -
            # but it is reported as an abstention rather than doubt, because absence
            # under sampling has many causes and the fusion model can learn what the
            # applicability flag means without being told a trust value here.
            return self.abstain("no resampled value for this attribute")

        canonical = normalise_value(value.raw, spec).normalised
        similarity = mean_pairwise_similarity([canonical, *others])

        if similarity >= 0.999:
            return self.ok(f"identical across {len(others) + 1} samples")

        trust = max(MIN_TRUST, similarity)
        verdict = "materially disagreeing" if similarity < DISAGREEMENT_FLOOR else "partial"
        detail = (
            f"{verdict}: {similarity:.2f} mean agreement across {len(others) + 1} "
            f"samples; others proposed {sorted(set(others))[:3]}"
        )
        return self.doubt(trust, detail)
