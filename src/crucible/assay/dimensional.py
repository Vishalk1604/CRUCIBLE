"""Dimensional algebra verifier.

The cheapest, hardest-to-argue-with check in the system. An attribute declares the
physical dimension it must have; the extracted value is parsed and its dimension
compared. No model, no threshold, no training data — just algebra.

It catches a failure mode that language models produce constantly and confidence scores
never flag, because the model is entirely sure of itself:

    thread_pitch: "4.2 kg"          -> [mass] where [length] was required
    max_pressure: "150 mm"          -> [length] where [pressure] was required
    weight:       "316"             -> a number with no unit at all

and the quieter, more expensive one:

    temp_rating:  "250 degF" stored into a degC column without conversion

Dimension mismatches are reported as hard failures. There is no confidence level at
which a mass may be published into a length column, so this verifier's veto is not
something the calibrated threshold is allowed to overrule.
"""

from __future__ import annotations

from crucible.assay.base import Verifier
from crucible.schema import (
    AttributeSpec,
    AttributeValue,
    NormalizedValue,
    ProductRecord,
    ValueKind,
)
from crucible.units import (
    UnitParseError,
    dimensionality,
    friendly_dimension,
    parse_quantity,
    registry,
)
from crucible.verdict import VerifierSignal

#: Trust assigned when a value parses cleanly but carried no unit, so the attribute's
#: canonical unit had to be assumed. Not a failure — most spec tables put the unit in
#: the column header — but not free either, since an unlabelled number is exactly how
#: inch/millimetre confusion enters a catalog.
_ASSUMED_UNIT_TRUST = 0.55


class DimensionalVerifier(Verifier):
    """Checks that a quantity's physical dimension matches what the attribute declares."""

    name = "dimensional"
    version = "0.1.0"

    def _check(
        self,
        value: AttributeValue,
        spec: AttributeSpec,
        record: ProductRecord,
    ) -> VerifierSignal:
        if spec.kind not in (ValueKind.QUANTITY, ValueKind.RANGE):
            return self.abstain(f"{spec.name} is {spec.kind.value}, not a physical quantity")
        if not spec.dimension:
            return self.abstain(f"{spec.name} declares no dimension to check against")

        try:
            parsed = parse_quantity(value.raw)
        except UnitParseError as exc:
            return self.fail(
                f"{spec.name} is declared as a {spec.kind.value} but the extracted text "
                f"{value.raw!r} contains no readable number ({exc})"
            )

        if spec.kind is ValueKind.RANGE and not parsed.is_range:
            return self.doubt(
                0.4,
                f"{spec.name} expects a range but {value.raw!r} parsed as a single value",
            )

        if parsed.unit is None:
            return self.doubt(
                _ASSUMED_UNIT_TRUST,
                f"{value.raw!r} carries no unit; assuming {spec.canonical_unit or spec.dimension}. "
                "Unlabelled numbers are how inch/millimetre errors enter a catalog.",
            )

        actual = dimensionality(parsed.unit)
        expected = self._expected_dimensionality(spec)
        if actual != expected:
            return self.fail(
                f"{spec.name} requires {friendly_dimension(expected)} but {value.raw!r} is "
                f"{friendly_dimension(actual)} (parsed as {parsed.magnitude:g} {parsed.unit}). "
                "No confidence level makes this publishable."
            )

        if (
            parsed.is_range
            and parsed.low is not None
            and parsed.high is not None
            and parsed.low > parsed.high
        ):
            return self.fail(f"{spec.name} range is inverted: {value.raw!r}")

        # Drop the parenthetical when the attribute is already named after its dimension,
        # so the log does not read "valid for pressure (pressure)".
        friendly = friendly_dimension(expected)
        qualifier = "" if friendly in spec.name.lower() else f" ({friendly})"
        return self.ok(
            f"{parsed.magnitude:g} {parsed.unit} is dimensionally valid for {spec.name}{qualifier}"
        )

    @staticmethod
    def _expected_dimensionality(spec: AttributeSpec) -> str:
        """Resolve the spec's declared dimension into pint's canonical string form.

        A spec may declare either a dimensionality ("[length]") or lean on its canonical
        unit ("millimeter"). Normalizing both through pint means the comparison is
        between canonical strings rather than however the schema author typed it.
        """
        assert spec.dimension is not None
        if spec.canonical_unit:
            return dimensionality(spec.canonical_unit)
        return str(registry().get_dimensionality(spec.dimension))


def normalize(value: AttributeValue, spec: AttributeSpec) -> NormalizedValue | None:
    """Convert a verified value into the attribute's canonical unit.

    Returns None when the value cannot be normalized, rather than guessing. Callers
    should already have run the verifier; this function assumes nothing about validity
    and simply refuses anything it cannot convert exactly.
    """
    if spec.kind not in (ValueKind.QUANTITY, ValueKind.RANGE):
        return None

    try:
        parsed = parse_quantity(value.raw)
    except UnitParseError:
        return None

    target = spec.canonical_unit
    unit = parsed.unit

    if unit is None:
        # No unit in the source: take the canonical unit at face value but record it, so
        # the assumption travels with the data instead of disappearing into a float.
        magnitude, low, high = parsed.magnitude, parsed.low, parsed.high
    elif target:
        ureg = registry()

        def to_target(v: float | None) -> float | None:
            return None if v is None else ureg.Quantity(v, unit).to(target).magnitude

        try:
            magnitude = to_target(parsed.magnitude)
            low = to_target(parsed.low)
            high = to_target(parsed.high)
        except Exception:  # noqa: BLE001 - incompatible units are the verifier's business
            return None
    else:
        magnitude, low, high = parsed.magnitude, parsed.low, parsed.high

    if spec.kind is ValueKind.RANGE:
        if low is None or high is None:
            return None
        return NormalizedValue(kind=ValueKind.RANGE, low=low, high=high, unit=target or unit)

    return NormalizedValue(kind=ValueKind.QUANTITY, magnitude=magnitude, unit=target or unit)
