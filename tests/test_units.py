"""Tests for industrial quantity parsing.

The cases here are transcribed from the notation that actually appears in distributor
catalogs and manufacturer spec tables. Each one breaks a naive float() plus split(),
which is why generic enrichment tools quietly mangle industrial data.
"""

import pytest

from crucible.units import (
    UnitParseError,
    clean_text,
    convert,
    dimensionality,
    normalize_unit,
    parse_quantity,
)


class TestNumberForms:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("12.7", 12.7),
            ("0.5", 0.5),
            (".500", 0.5),  # leading-dot decimal, common on machined dimensions
            ("1/2", 0.5),  # vulgar fraction
            ("3/8", 0.375),
            ("1-1/2", 1.5),  # mixed number, hyphen form
            ("1 1/2", 1.5),  # mixed number, space form
            ("12,7", 12.7),  # European decimal comma
            ("1,200", 1200.0),  # thousands separator
            ("-20", -20.0),
        ],
    )
    def test_reads_catalog_number_forms(self, text, expected):
        assert parse_quantity(text).magnitude == pytest.approx(expected)

    def test_rejects_text_with_no_number(self):
        # An attribute declared as a quantity whose value has no number is precisely the
        # failure the dimensional verifier must catch, so parsing has to refuse loudly.
        with pytest.raises(UnitParseError):
            parse_quantity("stainless steel")

    def test_rejects_empty(self):
        with pytest.raises(UnitParseError):
            parse_quantity("   ")

    def test_rejects_zero_denominator(self):
        with pytest.raises(UnitParseError):
            parse_quantity("1/0")


class TestUnitRecognition:
    @pytest.mark.parametrize(
        ("text", "magnitude", "unit"),
        [
            ('1/2"', 0.5, "inch"),  # inch mark
            ("1/2 in", 0.5, "inch"),
            ("1/2IN", 0.5, "inch"),
            ("12.7mm", 12.7, "millimeter"),
            ("12.7 MM", 12.7, "millimeter"),
            ("600WOG", 600.0, "wog"),  # trade rating glued to the number
            ("150 PSI", 150.0, "psi"),
            ("2.5 lbs", 2.5, "pound"),
            ("24 VDC", 24.0, "volt"),
            ("50 Hz", 50.0, "hertz"),
        ],
    )
    def test_parses_value_and_unit(self, text, magnitude, unit):
        parsed = parse_quantity(text)
        assert parsed.magnitude == pytest.approx(magnitude)
        assert parsed.unit == unit

    def test_bare_number_has_no_unit(self):
        # The parser must never invent a unit. An unlabelled number stays unlabelled so
        # the verifier can price in that uncertainty rather than inheriting a guess.
        assert parse_quantity("316").unit is None

    def test_unrecognized_unit_is_none_not_an_error(self):
        parsed = parse_quantity("1/2 NPT")  # a thread standard, not a unit
        assert parsed.magnitude == 0.5
        assert parsed.unit is None


class TestRanges:
    @pytest.mark.parametrize(
        ("text", "low", "high"),
        [
            ("-20 to 120 C", -20.0, 120.0),
            ("-20...120C", -20.0, 120.0),
            ("-20..120 degC", -20.0, 120.0),
            ("0 to 150 psi", 0.0, 150.0),
            ("-20/+120C", -20.0, 120.0),  # signed range
        ],
    )
    def test_parses_ranges(self, text, low, high):
        parsed = parse_quantity(text)
        assert parsed.is_range
        assert parsed.low == pytest.approx(low)
        assert parsed.high == pytest.approx(high)

    def test_range_is_not_read_as_single_negative_number(self):
        # The trap: "-20 to 120 C" read greedily yields -20 and silently drops the range.
        parsed = parse_quantity("-20 to 120 C")
        assert parsed.is_range
        assert parsed.unit == "degC"

    def test_inverted_range_is_ordered(self):
        parsed = parse_quantity("120 to -20 C")
        assert parsed.low == pytest.approx(-20.0)
        assert parsed.high == pytest.approx(120.0)


class TestUnicodeAndOcrNoise:
    @pytest.mark.parametrize(
        ("text", "magnitude", "unit"),
        [
            ("250°F", 250.0, "degF"),  # degree sign
            ("1/2″", 0.5, "inch"),  # double prime from a spec sheet
            ("Ø25mm", 25.0, "millimeter"),  # diameter symbol prefix
            ("50µm", 50.0, "micrometer"),  # micro sign
            ("−20 C", -20.0, "degC"),  # unicode minus
        ],
    )
    def test_survives_pdf_and_ocr_characters(self, text, magnitude, unit):
        parsed = parse_quantity(text)
        assert parsed.magnitude == pytest.approx(magnitude)
        assert parsed.unit == unit

    def test_clean_text_folds_typography(self):
        assert '"' in clean_text("1/2″")


class TestNormalizeUnit:
    def test_case_insensitive(self):
        assert normalize_unit("MM") == "millimeter"
        assert normalize_unit("mm") == "millimeter"

    def test_unknown_returns_none(self):
        assert normalize_unit("flanges") is None

    def test_empty_returns_none(self):
        assert normalize_unit("  ") is None


class TestConversion:
    def test_inch_to_millimeter(self):
        assert convert(parse_quantity('1/2"'), "millimeter") == pytest.approx(12.7)

    def test_trade_rating_converts_as_pressure(self):
        # 600 WOG is a pressure rating in psi; it must behave like one.
        assert convert(parse_quantity("600WOG"), "psi") == pytest.approx(600.0)
        assert convert(parse_quantity("600WOG"), "bar") == pytest.approx(41.37, rel=1e-3)

    def test_temperature_uses_offset_conversion(self):
        # The classic silent catalog bug: treating degC->degF as a bare multiplication.
        assert convert(parse_quantity("100 C"), "degF") == pytest.approx(212.0)

    def test_refuses_to_convert_unitless_value(self):
        with pytest.raises(UnitParseError):
            convert(parse_quantity("316"), "millimeter")


class TestDimensionality:
    def test_reports_pint_dimensions(self):
        assert dimensionality("inch") == "[length]"
        assert dimensionality("pound") == "[mass]"

    def test_trade_units_carry_real_dimensions(self):
        # If WOG were defined as a bare alias it would be dimensionless and the
        # dimensional verifier would wave through nonsense.
        assert dimensionality("wog") == dimensionality("psi")
