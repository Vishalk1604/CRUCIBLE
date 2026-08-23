"""Crucible ingest stage: real distributor CSVs in, RawProduct out."""

from crucible.ingest.csv_source import (
    INPUT_COLUMNS,
    IngestError,
    best_brand,
    erp_text,
    infer_columns,
    is_placeholder,
    read_products,
    split_part_manuf,
    to_raw_product,
)

__all__ = [
    "INPUT_COLUMNS",
    "IngestError",
    "best_brand",
    "erp_text",
    "infer_columns",
    "is_placeholder",
    "read_products",
    "split_part_manuf",
    "to_raw_product",
]
