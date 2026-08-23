"""Core data model for Crucible.

The whole system rests on one rule: a value is only as trustworthy as the evidence
it cites. That rule is encoded here rather than left to convention, so every
downstream stage inherits it.

The flow of types through the pipeline:

    RawProduct          what the distributor's ERP actually contains
      -> CategorySchema what attributes this kind of product should have
      -> AttributeValue a proposal, carrying the spans that justify it
      -> Assay          independent verdicts on that proposal
      -> CertifiedValue the proposal plus a publish/review decision
      -> Certificate    the population-level guarantee over a whole run
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field, model_validator

from crucible.units import abbreviate_unit


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------------------


class EvidenceKind(StrEnum):
    """Where a piece of evidence came from.

    Ordered loosely by how much we trust it: a manufacturer's own datasheet outranks
    a distributor's product page, which outranks the original ERP string.
    """

    DATASHEET_PDF = "datasheet_pdf"
    MANUFACTURER_PAGE = "manufacturer_page"
    DISTRIBUTOR_PAGE = "distributor_page"
    PRODUCT_IMAGE = "product_image"
    ERP_RECORD = "erp_record"


#: Prior trust weight per evidence kind, used as one input to the entailment verifier.
#: Deliberately coarse: these are priors, not conclusions, and entailment can override them.
EVIDENCE_TRUST: dict[EvidenceKind, float] = {
    EvidenceKind.DATASHEET_PDF: 1.00,
    EvidenceKind.MANUFACTURER_PAGE: 0.90,
    EvidenceKind.DISTRIBUTOR_PAGE: 0.70,
    EvidenceKind.PRODUCT_IMAGE: 0.60,
    EvidenceKind.ERP_RECORD: 0.50,
}


class EvidenceDoc(BaseModel):
    """A retrieved artifact that values may cite.

    content_sha256 exists so a certificate stays verifiable: if a manufacturer silently
    revises a datasheet, the hash stops matching and the guarantee issued against the
    old document is visibly stale rather than quietly wrong.
    """

    doc_id: str
    kind: EvidenceKind
    uri: str | None = None
    text: str | None = None
    content_sha256: str | None = None
    retrieved_at: datetime = Field(default_factory=_utcnow)

    @property
    def trust_prior(self) -> float:
        return EVIDENCE_TRUST[self.kind]


class SourceSpan(BaseModel):
    """A pointer into an EvidenceDoc that justifies one extracted value.

    Character offsets address text; page and bbox address a rendered PDF page so the UI
    can highlight the exact cell of a spec table. At least one addressing mode must be
    present, because a span that points nowhere cannot be verified.
    """

    doc_id: str
    quote: str
    start: int | None = None
    end: int | None = None
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def _require_an_address(self) -> SourceSpan:
        has_char_range = self.start is not None and self.end is not None
        has_page_locus = self.page is not None
        if not (has_char_range or has_page_locus):
            raise ValueError(
                f"SourceSpan into {self.doc_id!r} must carry either a character range or a "
                "page number; a span with no address cannot be verified."
            )
        if has_char_range and self.end <= self.start:  # type: ignore[operator]
            raise ValueError(f"SourceSpan range is empty or inverted: [{self.start}, {self.end})")
        return self


# --------------------------------------------------------------------------------------
# Attribute schema
# --------------------------------------------------------------------------------------


class ValueKind(StrEnum):
    """The shape a normalized value takes.

    Kept small on purpose. Industrial attributes are overwhelmingly a quantity, a term
    from a controlled vocabulary, a range, or a flag.
    """

    QUANTITY = "quantity"  # 12.7 mm: magnitude plus unit
    RANGE = "range"  # -20 to 120 degC
    NOMINAL = "nominal"  # "316 stainless steel", from a controlled vocabulary
    BOOLEAN = "boolean"
    TEXT = "text"  # free text, verifiable only by entailment


class AttributeSpec(BaseModel):
    """Definition of one attribute within a category.

    dimension is the physical dimensionality string understood by pint (for example
    "[length]" or "[mass] / [length] / [time] ** 2"). It is what lets the dimensional
    verifier reject "thread pitch: 4.2 kg" without anyone hand-writing that rule.

    The last three fields describe how the attribute *presents* in a delivery sheet rather
    than what it means. They live here because this is already where a category is
    configured: a product manager adding an attribute should be able to say what it is
    called on the sheet in the same place they say what it is, instead of that mapping
    living as a lookup table inside the exporter.

    `label` matters more than it looks. The delivery format's ATTRIBUTE_LABEL cells are a
    property of the category, not of the product - both reference rows for dishwashers
    carry the identical fifteen labels and differ only in which values are filled. So the
    label is emitted whenever the category is known, and the value only when one was
    actually established.
    """

    name: str
    kind: ValueKind
    description: str = ""
    dimension: str | None = None  # pint dimensionality, for QUANTITY / RANGE
    canonical_unit: str | None = None  # what we normalize to, e.g. "millimeter"
    vocabulary: list[str] | None = None  # allowed terms, for NOMINAL
    required: bool = False
    etim_feature: str | None = None  # e.g. "EF000008", for standards export
    label: str | None = None  # ATTRIBUTE_LABEL text, e.g. "Voltage Rating"
    display_uom: str | None = None  # ATTRIBUTE_UOM text, e.g. "V" - a symbol, not a name
    order: int | None = None  # position in the category's attribute template

    @property
    def sheet_label(self) -> str:
        """What the delivery sheet calls this attribute.

        Falls back to a title-cased attribute name so a schema authored without labels
        still exports something readable rather than snake_case leaking onto a sheet a
        customer reads.
        """
        return self.label or self.name.replace("_", " ").title()

    @model_validator(mode="after")
    def _check_kind_consistency(self) -> AttributeSpec:
        if self.kind in (ValueKind.QUANTITY, ValueKind.RANGE) and not self.dimension:
            raise ValueError(
                f"attribute {self.name!r} is {self.kind.value} and needs a dimension so the "
                "dimensional verifier has something to check against"
            )
        if self.kind is ValueKind.NOMINAL and not self.vocabulary:
            raise ValueError(
                f"attribute {self.name!r} is nominal and needs a vocabulary; without one "
                "there is no way to tell a valid term from a hallucinated one"
            )
        return self


class CategorySchema(BaseModel):
    """The attribute set for one product category, plus its cross-attribute constraints.

    Constraints are expression strings evaluated by the constraint verifier, for example
    "bore <= body_diameter". They live here rather than in code so a category can be
    authored, shipped, or mined as data.
    """

    category_id: str
    name: str
    attributes: list[AttributeSpec]
    constraints: list[str] = Field(default_factory=list)
    unspsc: str | None = None
    etim_class: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def attribute_names(self) -> list[str]:
        return [a.name for a in self.attributes]

    def get(self, name: str) -> AttributeSpec | None:
        return next((a for a in self.attributes if a.name == name), None)

    def template(self) -> list[AttributeSpec]:
        """Attributes in the order the delivery sheet lists them.

        Explicit `order` first and in ascending order, then anything unordered in
        declaration order. Stable across runs, because the ATTRIBUTE_LABEL n columns are a
        positional contract: a product re-exported after an unrelated edit must not have
        its Voltage Rating move from slot 4 to slot 7, or every downstream diff of that
        catalog becomes noise.
        """
        ordered = [a for a in self.attributes if a.order is not None]
        unordered = [a for a in self.attributes if a.order is None]
        return sorted(ordered, key=lambda a: a.order or 0) + unordered

    @model_validator(mode="after")
    def _unique_attribute_names(self) -> CategorySchema:
        names = [a.name for a in self.attributes]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"category {self.category_id!r} has duplicate attributes: {dupes}")
        return self


# --------------------------------------------------------------------------------------
# Products and values
# --------------------------------------------------------------------------------------


class RawProduct(BaseModel):
    """A product as it exists before Crucible touches it.

    Deliberately bleak: a part number, a truncated uppercase description, and a brand if
    you are lucky. Recovering structure from this is the actual job.
    """

    sku: str
    description: str
    brand: str | None = None
    mpn: str | None = None
    category_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class NormalizedValue(BaseModel):
    """A value after unit conversion and vocabulary mapping.

    Which fields are populated depends on kind; the dimensional verifier is what
    guarantees magnitude and unit agree with the attribute's declared dimension.
    """

    kind: ValueKind
    magnitude: float | None = None
    unit: str | None = None
    low: float | None = None  # RANGE
    high: float | None = None  # RANGE
    term: str | None = None  # NOMINAL
    flag: bool | None = None  # BOOLEAN
    text: str | None = None  # TEXT

    def render(self) -> str:
        """Human-readable form, for review queues and export.

        Units render as symbols rather than canonical names: a reviewer scanning a queue
        should see "12.7 mm", not "12.7 millimeter".
        """
        symbol = abbreviate_unit(self.unit) if self.unit else ""
        match self.kind:
            case ValueKind.QUANTITY:
                return f"{self.magnitude:g} {symbol}".strip() if self.magnitude is not None else "-"
            case ValueKind.RANGE:
                if self.low is None or self.high is None:
                    return "-"
                return f"{self.low:g} to {self.high:g} {symbol}".strip()
            case ValueKind.NOMINAL:
                return self.term or "-"
            case ValueKind.BOOLEAN:
                return "yes" if self.flag else "no"
            case _:
                return self.text or "-"


class AttributeValue(BaseModel):
    """A proposed value for one attribute of one product.

    raw preserves exactly what the model emitted; normalized is the canonical form.
    Keeping both is what makes an extraction error distinguishable from a normalization
    error when the numbers are audited later.

    A value with no spans is not rejected here. It is simply born untrusted, and the
    entailment verifier scores it accordingly.
    """

    attribute: str
    raw: str
    normalized: NormalizedValue | None = None
    spans: list[SourceSpan] = Field(default_factory=list)
    proposer: str = "unknown"  # model id that produced it

    @property
    def is_grounded(self) -> bool:
        """Whether any evidence was cited at all. Ungrounded values cannot be certified."""
        return len(self.spans) > 0


class Routing(BaseModel):
    """Where a product was classified, how, and on what evidence.

    Routing is treated as a value rather than a side effect, and carries spans for the
    same reason every other value does: the four classification columns are published
    data, and a Dept nobody can trace is exactly as unsupportable as a bore nobody can
    trace. `method` records which tier of the cascade decided, so a routing can be
    audited without re-running it.

    When nothing is recognised, `category_id` is the generic fallback and dept/klass/fine
    are None. Guessing a department to avoid an empty cell would be the classification
    equivalent of inventing a magnitude to satisfy a schema.
    """

    category_id: str
    dept: str | None = None
    klass: str | None = None
    fine: str | None = None
    classpath: str | None = None
    unspsc: str | None = None
    confidence: float = 0.0
    method: str = "none"
    spans: list[SourceSpan] = Field(default_factory=list)
    runners_up: list[tuple[str, float]] = Field(default_factory=list)

    @property
    def is_generic(self) -> bool:
        return self.dept is None


class ProductRecord(BaseModel):
    """A product mid-pipeline: raw input, resolved identity, proposed values."""

    raw: RawProduct
    category_id: str | None = None
    expanded_description: str | None = None
    routing: Routing | None = None
    evidence: list[EvidenceDoc] = Field(default_factory=list)
    values: list[AttributeValue] = Field(default_factory=list)

    @property
    def sku(self) -> str:
        return self.raw.sku

    def value_for(self, attribute: str) -> AttributeValue | None:
        return next((v for v in self.values if v.attribute == attribute), None)

    def evidence_for(self, doc_id: str) -> EvidenceDoc | None:
        return next((d for d in self.evidence if d.doc_id == doc_id), None)
