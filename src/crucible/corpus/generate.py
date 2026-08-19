"""Degrade-to-generate: manufacturing ground truth for calibration.

Conformal certification needs labelled data — several hundred values per category whose
correct answer is known. Hand-labelling that is exactly the expensive manual work this
project exists to eliminate, which makes it a poor way to start.

So the corpus is built backwards. A physically coherent product is generated from real
dimensional tables, then *degraded* into the compressed shorthand a distributor's ERP
actually stores:

    1 inch ball valve, bronze body, RPTFE seats, 600 WOG, NPT threaded, full port
    ->  1 BALL VLV BRZ RPTFE 600WOG SCRD FP

The degraded string becomes the pipeline's input and the original record is the answer
key. Ground truth is free, exact, and available in whatever quantity calibration needs.

Two design decisions worth stating.

**Descriptions are assembled from ordered tokens, not truncated prose.** Real distributor
descriptions front-load the identifying spec, because whoever wrote them knew the field
would be cut off. Degrading prose instead produces strings that lose the pressure rating
and keep the word "DEEP", which is not how catalogs fail.

**What counts as recoverable is derived, never assumed.** An attribute is only in the
answer key's recoverable set if the token evidencing it actually survived assembly.
Scoring a model against facts that were truncated away measures clairvoyance, not
extraction, and would make the error rate - and therefore the guarantee - meaningless.

The honest limitation: degradation is a *model* of how catalog data gets mangled, not a
sample of real mangling. A guarantee calibrated purely on this corpus is a guarantee
about this corpus. It is the right way to build and test the machinery, and the headline
number still has to be confirmed against a hand-labelled set of genuinely industrial
records.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from crucible.corpus import tables
from crucible.schema import RawProduct

#: Typical width of a legacy ERP short-description field.
DEFAULT_FIELD_WIDTH = 40


@dataclass(frozen=True)
class Token:
    """One fragment of an ERP description, and the attributes it evidences.

    Tokens are emitted in order of how identifying they are, so that when the field
    runs out it is the marginal detail that is lost rather than the part number.
    """

    text: str
    attributes: frozenset[str] = frozenset()

    @staticmethod
    def of(text: str, *attributes: str) -> Token:
        return Token(text=text, attributes=frozenset(attributes))


@dataclass
class GoldRecord:
    """A generated product, its degraded ERP form, and the answer key.

    `truth` holds every attribute of the real product. `recoverable` holds the subset an
    extractor could reasonably produce from the degraded description - the only subset
    it is fair to score against.
    """

    raw: RawProduct
    truth: dict[str, str]
    category_id: str
    clean_description: str
    recoverable: set[str] = field(default_factory=set)

    def scorable(self) -> dict[str, str]:
        """The answer key restricted to what the input actually supports."""
        return {k: v for k, v in self.truth.items() if k in self.recoverable}


def assemble(
    tokens: list[Token],
    rng: random.Random,
    field_width: int = DEFAULT_FIELD_WIDTH,
) -> tuple[str, set[str]]:
    """Join tokens into an ERP-style description, reporting what survived.

    Returns the description and the set of attributes still evidenced by it. Tokens are
    taken in order until the field is full; a token that does not fit is dropped along
    with its claim on the answer key.
    """
    parts: list[str] = []
    survived: set[str] = set()
    used = 0

    for token in tokens:
        text = token.text.strip()
        if not text:
            continue
        cost = len(text) + (1 if parts else 0)
        if used + cost > field_width:
            continue  # keep going: a later short token may still fit
        parts.append(text)
        survived |= set(token.attributes)
        used += cost

    description = " ".join(parts).upper()

    # Some systems strip the space before a trade rating, some do not. This is the kind
    # of inconsistency that makes a single regex insufficient in practice.
    if rng.random() < 0.5:
        description = description.replace(" WOG", "WOG").replace(" PSI", "PSI")

    return description, survived


def generate_bearing(rng: random.Random, index: int) -> GoldRecord:
    """A deep groove ball bearing, dimensioned per ISO 15."""
    spec = rng.choice(tables.BEARINGS)
    seal_code, seal_term = rng.choice(list(tables.BEARING_SEALS.items()))
    clearance = rng.choice(["", "C3", "C4"])
    designation = f"{spec.designation}{seal_code}"

    clean = (
        f"Deep groove ball bearing {designation}{' ' + clearance if clearance else ''}, "
        f"{spec.bore:g} mm bore, {spec.outside_diameter:g} mm outside diameter, "
        f"{spec.width:g} mm width, {seal_term}"
    )

    # The designation alone fixes bore, OD and width via ISO 15, which is why it leads.
    tokens = [
        Token.of("BRG BALL"),
        Token.of(designation, "bore_diameter", "outside_diameter", "width", "seal_type"),
        Token.of(clearance, "internal_clearance"),
        Token.of(f"{spec.bore:g}MM BORE", "bore_diameter"),
        Token.of(f"{spec.outside_diameter:g}MM OD", "outside_diameter"),
        Token.of(f"{spec.width:g}MM W", "width"),
    ]
    description, survived = assemble(tokens, rng)

    truth = {
        "bore_diameter": f"{spec.bore:g} mm",
        "outside_diameter": f"{spec.outside_diameter:g} mm",
        "width": f"{spec.width:g} mm",
        "seal_type": seal_term,
        "dynamic_load_rating": f"{spec.dynamic_load_n:g} N",
        "static_load_rating": f"{spec.static_load_n:g} N",
    }
    if clearance:
        truth["internal_clearance"] = clearance

    return GoldRecord(
        raw=RawProduct(
            sku=f"BRG-{index:05d}",
            description=description,
            mpn=designation,
            category_id="bearing.ball",
        ),
        truth=truth,
        category_id="bearing.ball",
        clean_description=clean,
        # Load ratings never appear in a short description; they come from a catalog
        # lookup, not from extraction.
        recoverable=survived & set(truth),
    )


def generate_screw(rng: random.Random, index: int) -> GoldRecord:
    """A hex cap screw, dimensioned per ASME B18.2.1."""
    thread = rng.choice(tables.THREADS)
    length = rng.choice(tables.SCREW_LENGTHS)
    grade_code = rng.choice(list(tables.SCREW_GRADES))
    grade_term, tensile = tables.SCREW_GRADES[grade_code]
    finish_code = rng.choice(list(tables.SCREW_FINISHES))
    finish_term = tables.SCREW_FINISHES[finish_code]
    material = tables.GRADE_MATERIAL[grade_code]

    # ASME thread length: 2D + 1/4 inch, or fully threaded if that exceeds the length.
    thread_length = min(2 * thread.nominal_diameter + 0.25, length)

    clean = (
        f"Hex cap screw {thread.label} x {length:g} inch, {grade_term}, {material}, {finish_term}"
    )

    tokens = [
        Token.of("HX CAP SCR"),
        Token.of(f"{thread.label}X{length:g}", "nominal_diameter", "threads_per_inch", "length"),
        Token.of(grade_code, "grade", "material"),
        Token.of(finish_code, "finish"),
    ]
    description, survived = assemble(tokens, rng)

    truth = {
        "nominal_diameter": f'{thread.nominal_diameter:g}"',
        "threads_per_inch": f"{thread.threads_per_inch} /in",
        "length": f'{length:g}"',
        "thread_length": f'{thread_length:g}"',
        "width_across_flats": f'{thread.width_across_flats:g}"',
        "head_height": f'{thread.head_height:g}"',
        "grade": grade_term,
        "material": material,
        "finish": finish_term,
        "tensile_strength": f"{tensile:g} psi",
        "drive_type": "external hex",
    }

    return GoldRecord(
        raw=RawProduct(
            sku=f"HCS-{index:05d}",
            description=description,
            mpn=f"{thread.label}X{length:g}{grade_code}{finish_code}",
            category_id="fastener.hex_cap_screw",
        ),
        truth=truth,
        category_id="fastener.hex_cap_screw",
        clean_description=clean,
        # Head geometry and tensile strength follow from thread and grade via the
        # standard. They are inferable from a table, not extractable from the string.
        recoverable=survived & set(truth),
    )


def generate_valve(rng: random.Random, index: int) -> GoldRecord:
    """A two-piece threaded ball valve."""
    size = rng.choice(tables.VALVE_SIZES)
    body_code = rng.choice(list(tables.VALVE_BODIES))
    body_term, pressure = tables.VALVE_BODIES[body_code]
    seat_code = rng.choice(list(tables.VALVE_SEATS))
    seat_term, (temp_low, temp_high) = tables.VALVE_SEATS[seat_code]
    end_code = rng.choice(list(tables.VALVE_ENDS))
    end_term = tables.VALVE_ENDS[end_code]

    full_port = rng.random() < 0.6
    bore = size.full_port_bore if full_port else size.reduced_port_bore
    port_term = "full port" if full_port else "reduced port"
    port_code = "FP" if full_port else "RP"

    clean = (
        f"{size.label} inch ball valve, {body_term} body, {seat_term} seats, "
        f"{pressure:g} WOG, {end_term}, {port_term}"
    )

    tokens = [
        Token.of(size.label, "nominal_size"),
        Token.of("BALL VLV"),
        Token.of(body_code, "body_material"),
        Token.of(f"{pressure:g} WOG", "pressure_rating"),
        Token.of(end_code, "end_connection"),
        Token.of(port_code, "port_type"),
        Token.of(seat_code, "seat_material"),
    ]
    description, survived = assemble(tokens, rng)

    truth = {
        "nominal_size": f'{size.label}"',
        "bore": f'{bore:g}"',
        "body_diameter": f'{size.body_diameter:g}"',
        "end_to_end": f'{size.end_to_end:g}"',
        "pressure_rating": f"{pressure:g} WOG",
        "temperature_range": f"{temp_low:g} to {temp_high:g} F",
        "body_material": body_term,
        "seat_material": seat_term,
        "end_connection": end_term,
        "port_type": port_term,
    }

    # Bore follows from nominal size and port type, so it is recoverable exactly when
    # both of those are - a small piece of real domain reasoning the pipeline must do.
    recoverable = survived & set(truth)
    if {"nominal_size", "port_type"} <= recoverable:
        recoverable.add("bore")

    return GoldRecord(
        raw=RawProduct(
            sku=f"BV-{index:05d}",
            description=description,
            mpn=f"{size.label}-{body_code}-{pressure:g}-{end_code}",
            category_id="valve.ball",
        ),
        truth=truth,
        category_id="valve.ball",
        clean_description=clean,
        recoverable=recoverable,
    )


GENERATORS = {
    "bearing.ball": generate_bearing,
    "fastener.hex_cap_screw": generate_screw,
    "valve.ball": generate_valve,
}


def generate_corpus(n_per_category: int, seed: int = 20260820) -> list[GoldRecord]:
    """Build a labelled corpus across all three demo categories.

    Deterministic given a seed, so a calibration run and the certificate it produces can
    be reproduced exactly.
    """
    if n_per_category < 1:
        raise ValueError(f"n_per_category must be positive, got {n_per_category}")

    rng = random.Random(seed)
    records: list[GoldRecord] = []
    for _category_id, generator in sorted(GENERATORS.items()):
        for i in range(n_per_category):
            records.append(generator(rng, i))
    return records
