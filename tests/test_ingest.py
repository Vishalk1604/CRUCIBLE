"""Tests for reading a distributor export.

Two failure modes drive most of these. The first is the placeholder brand: `-- Unbranded
--` appears on 799 of the 1000 sample rows and `-- No Unilog Brand --` on all 1000, so
anything that treats them as data sees them as the strongest signal in the file. The
second is the evidence document: a brand that lives in a column the extractor cannot see
is ungrounded by construction, gets discarded, and looks like an extraction failure.
"""

import csv
from pathlib import Path

import pytest

from crucible.ingest import (
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
from crucible.schema import RawProduct

SAMPLE = Path(__file__).resolve().parents[1] / "Unihack_ Sample Dataset - Input.csv"


def _write_csv(path: Path, rows: list[list[str]], encoding: str = "utf-8") -> Path:
    with path.open("w", newline="", encoding=encoding) as handle:
        csv.writer(handle).writerows(rows)
    return path


class TestPlaceholders:
    @pytest.mark.parametrize(
        "text",
        [
            "-- Unbranded --",
            "-- No Unilog Brand --",
            "-- No DIB Brand --",
            "-",
            "",
            "   ",
            "N/A",
            "none",
            "UNBRANDED",
            "--  unbranded  --",
        ],
    )
    def test_recognises_placeholders(self, text):
        assert is_placeholder(text)

    @pytest.mark.parametrize("text", ["Philips", "Diablo", "TREX", "3M", "Milwaukee"])
    def test_real_brands_are_not_placeholders(self, text):
        assert not is_placeholder(text)

    def test_none_is_a_placeholder(self):
        assert is_placeholder(None)


class TestBestBrand:
    def test_prefers_dib_over_e1(self):
        row = {"DIB_Brand": "Philips", "E1_Brand": "TREX", "Unilog_Brand": "-- No Unilog Brand --"}
        assert best_brand(row) == "Philips"

    def test_falls_through_placeholders(self):
        row = {
            "DIB_Brand": "-- No DIB Brand --",
            "E1_Brand": "TREX",
            "Unilog_Brand": "-- No Unilog Brand --",
        }
        assert best_brand(row) == "TREX"

    def test_returns_none_when_every_column_is_a_placeholder(self):
        # The important one: None, never the placeholder string itself.
        row = dict.fromkeys(("DIB_Brand", "E1_Brand", "Unilog_Brand"), "-- Unbranded --")
        assert best_brand(row) is None


class TestSplitPartManuf:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Freud Inc (2435)", ("Freud Inc", "2435")),
            (
                "Boise Cascade Building Materials (BOICA)",
                ("Boise Cascade Building Materials", "BOICA"),
            ),
            ("Phillips Lighting (5831)", ("Phillips Lighting", "5831")),
            ("Parksite", ("Parksite", None)),
            ("-", ("", None)),
            ("", ("", None)),
        ],
    )
    def test_splits_name_from_account_code(self, text, expected):
        assert split_part_manuf(text) == expected


class TestInferColumns:
    def test_maps_the_canonical_header(self):
        mapping = infer_columns(list(INPUT_COLUMNS))
        assert mapping["Part_Desc"] == "Part_Desc"

    def test_tolerates_case_and_separator_drift(self):
        # The evaluation export will not necessarily be byte-identical to the sample.
        mapping = infer_columns(["mfg part num", "PART-DESC", "e1_brand"])
        assert mapping["Part_Desc"] == "PART-DESC"
        assert mapping["Mfg_Part_Num"] == "mfg part num"

    def test_accepts_a_description_alias(self):
        assert infer_columns(["SKU", "Description"])["Part_Desc"] == "Description"

    def test_raises_without_any_description_column(self):
        # Refusing beats emitting a thousand empty rows.
        with pytest.raises(IngestError, match="no description column"):
            infer_columns(["Mfg_Part_Num", "E1_Brand"])


class TestToRawProduct:
    def test_preserves_every_source_column_verbatim(self):
        row = dict.fromkeys(INPUT_COLUMNS, "")
        row["Mfg_Part_Num"] = "ABC123"
        row["Part_Desc"] = "ABC123 Widget"
        row["E1_Brand"] = "-- Unbranded --"
        raw = to_raw_product(row, 0)
        for column in INPUT_COLUMNS:
            assert column in raw.extra
        # Passthrough columns are echoed by the delivery format, so they must survive
        # ingest unchanged - placeholder text included.
        assert raw.extra["E1_Brand"] == "-- Unbranded --"

    def test_falls_back_to_a_row_id_when_the_part_number_is_blank(self):
        raw = to_raw_product({"Part_Desc": "Mystery item"}, 7)
        assert raw.sku == "ROW-00007"
        assert raw.mpn is None

    def test_disambiguates_duplicate_part_numbers(self):
        seen: set[str] = set()
        first = to_raw_product({"Mfg_Part_Num": "X1", "Part_Desc": "a"}, 0, None, seen)
        second = to_raw_product({"Mfg_Part_Num": "X1", "Part_Desc": "b"}, 1, None, seen)
        # Without this the second row overwrites the first in every sku-keyed dict,
        # including the ensemble verifier's index.
        assert first.sku == "X1"
        assert second.sku == "X1#2"

    def test_records_the_manufacturer_split(self):
        raw = to_raw_product({"Part_Desc": "x", "Part_Manuf": "Freud Inc (2435)"}, 0)
        assert raw.extra["part_manuf_name"] == "Freud Inc"
        assert raw.extra["part_manuf_code"] == "2435"


class TestErpText:
    def test_degrades_to_the_description_without_extra(self):
        # Records built directly in other tests must behave exactly as before.
        raw = RawProduct(sku="S", description="A plain description")
        assert erp_text(raw) == "A plain description"

    def test_appends_a_brand_the_description_omits(self):
        raw = to_raw_product(
            {"Mfg_Part_Num": "S1", "Part_Desc": "S1 Highbay Light", "DIB_Brand": "Philips"}, 0
        )
        assert "Philips" in erp_text(raw)

    def test_does_not_repeat_a_brand_already_in_the_description(self):
        raw = to_raw_product(
            {"Mfg_Part_Num": "S1", "Part_Desc": "S1 Philips Highbay", "DIB_Brand": "Philips"}, 0
        )
        assert erp_text(raw).count("Philips") == 1

    def test_never_admits_a_placeholder(self):
        raw = to_raw_product(
            {"Part_Desc": "Sanding belt", "E1_Brand": "-- Unbranded --", "Part_Manuf": "-"}, 0
        )
        assert "Unbranded" not in erp_text(raw)


class TestReadProducts:
    def test_handles_bom_quoted_commas_and_embedded_quotes(self, tmp_path):
        path = _write_csv(
            tmp_path / "odd.csv",
            [
                list(INPUT_COLUMNS),
                [
                    "A1",
                    'A1 1/2" belt, 6pc',
                    "-- Unbranded --",
                    "-- No Unilog Brand --",
                    "Diablo",
                    "Freud Inc (2435)",
                ],
            ],
            encoding="utf-8-sig",
        )
        products = read_products(path)
        assert len(products) == 1
        assert products[0].description == 'A1 1/2" belt, 6pc'
        assert products[0].brand == "Diablo"

    def test_skips_entirely_blank_rows(self, tmp_path):
        path = _write_csv(
            tmp_path / "blanks.csv",
            [list(INPUT_COLUMNS), ["A1", "A widget", "", "", "", ""], ["", "", "", "", "", ""]],
        )
        assert len(read_products(path)) == 1

    def test_honours_a_limit(self, tmp_path):
        rows = [list(INPUT_COLUMNS)] + [[f"P{i}", f"Item {i}", "", "", "", ""] for i in range(10)]
        path = _write_csv(tmp_path / "many.csv", rows)
        assert len(read_products(path, limit=3)) == 3

    def test_raises_on_a_missing_file(self, tmp_path):
        with pytest.raises(IngestError, match="not found"):
            read_products(tmp_path / "nope.csv")

    def test_raises_on_a_header_with_no_rows(self, tmp_path):
        path = _write_csv(tmp_path / "headeronly.csv", [list(INPUT_COLUMNS)])
        with pytest.raises(IngestError, match="no product rows"):
            read_products(path)


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample dataset not present")
class TestAgainstTheRealSample:
    def test_reads_every_row(self):
        assert len(read_products(SAMPLE)) == 1000

    def test_no_product_carries_a_placeholder_brand(self):
        # The regression that matters: a placeholder reaching BRAND_NAME in the export.
        assert all(not is_placeholder(p.brand) for p in read_products(SAMPLE) if p.brand)

    def test_skus_are_unique_after_disambiguation(self):
        skus = [p.sku for p in read_products(SAMPLE)]
        assert len(set(skus)) == len(skus)

    def test_every_product_has_a_description(self):
        assert all(p.description for p in read_products(SAMPLE))
