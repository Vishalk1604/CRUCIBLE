"""Tests for canonicalisation.

The load-bearing test here is `test_bare_magnitude_is_not_given_a_unit`. An earlier
version supplied the schema's canonical unit to any unitless value, which rewrote `1/4`
on a quarter-inch screw as `0.25 millimeter`. That is fabrication, and specifically it is
the unit-confusion failure this system exists to detect, committed by the system itself
one stage before the verifier that would have caught it.
"""

import pytest

from crucible.normalize import (
    Normalisation,
    normalise_record,
    normalise_value,
    parse_fraction,
)
from crucible.ontology import get_schema
from crucible.schema import AttributeSpec, AttributeValue, ProductRecord, RawProduct, ValueKind

LENGTH = AttributeSpec(
    name="length", kind=ValueKind.QUANTITY, dimension="[length]", canonical_unit="millimeter"
)
SEAL = AttributeSpec(
    name="seal_type",
    kind=ValueKind.NOMINAL,
    vocabulary=["open", "Z metal shielded one side", "2RS rubber sealed both sides"],
)
MATERIAL = AttributeSpec(
    name="material", kind=ValueKind.NOMINAL, vocabulary=["304 stainless steel", "carbon steel"]
)


class TestFractions:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("1/4", 0.25), ("3/8", 0.375), ("1-1/4", 1.25), ("3 1/2", 3.5), ("7/8", 0.875)],
    )
    def test_reads_industrial_fraction_notation(self, text, expected):
        assert parse_fraction(text) == pytest.approx(expected)

    @pytest.mark.parametrize("text", ["25", "abc", "", "1/0", "1//4"])
    def test_returns_none_for_non_fractions(self, text):
        assert parse_fraction(text) is None


class TestNumericCanonicalisation:
    def test_fraction_becomes_decimal(self):
        assert normalise_value("1/4", LENGTH).normalised == "0.25"

    def test_bare_magnitude_is_not_given_a_unit(self):
        # The regression this module was rewritten for. Supplying canonical_unit here
        # produced "0.25 millimeter" for a quarter-inch screw: a fabricated value, and
        # exactly the unit confusion the dimensional verifier exists to report.
        result = normalise_value("1/4", LENGTH)
        assert result.normalised == "0.25"
        assert "millimeter" not in result.normalised

    def test_existing_unit_is_preserved_verbatim(self):
        assert normalise_value("25 mm", LENGTH).normalised == "25 mm"
        assert normalise_value("0.5 in", LENGTH).normalised == "0.5 in"

    def test_units_are_never_converted(self):
        # Rewriting 12.7 mm to 0.5" would hide unit-confusion faults from the verifier
        # that is supposed to find them.
        assert normalise_value("12.7 mm", LENGTH).normalised == "12.7 mm"

    def test_fraction_with_attached_unit(self):
        assert normalise_value('1-1/4"', LENGTH).normalised == '1.25"'

    def test_decimal_passes_through_unchanged(self):
        assert normalise_value("0.375", LENGTH).normalised == "0.375"


class TestCodeExpansion:
    def test_expands_a_known_code(self):
        assert normalise_value("Z", SEAL).normalised == "Z metal shielded one side"

    def test_expansion_is_case_insensitive(self):
        assert normalise_value("2rs", SEAL).normalised == "2RS rubber sealed both sides"

    def test_leaves_unknown_codes_alone(self):
        # C3 is a clearance code, not a seal code. Leaving it visibly unnormalised is
        # what lets it be caught; inventing a seal type for it would not.
        assert normalise_value("C3", SEAL).normalised == "C3"


class TestVocabulary:
    def test_exact_vocabulary_match_is_kept(self):
        assert normalise_value("open", SEAL).normalised == "open"

    def test_unambiguous_prefix_resolves(self):
        assert normalise_value("Z metal", SEAL).normalised == "Z metal shielded one side"

    def test_ambiguous_prefix_is_left_unchanged(self):
        spec = AttributeSpec(
            name="finish", kind=ValueKind.NOMINAL, vocabulary=["zinc plated", "zinc chromate"]
        )
        # Guessing between two candidates would manufacture a confident wrong value
        # where an obviously unnormalised one gets routed to review.
        assert normalise_value("zinc", spec).normalised == "zinc"

    def test_domain_synonym_resolves(self):
        assert normalise_value("18-8", MATERIAL).normalised == "304 stainless steel"

    def test_synonym_rejected_when_outside_the_vocabulary(self):
        spec = AttributeSpec(name="material", kind=ValueKind.NOMINAL, vocabulary=["brass"])
        assert normalise_value("18-8", spec).normalised == "18-8"


class TestBookkeeping:
    def test_reports_which_rule_fired(self):
        assert normalise_value("Z", SEAL).rule == "code-expansion"
        assert normalise_value("1/4", LENGTH).rule == "numeric"
        # "18-8" resolves by code expansion rather than synonym: it is a real key in the
        # grade table, and code expansion is tried first.
        assert normalise_value("18-8", MATERIAL).rule == "code-expansion"
        body = AttributeSpec(
            name="body_material",
            kind=ValueKind.NOMINAL,
            vocabulary=["316 stainless steel", "brass"],
        )
        assert normalise_value("stainless steel", body).rule == "synonym"

    def test_unchanged_values_are_marked(self):
        result = normalise_value("C3", SEAL)
        assert not result.changed
        assert result.rule == "unchanged"

    def test_empty_input(self):
        assert normalise_value("   ", LENGTH).rule == "empty"

    def test_normalisation_is_a_value_object(self):
        assert Normalisation("a", "b", "r").changed
        assert not Normalisation("a", "a", "r").changed


class TestRecords:
    def _record(self):
        return ProductRecord(
            raw=RawProduct(sku="B-1", description="BRG BALL 6205-Z C3"),
            category_id="bearing.ball",
            values=[
                AttributeValue(attribute="seal_type", raw="Z"),
                AttributeValue(attribute="bore_diameter", raw="25 mm"),
            ],
        )

    def test_normalises_every_value(self):
        result = normalise_record(self._record(), get_schema("bearing.ball"))
        seal = next(v for v in result.values if v.attribute == "seal_type")
        assert seal.raw == "Z metal shielded one side"

    def test_spans_survive(self):
        # Normalisation changes how a value is written, not what supports it.
        record = self._record()
        record.values[0].spans = [
            __import__("crucible.schema", fromlist=["SourceSpan"]).SourceSpan(
                doc_id="erp", quote="Z", start=14, end=15
            )
        ]
        result = normalise_record(record, get_schema("bearing.ball"))
        assert result.values[0].spans[0].quote == "Z"

    def test_unknown_attributes_pass_through(self):
        record = self._record()
        record.values.append(AttributeValue(attribute="not_in_schema", raw="x"))
        result = normalise_record(record, get_schema("bearing.ball"))
        assert any(v.attribute == "not_in_schema" for v in result.values)


class TestVocabularyContainment:
    """Catalogs qualify their terms; the vocabulary lists the bare ones.

    This data writes "Metal Cut-Off" where the vocabulary says `cut-off`, and
    "Grinding Wheel" where it says `grinding`. Resolving those is worth real compliance
    points - but only while it stays unable to arbitrate between two candidates.
    """

    @pytest.fixture
    def wheel_type(self):
        return get_schema("abrasive.cutoff_disc").get("wheel_type")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Metal Cut-Off", "cut-off"),
            ("Cut Off Disc", "cut-off"),
            ("Cut-Off", "cut-off"),
            ("Grinding Wheel", "grinding"),
        ],
    )
    def test_a_qualified_term_resolves_to_the_bare_one(self, wheel_type, raw, expected):
        assert normalise_value(raw, wheel_type).normalised == expected

    def test_hyphens_and_spaces_are_the_same_separator(self, wheel_type):
        assert (
            normalise_value("Cut Off", wheel_type).normalised
            == normalise_value("Cut-Off", wheel_type).normalised
        )

    def test_a_value_containing_no_term_is_left_alone(self, wheel_type):
        assert normalise_value("Dual Metal", wheel_type).normalised == "Dual Metal"

    def test_a_value_containing_two_terms_is_left_for_review(self):
        # The safety property: two candidates means ambiguous, and arbitrating between
        # them would manufacture a confident wrong answer.
        spec = get_schema("abrasive.cutoff_disc").get("material_application")
        assert normalise_value("Stainless Steel Metal", spec).normalised == "Stainless Steel Metal"

    def test_it_does_not_rescue_a_value_in_the_wrong_field(self):
        # 'Metal Cut-Off Disc' proposed as an abrasive *grain* is a genuine extraction
        # error. Containment must not launder it into a valid-looking value.
        spec = get_schema("abrasive.cutoff_disc").get("abrasive_grain")
        assert normalise_value("Metal Cut-Off Disc", spec).normalised == "Metal Cut-Off Disc"

    def test_matching_is_on_whole_words(self):
        from crucible.schema import AttributeSpec, ValueKind

        spec = AttributeSpec(name="x", kind=ValueKind.NOMINAL, vocabulary=["tin"])
        assert normalise_value("stainless", spec).normalised == "stainless"
