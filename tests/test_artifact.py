"""Tests for portable calibrations.

The interlock tests matter more than the round-trip ones. A calibration that fails to load
is a visible problem; a calibration that loads and quietly no longer applies is a bound
that does not hold, reported as though it does.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from crucible.certify.artifact import (
    FORMAT_VERSION,
    Calibration,
    CalibrationError,
    current_fingerprints,
    from_scorer,
    load_calibration,
    save_calibration,
)
from crucible.certify.scorer import LearnedScorer
from crucible.verdict import Assay, VerifierSignal

VERIFIERS = ["constraint", "dimensional", "identity"]


def make_assays(n: int = 120):
    rng = np.random.default_rng(0)
    assays, labels = [], []
    for i in range(n):
        is_error = i % 3 == 0
        centre = 0.1 if is_error else 0.9
        signals = [
            VerifierSignal(
                verifier=name,
                trust=float(np.clip(centre + rng.normal(0, 0.05), 0.0, 1.0)),
                applicable=True,
            )
            for name in VERIFIERS
        ]
        assays.append(Assay(sku=f"S{i}", attribute="x", signals=signals))
        labels.append(is_error)
    return assays, labels


@pytest.fixture
def fitted():
    assays, labels = make_assays()
    return LearnedScorer(VERIFIERS).fit(assays, labels), assays


class TestRoundTrip:
    def test_reloaded_scorer_gives_identical_scores(self, fitted, tmp_path):
        scorer, assays = fitted
        calibration = from_scorer(scorer, 0.42, alpha=0.05, delta=0.05, n_calibration=120)
        path = save_calibration(calibration, tmp_path / "calibration.json")

        reloaded = load_calibration(path).into_scorer()
        assert scorer.score_all(assays[:30]) == reloaded.score_all(assays[:30])

    def test_threshold_and_parameters_survive(self, fitted, tmp_path):
        scorer, _ = fitted
        calibration = from_scorer(scorer, 0.42, alpha=0.07, delta=0.05, n_calibration=876)
        back = load_calibration(save_calibration(calibration, tmp_path / "c.json"))
        assert back.threshold == 0.42
        assert back.alpha == 0.07
        assert back.n_calibration == 876
        assert back.verifiers == VERIFIERS

    def test_file_is_readable_json(self, fitted, tmp_path):
        # The point of not using pickle: a reviewer can open this.
        scorer, _ = fitted
        path = save_calibration(from_scorer(scorer, 0.42, 0.05, 0.05, 120), tmp_path / "c.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["format_version"] == FORMAT_VERSION
        assert set(payload) >= {"verifiers", "coefficients", "intercept", "threshold", "alpha"}

    def test_infeasible_calibration_stores_a_null_threshold(self, fitted, tmp_path):
        # Refusing to certify is a legitimate outcome and has to be storable.
        scorer, _ = fitted
        back = load_calibration(
            save_calibration(from_scorer(scorer, None, 0.02, 0.05, 120), tmp_path / "c.json")
        )
        assert back.threshold is None


class TestInterlocks:
    def test_schema_change_is_refused(self, fitted, tmp_path):
        scorer, _ = fitted
        fingerprints = current_fingerprints()
        calibration = from_scorer(scorer, 0.42, 0.05, 0.05, 120, schema_fingerprints=fingerprints)
        changed = {**fingerprints, next(iter(fingerprints)): "deadbeef"}
        with pytest.raises(CalibrationError, match="does not carry over"):
            calibration.check_applies(changed)

    def test_unchanged_schemas_are_accepted(self, fitted):
        scorer, _ = fitted
        fingerprints = current_fingerprints()
        calibration = from_scorer(scorer, 0.42, 0.05, 0.05, 120, schema_fingerprints=fingerprints)
        calibration.check_applies(fingerprints)  # must not raise

    def test_unseen_category_is_not_a_mismatch(self, fitted):
        # A catalog containing a category the calibration never saw is a stratum question,
        # not a reason to reject the whole file.
        scorer, _ = fitted
        fingerprints = current_fingerprints()
        calibration = from_scorer(scorer, 0.42, 0.05, 0.05, 120, schema_fingerprints=fingerprints)
        calibration.check_applies({**fingerprints, "category.nobody.calibrated": "abc123"})

    def test_unfitted_scorer_cannot_be_saved(self):
        with pytest.raises(CalibrationError, match="nothing to carry over"):
            from_scorer(LearnedScorer(VERIFIERS), 0.42, 0.05, 0.05, 120)

    def test_feature_layout_mismatch_is_caught(self, fitted, tmp_path):
        # Coefficients and verifier count must agree, or the reloaded scorer would be
        # reading the wrong feature as the wrong verifier.
        scorer, _ = fitted
        calibration = from_scorer(scorer, 0.42, 0.05, 0.05, 120)
        calibration.verifiers = [*VERIFIERS, "coherence"]
        with pytest.raises(CalibrationError, match="feature layout"):
            calibration.into_scorer()


class TestLoadFailures:
    def test_missing_file(self, tmp_path):
        with pytest.raises(CalibrationError, match="no calibration at"):
            load_calibration(tmp_path / "absent.json")

    def test_malformed_json(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(CalibrationError, match="not valid JSON"):
            load_calibration(path)

    def test_wrong_format_version_refuses_rather_than_guesses(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"format_version": FORMAT_VERSION + 1}), encoding="utf-8")
        with pytest.raises(CalibrationError, match="cannot be read by this build"):
            load_calibration(path)

    def test_missing_field_names_the_field(self, tmp_path):
        path = tmp_path / "c.json"
        path.write_text(json.dumps({"format_version": FORMAT_VERSION}), encoding="utf-8")
        with pytest.raises(CalibrationError, match="missing 'verifiers'"):
            load_calibration(path)


class TestProvenance:
    def test_simulated_defaults_to_true(self, fitted):
        # Non-negotiable #6: a calibration is simulated until someone proves otherwise.
        scorer, _ = fitted
        assert from_scorer(scorer, 0.42, 0.05, 0.05, 120).simulated is True

    def test_proposer_model_is_recorded(self, fitted, tmp_path):
        scorer, _ = fitted
        calibration = from_scorer(
            scorer, 0.42, 0.05, 0.05, 120, proposer_model="qwen3-vl:8b", simulated=False
        )
        back = load_calibration(save_calibration(calibration, tmp_path / "c.json"))
        assert back.proposer_model == "qwen3-vl:8b"
        assert back.simulated is False

    def test_created_at_is_stamped(self, fitted, tmp_path):
        scorer, _ = fitted
        back = load_calibration(
            save_calibration(from_scorer(scorer, 0.42, 0.05, 0.05, 120), tmp_path / "c.json")
        )
        assert back.created_at
        assert isinstance(Calibration.from_dict(back.to_dict()), Calibration)
