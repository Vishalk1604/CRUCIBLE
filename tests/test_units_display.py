"""Tests for splitting a value into the delivery sheet's two cells.

The sheet wants ATTRIBUTE_VALUE and ATTRIBUTE_UOM separately, and it wants the magnitude
written the way the trade writes it. The reference row has `50-1/4` with unit `in`, not
`50.25` - so the guiding rule here is that the magnitude is passed through untouched and
only the unit token is canonicalised.

Strings in these tests are taken from the real dataset and the reference sheet.
"""

import pytest

from crucible.schema import AttributeSpec, ValueKind
from crucible.units import (
    COLOUR_TEMPERATURE_SHORTHAND,
    DISPLAY_ONLY_UOM,
    expand_colour_temperature,
    parse_quantity,
    split_value_uom,
)


class TestSplitsRealValues:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # From the reference delivery sheet
            ("50-1/4 in", ("50-1/4", "in")),
            ("120", ("120", None)),
            ("47 dBA", ("47", "dBA")),
            ("5", ("5", None)),
            # From the input dataset
            ('1/2"', ("1/2", "in")),
            ('4-1/2"', ("4-1/2", "in")),
            ('7/8"', ("7/8", "in")),
            ('.045"', (".045", "in")),
            ("20mm", ("20", "mm")),
            ("500'", ("500", "ft")),
            ("120V", ("120", "V")),
            ("15A", ("15", "A")),
            ("8W", ("8", "W")),
            ("21CF", ("21", "CF")),
            ("8Ah", ("8", "Ah")),
        ],
    )
    def test_splits_magnitude_from_unit(self, text, expected):
        assert split_value_uom(text) == expected

    def test_preserves_the_fraction_rather_than_decimalising_it(self):
        # 50-1/4 must not become 50.25. It is a customer-visible value and the sheet
        # writes it as a mixed fraction.
        magnitude, _ = split_value_uom("50-1/4 in")
        assert magnitude == "50-1/4"
        assert "." not in magnitude

    def test_strips_surrounding_whitespace(self):
        assert split_value_uom("  32  ") == ("32", None)

    def test_empty_text_yields_empty_cells(self):
        assert split_value_uom("") == ("", None)
        assert split_value_uom(None) == ("", None)


class TestRefusesToGuess:
    @pytest.mark.parametrize(
        "text",
        [
            "24 in W x 24-1/4 in D",
            "8-1/2 in Upper Rack, 11-1/4 in Lower Rack",
            "316 stainless steel",
        ],
    )
    def test_composites_pass_through_whole_with_no_unit(self, text):
        # The sheet writes these into a single cell too. Inventing a unit for a composite
        # is worse than an empty UOM cell.
        value, uom = split_value_uom(text)
        assert value == text
        assert uom is None

    @pytest.mark.parametrize("text", ["6pc", "3pk", "50 Disc/Box"])
    def test_pack_counts_are_not_units(self, text):
        _, uom = split_value_uom(text)
        assert uom is None

    def test_a_bare_number_gets_no_unit_invented(self):
        # normalize.py once supplied a canonical unit for bare numbers and thereby
        # committed the exact fault this system exists to catch.
        assert split_value_uom("12") == ("12", None)

    def test_uses_a_declared_display_unit_but_never_an_inferred_one(self):
        spec = AttributeSpec(name="sound_level", kind=ValueKind.TEXT, display_uom="dBA")
        # Declared by a schema author: allowed.
        assert split_value_uom("47", spec) == ("47", "dBA")
        # Nothing declared: stays empty.
        assert split_value_uom("47") == ("47", None)


class TestDisplayOnlyUnits:
    def test_display_only_units_split_for_the_sheet(self):
        for token, symbol in DISPLAY_ONLY_UOM.items():
            if " " in token:
                continue
            assert split_value_uom(f"12{token}") == ("12", symbol)

    @pytest.mark.parametrize("text", ["47 dBA", "12 grit", "1800 RPM"])
    def test_but_the_physics_layer_still_claims_no_unit(self, text):
        # The asymmetry is the point: presentation knows more tokens than verification.
        # If these parsed as real quantities the dimensional verifier would report checks
        # it never performed. dBA is a logarithmic ratio, grit is a sieve number, and
        # neither is a dimension pint can reason about.
        assert parse_quantity(text).unit is None

    def test_cubic_feet_is_a_real_unit_not_a_display_only_one(self):
        # "21CF" on a freezer is genuinely 21 cubic feet, so it belongs in UNIT_ALIASES
        # where the dimensional verifier can check it - unlike dBA. It still *displays*
        # as CF rather than pint's ft**3.
        assert parse_quantity("21CF").unit == "foot ** 3"
        assert split_value_uom("21CF") == ("21", "CF")
        assert "cf" not in DISPLAY_ONLY_UOM


class TestColourTemperature:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("S21354 8W Led T9 Med 27k", "2700 K"),
            ("65-1222 Led Lt 50k", "5000 K"),
            ("40W Led B11 Med 30k 3pk", "3000 K"),
            ('801274 10w LED 6" Retro 50k', "5000 K"),
        ],
    )
    def test_expands_lamp_shorthand(self, text, expected):
        assert expand_colour_temperature(text) == expected

    def test_returns_none_when_there_is_no_shorthand(self):
        # Callers need to tell "not applicable" from "expanded to something".
        assert expand_colour_temperature("1x6-20' Azek PVC Decking") is None
        assert expand_colour_temperature("") is None

    def test_every_mapping_lands_in_the_range_lamps_are_sold_in(self):
        for shorthand, kelvin in COLOUR_TEMPERATURE_SHORTHAND.items():
            assert 1800 <= kelvin <= 7000, f"{shorthand} -> {kelvin} K is not a lamp"
            assert kelvin == int(shorthand[:2]) * 100

    def test_the_naive_reading_would_be_absurd(self):
        # 27k read as 27 kilo-kelvin is 27,000 K - hotter than the sun's surface, and not
        # a product anyone sells. This is why the table exists.
        assert COLOUR_TEMPERATURE_SHORTHAND["27k"] == 2700


class TestNoDuplicatedUnit:
    """A declared unit may fill an empty slot; it must never be appended to one in use.

    Found by stress-testing an unseen catalog: "5.7 cu ft" against a spec declaring `CF`
    rendered as `5.7 cu ft CF` in the delivery sheet - the unit printed twice, in a
    customer-visible cell.
    """

    @pytest.fixture
    def capacity(self):
        from crucible.ontology import get_schema

        return get_schema("appliance.major").get("capacity")

    def test_two_word_unit_is_split_off(self, capacity):
        assert split_value_uom("5.7 cu ft", capacity) == ("5.7", "CF")

    def test_closed_up_unit_still_splits(self, capacity):
        assert split_value_uom("21CF", capacity) == ("21", "CF")

    def test_bare_number_takes_the_declared_unit(self, capacity):
        assert split_value_uom("21", capacity) == ("21", "CF")

    def test_a_magnitude_keeping_its_unit_gets_no_second_one(self, capacity):
        # Whatever the parser could not split off stays in the magnitude, so the declared
        # unit must be withheld or the value reads "7.5 cu. ft. CF".
        magnitude, uom = split_value_uom("7.5 cu. ft.", capacity)
        assert "cu" in magnitude
        assert uom is None

    def test_a_composite_gets_no_declared_unit(self, capacity):
        magnitude, uom = split_value_uom("24 in W x 24-1/4 in D", capacity)
        assert magnitude == "24 in W x 24-1/4 in D"
        assert uom is None

    def test_an_unrecognised_suffix_gets_no_declared_unit(self, capacity):
        # "6pc" is a pack count, not 6 cubic feet.
        assert split_value_uom("6pc", capacity) == ("6pc", None)


class TestTradeFractions:
    """Decimals in, fractions out.

    The guide: *"Manufacturers publish decimals; trade buyers search fractions. Convert
    0.5 to 1/2 and 50.25 in to 50-1/4 in."* Decimal_Fraction.xlsx was never published, but
    the table is exactly n/64, so it is generated rather than transcribed.
    """

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.5, "1/2"),
            (0.25, "1/4"),
            (0.375, "3/8"),
            (0.875, "7/8"),
            (0.0625, "1/16"),
            (0.015625, "1/64"),
            (0.984375, "63/64"),
            (50.25, "50-1/4"),
            (1.5, "1-1/2"),
            (3.0, "3"),
        ],
    )
    def test_the_conversions_the_guide_names(self, value, expected):
        from crucible.units import to_trade_fraction

        assert to_trade_fraction(value) == expected

    @pytest.mark.parametrize("value", [0.51, 0.333, 1.234, 0.1])
    def test_a_non_standard_decimal_is_refused(self, value):
        # Forcing 0.51 to 1/2 would silently change a dimension. A decimal that is not a
        # standard sixty-fourth is information about the part, not a failure.
        from crucible.units import to_trade_fraction

        assert to_trade_fraction(value) is None

    def test_fractions_are_reduced(self):
        from crucible.units import to_trade_fraction

        assert to_trade_fraction(0.5) == "1/2"
        assert to_trade_fraction(0.75) == "3/4"

    def test_negatives_keep_their_sign(self):
        from crucible.units import to_trade_fraction

        assert to_trade_fraction(-0.5) == "-1/2"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("0.5 in", ("1/2", "in")), ("50.25 in", ("50-1/4", "in")), ('0.375"', ("3/8", "in"))],
    )
    def test_split_value_uom_prefers_the_fraction(self, raw, expected):
        assert split_value_uom(raw) == expected

    def test_an_existing_fraction_is_untouched(self):
        assert split_value_uom('1/2"') == ("1/2", "in")
        assert split_value_uom("50-1/4 in") == ("50-1/4", "in")

    def test_a_non_standard_decimal_survives_the_sheet(self):
        assert split_value_uom("0.51 in") == ("0.51", "in")
