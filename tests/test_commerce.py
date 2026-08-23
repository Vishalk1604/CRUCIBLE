"""Tests for the commerce columns: dimensions, selling quantity, application.

The tests that matter most here are in `TestRefusals`. Everything this populator writes is
a re-presentation of an already-verified value, so the interesting question is not whether
it fills cells but whether it declines to fill the ones it cannot support.
"""

from __future__ import annotations

import pytest

from crucible.emit.rows import (
    _DIMENSION_COLUMNS,
    DeliveryRow,
    EmitPolicy,
    FillMode,
    build_row,
    populate_commerce,
)
from crucible.ontology import get_schema
from crucible.schema import AttributeValue, ProductRecord, RawProduct, Routing, SourceSpan

DESCRIPTION = "1x6-20' Weathered Teak Grooved - Vintage Azek PVC Decking 6pc"


def value(attribute: str, raw: str) -> AttributeValue:
    return AttributeValue(
        attribute=attribute,
        raw=raw,
        spans=[SourceSpan(doc_id="erp", quote=raw, start=0, end=max(len(raw), 1))],
    )


def make_record(values, category="decking.board"):
    raw = RawProduct(sku="AZ-1", mpn="AZ-1", description=DESCRIPTION, category_id=category)
    routing = Routing(category_id=category, fine="Decking Boards", dept="Building Materials")
    return ProductRecord(raw=raw, category_id=category, routing=routing, values=values)


def commerce_cells(values, category="decking.board") -> dict[str, str]:
    schema = get_schema(category)
    row = DeliveryRow(sku="AZ-1")
    populate_commerce(row, make_record(values, category), schema)
    return {c: cell.value for c, cell in row.cells.items()}


class TestDimensions:
    def test_length_lands_in_its_own_column_with_its_unit(self):
        cells = commerce_cells([value("length", "20 ft")])
        assert cells["LENGTH"] == "20"
        assert cells["LENGTH_UOM"] == "ft"

    def test_nominal_width_maps_to_width(self):
        # Decking declares nominal_width; the sheet only offers WIDTH.
        cells = commerce_cells([value("nominal_width", "6 in")])
        assert cells["WIDTH"] == "6"

    def test_fraction_notation_survives_the_move(self):
        cells = commerce_cells([value("length", "20-1/2 in")])
        assert cells["LENGTH"] == "20-1/2"

    def test_every_mapped_attribute_targets_a_real_delivery_column(self):
        from crucible.emit.columns import DELIVERY_COLUMNS

        for value_col, uom_col in _DIMENSION_COLUMNS.values():
            assert value_col in DELIVERY_COLUMNS
            assert uom_col in DELIVERY_COLUMNS

    def test_category_specific_dimensions_are_not_forced_into_generic_columns(self):
        # A wheel's diameter is not a WIDTH and a board's thickness is not a HEIGHT.
        cells = commerce_cells(
            [value("disc_diameter", '5"'), value("thickness", '.045"')],
            category="abrasive.cutoff_disc",
        )
        assert "WIDTH" not in cells
        assert "HEIGHT" not in cells


class TestSellingQuantity:
    @pytest.mark.parametrize(
        ("raw", "qty", "uom"),
        [("6pc", "6", "PK"), ("3pk", "3", "PK"), ("12", "12", "EA"), ("50 Box", "50", "BX")],
    )
    def test_pack_quantity_splits_into_count_and_unit(self, raw, qty, uom):
        cells = commerce_cells([value("pack_quantity", raw)], category="abrasive.cutoff_disc")
        assert cells["Selling Qty"] == qty
        assert cells["Selling UOM"] == uom

    def test_an_unrecognised_suffix_is_not_invented_into_a_unit(self):
        cells = commerce_cells(
            [value("pack_quantity", "6 widgets")], category="abrasive.cutoff_disc"
        )
        assert "Selling Qty" not in cells
        assert "Selling UOM" not in cells

    def test_non_numeric_quantity_is_skipped(self):
        cells = commerce_cells([value("pack_quantity", "bulk")], category="abrasive.cutoff_disc")
        assert "Selling Qty" not in cells


class TestApplication:
    def test_application_comes_from_the_material_application_attribute(self):
        cells = commerce_cells(
            [value("material_application", "metal")], category="abrasive.cutoff_disc"
        )
        assert cells["Application"] == "metal"

    def test_absent_application_leaves_the_column_blank(self):
        assert "Application" not in commerce_cells([value("length", "20 ft")])


class TestRefusals:
    """The columns this populator will not fill, and must not start filling."""

    @pytest.mark.parametrize(
        "column",
        [
            "Product Image",
            "Alternate Image 1",
            "Specification Sheet",
            "Actual Image (Yes/No)",
            "MFR URL",
            "Ref URL 1",
            "PART_NUMBER",
            "SKU - MY_PART_NUMBER",
            "Country Of Origin",
            "UPC",
            "Warranty",
        ],
    )
    def test_unsupported_columns_stay_empty(self, column):
        """A filename is a claim that a file exists.

        The reference rows fill `Product Image` with `FRIGIDAIRE_PDSH4816AF.jpg` and the
        convention is obvious enough to synthesise for every product in seconds. We hold no
        images, so emitting the name of one would be a confidently-formatted assertion about
        something nobody looked for. `Actual Image (Yes/No)` = "Yes" would simply be false.
        """
        record = make_record([value("length", "20 ft"), value("nominal_width", "6 in")])
        row = build_row(record, get_schema("decking.board"), EmitPolicy(FillMode.GROUNDED))
        assert row.as_dict()[column] == ""

    def test_nothing_is_emitted_without_any_values(self):
        assert commerce_cells([]) == {}


class TestIntegration:
    def test_commerce_columns_reach_the_delivery_row(self):
        record = make_record([value("length", "20 ft"), value("nominal_width", "6 in")])
        cells = build_row(
            record, get_schema("decking.board"), EmitPolicy(FillMode.GROUNDED)
        ).as_dict()
        assert cells["LENGTH"] == "20"
        assert cells["WIDTH"] == "6"

    def test_dimensions_also_remain_in_the_attribute_grid(self):
        # The dedicated column is an addition, not a move: a PIM filters on LENGTH, a
        # buyer reads the attribute table, and both should see it.
        record = make_record([value("length", "20 ft")])
        cells = build_row(
            record, get_schema("decking.board"), EmitPolicy(FillMode.GROUNDED)
        ).as_dict()
        assert cells["LENGTH"] == "20"
        assert any(
            cells[f"ATTRIBUTE_VALUE {n}"] == "20"
            for n in range(1, 20)
            if f"ATTRIBUTE_VALUE {n}" in cells
        )
