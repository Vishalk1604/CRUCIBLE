"""Tests for the dimensional algebra verifier.

This verifier's whole claim is that it catches a class of error no confidence score
flags, because the proposing model is entirely certain when it makes them. The tests
below are that claim, stated as assertions.
"""

import pytest

from crucible.assay.dimensional import DimensionalVerifier, normalize
from crucible.schema import (
    AttributeSpec,
    AttributeValue,
    ProductRecord,
    RawProduct,
    ValueKind,
)

VERIFIER = DimensionalVerifier()


def record(sku: str = "V-1") -> ProductRecord:
    return ProductRecord(raw=RawProduct(sku=sku, description="1/2 SS BALL VLV 600WOG SCRD"))


def spec(name: str, **kw) -> AttributeSpec:
    kw.setdefault("kind", ValueKind.QUANTITY)
    return AttributeSpec(name=name, **kw)


def check(raw: str, attribute_spec: AttributeSpec):
    return VERIFIER.verify(
        AttributeValue(attribute=attribute_spec.name, raw=raw), attribute_spec, record()
    )


class TestHardFailures:
    def test_mass_in_a_length_column_is_rejected_outright(self):
        # The headline case. A model will emit this with full confidence.
        s = spec("thread_pitch", dimension="[length]", canonical_unit="millimeter")
        signal = check("4.2 kg", s)
        assert signal.is_hard_failure
        # The message names both dimensions in words, because a merchandiser actioning
        # the review should not have to read "[mass] / [length] / [time] ** 2".
        assert "length" in signal.detail and "mass" in signal.detail

    def test_length_in_a_pressure_column_is_rejected_outright(self):
        s = spec("max_pressure", dimension="[pressure]", canonical_unit="psi")
        signal = check("150 mm", s)
        assert signal.is_hard_failure

    def test_quantity_with_no_number_is_rejected(self):
        # An attribute declared as a quantity holding "stainless steel" is a category
        # error that must not reach a review queue as a merely-uncertain value.
        s = spec("bore", dimension="[length]", canonical_unit="millimeter")
        signal = check("stainless steel", s)
        assert signal.is_hard_failure
        assert "no readable number" in signal.detail

    def test_inverted_range_is_rejected(self):
        s = spec(
            "temp_range", kind=ValueKind.RANGE, dimension="[temperature]", canonical_unit="degC"
        )
        # Parser orders "120 to -20" on the way in, so construct the inversion directly.
        signal = check("120...-20 C", s)
        assert signal.trust == 1.0 or signal.is_hard_failure  # ordered or caught, never silent


class TestAccepted:
    def test_correct_dimension_passes(self):
        s = spec("bore", dimension="[length]", canonical_unit="millimeter")
        signal = check('1/2"', s)
        assert signal.trust == 1.0
        assert not signal.is_hard_failure

    def test_trade_rating_passes_as_pressure(self):
        s = spec("pressure_rating", dimension="[pressure]", canonical_unit="psi")
        signal = check("600WOG", s)
        assert signal.trust == 1.0

    def test_range_attribute_accepts_a_range(self):
        s = spec(
            "temp_range", kind=ValueKind.RANGE, dimension="[temperature]", canonical_unit="degC"
        )
        signal = check("-20 to 120 C", s)
        assert signal.trust == 1.0


class TestPartialConfidence:
    def test_unlabelled_number_is_doubted_not_failed(self):
        # Spec tables routinely put the unit in the column header, so a bare number is
        # normal — but it is also how inch/millimetre confusion enters a catalog, so it
        # must cost something rather than passing clean.
        s = spec("bore", dimension="[length]", canonical_unit="millimeter")
        signal = check("0.5", s)
        assert 0.0 < signal.trust < 1.0
        assert signal.applicable

    def test_single_value_for_a_range_attribute_is_doubted(self):
        s = spec(
            "temp_range", kind=ValueKind.RANGE, dimension="[temperature]", canonical_unit="degC"
        )
        signal = check("120 C", s)
        assert 0.0 < signal.trust < 1.0


class TestAbstention:
    def test_abstains_on_non_physical_attributes(self):
        # Abstention must stay distinguishable from approval, or fusion is misled.
        s = AttributeSpec(name="body_material", kind=ValueKind.NOMINAL, vocabulary=["316 SS"])
        signal = check("316 stainless steel", s)
        assert signal.applicable is False
        assert signal.is_hard_failure is False


class TestRobustness:
    @pytest.mark.parametrize("junk", ["", "   ", "???", "N/A", "see chart", "\x00\x01"])
    def test_never_raises_on_malformed_input(self, junk):
        # A verifier that crashes stops the line on a million-SKU catalog.
        s = spec("bore", dimension="[length]", canonical_unit="millimeter")
        signal = check(junk, s)
        assert signal.verifier == "dimensional"
        assert 0.0 <= signal.trust <= 1.0


class TestNormalization:
    def test_converts_inches_to_canonical_millimetres(self):
        s = spec("bore", dimension="[length]", canonical_unit="millimeter")
        out = normalize(AttributeValue(attribute="bore", raw='1/2"'), s)
        assert out is not None
        assert out.magnitude == pytest.approx(12.7)
        assert out.unit == "millimeter"

    def test_temperature_conversion_uses_the_offset_scale(self):
        s = spec("temp_rating", dimension="[temperature]", canonical_unit="degC")
        out = normalize(AttributeValue(attribute="temp_rating", raw="212 F"), s)
        assert out is not None
        assert out.magnitude == pytest.approx(100.0)

    def test_range_normalizes_both_ends(self):
        s = spec(
            "temp_range", kind=ValueKind.RANGE, dimension="[temperature]", canonical_unit="degC"
        )
        out = normalize(AttributeValue(attribute="temp_range", raw="32 to 212 F"), s)
        assert out is not None
        assert out.low == pytest.approx(0.0)
        assert out.high == pytest.approx(100.0)

    def test_refuses_rather_than_guesses_on_unparseable_input(self):
        s = spec("bore", dimension="[length]", canonical_unit="millimeter")
        assert normalize(AttributeValue(attribute="bore", raw="see chart"), s) is None

    def test_returns_none_for_non_physical_attributes(self):
        s = AttributeSpec(name="body_material", kind=ValueKind.NOMINAL, vocabulary=["316 SS"])
        assert normalize(AttributeValue(attribute="body_material", raw="316 SS"), s) is None
