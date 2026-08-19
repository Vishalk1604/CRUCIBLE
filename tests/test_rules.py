"""Tests for the rule-based extractor.

Note on what is *not* tested here: accuracy against the generated corpus. The corpus
builds descriptions from `corpus.tables` and this extractor reads them back with the same
tables, so any accuracy figure measures whether a lookup table agrees with itself. It
scores 100%, and that number means nothing.

What these tests do cover is behaviour that must hold regardless of accuracy: every value
carries provenance, nothing is invented to fill a gap, and token matching does not read
codes out of the middle of other codes.
"""

import pytest

from crucible.extract.rules import ERP_DOC_ID, extract
from crucible.schema import RawProduct


def erp(description: str, category_id: str) -> RawProduct:
    return RawProduct(sku="T-1", description=description, category_id=category_id)


def values_of(description: str, category_id: str) -> dict[str, str]:
    record = extract(erp(description, category_id))
    return {v.attribute: v.raw for v in record.values}


class TestProvenance:
    def test_every_value_cites_the_source(self):
        # The rule that a value is only as trustworthy as its evidence has no exemption
        # for values that happened to be easy to obtain.
        for description, category in [
            ("3 BALL VLV SS 1000WOG SW RP PTFE", "valve.ball"),
            ("HX CAP SCR 3/8-16X1.5 GR5 ZP", "fastener.hex_cap_screw"),
            ("BRG BALL 6205-2RS C3", "bearing.ball"),
        ]:
            record = extract(erp(description, category))
            assert record.values, f"nothing extracted from {description!r}"
            for value in record.values:
                assert value.is_grounded, f"{value.attribute} carries no span"
                assert value.spans[0].doc_id == ERP_DOC_ID

    def test_spans_point_at_real_substrings(self):
        description = "3 BALL VLV SS 1000WOG SW RP"
        record = extract(erp(description, "valve.ball"))
        for value in record.values:
            span = value.spans[0]
            assert description[span.start : span.end] == span.quote

    def test_erp_record_is_attached_as_evidence(self):
        record = extract(erp("BRG BALL 6205-2RS", "bearing.ball"))
        doc = record.evidence_for(ERP_DOC_ID)
        assert doc is not None
        assert doc.text == "BRG BALL 6205-2RS"


class TestDoesNotGuess:
    def test_omits_attributes_it_cannot_match(self):
        # A missing value costs one review. A confidently wrong value costs trust in the
        # whole catalog.
        got = values_of("BALL VLV", "valve.ball")
        assert "body_material" not in got
        assert "pressure_rating" not in got

    def test_unknown_category_yields_no_values_rather_than_raising(self):
        # One unrecognised product must not stop a catalog run.
        record = extract(erp("SOMETHING ODD", "valve.gate"))
        assert record.values == []

    def test_unknown_bearing_designation_yields_nothing(self):
        assert values_of("BRG BALL 9999-2RS", "bearing.ball") == {}

    def test_handles_empty_description(self):
        assert values_of("", "valve.ball") == {}


class TestTokenMatching:
    def test_does_not_read_a_code_out_of_another_code(self):
        # "304SS" contains "SS". Substring matching would silently downgrade a 304
        # stainless valve to 316.
        got = values_of("1 BALL VLV 304SS 1000WOG SCRD FP", "valve.ball")
        assert got["body_material"] == "304 stainless steel"

    def test_matches_longer_grade_codes_first(self):
        got = values_of("HX CAP SCR 1/2-13X2 10.9 ZP", "fastener.hex_cap_screw")
        assert got["grade"] == "ISO Class 10.9"


class TestDerivedValues:
    def test_bore_follows_from_size_and_port(self):
        full = values_of("1 BALL VLV SS 1000WOG SCRD FP", "valve.ball")
        reduced = values_of("1 BALL VLV SS 1000WOG SCRD RP", "valve.ball")
        assert full["bore"] == '1"'
        assert reduced["bore"] == '0.75"'

    def test_bore_is_absent_without_port_type(self):
        # The derivation needs both inputs; without one it must not be attempted.
        assert "bore" not in values_of("1 BALL VLV SS 1000WOG SCRD", "valve.ball")

    def test_bearing_dimensions_come_from_the_designation(self):
        # ISO 15 fixes all three from the code alone.
        got = values_of("BRG BALL 6205-2RS", "bearing.ball")
        assert got["bore_diameter"] == "25 mm"
        assert got["outside_diameter"] == "52 mm"
        assert got["width"] == "15 mm"
        assert got["seal_type"] == "2RS rubber sealed both sides"

    def test_thread_designation_yields_diameter_and_pitch(self):
        got = values_of("HX CAP SCR 3/8-16X1.5 GR5 ZP", "fastener.hex_cap_screw")
        assert got["nominal_diameter"] == '0.375"'
        assert got["threads_per_inch"] == "16 /in"
        assert got["length"] == '1.5"'


class TestSizeParsing:
    @pytest.mark.parametrize(
        ("description", "expected"),
        [
            ("3 BALL VLV SS 1000WOG SCRD FP", '3"'),
            ("1/2 BALL VLV SS 1000WOG SCRD FP", '1/2"'),
            ("1-1/4 BALL VLV SS 1000WOG SCRD FP", '1-1/4"'),
        ],
    )
    def test_reads_leading_nominal_size(self, description, expected):
        assert values_of(description, "valve.ball")["nominal_size"] == expected


class TestPressureRating:
    @pytest.mark.parametrize(
        "description",
        [
            "1 BALL VLV SS 600WOG SCRD FP",
            "1 BALL VLV SS 600 WOG SCRD FP",
            "1 BALL VLV SS 600 PSI SCRD FP",
        ],
    )
    def test_reads_rating_with_or_without_a_space(self, description):
        # Some systems strip the space before a trade rating and some do not.
        assert values_of(description, "valve.ball")["pressure_rating"] == "600 WOG"
