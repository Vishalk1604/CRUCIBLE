"""Fault injection: simulating an imperfect extractor.

This exists because of a problem the rule extractor exposed. On the generated corpus it
scores 100%, since the corpus writes descriptions from `corpus.tables` and the extractor
reads them back with the same tables. A perfect extractor produces no errors, conformal
calibration needs both classes to fit anything, and so the pipeline cannot be exercised
end to end at all without a realistic error distribution.

What this module is, and is not
-------------------------------
It is a **test fixture**. It takes correct values and corrupts a fraction of them in the
specific ways extractors actually fail, so the machinery downstream has something to
detect, fuse and calibrate against.

It is **not a source of headline numbers**. An automation rate measured against injected
faults measures how well the verifiers catch *these* faults at *this* rate. Quoting it as
system accuracy would be circular in the same way the 100% figure was, just less
obviously. Every number derived from this path has to be labelled as a simulation.

The fault taxonomy
------------------
The fault types are not arbitrary. Each is drawn from a documented failure mode of
LLM-based attribute extraction, and each is designed to be caught by a *different*
verifier. That correspondence is what makes the ablation study meaningful: removing the
dimensional verifier should visibly degrade detection of DIMENSION_SWAP and little else.

    UNIT_CONFUSION    12.7 becomes "12.7 in"        -> constraint, coherence
    UNIT_DROPPED      "12.7 mm" becomes "12.7"      -> dimensional (partial trust)
    ATTRIBUTE_SWAP    bore gets the OD value        -> constraint
    DIMENSION_SWAP    a length becomes a mass       -> dimensional (hard failure)
    DIGIT_ERROR       52 becomes 82                 -> constraint, coherence
    VOCABULARY_DRIFT  "316 stainless" -> "stainless" -> vocabulary
    FABRICATION       a value with no source at all -> entailment

A fault type with no verifier able to catch it is not a gap in this module. It is a gap
in the verifier suite, and one the ablation numbers will make visible.
"""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from crucible.schema import AttributeSpec, AttributeValue, CategorySchema, ProductRecord, ValueKind


class FaultType(StrEnum):
    """How an extractor got a value wrong."""

    UNIT_CONFUSION = "unit_confusion"
    UNIT_DROPPED = "unit_dropped"
    ATTRIBUTE_SWAP = "attribute_swap"
    DIMENSION_SWAP = "dimension_swap"
    DIGIT_ERROR = "digit_error"
    VOCABULARY_DRIFT = "vocabulary_drift"
    FABRICATION = "fabrication"


#: Relative frequency of each fault. Weighted towards the quiet failures - unit and
#: attribute confusion - because those are what actually survive into published catalogs.
#: Dimension swaps are loud and rare; they are included because catching them is free and
#: because a verifier suite that only handles subtle errors is not a verifier suite.
DEFAULT_MIX: dict[FaultType, float] = {
    FaultType.UNIT_CONFUSION: 0.24,
    FaultType.ATTRIBUTE_SWAP: 0.22,
    FaultType.DIGIT_ERROR: 0.18,
    FaultType.UNIT_DROPPED: 0.14,
    FaultType.VOCABULARY_DRIFT: 0.12,
    FaultType.FABRICATION: 0.06,
    FaultType.DIMENSION_SWAP: 0.04,
}

#: Units substituted during unit confusion, grouped by the dimension they belong to.
#: Substitutions stay within a dimension, which is what makes them hard: a dimensionally
#: valid wrong unit passes the cheapest check and has to be caught by a constraint.
_SAME_DIMENSION_UNITS: dict[str, list[str]] = {
    "[length]": ["mm", "in", "cm"],
    "[pressure]": ["psi", "bar", "kPa"],
    "[mass]": ["kg", "lb", "g"],
    "[temperature]": ["C", "F"],
    "[force]": ["N", "lbf"],
}

#: Units used for dimension swaps: deliberately from the wrong dimension entirely.
_WRONG_DIMENSION_UNITS = ["kg", "psi", "V", "N", "mm"]

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:/\d+)?")


@dataclass(frozen=True)
class InjectedFault:
    """Record of one corruption, kept so detection can be scored per fault type."""

    sku: str
    attribute: str
    fault: FaultType
    original: str
    corrupted: str


class FaultInjector:
    """Corrupts a fraction of extracted values in realistic ways.

    `rate` is the per-value probability of corruption. The default of 0.12 sits in the
    range reported for frontier models on product attribute extraction, which makes the
    simulation neither trivially easy nor hopeless.
    """

    def __init__(
        self,
        rate: float = 0.12,
        mix: dict[FaultType, float] | None = None,
        seed: int = 20260820,
    ) -> None:
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"rate must lie in [0, 1], got {rate}")
        self.rate = rate
        self.mix = mix or DEFAULT_MIX
        self._rng = random.Random(seed)
        self._types = list(self.mix)
        self._weights = [self.mix[t] for t in self._types]

    def inject(
        self, record: ProductRecord, schema: CategorySchema
    ) -> tuple[ProductRecord, list[InjectedFault]]:
        """Corrupt a record in place-ish, returning the new record and what was done.

        Returns a copy rather than mutating, so the caller keeps the clean version for
        scoring. The faults list is the answer key for detection.
        """
        faults: list[InjectedFault] = []
        new_values: list[AttributeValue] = []

        for value in record.values:
            spec = schema.get(value.attribute)
            if spec is None or self._rng.random() >= self.rate:
                new_values.append(value)
                continue

            corrupted, fault_type = self._corrupt(value, spec, record, schema)
            if corrupted is None or corrupted == value.raw:
                new_values.append(value)
                continue

            faults.append(
                InjectedFault(
                    sku=record.sku,
                    attribute=value.attribute,
                    fault=fault_type,
                    original=value.raw,
                    corrupted=corrupted,
                )
            )
            # Spans are preserved. A corrupted value still claims the same source, which
            # is exactly the situation entailment checking exists to catch: the citation
            # is present and does not support the value.
            new_values.append(value.model_copy(update={"raw": corrupted}))

        return record.model_copy(update={"values": new_values}), faults

    def _corrupt(
        self,
        value: AttributeValue,
        spec: AttributeSpec,
        record: ProductRecord,
        schema: CategorySchema,
    ) -> tuple[str | None, FaultType]:
        """Apply one fault, sampled from those that actually apply to this value.

        Applicability is established first, then a type is drawn from the applicable set
        with weights renormalised over it. Two earlier versions got this wrong in ways
        worth recording, because both silently distorted the fault mix and a distorted
        mix makes the ablation study uninterpretable - a verifier appears strong or weak
        according to how over-represented its fault happened to be.

        Sampling with replacement and taking the first type that applied put attribute
        swaps at 43% against a declared 22%: a type that always applies wins every race
        it enters. Sampling without replacement improved it to 39% but not more, because
        the real cause is applicability, not ordering. Nominal attributes cannot take
        unit or dimension faults at all, so on a catalog with many nominal attributes the
        unconditional mix is unreachable by construction.

        What this now delivers is the declared mix *conditional on applicability*, which
        is the strongest well-defined guarantee available. The achieved marginal mix still
        depends on the catalog: attribute swaps settle near 40% on the demo corpus because
        they are the only fault that applies to every value, while digit errors cannot
        touch fractional values like 1/2" and unit confusion has no substitute available
        for dimensions such as 1/[length]. That is a property of the data, not a defect.

        The consequence for reporting: detection must always be measured *per fault type*
        via `fault_mix`, never in aggregate. An aggregate detection rate on this corpus
        would mostly be reporting how well the constraint verifier catches attribute
        swaps.
        """
        applicable: list[tuple[FaultType, str]] = []
        for fault_type in self._types:
            result = self._apply(fault_type, value, spec, record, schema)
            if result is not None and result != value.raw:
                applicable.append((fault_type, result))

        if not applicable:
            return None, FaultType.DIGIT_ERROR

        weights = [self.mix[fault_type] for fault_type, _ in applicable]
        fault_type, result = self._rng.choices(applicable, weights=weights, k=1)[0]
        return result, fault_type

    def _apply(
        self,
        fault: FaultType,
        value: AttributeValue,
        spec: AttributeSpec,
        record: ProductRecord,
        schema: CategorySchema,
    ) -> str | None:
        text = value.raw
        numeric = spec.kind in (ValueKind.QUANTITY, ValueKind.RANGE)

        match fault:
            case FaultType.UNIT_CONFUSION:
                if not numeric or not spec.dimension:
                    return None
                options = _SAME_DIMENSION_UNITS.get(spec.dimension)
                if not options:
                    return None
                number = _NUMBER.search(text)
                if not number:
                    return None
                return f"{number.group(0)} {self._rng.choice(options)}"

            case FaultType.UNIT_DROPPED:
                if not numeric:
                    return None
                number = _NUMBER.search(text)
                return number.group(0) if number else None

            case FaultType.ATTRIBUTE_SWAP:
                # Take another value of the same kind from this record. This is the
                # failure mode where a model reads the right table row and the wrong
                # column, and it is invisible to any per-value check.
                siblings = [
                    other
                    for other in record.values
                    if other.attribute != value.attribute
                    and (s := schema.get(other.attribute)) is not None
                    and s.kind is spec.kind
                ]
                return self._rng.choice(siblings).raw if siblings else None

            case FaultType.DIMENSION_SWAP:
                if not numeric:
                    return None
                number = _NUMBER.search(text)
                if not number:
                    return None
                current = spec.canonical_unit or ""
                wrong = [u for u in _WRONG_DIMENSION_UNITS if u not in current]
                return f"{number.group(0)} {self._rng.choice(wrong)}"

            case FaultType.DIGIT_ERROR:
                number = _NUMBER.search(text)
                if not number or "/" in number.group(0):
                    return None
                digits = list(number.group(0))
                positions = [i for i, c in enumerate(digits) if c.isdigit()]
                if not positions:
                    return None
                position = self._rng.choice(positions)
                original_digit = digits[position]
                digits[position] = self._rng.choice(
                    [d for d in "0123456789" if d != original_digit]
                )
                return text.replace(number.group(0), "".join(digits), 1)

            case FaultType.VOCABULARY_DRIFT:
                if spec.kind is not ValueKind.NOMINAL:
                    return None
                # Truncate to a vaguer term, the way a model drops a qualifier it did
                # not think was load-bearing. "316 stainless steel" -> "stainless steel".
                words = text.split()
                return " ".join(words[1:]) if len(words) > 1 else None

            case FaultType.FABRICATION:
                if spec.kind is ValueKind.NOMINAL and spec.vocabulary:
                    alternatives = [v for v in spec.vocabulary if v != text]
                    return self._rng.choice(alternatives) if alternatives else None
                number = _NUMBER.search(text)
                if not number:
                    return None
                try:
                    magnitude = float(number.group(0))
                except ValueError:
                    return None
                invented = magnitude * self._rng.choice([2.0, 0.5, 3.0])
                return text.replace(number.group(0), f"{invented:g}", 1)

        return None


def inject_all(
    records: Sequence[ProductRecord],
    schemas: dict[str, CategorySchema],
    rate: float = 0.12,
    seed: int = 20260820,
) -> tuple[list[ProductRecord], list[InjectedFault]]:
    """Corrupt a whole corpus, returning the damaged records and the fault log."""
    injector = FaultInjector(rate=rate, seed=seed)
    out: list[ProductRecord] = []
    faults: list[InjectedFault] = []

    for record in records:
        schema = schemas.get(record.category_id or "")
        if schema is None:
            out.append(record)
            continue
        damaged, record_faults = injector.inject(record, schema)
        out.append(damaged)
        faults.extend(record_faults)

    return out, faults


def fault_mix(faults: Sequence[InjectedFault]) -> dict[FaultType, float]:
    """Achieved proportion of each fault type in an injection run.

    Always report alongside any detection number. The achieved mix differs from the
    declared one because applicability varies by attribute kind, so an aggregate
    detection rate on this corpus would largely be measuring how well one verifier
    catches one over-represented fault.
    """
    if not faults:
        return {}
    counts: dict[FaultType, int] = {}
    for fault in faults:
        counts[fault.fault] = counts.get(fault.fault, 0) + 1
    return {fault_type: n / len(faults) for fault_type, n in counts.items()}
