"""Building one delivery-format row from one verified product.

The delivery sheet has 252 columns and this module decides what goes in each of them. The
governing rule is inverted from what a spreadsheet usually invites:

    **Every cell is blank until a populator earns it.**

That is not caution for its own sake. The provided reference rows are two dishwashers that
carry the *identical* ATTRIBUTE_LABEL 1..15 sequence and blank *different* values - one
omits Model, Plug Type and Color, the other omits Number of Wash Cycles, Plug Type and
Maximum Height. The format is a per-class attribute template populated where the data
supports it, so a populated label beside an empty value is not a gap in the export. It is
the sheet saying, in its own vocabulary, that this attribute was looked for and not found.

Provenance travels with every cell, because "where did this come from" is a different
question from "is it right", and the evidence sidecar has to answer both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from crucible.emit.columns import (
    ATTRIBUTE_SLOTS,
    DELIVERY_COLUMNS,
    FEATURE_SLOTS,
    attribute_columns,
    feature_column,
)
from crucible.emit.compose import compose_all
from crucible.schema import AttributeValue, CategorySchema, ProductRecord, SourceSpan
from crucible.units import split_value_uom


class Provenance(StrEnum):
    """How a cell's content came to be, in descending order of directness."""

    PASSTHROUGH = "passthrough"  # copied verbatim from an input column
    ROUTED = "routed"  # decided by the category router
    EXTRACTED = "extracted"  # proposed by rules or the model, grounded in a span
    DERIVED = "derived"  # computed from other cells (a label from its schema)
    COMPOSED = "composed"  # assembled from verified values by a fixed template


class FillMode(StrEnum):
    """How much uncertainty the export is willing to publish.

    CERTIFIED is the product and the default. The other two exist because a scoring rubric
    that counts populated cells would punish correct abstention, and losing that way would
    be a worse outcome than publishing a clearly-marked uncertain value. In every mode the
    uncertainty is recorded in the sidecar and excluded from the certificate's scope, so
    this widens what is printed without ever changing what is claimed.
    """

    CERTIFIED = "certified"  # only values that passed the conformal threshold
    GROUNDED = "grounded"  # any value with a source span, confidence noted
    ALL = "all"  # grounded values plus flagged low-confidence proposals


@dataclass(frozen=True)
class EmittedCell:
    """One populated cell, with everything the sidecar needs to explain it."""

    column: str
    value: str
    provenance: Provenance
    spans: tuple[SourceSpan, ...] = ()
    attribute: str | None = None
    certified: bool = True
    nonconformity: float | None = None

    def __post_init__(self) -> None:
        if self.provenance is Provenance.EXTRACTED and not self.spans:
            raise ValueError(
                f"cell {self.column!r} claims to be extracted but carries no source span; "
                "an extracted value with no evidence is exactly what this system exists "
                "to keep out of a catalog"
            )


@dataclass
class EmitPolicy:
    """The knobs deciding how much of what was found actually gets printed."""

    fill_mode: FillMode = FillMode.CERTIFIED
    #: Values at or below this nonconformity are certified. None means nothing has been
    #: calibrated, in which case CERTIFIED mode publishes no attribute values at all
    #: rather than publishing everything. Refusing is the safe direction to fail.
    threshold: float | None = None
    include_marketing: bool = True

    def admits(self, value: AttributeValue, nonconformity: float | None) -> bool:
        """Whether this value may be printed under the current mode."""
        if not value.spans:
            return False
        if self.fill_mode in (FillMode.ALL, FillMode.GROUNDED):
            return True
        return self.is_certified(nonconformity)

    def is_certified(self, nonconformity: float | None) -> bool:
        if self.threshold is None or nonconformity is None:
            return False
        return nonconformity <= self.threshold


@dataclass
class DeliveryRow:
    """A row under construction: populated cells only, blanks by omission."""

    sku: str
    cells: dict[str, EmittedCell] = field(default_factory=dict)

    def put(self, cell: EmittedCell | None) -> None:
        if cell is None:
            return
        if cell.column not in _COLUMN_SET:
            raise ValueError(f"{cell.column!r} is not a delivery column")
        self.cells[cell.column] = cell

    def as_dict(self) -> dict[str, str]:
        """The row as the writer wants it: every column present, unset ones empty."""
        return {c: self.cells[c].value if c in self.cells else "" for c in DELIVERY_COLUMNS}

    @property
    def populated(self) -> int:
        return len(self.cells)


_COLUMN_SET = frozenset(DELIVERY_COLUMNS)


def _cell(
    column: str,
    value: str | None,
    provenance: Provenance,
    *,
    spans: tuple[SourceSpan, ...] = (),
    attribute: str | None = None,
    certified: bool = True,
    nonconformity: float | None = None,
) -> EmittedCell | None:
    """Make a cell, or None when there is nothing worth printing.

    Empty and whitespace-only values collapse to None here rather than being written as
    blanks, so that `DeliveryRow.populated` counts cells that actually say something.
    """
    text = (value or "").strip()
    if not text:
        return None
    return EmittedCell(
        column=column,
        value=text,
        provenance=provenance,
        spans=spans,
        attribute=attribute,
        certified=certified,
        nonconformity=nonconformity,
    )


# --------------------------------------------------------------------------------------
# Populators. Each owns one family of columns and knows nothing about the others.
# --------------------------------------------------------------------------------------


def populate_identity(row: DeliveryRow, record: ProductRecord) -> None:
    """Columns copied straight from the input, plus the brand ingest recovered.

    PASSTHROUGH is the one provenance exempt from needing a span, because the span would
    be circular: the value *is* the source.

    Note what is deliberately absent. PART_NUMBER and "SKU - MY_PART_NUMBER" are populated
    in the reference sheet but are distributor-internal identifiers that cannot be derived
    from these six input columns, so they stay blank rather than being filled with the
    manufacturer's part number wearing a different name.
    """
    raw = record.raw
    extra = raw.extra or {}

    row.put(_cell("Mfg_Part_Num", raw.mpn or raw.sku, Provenance.PASSTHROUGH))
    row.put(_cell("MANUFACTURER_PART_NUMBER", raw.mpn or raw.sku, Provenance.PASSTHROUGH))
    row.put(_cell("Part_Desc", raw.description, Provenance.PASSTHROUGH))

    # `extra` is keyed by the source file's own column names, not by lowercase slugs.
    # Guessing the slugs left four columns silently empty for every product.
    for column in ("E1_Brand", "Unilog_Brand", "DIB_Brand", "Part_Manuf"):
        row.put(_cell(column, extra.get(column), Provenance.PASSTHROUGH))

    row.put(_cell("MANUFACTURER_NAME", extra.get("part_manuf_name"), Provenance.PASSTHROUGH))

    # The brand column may be a placeholder on every input row while the description still
    # names the brand ("Diablo"), so fall back to the extracted, grounded value.
    brand = raw.brand or next(
        (v.raw for v in record.values if v.attribute == "brand" and (v.raw or "").strip()), None
    )
    row.put(_cell("BRAND_NAME", brand, Provenance.PASSTHROUGH))


def populate_taxonomy(row: DeliveryRow, record: ProductRecord) -> None:
    """Dept / Class / Fine / Classpath / UNSPSC, from the router.

    All five stay blank for a generically-routed product. A department invented to avoid
    an empty cell is the classification equivalent of inventing a magnitude to satisfy a
    schema: published data that nothing supports.
    """
    routing = record.routing
    if routing is None:
        return
    spans = tuple(routing.spans)
    for column, value in (
        ("Dept", routing.dept),
        ("Class", routing.klass),
        ("Fine", routing.fine),
        ("Classpath", routing.classpath),
        ("UNSPSC", routing.unspsc),
    ):
        row.put(_cell(column, value, Provenance.ROUTED, spans=spans))


def populate_attributes(
    row: DeliveryRow,
    record: ProductRecord,
    schema: CategorySchema,
    policy: EmitPolicy,
    nonconformity: dict[str, float] | None = None,
) -> None:
    """The 50 ATTRIBUTE_LABEL / VALUE / UOM triples - the heart of the sheet.

    The label is emitted whenever the category is known, because labels are a property of
    the category rather than of the product. The value and its unit are emitted only when
    one was established and the policy admits it.

    That asymmetry is the whole design. A reader can tell "this class does not carry that
    attribute" from "that attribute applies here and we could not establish it", and those
    are different facts that a uniformly blank column would conflate.
    """
    scores = nonconformity or {}
    by_attribute = {v.attribute: v for v in record.values}

    for slot, spec in enumerate(schema.template()[:ATTRIBUTE_SLOTS], start=1):
        label_col, value_col, uom_col = attribute_columns(slot)
        row.put(_cell(label_col, spec.sheet_label, Provenance.DERIVED, attribute=spec.name))

        value = by_attribute.get(spec.name)
        if value is None:
            continue

        score = scores.get(spec.name)
        if not policy.admits(value, score):
            continue

        magnitude, uom = split_value_uom(value.raw, spec)
        certified = policy.is_certified(score)
        row.put(
            _cell(
                value_col,
                magnitude,
                Provenance.EXTRACTED,
                spans=tuple(value.spans),
                attribute=spec.name,
                certified=certified,
                nonconformity=score,
            )
        )
        row.put(
            _cell(
                uom_col,
                uom,
                Provenance.DERIVED,
                attribute=spec.name,
                certified=certified,
                nonconformity=score,
            )
        )


def populate_descriptions(row: DeliveryRow, record: ProductRecord, schema: CategorySchema) -> None:
    """The five descriptions and the feature bullets.

    Provenance is COMPOSED rather than EXTRACTED: the text was assembled here, but every
    fact in it came from a verified value, and the spans are the union of those values'
    spans. That is why COMPOSED is exempt from the span requirement in `EmittedCell` while
    still carrying spans in practice - the cell can cite a source for every clause.

    Composers return nothing rather than padding when the product has no name to build a
    sentence around. A description assembled from three verified facts and one guess is the
    exact failure the client's guide calls out: "a fluent description made of invented
    values scores zero".
    """
    fields, features = compose_all(record, schema)

    for column, composed in fields.items():
        row.put(_cell(column, composed.text, Provenance.COMPOSED, spans=composed.spans))

    for slot, feature in enumerate(features[:FEATURE_SLOTS], start=1):
        row.put(_cell(feature_column(slot), feature.text, Provenance.COMPOSED, spans=feature.spans))

    # Product Name is the noun the descriptions are built around, so it is published as its
    # own column rather than left implicit inside the prose.
    name = next(
        (
            v
            for v in record.values
            if v.attribute
            in (
                "product_name",
                "appliance_type",
                "tool_type",
                "fixture_type",
                "component_type",
                "bit_type",
            )
        ),
        None,
    )
    if name is not None:
        row.put(
            _cell(
                "Product Name",
                " ".join(w if w[:2].isupper() else w.capitalize() for w in name.raw.split()),
                Provenance.DERIVED,
                spans=tuple(name.spans),
                attribute=name.attribute,
            )
        )


#: Attributes that describe an overall physical dimension, mapped to the delivery sheet's
#: dedicated dimension columns. These are separate from the ATTRIBUTE_VALUE grid because a
#: PIM filters and ships on them, so they get first-class columns.
_DIMENSION_COLUMNS: dict[str, tuple[str, str]] = {
    "length": ("LENGTH", "LENGTH_UOM"),
    "overall_length": ("LENGTH", "LENGTH_UOM"),
    "width": ("WIDTH", "WIDTH_UOM"),
    "nominal_width": ("WIDTH", "WIDTH_UOM"),
    "height": ("HEIGHT", "HEIGHT_UOM"),
    "weight": ("WEIGHT", "WEIGHT_UOM"),
    "volume": ("VOLUME", "VOLUME_UOM"),
    "capacity": ("VOLUME", "VOLUME_UOM"),
}
#: Deliberately absent: `thickness`, `nominal_thickness`, `disc_diameter`, `bore` and the
#: other category-specific dimensions. The delivery format offers only LENGTH / WIDTH /
#: HEIGHT / WEIGHT / VOLUME, and forcing a board's thickness into HEIGHT or a wheel's
#: diameter into WIDTH would put a value under a label that does not mean it. They stay in
#: the ATTRIBUTE grid, under their own correct labels.

#: Attributes naming what a product is used on or for.
_APPLICATION_ATTRS = ("material_application", "application", "location_rating")

#: Pack-quantity suffixes seen in this catalog, mapped to a selling unit of measure.
_PACK_UNITS: dict[str, str] = {
    "pc": "PK",
    "pcs": "PK",
    "pk": "PK",
    "pack": "PK",
    "ea": "EA",
    "each": "EA",
    "box": "BX",
    "bx": "BX",
    "ct": "PK",
    "set": "ST",
    "roll": "RL",
    "rl": "RL",
}


def populate_commerce(row: DeliveryRow, record: ProductRecord, schema: CategorySchema) -> None:
    """Columns a PIM filters and ships on: dimensions, selling quantity, application.

    Everything here is a re-presentation of a value already extracted and verified, moved
    into the dedicated column the delivery format gives it. Nothing new is claimed.

    What is deliberately *not* populated, and why it matters
    -------------------------------------------------------
    The reference rows fill `Product Image` with `FRIGIDAIRE_PDSH4816AF.jpg`,
    `Specification Sheet` with `..._Specification_Sheet.pdf`, and `Actual Image (Yes/No)`
    with `Yes`. The naming convention is obvious - brand, part number, suffix - and we could
    synthesise all of those strings for every product in seconds.

    We do not, because a filename is a claim that a file exists. We hold no images and no
    datasheets; emitting the name of one would be a confidently-formatted assertion about
    something nobody looked for, which is the precise failure this system exists to prevent.
    `Actual Image (Yes/No)` = "Yes" would be simply false.

    The same reasoning blanks `MFR URL` and `Ref URL 1..5` (retrieval we do not perform, and
    the guide's sourcing rules restrict), and `PART_NUMBER` / `SKU - MY_PART_NUMBER`
    (distributor-internal identifiers absent from the input).
    """
    by_name = {v.attribute: v for v in record.values}

    for attribute, (value_col, uom_col) in _DIMENSION_COLUMNS.items():
        value = by_name.get(attribute)
        if value is None or value_col in row.cells:
            continue
        magnitude, uom = split_value_uom(value.raw, schema.get(attribute))
        if not magnitude:
            continue
        spans = tuple(value.spans)
        row.put(_cell(value_col, magnitude, Provenance.DERIVED, spans=spans, attribute=attribute))
        row.put(_cell(uom_col, uom, Provenance.DERIVED, attribute=attribute))

    for attribute in _APPLICATION_ATTRS:
        value = by_name.get(attribute)
        if value is not None and (value.raw or "").strip():
            row.put(
                _cell(
                    "Application",
                    value.raw,
                    Provenance.DERIVED,
                    spans=tuple(value.spans),
                    attribute=attribute,
                )
            )
            break

    _populate_selling_quantity(row, by_name, schema)

    extra = by_name.get("additional_information")
    if extra is not None and (extra.raw or "").strip():
        row.put(
            _cell(
                "With",
                f"With {extra.raw.strip()}",
                Provenance.COMPOSED,
                spans=tuple(extra.spans),
                attribute="additional_information",
            )
        )


def _populate_selling_quantity(
    row: DeliveryRow, by_name: dict[str, AttributeValue], schema: CategorySchema
) -> None:
    """`6pc` -> Selling Qty 6, Selling UOM PK. A bare number sells as one EA."""
    value = by_name.get("pack_quantity") or by_name.get("quantity")
    if value is None or not (value.raw or "").strip():
        return

    text = value.raw.strip()
    match = re.match(r"^\s*(\d+)\s*([A-Za-z]*)\s*$", text)
    if match is None:
        return

    count, suffix = match.group(1), match.group(2).lower()
    uom = _PACK_UNITS.get(suffix, "EA" if not suffix else "")
    if not uom:
        return  # an unrecognised suffix is not a unit we can name

    spans = tuple(value.spans)
    row.put(_cell("Selling Qty", count, Provenance.DERIVED, spans=spans, attribute=value.attribute))
    row.put(_cell("Selling UOM", uom, Provenance.DERIVED, attribute=value.attribute))


def build_row(
    record: ProductRecord,
    schema: CategorySchema,
    policy: EmitPolicy | None = None,
    nonconformity: dict[str, float] | None = None,
) -> DeliveryRow:
    """Assemble one delivery row. Populators are independent and order-insensitive."""
    policy = policy or EmitPolicy()
    row = DeliveryRow(sku=record.sku)
    populate_identity(row, record)
    populate_taxonomy(row, record)
    populate_attributes(row, record, schema, policy, nonconformity)
    populate_descriptions(row, record, schema)
    populate_commerce(row, record, schema)
    return row
