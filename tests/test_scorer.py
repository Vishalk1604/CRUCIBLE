"""Tests for signal fusion.

Two properties matter. The scorer must keep "not checked" distinguishable from "checked
and satisfied", because conflating them makes an unverified value look verified. And the
learned fusion must actually beat the min-trust heuristic on data where one verifier is
noisy, otherwise the extra machinery is not earning its place.
"""

import numpy as np
import pytest

from crucible.certify.scorer import (
    HARD_FAILURE_SCORE,
    HeuristicScorer,
    LearnedScorer,
    discrimination,
)
from crucible.verdict import Assay, VerifierSignal

VERIFIERS = ["dimensional", "constraint", "entailment"]


def assay(**signals: float | None) -> Assay:
    """Build an assay. A None value means that verifier abstained."""
    built = []
    for name, trust in signals.items():
        if trust is None:
            built.append(VerifierSignal(verifier=name, trust=0.0, applicable=False))
        else:
            built.append(VerifierSignal(verifier=name, trust=trust, applicable=True))
    return Assay(sku="X-1", attribute="bore", signals=built)


class TestHeuristicScorer:
    def test_hard_failure_scores_maximum(self):
        s = HeuristicScorer().score(assay(dimensional=0.0, constraint=1.0))
        assert s == HARD_FAILURE_SCORE

    def test_takes_the_least_convinced_verifier(self):
        # One verifier finding a real problem must not be outvoted by others with
        # nothing to say.
        s = HeuristicScorer().score(assay(dimensional=1.0, constraint=0.4, entailment=0.9))
        assert s == pytest.approx(0.6)

    def test_all_satisfied_scores_zero(self):
        assert HeuristicScorer().score(assay(dimensional=1.0, constraint=1.0)) == 0.0

    def test_nothing_checkable_is_maximally_uncertain_not_fine(self):
        # A value no verifier could examine is unverified. Scoring it as trustworthy
        # would auto-publish exactly the values nothing looked at.
        assert (
            HeuristicScorer().score(assay(dimensional=None, constraint=None)) == HARD_FAILURE_SCORE
        )


class TestFeatureEncoding:
    def test_abstention_is_distinguishable_from_zero_trust(self):
        scorer = LearnedScorer(VERIFIERS)
        abstained = scorer.features(assay(dimensional=None, constraint=1.0, entailment=1.0))
        distrusted = scorer.features(assay(dimensional=0.001, constraint=1.0, entailment=1.0))
        assert not np.allclose(abstained, distrusted), (
            "a verifier that had no opinion encodes identically to one that nearly "
            "rejected the value; unchecked values would look checked"
        )

    def test_layout_is_stable_regardless_of_signal_order(self):
        scorer = LearnedScorer(VERIFIERS)
        a = scorer.features(assay(dimensional=1.0, constraint=0.5, entailment=0.2))
        b = scorer.features(assay(entailment=0.2, constraint=0.5, dimensional=1.0))
        assert np.allclose(a, b)

    def test_missing_verifier_occupies_its_slot(self):
        # A verifier absent from an assay must not shift every downstream feature.
        scorer = LearnedScorer(VERIFIERS)
        full = scorer.features(assay(dimensional=1.0, constraint=1.0, entailment=1.0))
        partial = scorer.features(assay(dimensional=1.0, constraint=1.0))
        assert len(full) == len(partial)


class TestLearnedScorer:
    def _training_data(self, n=600, seed=0):
        """Data where `constraint` is decisive and `entailment` is pure noise.

        The heuristic minimum is dragged down by the noisy verifier; a learned fusion
        should discover it carries no information and ignore it.
        """
        rng = np.random.default_rng(seed)
        assays, errors = [], []
        for _ in range(n):
            wrong = rng.random() < 0.25
            constraint_trust = rng.uniform(0.0, 0.4) if wrong else rng.uniform(0.7, 1.0)
            assays.append(
                assay(
                    dimensional=1.0,
                    constraint=constraint_trust,
                    entailment=rng.uniform(0.0, 1.0),  # noise
                )
            )
            errors.append(wrong)
        return assays, errors

    def test_falls_back_before_fitting(self):
        scorer = LearnedScorer(VERIFIERS)
        assert not scorer.is_fitted
        assert scorer.score(assay(dimensional=1.0, constraint=0.4)) == pytest.approx(0.6)

    def test_hard_failure_bypasses_the_model(self):
        scorer = LearnedScorer(VERIFIERS).fit(*self._training_data())
        assert scorer.score(assay(dimensional=0.0, constraint=1.0)) == HARD_FAILURE_SCORE

    def test_beats_the_heuristic_when_a_verifier_is_noisy(self):
        train_assays, train_errors = self._training_data(seed=1)
        test_assays, test_errors = self._training_data(seed=2)

        learned = LearnedScorer(VERIFIERS).fit(train_assays, train_errors)
        heuristic = HeuristicScorer()

        learned_auc = discrimination(learned.score_all(test_assays), test_errors)
        heuristic_auc = discrimination(heuristic.score_all(test_assays), test_errors)

        assert learned_auc > heuristic_auc, (
            f"learned fusion ({learned_auc:.3f}) did not beat min-trust "
            f"({heuristic_auc:.3f}); the extra machinery is not earning its place"
        )

    def test_weights_expose_the_uninformative_verifier(self):
        # The ablation story depends on this: a verifier carrying no weight should be
        # visibly carrying no weight.
        scorer = LearnedScorer(VERIFIERS).fit(*self._training_data(seed=3))
        weights = scorer.weights()
        assert abs(weights["entailment.trust"]) < abs(weights["constraint.trust"])

    def test_refuses_single_class_labels(self):
        assays = [assay(dimensional=1.0, constraint=0.9) for _ in range(50)]
        with pytest.raises(ValueError, match="both classes"):
            LearnedScorer(VERIFIERS).fit(assays, [False] * 50)

    def test_refuses_mismatched_inputs(self):
        with pytest.raises(ValueError, match="differ in length"):
            LearnedScorer(VERIFIERS).fit([assay(dimensional=1.0)], [True, False])

    def test_refuses_when_everything_hard_failed(self):
        assays = [assay(dimensional=0.0) for _ in range(20)]
        with pytest.raises(ValueError, match="hard failure"):
            LearnedScorer(VERIFIERS).fit(assays, [True] * 20)

    def test_annotate_writes_scores_onto_assays(self):
        scorer = LearnedScorer(VERIFIERS).fit(*self._training_data())
        assays = [assay(dimensional=1.0, constraint=0.9), assay(dimensional=1.0, constraint=0.1)]
        scorer.annotate(assays)
        assert all(a.nonconformity is not None for a in assays)
        assert assays[0].nonconformity < assays[1].nonconformity


class TestDiscrimination:
    def test_perfect_ordering_scores_one(self):
        assert discrimination([0.1, 0.2, 0.8, 0.9], [False, False, True, True]) == 1.0

    def test_inverted_ordering_scores_zero(self):
        assert discrimination([0.9, 0.8, 0.2, 0.1], [False, False, True, True]) == 0.0

    def test_random_ordering_is_near_a_half(self):
        rng = np.random.default_rng(0)
        scores = rng.uniform(size=4000)
        errors = rng.random(4000) < 0.3
        assert discrimination(scores, errors) == pytest.approx(0.5, abs=0.05)

    def test_constant_scores_give_a_half(self):
        # Verifier outputs are discrete and tie heavily; ties must average, not sort
        # arbitrarily, or the metric reports whatever order the array happened to be in.
        assert discrimination([0.5] * 100, [True] * 50 + [False] * 50) == pytest.approx(0.5)

    def test_undefined_without_both_classes(self):
        assert np.isnan(discrimination([0.1, 0.2], [False, False]))
