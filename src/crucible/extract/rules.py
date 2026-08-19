"""Rule-based extraction: the cheapest tier of the cascade.

Industrial short descriptions are compressed but not unstructured. `3 BALL VLV SS
1000WOG SW RP` follows a grammar a distributor's buyers can read at a glance, and much of
it yields to pattern matching at effectively zero cost. Sending every one of a million
SKUs to a language model when a regex resolves eighty percent of them is how enrichment
projects end up costing more than the humans they replaced.

So this is tier zero. It extracts only what it can match confidently, leaves everything
else absent, and lets the model handle the remainder. That division is the cost story.

Two things it deliberately does *not* do:

**It does not guess.** An unmatched attribute is omitted, never filled with a plausible
default. A missing value costs one review; a confidently wrong value costs trust in the
whole catalog.

**It does not skip provenance.** Every value carries a span into the source description,
exactly as the model-based extractor must. The rule that a value is only as trustworthy
as the evidence it cites has no exemption for values that were easy to obtain.

Where it goes beyond pattern matching, it does so through published standards rather than
inference. A bearing designated 6205 has a 25 mm bore because ISO 15 says so, and looking
that up is domain knowledge, not a guess - but the resulting value cites the designation
it was derived from, so a reviewer can follow the reasoning.

A measurement warning, recorded here so it cannot be mistaken for a result
--------------------------------------------------------------------------
This extractor scores 100% on the generated corpus. That number is meaningless. The
corpus assembles descriptions from `corpus.tables` and this module reads them back using
the same tables, so the two are circular by construction: the test is asking whether a
lookup table agrees with itself.

Two consequences follow, and neither is cosmetic.

The 100% figure must never be quoted as extraction accuracy. On real distributor data,
where codes are inconsistent, misspelled, vendor-specific and frequently absent, a rule
extractor of this shape does considerably worse.

More importantly, a perfect extractor produces no errors, and conformal calibration needs
both classes to fit anything at all. So the synthetic corpus can exercise the pipeline's
machinery but cannot by itself validate the guarantee. A realistic error distribution has
to come from somewhere else: the model-based extractor, or hand-labelled records. Until
then the certificate this path produces describes a scenario, not a system.
"""

from __future__ import annotations

import re

from crucible.corpus import tables
from crucible.schema import (
    AttributeValue,
    EvidenceDoc,
    EvidenceKind,
    ProductRecord,
    RawProduct,
    SourceSpan,
)

#: Identifier for the pseudo-document formed by the ERP description itself. Every span
#: from this extractor points here.
ERP_DOC_ID = "erp"

EXTRACTOR_NAME = "rules-v1"


def _erp_evidence(raw: RawProduct) -> EvidenceDoc:
    return EvidenceDoc(doc_id=ERP_DOC_ID, kind=EvidenceKind.ERP_RECORD, text=raw.description)


def _span(description: str, needle: str) -> SourceSpan | None:
    """Locate a matched token in the description, for provenance."""
    index = description.find(needle)
    if index < 0:
        return None
    return SourceSpan(doc_id=ERP_DOC_ID, quote=needle, start=index, end=index + len(needle))


def _value(
    attribute: str, raw_value: str, description: str, evidence_token: str
) -> AttributeValue | None:
    """Build a value, or None if its evidence cannot be located.

    A value whose supporting token cannot be found in the source is not emitted at all.
    Emitting it ungrounded would push an unverifiable value into the pipeline for the
    entailment verifier to reject later, which wastes the work and muddies the signal.
    """
    span = _span(description, evidence_token)
    if span is None:
        return None
    return AttributeValue(attribute=attribute, raw=raw_value, spans=[span], proposer=EXTRACTOR_NAME)


# --------------------------------------------------------------------------------------
# Ball valves
# --------------------------------------------------------------------------------------

#: Leading nominal size: "3", "1/2", "1-1/4".
_VALVE_SIZE = re.compile(r"^(\d+(?:-\d+/\d+)?(?:/\d+)?)\s")
_WOG = re.compile(r"(\d+)\s?(WOG|PSI|CWP)")


def extract_valve(raw: RawProduct) -> list[AttributeValue]:
    text = raw.description
    values: list[AttributeValue] = []

    size_label: str | None = None
    match = _VALVE_SIZE.search(text)
    if match:
        size_label = match.group(1)
        values.append(_value("nominal_size", f'{size_label}"', text, size_label))

    rating = _WOG.search(text)
    if rating:
        values.append(_value("pressure_rating", f"{rating.group(1)} WOG", text, rating.group(0)))

    # Codes are matched as whole tokens. Substring matching would read the "SS" inside
    # "304SS" as 316 stainless and quietly downgrade the material.
    tokens = text.split()

    for code, (term, _) in tables.VALVE_BODIES.items():
        if code in tokens:
            values.append(_value("body_material", term, text, code))
            break

    for code, (term, _) in tables.VALVE_SEATS.items():
        if code in tokens:
            values.append(_value("seat_material", term, text, code))
            break

    for code, term in tables.VALVE_ENDS.items():
        if code in tokens:
            values.append(_value("end_connection", term, text, code))
            break

    port_term = None
    if "FP" in tokens:
        port_term, port_code = "full port", "FP"
    elif "RP" in tokens:
        port_term, port_code = "reduced port", "RP"
    if port_term:
        values.append(_value("port_type", port_term, text, port_code))

    # Bore follows from nominal size and port type via the published dimension table.
    # It cites the size token, since that is what the derivation rests on.
    if size_label and port_term:
        size = next((s for s in tables.VALVE_SIZES if s.label == size_label), None)
        if size:
            bore = size.full_port_bore if port_term == "full port" else size.reduced_port_bore
            values.append(_value("bore", f'{bore:g}"', text, size_label))

    return [v for v in values if v is not None]


# --------------------------------------------------------------------------------------
# Hex cap screws
# --------------------------------------------------------------------------------------

#: "3/8-16X1.5" or "1-8X2" — thread designation glued to length by an X.
_SCREW_THREAD = re.compile(r"(\d+(?:/\d+)?-\d+)X(\d+(?:\.\d+)?)")


def extract_screw(raw: RawProduct) -> list[AttributeValue]:
    text = raw.description
    values: list[AttributeValue] = []
    tokens = text.split()

    match = _SCREW_THREAD.search(text)
    if match:
        thread_label, length = match.group(1), match.group(2)
        spec = next((t for t in tables.THREADS if t.label == thread_label), None)
        if spec:
            token = match.group(0)
            values.append(_value("nominal_diameter", f'{spec.nominal_diameter:g}"', text, token))
            values.append(_value("threads_per_inch", f"{spec.threads_per_inch} /in", text, token))
            values.append(_value("length", f'{float(length):g}"', text, token))

    # Grade codes are matched longest-first so "10.9" is not shadowed by a shorter code.
    for code in sorted(tables.SCREW_GRADES, key=len, reverse=True):
        if code in tokens:
            term, tensile = tables.SCREW_GRADES[code]
            values.append(_value("grade", term, text, code))
            values.append(_value("material", tables.GRADE_MATERIAL[code], text, code))
            values.append(_value("tensile_strength", f"{tensile:g} psi", text, code))
            break

    for code, term in tables.SCREW_FINISHES.items():
        if code in tokens:
            values.append(_value("finish", term, text, code))
            break

    return [v for v in values if v is not None]


# --------------------------------------------------------------------------------------
# Ball bearings
# --------------------------------------------------------------------------------------

#: "6205", "6205-2RS" — four-digit designation with an optional seal suffix.
_BEARING = re.compile(r"\b(\d{4})(-2RS|-RS|-2Z|-Z)?\b")


def extract_bearing(raw: RawProduct) -> list[AttributeValue]:
    text = raw.description
    values: list[AttributeValue] = []

    match = _BEARING.search(text)
    if not match:
        return values

    designation, suffix = match.group(1), match.group(2) or ""
    spec = next((b for b in tables.BEARINGS if b.designation == designation), None)
    if spec is None:
        return values

    token = match.group(0)
    # ISO 15 fixes all three principal dimensions from the designation alone. This is a
    # standards lookup, not an inference, but it still cites the designation it used.
    values.append(_value("bore_diameter", f"{spec.bore:g} mm", text, token))
    values.append(_value("outside_diameter", f"{spec.outside_diameter:g} mm", text, token))
    values.append(_value("width", f"{spec.width:g} mm", text, token))
    values.append(_value("seal_type", tables.BEARING_SEALS[suffix], text, token))

    for clearance in ("C3", "C4", "C2", "C5"):
        if clearance in text.split():
            values.append(_value("internal_clearance", clearance, text, clearance))
            break

    return [v for v in values if v is not None]


EXTRACTORS = {
    "valve.ball": extract_valve,
    "fastener.hex_cap_screw": extract_screw,
    "bearing.ball": extract_bearing,
}


def extract(raw: RawProduct, category_id: str | None = None) -> ProductRecord:
    """Run the rule extractor over one product.

    Unknown categories yield a record with no values rather than an error: the cascade's
    next tier is expected to handle whatever tier zero cannot, and an exception here
    would stop a catalog run over a single unrecognised product.
    """
    category = category_id or raw.category_id
    extractor = EXTRACTORS.get(category or "")
    values = extractor(raw) if extractor else []

    return ProductRecord(
        raw=raw,
        category_id=category,
        evidence=[_erp_evidence(raw)],
        values=values,
    )
