"""Tests for the delivery format's column contract.

The brief is unambiguous that headers must not be removed, renamed, modified or
reordered, and an export that violates that fails before anyone looks at the data
quality. So the column tuple is pinned against the shipped reference sheet here rather
than trusted: if the brief reissues the format, this test fails loudly at the one place
that can be fixed in a minute, instead of the export failing silently at submission.
"""

from pathlib import Path

import pytest

from crucible.emit.columns import (
    ATTRIBUTE_SLOTS,
    DELIVERY_COLUMNS,
    FEATURE_SLOTS,
    N_COLUMNS,
    REF_URL_SLOTS,
    ColumnError,
    attribute_columns,
    feature_column,
    load_reference_header,
    ref_url_column,
    validate_header,
)

REFERENCE = Path(__file__).resolve().parents[1] / "Unihack_ Expected Output - Delivery Format.csv"


class TestAgainstReference:
    def test_matches_the_shipped_reference_sheet_exactly(self):
        # The anchor. Everything else in emit/ trusts this tuple.
        assert load_reference_header(REFERENCE) == DELIVERY_COLUMNS

    def test_has_252_columns(self):
        assert N_COLUMNS == 252

    def test_column_names_are_unique(self):
        # Duplicates would make a dict-based row silently drop a cell.
        assert len(set(DELIVERY_COLUMNS)) == N_COLUMNS


class TestSlotFamilies:
    def test_every_attribute_slot_is_present_as_a_triple(self):
        for slot in range(1, ATTRIBUTE_SLOTS + 1):
            for column in attribute_columns(slot):
                assert column in DELIVERY_COLUMNS

    def test_attribute_triples_are_contiguous_and_ordered(self):
        # The sheet groups label/value/uom together; emitting them apart would still
        # validate on membership but would not match the format.
        for slot in range(1, ATTRIBUTE_SLOTS + 1):
            label, value, uom = (DELIVERY_COLUMNS.index(c) for c in attribute_columns(slot))
            assert value == label + 1
            assert uom == label + 2

    def test_every_feature_slot_is_present(self):
        for slot in range(1, FEATURE_SLOTS + 1):
            assert feature_column(slot) in DELIVERY_COLUMNS

    def test_every_ref_url_slot_is_present(self):
        for slot in range(1, REF_URL_SLOTS + 1):
            assert ref_url_column(slot) in DELIVERY_COLUMNS

    def test_there_are_exactly_50_attribute_slots(self):
        # A 51st slot would mean the tuple drifted from the format.
        assert "ATTRIBUTE_LABEL 51" not in DELIVERY_COLUMNS

    @pytest.mark.parametrize("slot", [0, -1, ATTRIBUTE_SLOTS + 1])
    def test_out_of_range_attribute_slots_raise(self, slot):
        with pytest.raises(ColumnError):
            attribute_columns(slot)


class TestValidateHeader:
    def test_accepts_the_delivery_header(self):
        validate_header(DELIVERY_COLUMNS)

    def test_rejects_a_short_header(self):
        with pytest.raises(ColumnError, match="252"):
            validate_header(DELIVERY_COLUMNS[:-1])

    def test_rejects_a_renamed_column(self):
        mangled = ("MFR_URL",) + DELIVERY_COLUMNS[1:]
        with pytest.raises(ColumnError, match="column 0"):
            validate_header(mangled)

    def test_rejects_a_reordered_header(self):
        # Same membership, wrong order. Loads fine in Excel, imports wrong.
        swapped = (DELIVERY_COLUMNS[1], DELIVERY_COLUMNS[0]) + DELIVERY_COLUMNS[2:]
        with pytest.raises(ColumnError):
            validate_header(swapped)


class TestLoadReferenceHeader:
    def test_rejects_an_empty_file(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")
        with pytest.raises(ColumnError, match="empty"):
            load_reference_header(path)

    def test_strips_a_utf8_bom(self, tmp_path):
        path = tmp_path / "bom.csv"
        path.write_text("﻿Alpha,Beta\n", encoding="utf-8")
        assert load_reference_header(path) == ("Alpha", "Beta")
