"""Tests for the delivery-format emit stage.

The abstention tests here are the load-bearing ones. It is easy to write an exporter that
fills every cell and passes a format check; the thing that has to be protected by a test is
that it *does not*.
"""

from __future__ import annotations

import csv

import pytest

from crucible.emit.columns import DELIVERY_COLUMNS, N_COLUMNS, attribute_columns
from crucible.emit.rows import (
    DeliveryRow,
    EmitPolicy,
    EmittedCell,
    FillMode,
    Provenance,
    build_row,
)
from crucible.emit.writer import EVIDENCE_COLUMNS, write_csv, write_evidence, write_xlsx
from crucible.ontology import generic_schema, get_schema
from crucible.schema import AttributeValue, ProductRecord, RawProduct, Routing, SourceSpan

DESCRIPTION = 'Milw 5" x .045" x 7/8" Metal Cut Off Disc 6pc'


def span(quote: str) -> SourceSpan:
    start = DESCRIPTION.find(quote)
    return SourceSpan(
        doc_id="erp", quote=quote, start=max(start, 0), end=max(start, 0) + len(quote)
    )


def value(attribute: str, raw: str, quote: str | None = None) -> AttributeValue:
    return AttributeValue(attribute=attribute, raw=raw, spans=[span(quote or raw)])


def make_record(values: list[AttributeValue], category: str = "abrasive.cutoff_disc"):
    raw = RawProduct(
        sku="49-94-0013",
        mpn="49-94-0013",
        description=DESCRIPTION,
        brand="Milwaukee",
        category_id=category,
        extra={"part_manuf": "Milwaukee Accessory (4031)", "e1_brand": "-- Unbranded --"},
    )
    routing = Routing(
        category_id=category,
        dept="Tools & Equipment",
        klass="Power Tool Accessories",
        fine="Cut-Off Wheels",
        classpath="Tools & Equipment>Power Tool Accessories>Cut-Off Wheels",
        unspsc="23101500",
        confidence=0.9,
        method="lexical",
        spans=[span("Cut Off")],
    )
    return ProductRecord(raw=raw, category_id=category, routing=routing, values=values)


class TestRowShape:
    def test_row_has_every_delivery_column(self):
        record = make_record([value("disc_diameter", '5"', '5"')])
        row = build_row(record, get_schema("abrasive.cutoff_disc"), EmitPolicy(FillMode.GROUNDED))
        assert list(row.as_dict()) == list(DELIVERY_COLUMNS)
        assert len(row.as_dict()) == N_COLUMNS

    def test_unset_columns_are_empty_not_placeholders(self):
        record = make_record([])
        row = build_row(record, get_schema("abrasive.cutoff_disc"))
        blanks = {v for k, v in row.as_dict().items() if k not in row.cells}
        assert blanks == {""}
        for forbidden in ("N/A", "None", "nan", "null", "-"):
            assert forbidden not in blanks

    def test_rejects_unknown_column(self):
        row = DeliveryRow(sku="X")
        with pytest.raises(ValueError, match="not a delivery column"):
            row.put(EmittedCell(column="Not A Column", value="x", provenance=Provenance.DERIVED))


class TestAbstention:
    """The behaviour that distinguishes this exporter from a fill-everything one."""

    def test_label_is_emitted_without_a_value(self):
        # The reference rows do exactly this: a full label template, values where known.
        record = make_record([value("disc_diameter", '5"', '5"')])
        row = build_row(record, get_schema("abrasive.cutoff_disc"), EmitPolicy(FillMode.GROUNDED))
        cells = row.as_dict()

        label_1, value_1, _ = attribute_columns(1)
        label_4, value_4, _ = attribute_columns(4)

        assert cells[label_1] == "Diameter"
        assert cells[value_1] == "5"
        assert cells[label_4] == "Application"  # looked for
        assert cells[value_4] == ""  # and not found

    def test_every_template_attribute_gets_a_label(self):
        schema = get_schema("abrasive.cutoff_disc")
        row = build_row(make_record([]), schema)
        cells = row.as_dict()
        for slot, spec in enumerate(schema.template(), start=1):
            label, _, _ = attribute_columns(slot)
            assert cells[label] == spec.sheet_label

    def test_ungrounded_values_are_never_published(self):
        # A value with no span is discarded no matter how permissive the fill mode.
        ungrounded = AttributeValue(attribute="disc_diameter", raw='5"', spans=[])
        record = make_record([ungrounded])
        for mode in FillMode:
            row = build_row(
                record, get_schema("abrasive.cutoff_disc"), EmitPolicy(mode, threshold=1.0)
            )
            _, value_col, _ = attribute_columns(1)
            assert row.as_dict()[value_col] == ""

    def test_certified_mode_without_calibration_publishes_no_values(self):
        # Refusing is the safe direction to fail: with no threshold there is no basis for
        # a certified claim, so the attribute grid stays empty rather than filling.
        record = make_record([value("disc_diameter", '5"', '5"')])
        row = build_row(record, get_schema("abrasive.cutoff_disc"), EmitPolicy(FillMode.CERTIFIED))
        _, value_col, _ = attribute_columns(1)
        assert row.as_dict()[value_col] == ""
        assert row.as_dict()[attribute_columns(1)[0]] == "Diameter"  # label still there

    def test_threshold_gates_certified_mode(self):
        record = make_record([value("disc_diameter", '5"', '5"')])
        policy = EmitPolicy(FillMode.CERTIFIED, threshold=0.5)
        _, value_col, _ = attribute_columns(1)

        passing = build_row(
            record, get_schema("abrasive.cutoff_disc"), policy, {"disc_diameter": 0.2}
        )
        failing = build_row(
            record, get_schema("abrasive.cutoff_disc"), policy, {"disc_diameter": 0.9}
        )
        assert passing.as_dict()[value_col] == "5"
        assert failing.as_dict()[value_col] == ""


class TestTaxonomy:
    def test_generic_routing_leaves_classification_blank(self):
        record = make_record([], category="generic")
        record = record.model_copy(update={"routing": Routing(category_id="generic")})
        cells = build_row(record, generic_schema()).as_dict()
        for column in ("Dept", "Class", "Fine", "Classpath", "UNSPSC"):
            assert cells[column] == ""

    def test_routed_product_publishes_its_taxonomy(self):
        cells = build_row(make_record([]), get_schema("abrasive.cutoff_disc")).as_dict()
        assert cells["Dept"] == "Tools & Equipment"
        assert cells["Classpath"].endswith("Cut-Off Wheels")


class TestValueUomSplit:
    def test_value_and_unit_land_in_separate_cells(self):
        record = make_record([value("disc_diameter", '5"', '5"')])
        row = build_row(record, get_schema("abrasive.cutoff_disc"), EmitPolicy(FillMode.GROUNDED))
        _, value_col, uom_col = attribute_columns(1)
        assert row.as_dict()[value_col] == "5"
        assert row.as_dict()[uom_col] == "in"

    def test_fraction_notation_is_preserved(self):
        # 7/8 must not become 0.875: the sheet writes what the trade writes.
        record = make_record([value("arbor_diameter", '7/8"', '7/8"')])
        row = build_row(record, get_schema("abrasive.cutoff_disc"), EmitPolicy(FillMode.GROUNDED))
        _, value_col, uom_col = attribute_columns(3)
        assert row.as_dict()[value_col] == "7/8"
        assert row.as_dict()[uom_col] == "in"


class TestWriters:
    def test_csv_round_trips_with_exact_header(self, tmp_path):
        record = make_record([value("disc_diameter", '5"', '5"')])
        row = build_row(record, get_schema("abrasive.cutoff_disc"), EmitPolicy(FillMode.GROUNDED))
        path = tmp_path / "delivery.csv"
        assert write_csv([row], path) == 1

        with path.open(newline="", encoding="utf-8") as handle:
            read = list(csv.DictReader(handle))
        assert len(read) == 1
        assert list(read[0]) == list(DELIVERY_COLUMNS)
        assert read[0]["Mfg_Part_Num"] == "49-94-0013"

    def test_xlsx_keeps_fractions_as_text(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        record = make_record([value("arbor_diameter", '7/8"', '7/8"')])
        row = build_row(record, get_schema("abrasive.cutoff_disc"), EmitPolicy(FillMode.GROUNDED))
        path = tmp_path / "delivery.xlsx"
        write_xlsx([row], path)

        sheet = openpyxl.load_workbook(path).active
        header = [c.value for c in next(sheet.iter_rows(min_row=1, max_row=1))]
        cells = list(next(sheet.iter_rows(min_row=2, max_row=2)))
        index = header.index(attribute_columns(3)[1])
        assert cells[index].value == "7/8"
        assert cells[index].number_format == "@"

    def test_evidence_records_every_populated_cell(self, tmp_path):
        record = make_record([value("disc_diameter", '5"', '5"')])
        row = build_row(record, get_schema("abrasive.cutoff_disc"), EmitPolicy(FillMode.GROUNDED))
        signals = {"disc_diameter": [("dimensional", 1.0, True, "valid"), ("x", 0.0, False, "")]}
        path = tmp_path / "evidence.csv"
        written = write_evidence([(row, signals)], path)

        assert written == row.populated
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert list(rows[0]) == list(EVIDENCE_COLUMNS)

        by_column = {r["Column"]: r for r in rows}
        _, value_col, _ = attribute_columns(1)
        assert by_column[value_col]["Source Quote"] == '5"'
        assert by_column[value_col]["Provenance"] == "extracted"

    def test_abstention_reads_differently_from_approval(self, tmp_path):
        # Non-negotiable #3, enforced in the artifact a human actually opens.
        record = make_record([value("disc_diameter", '5"', '5"')])
        row = build_row(record, get_schema("abrasive.cutoff_disc"), EmitPolicy(FillMode.GROUNDED))
        signals = {
            "disc_diameter": [
                ("dimensional", 1.0, True, "checked and fine"),
                ("constraint", 0.0, False, "nothing to check"),
            ]
        }
        path = tmp_path / "evidence.csv"
        write_evidence([(row, signals)], path)
        text = path.read_text(encoding="utf-8")
        assert "constraint: abstained" in text
        assert "dimensional: 1.00" in text
