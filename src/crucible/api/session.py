"""Holding a certified catalog in memory so the risk dial responds instantly.

The expensive work - extraction, verification, fitting the fusion model - depends only on
the catalog and the verifier suite. None of it depends on alpha. Only the final
threshold does.

So it is all done once, at startup, and the nonconformity score for every value is kept.
Moving the dial then costs one pass over an array: pick a new threshold from the
calibration scores, re-partition the test set, recount. That is milliseconds over
thousands of values, against roughly fifteen minutes if the pipeline were re-run.

This split is not just a performance convenience, it is the honest structure of the
method. The verifiers form their opinions without knowing what risk the operator will
ask for; alpha enters at the end, as a business decision about how much error is
tolerable, applied to evidence gathered independently of it. A demo that recomputed
everything per alpha would imply the evidence changes with the appetite for risk, which
would be exactly backwards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from crucible.assay.coherence import CoherenceVerifier
from crucible.assay.coherence import fit as fit_profile
from crucible.assay.constraints import ConstraintVerifier
from crucible.assay.dimensional import DimensionalVerifier
from crucible.assay.ensemble import EnsembleIndex, EnsembleVerifier, build_index
from crucible.certify.conformal import apply_threshold, select_threshold
from crucible.certify.scorer import LearnedScorer, discrimination
from crucible.corpus.harvest import harvest, harvest_sample
from crucible.normalize import normalise_record
from crucible.ontology import load_all
from crucible.pipeline import ScoredValue, values_agree
from crucible.verdict import Assay, Decision

logger = logging.getLogger(__name__)


@dataclass
class DialResult:
    """What the operator sees for one setting of the dial."""

    alpha: float
    feasible: bool
    reason: str
    threshold: float | None
    automation_rate: float
    n_auto_published: int
    n_review: int
    n_total: int
    realized_error: float
    certified_bound: float | None
    baseline_error: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha": self.alpha,
            "feasible": self.feasible,
            "reason": self.reason,
            "threshold": self.threshold,
            "automationRate": self.automation_rate,
            "nAutoPublished": self.n_auto_published,
            "nReview": self.n_review,
            "nTotal": self.n_total,
            "realizedError": self.realized_error,
            "certifiedBound": self.certified_bound,
            "baselineError": self.baseline_error,
        }


class CertificationSession:
    """A catalog, verified and scored once, re-certifiable at any alpha."""

    def __init__(
        self,
        n_per_category: int = 200,
        model: str = "qwen3-vl:8b",
        seed: int = 20260820,
        use_rules: bool = False,
        delta: float = 0.05,
        ensemble_samples: int = 2,
    ) -> None:
        self.model = model
        self.delta = delta
        self.schemas = load_all()

        harvested = harvest(
            model=model, n_per_category=n_per_category, seed=seed, use_rules=use_rules
        )
        self.records = [
            normalise_record(r, self.schemas[r.category_id])
            for r in harvested.records
            if r.category_id in self.schemas
        ]
        self.gold = harvested.gold
        self.from_cache = harvested.from_cache

        # The coherence profile is fitted on the extraction, so it must be built before
        # the verifiers run and cannot be a per-value construction.
        self.profile = fit_profile(self.records, self.schemas)

        # Resampling passes are loaded only from cache. Generating one takes twenty
        # minutes of inference, and silently doing that inside a constructor - during
        # app startup, no less - would look like a hang. Absent samples simply mean the
        # ensemble verifier abstains everywhere, which the fusion model handles.
        self.ensemble = self._load_ensemble(ensemble_samples, model, n_per_category, seed)

        self.scored = self._assay()
        if not self.scored:
            raise RuntimeError("no scorable values; harvest or schemas are misconfigured")

        self.fit_split = self.scored[0::3]
        self.calibration_split = self.scored[1::3]
        self.test_split = self.scored[2::3]

        self.verifier_names = sorted({sig.verifier for s in self.scored for sig in s.assay.signals})
        self.scorer = LearnedScorer(self.verifier_names)
        self.scorer.fit([s.assay for s in self.fit_split], [s.is_error for s in self.fit_split])

        for split in (self.calibration_split, self.test_split):
            self.scorer.annotate([s.assay for s in split])

        self.auroc = discrimination(
            [s.assay.nonconformity for s in self.test_split],
            [s.is_error for s in self.test_split],
        )
        self.baseline_error = (
            sum(s.is_error for s in self.test_split) / len(self.test_split)
            if self.test_split
            else 0.0
        )
        logger.info(
            "session ready: %d values, AUROC %.3f, baseline error %.1f%%",
            len(self.scored),
            self.auroc,
            self.baseline_error * 100,
        )

    def _load_ensemble(
        self, n_samples: int, model: str, n_per_category: int, seed: int
    ) -> EnsembleIndex:
        passes = []
        for i in range(1, n_samples + 1):
            try:
                sample = harvest_sample(i, model=model, n_per_category=n_per_category, seed=seed)
            except Exception:
                logger.warning("resampling pass %d unavailable", i)
                continue
            if not sample.from_cache:
                # Ran inference rather than reading cache. Keep it - it is valid - but
                # say so, because a constructor that quietly spends twenty minutes is a
                # bug waiting to be reported as a hang.
                logger.warning("resampling pass %d was generated, not cached", i)
            passes.append(sample.records)

        if not passes:
            logger.info("no resampling passes found; ensemble verifier will abstain")
            return EnsembleIndex()

        index = build_index(passes, self.schemas)
        logger.info("ensemble index: %d values across %d passes", len(index), len(passes))
        return index

    def _verifiers(self, schema) -> list:
        return [
            DimensionalVerifier(),
            ConstraintVerifier(schema),
            CoherenceVerifier(self.profile),
            EnsembleVerifier(self.ensemble),
        ]

    def _assay(self) -> list[ScoredValue]:
        scored: list[ScoredValue] = []
        for record in self.records:
            schema = self.schemas.get(record.category_id or "")
            answer = self.gold.get(record.sku)
            if schema is None or answer is None:
                continue

            verifiers = self._verifiers(schema)
            key = answer.scorable()

            for value in record.values:
                spec = schema.get(value.attribute)
                if spec is None or value.attribute not in key:
                    continue
                assay = Assay(
                    sku=record.sku,
                    attribute=value.attribute,
                    signals=[v.verify(value, spec, record) for v in verifiers],
                )
                scored.append(
                    ScoredValue(
                        sku=record.sku,
                        category_id=record.category_id or "",
                        attribute=value.attribute,
                        extracted=value.raw,
                        expected=key[value.attribute],
                        assay=assay,
                        is_error=not values_agree(value.raw, key[value.attribute], spec),
                    )
                )
        return scored

    def certify_at(self, alpha: float) -> DialResult:
        """Re-certify at a new risk level. Cheap: no inference, no refitting."""
        selection = select_threshold(
            [s.assay.nonconformity for s in self.calibration_split],
            [s.is_error for s in self.calibration_split],
            alpha=alpha,
            delta=self.delta,
        )

        if not selection.feasible or selection.threshold is None:
            return DialResult(
                alpha=alpha,
                feasible=False,
                reason=selection.reason,
                threshold=None,
                automation_rate=0.0,
                n_auto_published=0,
                n_review=len(self.test_split),
                n_total=len(self.test_split),
                realized_error=0.0,
                certified_bound=None,
                baseline_error=self.baseline_error,
            )

        decisions = apply_threshold([s.assay for s in self.test_split], selection.threshold)
        published = [
            s for s, d in zip(self.test_split, decisions, strict=True) if d is Decision.AUTO_PUBLISH
        ]
        n_published = len(published)
        realized = sum(s.is_error for s in published) / n_published if n_published else 0.0

        return DialResult(
            alpha=alpha,
            feasible=True,
            reason=selection.reason,
            threshold=selection.threshold,
            automation_rate=n_published / len(self.test_split) if self.test_split else 0.0,
            n_auto_published=n_published,
            n_review=len(self.test_split) - n_published,
            n_total=len(self.test_split),
            realized_error=realized,
            certified_bound=selection.stats.error_upper_bound if selection.stats else None,
            baseline_error=self.baseline_error,
        )

    def sweep(self, alphas: list[float]) -> list[DialResult]:
        """The risk-coverage curve: what every risk level buys."""
        return [self.certify_at(a) for a in alphas]

    def review_queue(self, alpha: float, limit: int = 25) -> list[dict[str, Any]]:
        """Values routed to a human at this alpha, most suspect first.

        Ordered by nonconformity, which is the closest available proxy for where review
        time is best spent. Ranking by revenue impact needs sales data the corpus does
        not carry, and inventing it would make the ordering look more principled than it
        is.
        """
        selection = select_threshold(
            [s.assay.nonconformity for s in self.calibration_split],
            [s.is_error for s in self.calibration_split],
            alpha=alpha,
            delta=self.delta,
        )
        threshold = selection.threshold if selection.threshold is not None else -1.0

        flagged = [s for s in self.test_split if (s.assay.nonconformity or 1.0) > threshold]
        flagged.sort(key=lambda s: s.assay.nonconformity or 1.0, reverse=True)

        return [
            {
                "sku": s.sku,
                "category": s.category_id,
                "attribute": s.attribute,
                "extracted": s.extracted,
                "expected": s.expected,
                "isError": s.is_error,
                "nonconformity": s.assay.nonconformity,
                "signals": [
                    {
                        "verifier": sig.verifier,
                        "trust": sig.trust,
                        "applicable": sig.applicable,
                        "detail": sig.detail,
                    }
                    for sig in s.assay.signals
                ],
            }
            for s in flagged[:limit]
        ]

    def stats(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "fromCache": self.from_cache,
            "nProducts": len(self.records),
            "nValues": len(self.scored),
            "nFit": len(self.fit_split),
            "nCalibration": len(self.calibration_split),
            "nTest": len(self.test_split),
            "auroc": self.auroc,
            "baselineError": self.baseline_error,
            "verifiers": self.verifier_names,
            "weights": self.scorer.weights(),
            # Every number here comes from a generated corpus, not a real catalog.
            "simulatedCorpus": True,
        }
