"""Canonicalisation: putting extracted values into the schema's vocabulary.

This stage was missing, and its absence was quietly corrupting the whole measurement.

The model returns what the source says. The source says `Z`, `1/4`, `18-8`. The schema
says `Z metal shielded one side`, `0.25"`, `304 stainless steel`. Comparing those
directly scored the model 62.9% wrong, when in truth it had read most of them correctly
and merely expressed them in the source's vocabulary rather than the catalog's.

That mattered far beyond a cosmetic accuracy figure. Calibration learns to separate right
from wrong from labelled examples, so labels dominated by vocabulary mismatch would have
taught the verifiers to detect *formatting*, and the resulting certificate would have
been a guarantee about notation rather than correctness. The bound would have held
perfectly and meant nothing.

The three transformations, in the order they are tried
-----------------------------------------------------
**Code expansion.** A value matching a known code for the attribute becomes the term that
code denotes. This is a dictionary lookup against the same tables the corpus and rule
extractor use, so it is exact rather than a guess.

**Numeric canonicalisation.** `1/4` becomes `0.25`. Fractions are ordinary in industrial
data and a system that treats 1/4 and 0.25 as different values is not usable on it. Only
the magnitude is rewritten; whatever unit the extractor wrote is preserved verbatim.

A bare magnitude stays bare. An earlier version supplied the attribute's canonical unit
when none was present, which turned `1/4` on a quarter-inch screw into `0.25 millimeter` -
the schema's canonical unit being millimetres while imperial fasteners are written in
inches. That is fabrication, and worse, it is precisely the unit-confusion failure this
system exists to detect, committed by the system itself one stage before the verifier that
would have caught it. A missing unit is a fact about the extraction and belongs to the
dimensional verifier to report, not to this stage to paper over.

**Vocabulary matching.** A value that is a known synonym of, or an unambiguous prefix of,
exactly one term in the attribute's vocabulary becomes that term.

What it deliberately will not do
--------------------------------
Ambiguity is left alone. If a value could map to two vocabulary terms it is returned
unchanged, because guessing between them would manufacture a confident wrong value where
an obviously unnormalised one would have been routed to review.

Unit conversion is *not* performed here. `12.7 mm` is not rewritten to `0.5"` even where
the schema prefers inches, because the dimensional verifier needs to see the unit the
extractor actually produced. Normalising units away here would hide the unit-confusion
failures that verifier exists to catch - the same mistake as comparing on formatting,
made one stage later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

from crucible.corpus import tables
from crucible.schema import AttributeSpec, AttributeValue, CategorySchema, ProductRecord, ValueKind

#: Domain synonyms that no code table covers. Kept small and explicit: an open-ended
#: synonym list becomes a place where wrong mappings hide, so entries earn their place by
#: appearing in real extractions.
SYNONYMS: dict[str, str] = {
    "18-8": "304 stainless steel",
    "ss": "316 stainless steel",
    "sst": "316 stainless steel",
    "stainless": "316 stainless steel",
    "stainless steel": "316 stainless steel",
    "carbon steel": "carbon steel",
    "cs": "carbon steel",
    "brass": "brass",
    "brs": "brass",
    "pvc": "PVC",
    "ptfe": "PTFE",
    "teflon": "PTFE",
}

_FRACTION = re.compile(r"^(\d+)?[\s-]*(\d+)/(\d+)$")
_LEADING_NUMBER = re.compile(r"^(-?\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class Normalisation:
    """What happened to one value, kept so the change is auditable."""

    original: str
    normalised: str
    rule: str

    @property
    def changed(self) -> bool:
        return self.original != self.normalised


def _code_tables(attribute: str) -> dict[str, str]:
    """Codes valid for one attribute, flattened to code -> term."""
    match attribute:
        case "body_material":
            return {c: t for c, (t, _) in tables.VALVE_BODIES.items()}
        case "seat_material":
            return {c: t for c, (t, _) in tables.VALVE_SEATS.items()}
        case "end_connection":
            return dict(tables.VALVE_ENDS)
        case "grade":
            return {c: t for c, (t, _) in tables.SCREW_GRADES.items()}
        case "material":
            return dict(tables.GRADE_MATERIAL)
        case "finish":
            return dict(tables.SCREW_FINISHES)
        case "seal_type":
            # Stored with the leading hyphen because they are designation suffixes in
            # "6205-2RS". Extractors report the bare code, so match on that.
            return {c.lstrip("-"): t for c, t in tables.BEARING_SEALS.items() if c}
        case "port_type":
            return {"FP": "full port", "RP": "reduced port"}
        case _:
            return {}


def parse_fraction(text: str) -> float | None:
    """Read industrial fraction notation. `1/4`, `1-1/4`, `3 1/2`."""
    match = _FRACTION.match(text.strip())
    if not match:
        return None
    whole, numerator, denominator = match.groups()
    try:
        value = float(Fraction(int(numerator), int(denominator)))
    except (ValueError, ZeroDivisionError):
        return None
    return float(whole) + value if whole else value


def _canonical_number(text: str, spec: AttributeSpec) -> str | None:
    """Rewrite a numeric value into decimal form, preserving its unit exactly.

    A bare magnitude stays bare. Supplying the schema's canonical unit would fabricate
    one, and demonstrably the wrong one - imperial fasteners are written in inches while
    the schema canonicalises to millimetres.
    """
    stripped = text.strip()
    suffix = ""

    # Trailing unit is preserved exactly as written; only the magnitude is rewritten.
    number_match = _LEADING_NUMBER.match(stripped)
    fraction_value = parse_fraction(stripped)

    if fraction_value is not None:
        magnitude, suffix = fraction_value, ""
    else:
        # A fraction with a unit attached is tried before the plain leading-number
        # branch: that branch matches the "1" of `1-1/4"` and leaves `-1/4"` as the
        # unit, silently turning one and a quarter inches into one.
        for unit_start in range(len(stripped) - 1, 0, -1):
            candidate = parse_fraction(stripped[:unit_start])
            if candidate is not None:
                magnitude, suffix = candidate, stripped[unit_start:].strip()
                break
        else:
            if not number_match:
                return None
            magnitude = float(number_match.group(1))
            suffix = stripped[number_match.end() :].strip()

    formatted = f"{magnitude:g}"
    return f"{formatted}{suffix}" if suffix in ('"', "'") else f"{formatted} {suffix}".strip()


def _from_vocabulary(text: str, spec: AttributeSpec) -> str | None:
    """Match a value against the attribute's declared vocabulary.

    Exact match first, then unambiguous prefix. Ambiguous matches return None so the
    value stays visibly unnormalised rather than becoming a confident wrong guess.
    """
    if not spec.vocabulary:
        return None

    lowered = text.strip().lower()
    for term in spec.vocabulary:
        if term.lower() == lowered:
            return term

    prefixed = [t for t in spec.vocabulary if t.lower().startswith(lowered)]
    return prefixed[0] if len(prefixed) == 1 else None


def normalise_value(text: str, spec: AttributeSpec) -> Normalisation:
    """Canonicalise one value against one attribute spec."""
    original = text
    stripped = text.strip()
    if not stripped:
        return Normalisation(original, original, "empty")

    codes = _code_tables(spec.name)
    for code, term in codes.items():
        if code.lower() == stripped.lower():
            # A declared vocabulary constrains code expansion just as it constrains
            # synonyms; otherwise a code valid for one attribute leaks into another.
            if not spec.vocabulary or term in spec.vocabulary:
                return Normalisation(original, term, "code-expansion")
            break

    if spec.kind in (ValueKind.QUANTITY, ValueKind.RANGE):
        canonical = _canonical_number(stripped, spec)
        if canonical is not None:
            return Normalisation(original, canonical, "numeric")

    vocabulary_match = _from_vocabulary(stripped, spec)
    if vocabulary_match is not None:
        return Normalisation(original, vocabulary_match, "vocabulary")

    # Only accept a synonym the attribute actually permits, otherwise `ss` would resolve
    # to a stainless grade on an attribute where it means something else entirely.
    synonym = SYNONYMS.get(stripped.lower())
    if synonym is not None and (not spec.vocabulary or synonym in spec.vocabulary):
        return Normalisation(original, synonym, "synonym")

    return Normalisation(original, original, "unchanged")


def normalise_record(record: ProductRecord, schema: CategorySchema) -> ProductRecord:
    """Canonicalise every value in a record, preserving spans.

    Spans survive untouched: normalisation changes how a value is written, not what
    supports it, and the citation must still point at the text the extractor read.
    """
    values: list[AttributeValue] = []
    for value in record.values:
        spec = schema.get(value.attribute)
        if spec is None:
            values.append(value)
            continue
        result = normalise_value(value.raw, spec)
        values.append(
            value.model_copy(update={"raw": result.normalised}) if result.changed else value
        )

    return record.model_copy(update={"values": values})
