"""Verdict types: what the verifiers say, and what the certificate promises.

Two ideas are kept deliberately separate here, because conflating them is the mistake
the rest of the market makes:

  * A **signal** is one verifier's opinion about one value. It is evidence, not a decision.
  * A **certificate** is a population-level promise about a whole run, backed by a
    calibration set and a statistical bound.

A per-value confidence number cannot tell you how much of your catalog is safe to
publish. Only the calibrated threshold in a Certificate can, and only for values drawn
from the same distribution as the calibration set. That caveat is recorded on the
certificate itself rather than left implicit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

from crucible.schema import AttributeValue


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Decision(StrEnum):
    """What happens to a value once it has been scored."""

    AUTO_PUBLISH = "auto_publish"  # below threshold: ships without human review
    REVIEW = "review"  # ambiguous: queued for a human, ranked by impact
    REJECT = "reject"  # actively contradicted by a verifier; do not publish


class VerifierSignal(BaseModel):
    """One verifier's opinion about one proposed value.

    trust runs 0..1, where 1 means "this verifier is fully satisfied". Verifiers that
    cannot form an opinion (no evidence to check, attribute out of scope) return
    applicable=False rather than a neutral score, so that abstention stays
    distinguishable from mild approval when the signals are combined.

    detail is what the reviewer actually reads. "constraint bore <= body_diameter
    violated: 200.0 > 15.0" is worth more to a human than any number.
    """

    verifier: str
    trust: float = Field(ge=0.0, le=1.0)
    applicable: bool = True
    detail: str = ""

    @property
    def is_hard_failure(self) -> bool:
        """A verifier actively contradicting the value, not merely doubting it.

        Hard failures bypass the calibrated threshold entirely: no confidence score
        should be able to publish a value that violates dimensional analysis or a
        physical constraint.
        """
        return self.applicable and self.trust == 0.0


class Assay(BaseModel):
    """The full set of verifier opinions on one value, plus the fused score.

    nonconformity is the quantity conformal calibration actually thresholds. Higher
    means less trustworthy, following the usual convention in the conformal literature.
    It is populated by the calibrated scorer; before that it is None, and the value
    cannot be certified.
    """

    sku: str
    attribute: str
    signals: list[VerifierSignal] = Field(default_factory=list)
    nonconformity: float | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_hard_failure(self) -> bool:
        return any(s.is_hard_failure for s in self.signals)

    @property
    def applicable_signals(self) -> list[VerifierSignal]:
        return [s for s in self.signals if s.applicable]

    def signal(self, verifier: str) -> VerifierSignal | None:
        return next((s for s in self.signals if s.verifier == verifier), None)

    def failure_reasons(self) -> list[str]:
        """Why a human is being asked to look at this. Ordered worst-first."""
        worst_first = sorted(self.applicable_signals, key=lambda s: s.trust)
        return [s.detail for s in worst_first if s.detail]


class CertifiedValue(BaseModel):
    """A value that has been through the full pipeline and received a decision.

    impact is a business weight (revenue, search demand) used only to order the review
    queue. It deliberately plays no part in the decision itself: letting commercial
    importance change the accuracy bar would quietly break the guarantee.
    """

    value: AttributeValue
    assay: Assay
    decision: Decision
    threshold: float  # the tau this decision was made against
    impact: float = 0.0

    @property
    def review_priority(self) -> float:
        """Ordering key for the human queue: uncertain and commercially important first."""
        if self.assay.nonconformity is None:
            return self.impact
        return self.assay.nonconformity * max(self.impact, 1e-9)


class CalibrationStats(BaseModel):
    """Everything needed to reproduce and audit a threshold choice."""

    n_calibration: int
    alpha: float  # the error rate the operator asked for
    delta: float  # 1 - delta is the confidence in the bound
    threshold: float  # tau selected on the calibration set
    empirical_error: float  # realized error among accepted calibration points
    error_upper_bound: float  # Clopper-Pearson upper bound on that error
    coverage: float  # fraction of calibration points accepted at tau

    @property
    def is_valid(self) -> bool:
        """Whether the selected threshold actually honours the requested risk level."""
        return self.error_upper_bound <= self.alpha


class Certificate(BaseModel):
    """The population-level guarantee attached to one enrichment run.

    This is the artifact that distinguishes Crucible from a confidence score: it states
    a bound, the evidence for that bound, and the assumption under which it holds.
    """

    run_id: str
    created_at: datetime = Field(default_factory=_utcnow)
    category_id: str | None = None

    calibration: CalibrationStats
    n_values_scored: int
    n_auto_published: int
    n_review: int
    n_rejected: int

    proposer_model: str = "unknown"
    verifier_versions: dict[str, str] = Field(default_factory=dict)
    schema_fingerprint: str | None = None

    #: The exchangeability caveat, carried with the guarantee so it cannot be lost in
    #: translation. Conformal bounds hold for values drawn like the calibration set; a
    #: new category or a new supplier's document format breaks that assumption.
    assumption: str = (
        "Bound holds for values exchangeable with the calibration set. Products from "
        "categories or evidence sources not represented in calibration are outside its scope."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def automation_rate(self) -> float:
        """The headline number: what fraction shipped without a human."""
        if self.n_values_scored == 0:
            return 0.0
        return self.n_auto_published / self.n_values_scored

    def summary(self) -> str:
        """One line for a terminal, a slide, or a commit message."""
        return (
            f"auto-published {self.n_auto_published}/{self.n_values_scored} "
            f"({self.automation_rate:.1%}) at certified error <= {self.calibration.alpha:.1%} "
            f"with {1 - self.calibration.delta:.0%} confidence"
        )
