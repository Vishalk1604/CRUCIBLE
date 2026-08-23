"""The delivery format: 252 columns, in order, exactly as the brief specifies.

The column list is hardcoded rather than read from the reference CSV at runtime, because
the writer has to work on a machine that has the input file but not the sample output -
which is precisely the situation during evaluation. `tests/test_columns.py` asserts this
tuple against the shipped reference file, so the two cannot drift apart silently: if the
brief reissues the sheet with a changed column, the test fails rather than the export.

Three slot families repeat, and their widths are part of the contract:

* 50 attribute triples - ATTRIBUTE_LABEL n / ATTRIBUTE_VALUE n / ATTRIBUTE_UOM n
* 20 ITEM_FEATURES_n
* 5 Ref URL n

The attribute triples carry the format's most important and most easily missed
instruction. In the two reference rows - both dishwashers - the fifteen ATTRIBUTE_LABEL
cells are identical, while the ATTRIBUTE_VALUE cells are blank in *different* places:
row one has no Model, Plug Type or Color, row two has no Number of Wash Cycles, Plug Type
or Maximum Height. So a label is a property of the category and a value is a property of
the product, and the sheet already expects a populated label beside an empty value.

That is worth stating plainly because it inverts the obvious reading of the task. The
temptation with 252 columns is to fill them. The format is asking for the opposite: say
what was looked for, and leave blank what was not found. An empty ATTRIBUTE_VALUE under a
populated ATTRIBUTE_LABEL is not a gap in the export, it is the export reporting that the
system searched for that property and could not ground it - which is strictly more
informative than a blank column, and is the same abstention the rest of the pipeline is
built around.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


class ColumnError(ValueError):
    """Raised when a header does not match the delivery format."""


ATTRIBUTE_SLOTS = 50
FEATURE_SLOTS = 20
REF_URL_SLOTS = 5

DELIVERY_COLUMNS: tuple[str, ...] = (
    "MFR URL",
    "Ref URL 1",
    "Ref URL 2",
    "Ref URL 3",
    "Ref URL 4",
    "Ref URL 5",
    "PART_NUMBER",
    "Dept",
    "Class",
    "Fine",
    "SKU - MY_PART_NUMBER",
    "Mfg_Part_Num",
    "Part_Desc",
    "E1_Brand",
    "Unilog_Brand",
    "DIB_Brand",
    "Part_Manuf",
    "MANUFACTURER_NAME",
    "BRAND_NAME",
    "TRADE_NAME",
    "MANUFACTURER_PART_NUMBER",
    "ALTERNATE_PART_NUMBER",
    "Classpath",
    "MOBILE_DESC",
    "INVOICE_DESC",
    "SHORT_DESC",
    "LONG_DESC1",
    "RETAIL_DESC",
    "MARKETING_DESCRIPTION",
    "ITEM_FEATURES_1",
    "ITEM_FEATURES_2",
    "ITEM_FEATURES_3",
    "ITEM_FEATURES_4",
    "ITEM_FEATURES_5",
    "ITEM_FEATURES_6",
    "ITEM_FEATURES_7",
    "ITEM_FEATURES_8",
    "ITEM_FEATURES_9",
    "ITEM_FEATURES_10",
    "ITEM_FEATURES_11",
    "ITEM_FEATURES_12",
    "ITEM_FEATURES_13",
    "ITEM_FEATURES_14",
    "ITEM_FEATURES_15",
    "ITEM_FEATURES_16",
    "ITEM_FEATURES_17",
    "ITEM_FEATURES_18",
    "ITEM_FEATURES_19",
    "ITEM_FEATURES_20",
    "With",
    "Standard/Approvals",
    "Prop 65",
    "Application",
    "Includes",
    "Product Name",
    "ATTRIBUTE_LABEL 1",
    "ATTRIBUTE_VALUE 1",
    "ATTRIBUTE_UOM 1",
    "ATTRIBUTE_LABEL 2",
    "ATTRIBUTE_VALUE 2",
    "ATTRIBUTE_UOM 2",
    "ATTRIBUTE_LABEL 3",
    "ATTRIBUTE_VALUE 3",
    "ATTRIBUTE_UOM 3",
    "ATTRIBUTE_LABEL 4",
    "ATTRIBUTE_VALUE 4",
    "ATTRIBUTE_UOM 4",
    "ATTRIBUTE_LABEL 5",
    "ATTRIBUTE_VALUE 5",
    "ATTRIBUTE_UOM 5",
    "ATTRIBUTE_LABEL 6",
    "ATTRIBUTE_VALUE 6",
    "ATTRIBUTE_UOM 6",
    "ATTRIBUTE_LABEL 7",
    "ATTRIBUTE_VALUE 7",
    "ATTRIBUTE_UOM 7",
    "ATTRIBUTE_LABEL 8",
    "ATTRIBUTE_VALUE 8",
    "ATTRIBUTE_UOM 8",
    "ATTRIBUTE_LABEL 9",
    "ATTRIBUTE_VALUE 9",
    "ATTRIBUTE_UOM 9",
    "ATTRIBUTE_LABEL 10",
    "ATTRIBUTE_VALUE 10",
    "ATTRIBUTE_UOM 10",
    "ATTRIBUTE_LABEL 11",
    "ATTRIBUTE_VALUE 11",
    "ATTRIBUTE_UOM 11",
    "ATTRIBUTE_LABEL 12",
    "ATTRIBUTE_VALUE 12",
    "ATTRIBUTE_UOM 12",
    "ATTRIBUTE_LABEL 13",
    "ATTRIBUTE_VALUE 13",
    "ATTRIBUTE_UOM 13",
    "ATTRIBUTE_LABEL 14",
    "ATTRIBUTE_VALUE 14",
    "ATTRIBUTE_UOM 14",
    "ATTRIBUTE_LABEL 15",
    "ATTRIBUTE_VALUE 15",
    "ATTRIBUTE_UOM 15",
    "ATTRIBUTE_LABEL 16",
    "ATTRIBUTE_VALUE 16",
    "ATTRIBUTE_UOM 16",
    "ATTRIBUTE_LABEL 17",
    "ATTRIBUTE_VALUE 17",
    "ATTRIBUTE_UOM 17",
    "ATTRIBUTE_LABEL 18",
    "ATTRIBUTE_VALUE 18",
    "ATTRIBUTE_UOM 18",
    "ATTRIBUTE_LABEL 19",
    "ATTRIBUTE_VALUE 19",
    "ATTRIBUTE_UOM 19",
    "ATTRIBUTE_LABEL 20",
    "ATTRIBUTE_VALUE 20",
    "ATTRIBUTE_UOM 20",
    "ATTRIBUTE_LABEL 21",
    "ATTRIBUTE_VALUE 21",
    "ATTRIBUTE_UOM 21",
    "ATTRIBUTE_LABEL 22",
    "ATTRIBUTE_VALUE 22",
    "ATTRIBUTE_UOM 22",
    "ATTRIBUTE_LABEL 23",
    "ATTRIBUTE_VALUE 23",
    "ATTRIBUTE_UOM 23",
    "ATTRIBUTE_LABEL 24",
    "ATTRIBUTE_VALUE 24",
    "ATTRIBUTE_UOM 24",
    "ATTRIBUTE_LABEL 25",
    "ATTRIBUTE_VALUE 25",
    "ATTRIBUTE_UOM 25",
    "ATTRIBUTE_LABEL 26",
    "ATTRIBUTE_VALUE 26",
    "ATTRIBUTE_UOM 26",
    "ATTRIBUTE_LABEL 27",
    "ATTRIBUTE_VALUE 27",
    "ATTRIBUTE_UOM 27",
    "ATTRIBUTE_LABEL 28",
    "ATTRIBUTE_VALUE 28",
    "ATTRIBUTE_UOM 28",
    "ATTRIBUTE_LABEL 29",
    "ATTRIBUTE_VALUE 29",
    "ATTRIBUTE_UOM 29",
    "ATTRIBUTE_LABEL 30",
    "ATTRIBUTE_VALUE 30",
    "ATTRIBUTE_UOM 30",
    "ATTRIBUTE_LABEL 31",
    "ATTRIBUTE_VALUE 31",
    "ATTRIBUTE_UOM 31",
    "ATTRIBUTE_LABEL 32",
    "ATTRIBUTE_VALUE 32",
    "ATTRIBUTE_UOM 32",
    "ATTRIBUTE_LABEL 33",
    "ATTRIBUTE_VALUE 33",
    "ATTRIBUTE_UOM 33",
    "ATTRIBUTE_LABEL 34",
    "ATTRIBUTE_VALUE 34",
    "ATTRIBUTE_UOM 34",
    "ATTRIBUTE_LABEL 35",
    "ATTRIBUTE_VALUE 35",
    "ATTRIBUTE_UOM 35",
    "ATTRIBUTE_LABEL 36",
    "ATTRIBUTE_VALUE 36",
    "ATTRIBUTE_UOM 36",
    "ATTRIBUTE_LABEL 37",
    "ATTRIBUTE_VALUE 37",
    "ATTRIBUTE_UOM 37",
    "ATTRIBUTE_LABEL 38",
    "ATTRIBUTE_VALUE 38",
    "ATTRIBUTE_UOM 38",
    "ATTRIBUTE_LABEL 39",
    "ATTRIBUTE_VALUE 39",
    "ATTRIBUTE_UOM 39",
    "ATTRIBUTE_LABEL 40",
    "ATTRIBUTE_VALUE 40",
    "ATTRIBUTE_UOM 40",
    "ATTRIBUTE_LABEL 41",
    "ATTRIBUTE_VALUE 41",
    "ATTRIBUTE_UOM 41",
    "ATTRIBUTE_LABEL 42",
    "ATTRIBUTE_VALUE 42",
    "ATTRIBUTE_UOM 42",
    "ATTRIBUTE_LABEL 43",
    "ATTRIBUTE_VALUE 43",
    "ATTRIBUTE_UOM 43",
    "ATTRIBUTE_LABEL 44",
    "ATTRIBUTE_VALUE 44",
    "ATTRIBUTE_UOM 44",
    "ATTRIBUTE_LABEL 45",
    "ATTRIBUTE_VALUE 45",
    "ATTRIBUTE_UOM 45",
    "ATTRIBUTE_LABEL 46",
    "ATTRIBUTE_VALUE 46",
    "ATTRIBUTE_UOM 46",
    "ATTRIBUTE_LABEL 47",
    "ATTRIBUTE_VALUE 47",
    "ATTRIBUTE_UOM 47",
    "ATTRIBUTE_LABEL 48",
    "ATTRIBUTE_VALUE 48",
    "ATTRIBUTE_UOM 48",
    "ATTRIBUTE_LABEL 49",
    "ATTRIBUTE_VALUE 49",
    "ATTRIBUTE_UOM 49",
    "ATTRIBUTE_LABEL 50",
    "ATTRIBUTE_VALUE 50",
    "ATTRIBUTE_UOM 50",
    "UPC",
    "EAN",
    "GTIN",
    "UNSPSC",
    "Warranty",
    "List Price",
    "Selling Qty",
    "Selling UOM",
    "Standard Packaging Information",
    "LENGTH",
    "LENGTH_UOM",
    "HEIGHT",
    "HEIGHT_UOM",
    "WIDTH",
    "WIDTH_UOM",
    "WEIGHT",
    "WEIGHT_UOM",
    "VOLUME",
    "VOLUME_UOM",
    "Product Image",
    "Alternate Image 1",
    "Alternate Image 2",
    "Alternate Image 3",
    "Alternate Image 4",
    "SDS",
    "SDS_1",
    "Warranty Information",
    "Catalog",
    "Specification Sheet",
    "Instruction/Installation Manual",
    "Service Manual",
    "Owners/User Manual",
    "Line Drawing",
    "MTR",
    "RoHS",
    "Full Engineering Drawing",
    "Energy Star Guide",
    "Technical Bulletin",
    "Submittal",
    "Compatibility Chart",
    "Size Chart",
    "Product Label/Insert",
    "Video Link",
    "Video Link 1",
    "Country Of Origin",
    "Discontinued",
    "Actual Image (Yes/No)",
)

N_COLUMNS = len(DELIVERY_COLUMNS)


def attribute_columns(slot: int) -> tuple[str, str, str]:
    """The (label, value, uom) column names for a 1-based attribute slot."""
    if not 1 <= slot <= ATTRIBUTE_SLOTS:
        raise ColumnError(f"attribute slot {slot} outside 1..{ATTRIBUTE_SLOTS}")
    return (
        f"ATTRIBUTE_LABEL {slot}",
        f"ATTRIBUTE_VALUE {slot}",
        f"ATTRIBUTE_UOM {slot}",
    )


def feature_column(slot: int) -> str:
    """The ITEM_FEATURES column name for a 1-based slot."""
    if not 1 <= slot <= FEATURE_SLOTS:
        raise ColumnError(f"feature slot {slot} outside 1..{FEATURE_SLOTS}")
    return f"ITEM_FEATURES_{slot}"


def ref_url_column(slot: int) -> str:
    """The Ref URL column name for a 1-based slot."""
    if not 1 <= slot <= REF_URL_SLOTS:
        raise ColumnError(f"ref url slot {slot} outside 1..{REF_URL_SLOTS}")
    return f"Ref URL {slot}"


def validate_header(columns: Sequence[str]) -> None:
    """Raise unless `columns` is the delivery header exactly, in order.

    Deliberately strict about order as well as membership. A reordered sheet loads
    cleanly in Excel and is wrong in a way no one notices until the import fails.
    """
    if len(columns) != N_COLUMNS:
        raise ColumnError(f"expected {N_COLUMNS} columns, got {len(columns)}")
    for i, (got, want) in enumerate(zip(columns, DELIVERY_COLUMNS, strict=True)):
        if got != want:
            raise ColumnError(f"column {i}: expected {want!r}, got {got!r}")


def load_reference_header(path: Path) -> tuple[str, ...]:
    """Read the header row of a delivery-format CSV, for tests and `verify-format`."""
    import csv

    with path.open(newline="", encoding="utf-8-sig") as handle:
        try:
            return tuple(next(csv.reader(handle)))
        except StopIteration:
            raise ColumnError(f"{path} is empty") from None
