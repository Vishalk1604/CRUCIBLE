"""The end-to-end run: extract, verify, score, calibrate, certify.

This is where the parts meet. A corpus goes in; a certificate and a set of decisions come
out, along with the one number that matters — the error rate actually realised among
auto-published values on data the calibration never saw.

Three splits, not two
---------------------
The corpus is divided into *fit*, *calibrate* and *test*. Two would be simpler and would
be wrong. The fusion scorer learns from labelled data, and calibrating a threshold on the
same rows the scorer was fitted on measures the scorer's memory rather than its
performance, producing a threshold that is too generous and a guarantee that fails out of
sample. So the scorer is fitted on one split, the threshold calibrated on a second it has
never seen, and the promise tested on a third.

What counts as an error
-----------------------
An extracted value is wrong when it disagrees with the answer key *after normalisation*.
`0.5 in` and `12.7 mm` are the same bore, and scoring them as a disagreement would
manufacture errors the system then has to be certified against. Comparison therefore runs
through the same unit machinery the verifiers use.

Values the input cannot support are excluded entirely. If truncation removed the port
code, the answer key does not get to demand the port type - that would measure
clairvoyance and inflate the error rate the guarantee is calibrated against.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from crucible.assay.base import Verifier
from crucible.assay.constraints import ConstraintVerifier
from crucible.assay.dimensional import DimensionalVerifier
from crucible.certify.conformal import ThresholdSelection, apply_threshold, select_threshold
from crucible.certify.scorer import LearnedScorer, discrimination
from crucible.corpus.faults import InjectedFault, inject_all
from crucible.corpus.generate import GoldRecord, generate_corpus
from crucible.extract.rules import extract
from crucible.ontology import fingerprint, load_all
from crucible.schema import AttributeSpec, CategorySchema, ProductRecord, ValueKind
from crucible.units import UnitParseError, parse_quantity, registry
from crucible.verdict import Assay, CalibrationStats, Certificate, CertifiedValue, Decision

#: Relative tolerance when comparing numeric values against the answer key. Loose enough
#: to absorb unit-conversion rounding, tight enough that a digit error never passes.
NUMERIC_TOLERANCE = 1e-3


def values_agree(got: str, want: str, spec: AttributeSpec) -> bool:
    """Whether an extracted value matches the answer key.

    Numeric comparison happens after unit normalisation, so `0.5 in` and `12.7 mm` agree.
    A value that cannot be parsed as a quantity falls back to a normalised string
    comparison, which is the right behaviour for nominal terms and the only available
    behaviour for free text.
    """
    if got.strip().lower() == want.strip().lower():
        return True

    if spec.kind not in (ValueKind.QUANTITY, ValueKind.RANGE):
        return False

    try:
        got_q = parse_quantity(got)
        want_q = parse_quantity(want)
    except UnitParseError:
        return False

    # A missing unit on either side makes the comparison ambiguous. Treating "12.7" as
    # agreeing with "12.7 mm" would hide exactly the unit-dropped fault we inject.
    if got_q.unit is None or want_q.unit is None:
        return got_q.unit == want_q.unit and abs(got_q.magnitude - want_q.magnitude) < 1e-9

    try:
        ureg = registry()
        converted = ureg.Quantity(got_q.magnitude, got_q.unit).to(want_q.unit).magnitude
    except Exception:  # noqa: BLE001 - incompatible units are a genuine disagreement
        return False

    denominator = max(abs(want_q.magnitude), 1e-9)
    return abs(converted - want_q.magnitude) / denominator < NUMERIC_TOLERANCE


def build_verifiers(schema: CategorySchema) -> list[Verifier]:
    """The verifier suite for one category.

    Only the model-free verifiers are wired so far. Entailment, catalog coherence and
    ensemble disagreement join here once the extractor produces model output for them to
    check.
    """
    return [DimensionalVerifier(), ConstraintVerifier(schema)]


@dataclass
class ScoredValue:
    """One value carried through the pipeline with everything needed to judge it."""

    sku: str
    category_id: str
    attribute: str
    extracted: str
    expected: str
    assay: Assay
    is_error: bool


@dataclass
class RunResult:
    """Everything produced by one end-to-end run."""

    certificate: Certificate | None
    selection: ThresholdSelection
    certified: list[CertifiedValue]
    realized_error: float
    baseline_error: float
    auroc: float
    n_test: int
    faults: list[InjectedFault] = field(default_factory=list)
    #: True when the input error distribution came from fault injection rather than a
    #: real extractor. Every number in this result inherits that caveat.
    simulated: bool = True

    def summary(self) -> str:
        if self.certificate is None:
            return f"no guarantee available: {self.selection.reason}"
        stats = self.certificate.calibration
        return (
            f"auto-published {self.certificate.automation_rate:.1%} at a certified "
            f"<= {stats.alpha:.1%} error rate; realised {self.realized_error:.2%} on "
            f"{self.n_test} held-out values (baseline {self.baseline_error:.1%})"
        )


def assay_values(
    records: Sequence[ProductRecord],
    gold: dict[str, GoldRecord],
    schemas: dict[str, CategorySchema],
) -> list[ScoredValue]:
    """Run every verifier over every value and label it against the answer key."""
    scored: list[ScoredValue] = []

    for record in records:
        schema = schemas.get(record.category_id or "")
        answer = gold.get(record.sku)
        if schema is None or answer is None:
            continue

        verifiers = build_verifiers(schema)
        key = answer.scorable()

        for value in record.values:
            spec = schema.get(value.attribute)
            # Only values the input could actually support are judged. Anything else
            # would measure clairvoyance and inflate the error rate the guarantee is
            # calibrated against.
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


def run(
    alpha: float = 0.02,
    delta: float = 0.05,
    n_per_category: int = 500,
    fault_rate: float = 0.12,
    seed: int = 20260820,
) -> RunResult:
    """Execute a full simulated run and return the certificate it supports."""
    schemas = load_all()
    corpus = generate_corpus(n_per_category, seed=seed)
    gold = {g.raw.sku: g for g in corpus}

    extracted = [extract(g.raw) for g in corpus]
    damaged, faults = inject_all(extracted, schemas, rate=fault_rate, seed=seed)
    scored = assay_values(damaged, gold, schemas)

    if not scored:
        raise RuntimeError("no scorable values produced; corpus or extractor is misconfigured")

    # Deterministic three-way split. Interleaving by index rather than slicing keeps each
    # split representative across categories, which are generated in blocks.
    fit = [s for i, s in enumerate(scored) if i % 3 == 0]
    calibrate = [s for i, s in enumerate(scored) if i % 3 == 1]
    test = [s for i, s in enumerate(scored) if i % 3 == 2]

    verifier_names = sorted({sig.verifier for s in scored for sig in s.assay.signals})
    scorer = LearnedScorer(verifier_names)
    scorer.fit([s.assay for s in fit], [s.is_error for s in fit])

    for split in (calibrate, test):
        scorer.annotate([s.assay for s in split])

    selection = select_threshold(
        [s.assay.nonconformity for s in calibrate],
        [s.is_error for s in calibrate],
        alpha=alpha,
        delta=delta,
    )

    certified = [
        CertifiedValue(
            value=_placeholder_value(s),
            assay=s.assay,
            decision=decision,
            threshold=selection.threshold,
        )
        for s, decision in zip(
            test,
            apply_threshold([s.assay for s in test], selection.threshold),
            strict=True,
        )
    ]

    published = [
        s for s, c in zip(test, certified, strict=True) if c.decision is Decision.AUTO_PUBLISH
    ]
    realized = sum(s.is_error for s in published) / len(published) if published else 0.0
    baseline = sum(s.is_error for s in test) / len(test) if test else 0.0
    auroc = discrimination([s.assay.nonconformity for s in test], [s.is_error for s in test])

    certificate = None
    if selection.feasible and selection.stats:
        certificate = _build_certificate(selection.stats, certified, schemas, verifier_names, seed)

    return RunResult(
        certificate=certificate,
        selection=selection,
        certified=certified,
        realized_error=realized,
        baseline_error=baseline,
        auroc=auroc,
        n_test=len(test),
        faults=faults,
        simulated=True,
    )


def _placeholder_value(scored: ScoredValue):
    """Reconstruct a minimal AttributeValue for the certified record."""
    from crucible.schema import AttributeValue

    return AttributeValue(attribute=scored.attribute, raw=scored.extracted)


def _build_certificate(
    stats: CalibrationStats,
    certified: Sequence[CertifiedValue],
    schemas: dict[str, CategorySchema],
    verifier_names: Sequence[str],
    seed: int,
) -> Certificate:
    counts = {d: 0 for d in Decision}
    for value in certified:
        counts[value.decision] += 1

    return Certificate(
        run_id=f"run-{seed}-{uuid.uuid4().hex[:8]}",
        calibration=stats,
        n_values_scored=len(certified),
        n_auto_published=counts[Decision.AUTO_PUBLISH],
        n_review=counts[Decision.REVIEW],
        n_rejected=counts[Decision.REJECT],
        proposer_model="rules-v1 + simulated faults",
        verifier_versions=dict.fromkeys(verifier_names, "0.1.0"),
        schema_fingerprint=",".join(sorted(fingerprint(s) for s in schemas.values())),
    )


def run_real(
    alpha: float = 0.05,
    delta: float = 0.05,
    n_per_category: int = 200,
    seed: int = 20260820,
    model: str = "qwen3-vl:8b",
    use_rules: bool = False,
) -> RunResult:
    """Certify against a real extractor's real mistakes.

    The difference from `run` is the error distribution. That one injects faults, because
    the rule extractor is circular against this corpus and yields nothing to calibrate
    on. This one runs the model and takes whatever it gets wrong.

    `use_rules` defaults off, which inverts the production cascade on purpose. With rules
    first the error rate on this corpus is zero - rules win every contested attribute and
    are perfect here by construction - so the cascade as shipped would leave calibration
    nothing to learn from. Reading the model-only path is what makes the labels real.

    Normalisation runs before scoring. Without it the labels are dominated by vocabulary
    mismatch rather than correctness, and the verifiers would be tuned to detect
    formatting.
    """
    from crucible.corpus.harvest import harvest
    from crucible.normalize import normalise_record

    schemas = load_all()
    harvested = harvest(model=model, n_per_category=n_per_category, seed=seed, use_rules=use_rules)
    records = [
        normalise_record(r, schemas[r.category_id])
        for r in harvested.records
        if r.category_id in schemas
    ]

    scored = assay_values(records, harvested.gold, schemas)
    if not scored:
        raise RuntimeError("no scorable values; harvest or schemas are misconfigured")

    fit = [s for i, s in enumerate(scored) if i % 3 == 0]
    calibrate = [s for i, s in enumerate(scored) if i % 3 == 1]
    test = [s for i, s in enumerate(scored) if i % 3 == 2]

    verifier_names = sorted({sig.verifier for s in scored for sig in s.assay.signals})
    scorer = LearnedScorer(verifier_names)
    scorer.fit([s.assay for s in fit], [s.is_error for s in fit])
    for split in (calibrate, test):
        scorer.annotate([s.assay for s in split])

    selection = select_threshold(
        [s.assay.nonconformity for s in calibrate],
        [s.is_error for s in calibrate],
        alpha=alpha,
        delta=delta,
    )

    certified = [
        CertifiedValue(
            value=_placeholder_value(s),
            assay=s.assay,
            decision=decision,
            threshold=selection.threshold,
        )
        for s, decision in zip(
            test, apply_threshold([s.assay for s in test], selection.threshold), strict=True
        )
    ]

    published = [
        s for s, c in zip(test, certified, strict=True) if c.decision is Decision.AUTO_PUBLISH
    ]
    realized = sum(s.is_error for s in published) / len(published) if published else 0.0
    baseline = sum(s.is_error for s in test) / len(test) if test else 0.0
    auroc = discrimination([s.assay.nonconformity for s in test], [s.is_error for s in test])

    certificate = None
    if selection.feasible and selection.stats:
        certificate = _build_certificate(selection.stats, certified, schemas, verifier_names, seed)
        certificate.proposer_model = model

    return RunResult(
        certificate=certificate,
        selection=selection,
        certified=certified,
        realized_error=realized,
        baseline_error=baseline,
        auroc=auroc,
        n_test=len(test),
        faults=[],
        # Not a simulation. These are the model's own mistakes.
        simulated=False,
    )
