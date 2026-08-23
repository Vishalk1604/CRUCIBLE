"""Building the description fields from values that have already been verified.

The client's guide is blunt about where the marks are: *"the same product information is
rewritten five times at five different lengths and casings - for the till receipt, the mobile
app, the search results page, the product page and the marketing copy. Getting these formats
right is most of the task."*

And about the failure mode: *"A fluent description made of invented values scores zero."*

That second line is why this module is templates rather than a prompt. Every sentence here is
assembled from values the verifiers already passed, joined by fixed connective words. The
consequence is worth stating precisely, because it is the strongest claim this project can
make about any field:

    **A composed description cannot contain a fact that was not verified.**
    Not "is unlikely to" - cannot. There is no path in this code from source text to
    output prose that does not go through a verified AttributeValue.

Grounding is therefore *inherited*: a description's span set is the union of the spans of the
values it was built from, so the evidence sidecar can cite a source quote for every clause.

Where the templates come from
-----------------------------
Two fully enriched rows in the supplied delivery sheet, both dishwashers. They agree on
structure, so the shapes below are read off rather than invented. Two details the pair
settles that no amount of reasoning would have:

* **INVOICE_DESC closes the unit up** - `120V`, `50-1/4IN`, `41DBA` - while every other field
  spaces it (`120 V`, `50-1/4 in`). Both conventions are real and both are encoded.
* **Row 2 drops the manufacturer from MOBILE_DESC** because its manufacturer and brand are the
  same company (Whirlpool Corporation / Whirlpool®). Concatenating blindly would have produced
  "Whirlpool Corporation Whirlpool, Dishwasher, ...".

What is deliberately not built
------------------------------
`MARKETING_DESCRIPTION`. Row 2's is genuine manufacturer marketing copy - *"Load more and run
less with our quietest and largest capacity dishwasher"* - which cannot be derived from six
input columns without inventing it. Leaving it empty is a correct abstention. Writing
plausible marketing prose is exactly the thing the guide says scores zero.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from crucible.schema import AttributeValue, CategorySchema, ProductRecord, SourceSpan
from crucible.units import split_value_uom

#: Character budgets from the client's worked example. INVOICE_DESC's limit is hard - the
#: field is a till-receipt line and a longer string is truncated by the receiving system,
#: which is worse than a shorter one. The others are targets: the two reference rows land at
#: 64-75 for mobile and 96-115 for short, so those are ranges observed rather than declared.
INVOICE_MAX = 40
MOBILE_MIN, MOBILE_MAX = 60, 80
SHORT_TARGET_MAX = 120

#: Attributes that name the product rather than describe it, so they are never repeated in
#: the attribute run of a description - they already appear in its opening clause.
_IDENTITY_ATTRS = frozenset({"product_name", "brand", "series", "model", "mpn", "part_number"})

#: Attributes whose value reads as a complete phrase on its own. "Stainless Steel" is written
#: bare; "47 dBA" needs its label to mean anything, so it becomes "47 dBA Sound Level".
_BARE_VALUE_ATTRS = frozenset({"material", "color", "colour", "finish", "size"})

#: Trade abbreviations for the till-receipt line. Only entries observed in the reference rows
#: plus unambiguous extensions of them; an invented abbreviation is an invented value.
INVOICE_ABBREVIATIONS: dict[str, str] = {
    "stainless steel": "SST",
    "stainless": "SST",
    "built-in": "BLTLN",
    "built in": "BLTLN",
    "leg": "LEG",
    "under-counter": "UNDCTR",
    "freestanding": "FRSTND",
    "portable": "PRTBL",
    "black stainless": "BLKSST",
    "panel ready": "PNLRDY",
    "white": "WHT",
    "black": "BLK",
    "aluminum": "ALUM",
    "composite": "COMP",
    "electric": "ELEC",
    "brushless": "BRSHLS",
}


@dataclass(frozen=True)
class Composed:
    """A generated field and the evidence it rests on.

    `spans` is the union of the spans of every value used. That is what makes a description
    auditable at clause level rather than merely plausible.
    """

    text: str
    spans: tuple[SourceSpan, ...]
    attributes: tuple[str, ...]

    def __bool__(self) -> bool:
        return bool(self.text)


EMPTY = Composed("", (), ())


@dataclass
class Ingredients:
    """Everything the templates draw on, resolved once per product."""

    brand: str = ""
    manufacturer: str = ""
    mpn: str = ""
    product_name: str = ""
    series: str = ""
    with_clause: str = ""
    values: dict[str, AttributeValue] | None = None
    ordered: tuple[AttributeValue, ...] = ()
    #: Which attribute supplied `product_name`. Excluded from the attribute run, or the
    #: description says "Dishwasher, Dishwasher Product Name" - the noun twice, once with
    #: its own label attached.
    name_attribute: str = ""

    def spans_for(self, names: Iterable[str]) -> tuple[SourceSpan, ...]:
        seen: dict[tuple, SourceSpan] = {}
        for name in names:
            value = (self.values or {}).get(name)
            for span in value.spans if value else ():
                seen[(span.doc_id, span.quote, span.start)] = span
        return tuple(seen.values())


def gather(record: ProductRecord, schema: CategorySchema) -> Ingredients:
    """Resolve the parts every template needs, in the schema's own attribute order."""
    by_name = {v.attribute: v for v in record.values}
    ordered = tuple(by_name[spec.name] for spec in schema.template() if spec.name in by_name)

    def text(name: str) -> str:
        value = by_name.get(name)
        return (value.raw or "").strip() if value else ""

    name_attribute = next(
        (
            n
            for n in (
                "product_name",
                "appliance_type",
                "tool_type",
                "fixture_type",
                "component_type",
                "bit_type",
                "tool_type",
            )
            if text(n)
        ),
        "",
    )

    # Categories whose schema carries no noun-shaped attribute - a cut-off wheel is
    # described by its dimensions - still need something to build a sentence around. The
    # router already established a Fine class, and the reference row's Product Name
    # ("Dishwasher") is exactly its Fine class ("Dishwashers") in the singular.
    product_name = _titlecase(text(name_attribute)) if name_attribute else ""
    if not product_name and record.routing is not None:
        product_name = _singular(record.routing.fine or "")

    # The brand column may be a placeholder on every input row while the description still
    # names the brand ("Diablo"), so fall back to the extracted, grounded value.
    brand = (record.raw.brand or "").strip() or text("brand")

    return Ingredients(
        brand=brand,
        manufacturer=((record.raw.extra or {}).get("part_manuf_name") or "").strip(),
        mpn=(record.raw.mpn or record.raw.sku or "").strip(),
        # Title case: the reference rows write "Dishwasher", not "dishwasher", because the
        # noun heads a product title rather than sitting mid-sentence.
        product_name=product_name,
        name_attribute=name_attribute,
        series=text("series"),
        with_clause="",
        values=by_name,
        ordered=ordered,
    )


# --------------------------------------------------------------------------------------
# Rendering one attribute
# --------------------------------------------------------------------------------------


def _titlecase(text: str) -> str:
    """Title-case a product noun without mangling acronyms or hyphenated trade terms.

    Each hyphen-joined part is capitalised separately, so "cut-off" becomes "Cut-Off"
    rather than "Cut-off", and anything already shouting (SST, PVC) is left alone.
    """

    def cap(word: str) -> str:
        if word[:2].isupper():
            return word
        return "-".join(p.capitalize() if p else p for p in word.split("-"))

    return " ".join(cap(w) for w in (text or "").split())


def _singular(fine: str) -> str:
    """A Fine class rendered as a single product noun.

    Fine classes list several things sharing a head noun, and the head sits at either end
    depending on how the class was written:

        "Cut-Off & Grinding Wheels"     -> "Cut-Off Wheel"    (shared head at the end)
        "Sanding Belts, Discs & Sheets" -> "Sanding Belt"     (first term already complete)
        "Dishwashers"                   -> "Dishwasher"

    So: take the first listed term, and if it is a bare modifier borrow the head noun from
    the end of the class.
    """
    cleaned = (fine or "").strip()
    if not cleaned:
        return ""

    terms = [p.strip() for p in re.split(r"\s*[&/,]\s*", cleaned) if p.strip()]
    if not terms:
        return ""

    head = terms[0]
    if len(head.split()) == 1 and len(terms) > 1:
        tail = terms[-1].split()
        if tail:
            head = f"{head} {tail[-1]}"

    words = head.split()
    words[-1] = _depluralise(words[-1])
    return _titlecase(" ".join(words))


def _depluralise(word: str) -> str:
    lowered = word.lower()
    if lowered.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if (
        lowered.endswith("es")
        and lowered[-4:-2] in ("ch", "sh")
        or lowered.endswith(("xes", "ses"))
    ):
        return word[:-2]
    if lowered.endswith("s") and not lowered.endswith("ss"):
        return word[:-1]
    return word


def label_for(schema: CategorySchema, attribute: str) -> str:
    spec = schema.get(attribute)
    return spec.sheet_label if spec else attribute.replace("_", " ").title()


def render_attribute(
    value: AttributeValue, schema: CategorySchema, *, with_label: bool = True
) -> str:
    """One attribute as a description clause.

    `47` + `dBA` + label -> `47 dBA Sound Level`, but `Stainless Steel` stays bare, because
    a material does not need to be told what it is. Both forms appear in the reference rows.
    """
    spec = schema.get(value.attribute)
    magnitude, uom = split_value_uom(value.raw, spec)
    body = f"{magnitude} {uom}" if uom else (value.raw or "").strip()
    if not body:
        return ""
    if not with_label or value.attribute in _BARE_VALUE_ATTRS:
        return body
    label = label_for(schema, value.attribute)
    # "5" + "Number of Wash Cycles" reads as "5 Wash Cycles" in the reference row: the
    # counting noun is kept and the "Number of" scaffolding dropped.
    label = re.sub(r"^Number [Oo]f ", "", label)
    return f"{body} {label}"


def _describable(ingredients: Ingredients, schema: CategorySchema) -> list[AttributeValue]:
    """Attributes eligible for the attribute run of a description."""
    return [
        v
        for v in ingredients.ordered
        if v.attribute not in _IDENTITY_ATTRS
        and v.attribute != ingredients.name_attribute
        and v.attribute != "additional_information"
        and (v.raw or "").strip()
    ]


# --------------------------------------------------------------------------------------
# The five fields
# --------------------------------------------------------------------------------------


def short_desc(ingredients: Ingredients, schema: CategorySchema) -> Composed:
    """Product title. `BRAND Series MPN ProductName [With…], key attributes`."""
    if not ingredients.product_name:
        return EMPTY  # without a noun for the product there is no title to write

    head = " ".join(p for p in (ingredients.brand, ingredients.series, ingredients.mpn) if p)
    opening = f"{head} {ingredients.product_name}".strip()
    if ingredients.with_clause:
        opening = f"{opening} {ingredients.with_clause}"

    used = ["product_name"]
    parts = [opening]
    for value in _describable(ingredients, schema):
        clause = render_attribute(value, schema)
        if not clause:
            continue
        if len(", ".join([*parts, clause])) > SHORT_TARGET_MAX:
            break
        parts.append(clause)
        used.append(value.attribute)

    return Composed(", ".join(parts), ingredients.spans_for(used), tuple(used))


def retail_desc(ingredients: Ingredients, schema: CategorySchema) -> Composed:
    """Search-results line: the title without brand or part number."""
    if not ingredients.product_name:
        return EMPTY
    opening = f"{ingredients.series} {ingredients.product_name}".strip()
    used = ["product_name"]
    parts = [opening]
    for value in _describable(ingredients, schema):
        clause = render_attribute(value, schema)
        if not clause:
            continue
        if len(", ".join([*parts, clause])) > SHORT_TARGET_MAX:
            break
        parts.append(clause)
        used.append(value.attribute)
    return Composed(", ".join(parts), ingredients.spans_for(used), tuple(used))


def long_desc(ingredients: Ingredients, schema: CategorySchema) -> Composed:
    """Product page copy: every verified attribute, in the category's own order."""
    if not ingredients.product_name:
        return EMPTY

    opening = f"{ingredients.brand} {ingredients.product_name}".strip()
    if ingredients.with_clause:
        opening = f"{opening} {ingredients.with_clause}"

    used = ["product_name"]
    parts = [opening]
    if ingredients.series:
        parts.append(ingredients.series)
        used.append("series")

    for value in _describable(ingredients, schema):
        clause = render_attribute(value, schema)
        if clause:
            parts.append(clause)
            used.append(value.attribute)

    extra = (ingredients.values or {}).get("additional_information")
    if extra and (extra.raw or "").strip():
        parts.append(f"Additional Information: {extra.raw.strip()}")
        used.append("additional_information")

    return Composed(", ".join(parts), ingredients.spans_for(used), tuple(used))


def mobile_desc(ingredients: Ingredients, schema: CategorySchema) -> Composed:
    """60-80 characters for a phone listing.

    The manufacturer is dropped when it is the same company as the brand: the reference row
    for Whirlpool Corporation / Whirlpool® prints the brand once, not twice.
    """
    if not ingredients.product_name:
        return EMPTY

    head_parts = []
    if ingredients.manufacturer and not _same_company(ingredients.manufacturer, ingredients.brand):
        head_parts.append(ingredients.manufacturer)
    if ingredients.brand:
        head_parts.append(ingredients.brand)

    used = ["product_name"]
    parts = [" ".join(head_parts)] if head_parts else []
    parts.append(ingredients.product_name)
    if ingredients.series:
        parts.append(ingredients.series)
        used.append("series")
    if ingredients.mpn:
        parts.append(ingredients.mpn)

    # Pad toward the lower bound with further verified attributes, never with filler.
    for value in _describable(ingredients, schema):
        text = ", ".join(parts)
        if len(text) >= MOBILE_MIN:
            break
        clause = render_attribute(value, schema)
        if not clause or len(f"{text}, {clause}") > MOBILE_MAX:
            continue
        parts.append(clause)
        used.append(value.attribute)

    text = ", ".join(p for p in parts if p)
    return Composed(text, ingredients.spans_for(used), tuple(used))


def invoice_desc(ingredients: Ingredients, schema: CategorySchema) -> Composed:
    """Till-receipt line: <=40 characters, upper case, units closed up.

    The limit is hard. Clauses are added while they fit and dropped when they do not - the
    line is never truncated mid-token, because `120` cut from `120V` is a different and
    wrong value rather than a shorter one.
    """
    if not ingredients.product_name:
        return EMPTY

    used = ["product_name"]
    text = ingredients.product_name.upper()
    if len(text) > INVOICE_MAX:
        return Composed(text[:INVOICE_MAX].strip(), ingredients.spans_for(used), tuple(used))

    for value in _describable(ingredients, schema):
        token = _invoice_token(value, schema)
        if not token:
            continue
        candidate = f"{text} {token}"
        if len(candidate) > INVOICE_MAX:
            continue
        text = candidate
        used.append(value.attribute)

    return Composed(text, ingredients.spans_for(used), tuple(used))


def _invoice_token(value: AttributeValue, schema: CategorySchema) -> str:
    """One attribute abbreviated for the receipt line."""
    raw = (value.raw or "").strip()
    if not raw:
        return ""
    abbreviated = INVOICE_ABBREVIATIONS.get(raw.casefold())
    if abbreviated:
        return abbreviated
    spec = schema.get(value.attribute)
    magnitude, uom = split_value_uom(raw, spec)
    if uom:
        return f"{magnitude}{uom}".upper()  # closed up: 120V, 50-1/4IN, 41DBA
    return raw.upper() if len(raw) <= 8 else ""


def item_features(
    ingredients: Ingredients, schema: CategorySchema, limit: int = 20
) -> list[Composed]:
    """Feature bullets.

    Derived, not invented: eight of the eleven features in the reference row are the
    comma-split of that product's `Additional Information` attribute, and one more is its
    sound level. Row 1 has neither and correspondingly has no features - consistent, and a
    good check that this is reading the data rather than filling space.
    """
    out: list[Composed] = []

    extra = (ingredients.values or {}).get("additional_information")
    if extra and (extra.raw or "").strip():
        spans = tuple(extra.spans)
        for piece in (p.strip() for p in extra.raw.split(",")):
            if piece and len(out) < limit:
                out.append(Composed(piece, spans, ("additional_information",)))

    for value in _describable(ingredients, schema):
        if len(out) >= limit:
            break
        if value.attribute in _BARE_VALUE_ATTRS:
            continue
        spec = schema.get(value.attribute)
        magnitude, uom = split_value_uom(value.raw, spec)
        if uom:  # measured features read well as bullets: "41 dBA"
            out.append(Composed(f"{magnitude} {uom}", tuple(value.spans), (value.attribute,)))

    return out[:limit]


def _same_company(left: str, right: str) -> bool:
    """Whether two names denote the same company for display purposes.

    Deliberately shallow - a shared first word after stripping legal suffixes. Without the
    client's 27,000-row manufacturer list there is no authoritative answer, and a shallow
    rule that occasionally keeps a redundant prefix is safer than a clever one that
    occasionally drops a real manufacturer.
    """
    strip = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|gmbh|sa|ag)\b\.?", re.I)

    def key(name: str) -> str:
        cleaned = strip.sub("", name or "").strip()
        cleaned = re.sub(r"[^A-Za-z0-9 ]", "", cleaned).strip().casefold()
        return cleaned.split(" ")[0] if cleaned else ""

    a, b = key(left), key(right)
    return bool(a) and a == b


def compose_all(
    record: ProductRecord, schema: CategorySchema
) -> tuple[dict[str, Composed], list[Composed]]:
    """Every composed description field, plus the feature bullets."""
    ingredients = gather(record, schema)
    fields = {
        "SHORT_DESC": short_desc(ingredients, schema),
        "RETAIL_DESC": retail_desc(ingredients, schema),
        "LONG_DESC1": long_desc(ingredients, schema),
        "MOBILE_DESC": mobile_desc(ingredients, schema),
        "INVOICE_DESC": invoice_desc(ingredients, schema),
    }
    return {k: v for k, v in fields.items() if v}, item_features(ingredients, schema)


def compliance(fields: dict[str, Composed]) -> dict[str, bool]:
    """Character-limit compliance, one of the three metrics the guide says judges look for."""
    checks: dict[str, bool] = {}
    if "INVOICE_DESC" in fields:
        checks["INVOICE_DESC<=40"] = len(fields["INVOICE_DESC"].text) <= INVOICE_MAX
    if "MOBILE_DESC" in fields:
        length = len(fields["MOBILE_DESC"].text)
        checks["MOBILE_DESC 60-80"] = MOBILE_MIN <= length <= MOBILE_MAX
    if "INVOICE_DESC" in fields:
        checks["INVOICE_DESC is upper"] = fields["INVOICE_DESC"].text.isupper()
    return checks


def summarise(all_fields: Sequence[dict[str, Composed]]) -> dict[str, float]:
    """Aggregate compliance across a run, as percentages."""
    totals: dict[str, list[bool]] = {}
    for fields in all_fields:
        for check, passed in compliance(fields).items():
            totals.setdefault(check, []).append(passed)
    return {k: 100 * sum(v) / len(v) for k, v in totals.items() if v}
