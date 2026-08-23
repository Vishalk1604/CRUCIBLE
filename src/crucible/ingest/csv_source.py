"""Reading a distributor's ERP export into RawProduct.

What the input actually looks like
----------------------------------
Six columns, and four of them are mostly noise. Measured over the 1000-row sample:

* `Part_Desc` averages 38 characters, from 13 to 70. This is the whole signal.
* `Unilog_Brand` is the literal string `-- No Unilog Brand --` on **all 1000 rows**.
* `E1_Brand` is `-- Unbranded --` on 799, `DIB_Brand` is `-- No DIB Brand --` on 755.
* `Part_Manuf` is a name with a trailing account code, `Freud Inc (2435)`, and is `-`
  on 41 rows.

So brand is usually recoverable only from the description text, which is why
`best_brand` prefers the most specific populated column and returns None rather than
handing a placeholder downstream as though it were a brand. A placeholder that reaches
the extractor becomes a proposed value, and a proposed value that survives verification
becomes a published one - `-- Unbranded --` printed in a BRAND_NAME cell is exactly the
class of confident nonsense this project exists to prevent.

Why `extra` holds all six columns verbatim
------------------------------------------
The delivery format echoes `Mfg_Part_Num`, `Part_Desc`, `E1_Brand`, `Unilog_Brand`,
`DIB_Brand` and `Part_Manuf` back byte-identically. Round-tripping them through parsing
and reconstruction would risk changing them; keeping the originals means the passthrough
columns are copies rather than renderings.

Why the evidence document is wider than the description
-------------------------------------------------------
`erp_text` composes description, brand and manufacturer into the text that extraction
grounds against. Without it a brand read from `DIB_Brand` cannot be located in
`Part_Desc`, so `_ground()` correctly discards it, and the failure looks like a weak
extractor when it is really an evidence document that omitted the field the value came
from. The composition is the fix; the grounding rule stays strict.
"""

from __future__ import annotations

import csv
import logging
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from crucible.schema import RawProduct

logger = logging.getLogger(__name__)

INPUT_COLUMNS: tuple[str, ...] = (
    "Mfg_Part_Num",
    "Part_Desc",
    "E1_Brand",
    "Unilog_Brand",
    "DIB_Brand",
    "Part_Manuf",
)

BRAND_COLUMNS: tuple[str, ...] = ("DIB_Brand", "E1_Brand", "Unilog_Brand")

# Placeholders seen in the sample, plus the obvious neighbours. Matched after casefolding
# and whitespace collapse, so "-- UNBRANDED --" and "--unbranded--" both land here.
PLACEHOLDER_BRANDS: frozenset[str] = frozenset(
    {
        "",
        "-",
        "--",
        "n/a",
        "na",
        "none",
        "null",
        "unknown",
        "-- unbranded --",
        "--unbranded--",
        "unbranded",
        "-- no unilog brand --",
        "--no unilog brand--",
        "-- no dib brand --",
        "--no dib brand--",
        "-- no brand --",
        "no brand",
        "commodity - unbranded",
    }
)

# "Freud Inc (2435)" -> name, account code. The code is a distributor account id, not a
# manufacturer identifier, so it is kept beside the name rather than folded into it.
_PART_MANUF = re.compile(r"^\s*(?P<name>.*?)\s*\(\s*(?P<code>[A-Za-z0-9._-]+)\s*\)\s*$")

_WHITESPACE = re.compile(r"\s+")


class IngestError(ValueError):
    """Raised when an input file cannot be read as a product export."""


def _squash(text: str | None) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip())


def is_placeholder(text: str | None) -> bool:
    """True when a cell carries a 'deliberately empty' marker rather than a value.

    Treating these as data is worse than treating them as blank: they are uniform across
    thousands of rows, so they look like strong evidence to anything that counts.
    """
    return _squash(text).casefold() in PLACEHOLDER_BRANDS


def _norm_header(name: str) -> str:
    """Fold a header to a comparison key: case, spaces, underscores and hyphens."""
    return re.sub(r"[\s_\-]+", "", name or "").casefold()


def infer_columns(header: Sequence[str]) -> dict[str, str]:
    """Map canonical input column names onto the header actually present.

    Tolerant of case, spacing and underscore style, because the evaluation export will
    not necessarily be byte-identical to the sample. Raises when no description-like
    column exists at all: without it every row would extract nothing, and a thousand
    empty rows is a far more expensive failure than refusing to start.
    """
    lookup = {_norm_header(h): h for h in header}
    mapping: dict[str, str] = {}
    for canonical in INPUT_COLUMNS:
        found = lookup.get(_norm_header(canonical))
        if found is not None:
            mapping[canonical] = found

    if "Part_Desc" not in mapping:
        for alias in ("Description", "PartDescription", "Item_Desc", "Product_Description"):
            found = lookup.get(_norm_header(alias))
            if found is not None:
                mapping["Part_Desc"] = found
                break

    if "Part_Desc" not in mapping:
        raise IngestError(
            "no description column found; expected one of Part_Desc, Description, "
            f"Item_Desc. Header was: {list(header)}"
        )
    return mapping


def best_brand(row: Mapping[str, str]) -> str | None:
    """The most specific real brand in a row, or None.

    DIB before E1 before Unilog: that is their order of specificity in the sample, where
    DIB_Brand carries actual marques (Philips, Diablo) while E1_Brand is mostly a
    placeholder and Unilog_Brand is a placeholder on every single row.
    """
    for column in BRAND_COLUMNS:
        value = _squash(row.get(column))
        if value and not is_placeholder(value):
            return value
    return None


def split_part_manuf(text: str | None) -> tuple[str, str | None]:
    """Split "Freud Inc (2435)" into ("Freud Inc", "2435").

    Returns the whole string as the name when there is no parenthesised code, and an
    empty name for the bare "-" that appears on 41 rows of the sample.
    """
    value = _squash(text)
    if not value or is_placeholder(value):
        return "", None
    match = _PART_MANUF.match(value)
    if match is None:
        return value, None
    return match.group("name"), match.group("code")


def erp_text(raw: RawProduct) -> str:
    """The evidence document a value must be groundable in.

    Description first, then brand and manufacturer if they are real and not already
    present in the description. Falls back to the bare description when `extra` is empty,
    so records built directly in tests behave exactly as they did before this module
    existed.
    """
    description = raw.description or ""
    if not raw.extra:
        return description

    parts = [description]
    seen = description.casefold()
    for candidate in (raw.brand, raw.extra.get("part_manuf_name")):
        value = _squash(candidate if isinstance(candidate, str) else None)
        if value and not is_placeholder(value) and value.casefold() not in seen:
            parts.append(value)
            seen = f"{seen} {value.casefold()}"
    return " ".join(parts)


def to_raw_product(
    row: Mapping[str, str],
    index: int,
    mapping: Mapping[str, str] | None = None,
    seen_skus: set[str] | None = None,
) -> RawProduct:
    """Build one RawProduct, preserving every source cell verbatim in `extra`."""
    get = (
        (lambda c: _squash(row.get(mapping.get(c, c))))
        if mapping is not None
        else (lambda c: _squash(row.get(c)))
    )

    mpn = get("Mfg_Part_Num")
    description = get("Part_Desc")
    manuf_name, manuf_code = split_part_manuf(get("Part_Manuf"))

    sku = mpn or f"ROW-{index:05d}"
    if seen_skus is not None:
        if sku in seen_skus:
            # A duplicate part number is real in this data (the sample has one). Suffixing
            # keeps the two rows addressable rather than letting the second overwrite the
            # first in every dict keyed by sku - including the ensemble verifier's index.
            original = sku
            suffix = 2
            while f"{original}#{suffix}" in seen_skus:
                suffix += 1
            sku = f"{original}#{suffix}"
            logger.debug("duplicate part number %s, emitting as %s", original, sku)
        seen_skus.add(sku)

    extra: dict[str, object] = {
        column: _squash(row.get(mapping.get(column, column) if mapping else column))
        for column in INPUT_COLUMNS
    }
    extra["part_manuf_name"] = manuf_name
    extra["part_manuf_code"] = manuf_code
    extra["row_index"] = index

    return RawProduct(
        sku=sku,
        description=description,
        brand=best_brand({c: extra.get(c, "") for c in BRAND_COLUMNS}),  # type: ignore[arg-type]
        mpn=mpn or None,
        category_id=None,
        extra=extra,
    )


def iter_products(path: Path, limit: int | None = None) -> Iterator[RawProduct]:
    """Stream products from a delimited export, tolerating BOM and quoted commas."""
    if not path.exists():
        raise IngestError(f"input file not found: {path}")

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise IngestError(f"{path} is empty") from None

        mapping = infer_columns(header)
        seen: set[str] = set()
        for index, values in enumerate(reader):
            if not any(v.strip() for v in values):
                continue
            # Deliberately not strict: a row with a missing trailing cell is common in
            # ERP exports and should cost that cell, not the other 999 rows.
            row = dict(zip(header, values, strict=False))
            yield to_raw_product(row, index, mapping, seen)
            if limit is not None and index + 1 >= limit:
                return


def read_products(path: Path, limit: int | None = None) -> list[RawProduct]:
    """Read an entire export into memory."""
    products = list(iter_products(path, limit))
    if not products:
        raise IngestError(f"{path} contained a header but no product rows")
    logger.info("ingested %d products from %s", len(products), path.name)
    return products
