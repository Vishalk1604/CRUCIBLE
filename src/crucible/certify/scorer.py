"""Fusing verifier signals into a single nonconformity score.

The verifiers each answer a narrow question. Certification needs one number per value,
ordered so that thresholding it separates correct values from incorrect ones. This
module is the join between them.

Why not just average the trust scores
-------------------------------------
Because the signals are not interchangeable. A dimensional check that passes is close to
proof; an entailment check that passes is weak evidence. Averaging treats them alike and
throws away the difference. Worse, averaging mishandles abstention: a verifier with no
opinion is not a verifier giving a middling score, and folding the two together makes an
unchecked value look half-checked.

So the fusion is learned. A logistic model is fitted on calibration data to predict
"is this value wrong" from the signal vector, and its predicted probability becomes the
nonconformity score. Learning the weights is also what lets the ablation study mean
something: if a verifier carries no weight, the model says so.

The score is *not* required to be well calibrated as a probability. Conformal
certification only needs the ordering to be informative - it supplies the calibration
itself. This is worth stating because it is the reason the whole approach tolerates the
known miscalibration of model confidence: we never trust the number, only the ranking.

Hard failures bypass all of this and score 1.0. A dimensional contradiction is not
evidence to be weighed against other evidence.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

import numpy as np
from sklearn.linear_model import LogisticRegression

from crucible.verdict import Assay

#: Score assigned to any value a verifier actively contradicted. Nothing outranks this.
HARD_FAILURE_SCORE = 1.0


class Scorer(ABC):
    """Turns an Assay into a nonconformity score in [0, 1]. Higher means less trusted."""

    name: str = "scorer"

    @abstractmethod
    def score(self, assay: Assay) -> float: ...

    def score_all(self, assays: Sequence[Assay]) -> list[float]:
        return [self.score(a) for a in assays]

    def annotate(self, assays: Sequence[Assay]) -> None:
        """Write scores back onto the assays, in place."""
        for assay in assays:
            assay.nonconformity = self.score(assay)


class HeuristicScorer(Scorer):
    """Cold-start scorer: a value is as trustworthy as its least convinced verifier.

    Used before any calibration data exists, and as the baseline the learned scorer has
    to beat. Taking the minimum rather than the mean is deliberate - one verifier finding
    a real problem should not be outvoted by four that had nothing to say.
    """

    name = "heuristic-min"

    def score(self, assay: Assay) -> float:
        if assay.has_hard_failure:
            return HARD_FAILURE_SCORE
        applicable = assay.applicable_signals
        if not applicable:
            # Nothing could be checked. That is maximally uncertain, not fine.
            return HARD_FAILURE_SCORE
        return 1.0 - min(s.trust for s in applicable)


class LearnedScorer(Scorer):
    """Logistic fusion of the signal vector, fitted on labelled calibration data."""

    name = "learned-logistic"

    def __init__(self, verifiers: Sequence[str]) -> None:
        #: Fixed at construction so the feature vector has a stable layout. A verifier
        #: that abstained on every calibration row still occupies its slot, otherwise
        #: scoring a value it *does* check would shift every downstream feature.
        self.verifiers = sorted(verifiers)
        self._model: LogisticRegression | None = None
        self._fallback = HeuristicScorer()

    @property
    def is_fitted(self) -> bool:
        return self._model is not None

    def features(self, assay: Assay) -> np.ndarray:
        """Signal vector for one value.

        Each verifier contributes two numbers: its trust, and whether it had an opinion
        at all. Encoding applicability separately is what keeps "checked and satisfied"
        distinguishable from "not checked", which a single trust column cannot express.
        """
        row: list[float] = []
        for name in self.verifiers:
            signal = assay.signal(name)
            if signal is None or not signal.applicable:
                row.extend([0.0, 0.0])
            else:
                row.extend([signal.trust, 1.0])

        applicable = assay.applicable_signals
        trusts = [s.trust for s in applicable]
        row.append(min(trusts) if trusts else 0.0)
        row.append(float(np.mean(trusts)) if trusts else 0.0)
        row.append(len(applicable) / max(len(self.verifiers), 1))
        return np.asarray(row, dtype=float)

    def fit(self, assays: Sequence[Assay], is_error: Sequence[bool]) -> LearnedScorer:
        """Fit the fusion model.

        Rows with hard failures are excluded from training. They never reach the scorer
        at inference time, so including them would teach the model to predict something
        it is never asked about while distorting the weights for everything else.
        """
        if len(assays) != len(is_error):
            raise ValueError(
                f"assays and labels differ in length: {len(assays)} vs {len(is_error)}"
            )

        rows = [
            (self.features(a), bool(err))
            for a, err in zip(assays, is_error, strict=True)
            if not a.has_hard_failure
        ]
        if not rows:
            raise ValueError("no trainable rows: every assay carried a hard failure")

        x = np.vstack([r[0] for r in rows])
        y = np.asarray([r[1] for r in rows], dtype=int)

        if len(np.unique(y)) < 2:
            # All correct or all wrong. Logistic regression cannot fit that, and a
            # scorer that pretends otherwise would hand conformal calibration a constant.
            raise ValueError(
                f"calibration labels are all {'errors' if y[0] else 'correct'}; "
                "fusion needs both classes to learn an ordering"
            )

        # Balanced weighting because errors are the minority class by design, and an
        # unweighted fit would minimise loss by calling everything correct.
        self._model = LogisticRegression(max_iter=1000, class_weight="balanced", C=1.0).fit(x, y)
        return self

    def score(self, assay: Assay) -> float:
        if assay.has_hard_failure:
            return HARD_FAILURE_SCORE
        if self._model is None:
            return self._fallback.score(assay)
        probability = self._model.predict_proba(self.features(assay).reshape(1, -1))[0, 1]
        return float(probability)

    def weights(self) -> dict[str, float]:
        """Fitted coefficient per verifier, for the ablation story.

        Positive means the feature pushes towards "this value is wrong". A verifier whose
        trust coefficient sits near zero is not contributing, and should be either fixed
        or dropped rather than left in to pad the architecture diagram.
        """
        if self._model is None:
            return {}
        coefficients = self._model.coef_[0]
        named: dict[str, float] = {}
        for i, name in enumerate(self.verifiers):
            named[f"{name}.trust"] = float(coefficients[2 * i])
            named[f"{name}.applicable"] = float(coefficients[2 * i + 1])
        offset = 2 * len(self.verifiers)
        named["agg.min_trust"] = float(coefficients[offset])
        named["agg.mean_trust"] = float(coefficients[offset + 1])
        named["agg.coverage"] = float(coefficients[offset + 2])
        return named


def discrimination(scores: Sequence[float], is_error: Sequence[bool]) -> float:
    """AUROC of the scorer: the probability a wrong value outranks a correct one.

    Reported because it is the property conformal certification actually consumes. A
    scorer at 0.5 carries no ordering information, and no amount of calibration will
    extract automation from it - `select_threshold` will simply refuse. Measuring this
    separately is how a failure to certify gets diagnosed as a weak scorer rather than
    a broken bound.
    """
    score_array = np.asarray(scores, dtype=float)
    error_array = np.asarray(is_error, dtype=bool)

    n_pos = int(error_array.sum())
    n_neg = int((~error_array).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")

    # Rank-based AUROC, with ties averaged. Avoids importing another sklearn metric and
    # makes the tie handling explicit, which matters because verifier scores are discrete
    # and produce a lot of ties.
    order = np.argsort(score_array, kind="mergesort")
    ranks = np.empty(len(score_array), dtype=float)
    ranks[order] = np.arange(1, len(score_array) + 1)

    unique, inverse, counts = np.unique(score_array, return_inverse=True, return_counts=True)
    for i, count in enumerate(counts):
        if count > 1:
            ranks[inverse == i] = ranks[inverse == i].mean()

    return float((ranks[error_array].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))
