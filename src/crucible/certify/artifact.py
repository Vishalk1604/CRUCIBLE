"""Saving and reloading a calibration, so a guarantee outlives the run that produced it.

Calibrating means fitting a fusion model and selecting a conformal threshold, and both are
expensive: the fit needs labelled data and the threshold needs a held-out split. Neither
should have to happen again to enrich a file the system has never seen. That is the whole
point of the split-conformal construction - calibrate once, apply to any exchangeable
stream - and without persistence the code cannot actually take advantage of it.

Why JSON and not pickle
-----------------------
A pickled `LogisticRegression` is a fragile, unauditable blob. It breaks across scikit-learn
versions, it executes arbitrary code on load, and - most relevant here - nobody can read it.
A calibration is the artifact that carries a promise about error rates; if a reviewer cannot
open it and see the coefficients, the threshold, and which verifiers produced them, then the
guarantee is only as inspectable as the pickle format, which is not at all.

The stored fingerprints are the safety interlock. A calibration fitted against one set of
schemas says nothing about values produced under a different set, and reloading it silently
would be the quietest possible way to issue a bound that does not hold.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from crucible.certify.scorer import LearnedScorer

#: Bumped when the stored shape changes in a way older files cannot satisfy.
FORMAT_VERSION = 1


class CalibrationError(ValueError):
    """Raised when a calibration cannot be loaded, or does not apply to this run."""


@dataclass
class Calibration:
    """A fitted scorer plus the threshold it was calibrated to, and their provenance."""

    verifiers: list[str]
    coefficients: list[float]
    intercept: float
    threshold: float | None
    alpha: float
    delta: float
    n_calibration: int
    schema_fingerprints: dict[str, str] = field(default_factory=dict)
    proposer_model: str | None = None
    simulated: bool = True
    created_at: str = ""
    format_version: int = FORMAT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "created_at": self.created_at or datetime.now(UTC).isoformat(),
            "verifiers": list(self.verifiers),
            "coefficients": list(self.coefficients),
            "intercept": self.intercept,
            "threshold": self.threshold,
            "alpha": self.alpha,
            "delta": self.delta,
            "n_calibration": self.n_calibration,
            "schema_fingerprints": dict(self.schema_fingerprints),
            "proposer_model": self.proposer_model,
            "simulated": self.simulated,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Calibration:
        version = payload.get("format_version")
        if version != FORMAT_VERSION:
            raise CalibrationError(
                f"calibration format version {version!r} cannot be read by this build "
                f"(expected {FORMAT_VERSION}); re-run the calibration rather than "
                "reinterpreting a file whose meaning may have changed"
            )
        try:
            return cls(
                verifiers=list(payload["verifiers"]),
                coefficients=list(payload["coefficients"]),
                intercept=float(payload["intercept"]),
                threshold=payload["threshold"],
                alpha=float(payload["alpha"]),
                delta=float(payload["delta"]),
                n_calibration=int(payload["n_calibration"]),
                schema_fingerprints=dict(payload.get("schema_fingerprints") or {}),
                proposer_model=payload.get("proposer_model"),
                simulated=bool(payload.get("simulated", True)),
                created_at=str(payload.get("created_at", "")),
                format_version=version,
            )
        except KeyError as exc:
            raise CalibrationError(f"calibration file is missing {exc.args[0]!r}") from exc

    def check_applies(self, fingerprints: dict[str, str]) -> None:
        """Refuse to apply a calibration fitted under different schemas.

        Only categories present in *both* are compared. A run over a catalog that happens
        to contain a category the calibration never saw is not itself a mismatch - that
        value simply falls outside the calibrated stratum and should be handled by the
        per-stratum logic, not by rejecting the whole file.
        """
        conflicts = [
            f"{cid}: calibrated against {self.schema_fingerprints[cid]}, now {fingerprints[cid]}"
            for cid in sorted(set(self.schema_fingerprints) & set(fingerprints))
            if self.schema_fingerprints[cid] != fingerprints[cid]
        ]
        if conflicts:
            raise CalibrationError(
                "this calibration was fitted against different schemas and its bound does "
                "not carry over:\n  " + "\n  ".join(conflicts)
            )

    def into_scorer(self) -> LearnedScorer:
        """Rebuild a fitted scorer without refitting."""
        from sklearn.linear_model import LogisticRegression

        scorer = LearnedScorer(self.verifiers)
        model = LogisticRegression()
        expected = 2 * len(self.verifiers) + 3
        if len(self.coefficients) != expected:
            raise CalibrationError(
                f"calibration has {len(self.coefficients)} coefficients but "
                f"{len(self.verifiers)} verifiers imply {expected}; the file and this "
                "build disagree about the feature layout"
            )
        model.coef_ = np.asarray([self.coefficients], dtype=float)
        model.intercept_ = np.asarray([self.intercept], dtype=float)
        # Class order matters: `predict_proba` column 1 must mean "is an error".
        model.classes_ = np.asarray([False, True])
        model.n_features_in_ = expected
        scorer._model = model  # noqa: SLF001 - reconstructing our own state
        return scorer


def from_scorer(
    scorer: LearnedScorer,
    threshold: float | None,
    alpha: float,
    delta: float,
    n_calibration: int,
    schema_fingerprints: dict[str, str] | None = None,
    proposer_model: str | None = None,
    simulated: bool = True,
) -> Calibration:
    """Capture a fitted scorer and its threshold as a portable calibration."""
    if not scorer.is_fitted:
        raise CalibrationError("cannot save an unfitted scorer; there is nothing to carry over")
    model = scorer._model  # noqa: SLF001 - reading our own state
    assert model is not None
    return Calibration(
        verifiers=list(scorer.verifiers),
        coefficients=[float(x) for x in np.ravel(model.coef_)],
        intercept=float(np.ravel(model.intercept_)[0]),
        threshold=None if threshold is None else float(threshold),
        alpha=float(alpha),
        delta=float(delta),
        n_calibration=int(n_calibration),
        schema_fingerprints=dict(schema_fingerprints or {}),
        proposer_model=proposer_model,
        simulated=simulated,
        created_at=datetime.now(UTC).isoformat(),
    )


def save_calibration(calibration: Calibration, path: Path) -> Path:
    """Write a calibration as readable JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(calibration.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def load_calibration(path: Path) -> Calibration:
    """Read a calibration, failing loudly rather than approximately."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CalibrationError(f"no calibration at {path}") from exc
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CalibrationError(f"{path} does not contain a calibration object")
    return Calibration.from_dict(payload)


def current_fingerprints() -> dict[str, str]:
    """Fingerprints of every shipped schema, for stamping onto a calibration."""
    from crucible.ontology import fingerprint, load_all

    return {cid: fingerprint(schema) for cid, schema in load_all().items()}
