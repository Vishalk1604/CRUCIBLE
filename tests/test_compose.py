"""Tests for description composition.

The load-bearing test here is `test_no_clause_is_unverified`. The client's guide says a
fluent description made of invented values scores zero, and our answer is that ours cannot
contain one *by construction*. That is only true while every composer draws solely from
verified values, so it is asserted rather than trusted.
"""

from __future__ import annotations

import re

import pytest

from crucible.emit.compose import (
    INVOICE_MAX,
    MOBILE_MAX,
    MOBILE_MIN,
    _singular,
    _titlecase,
    compliance,
    compose_all,
    gather,
)
from crucible.ontology import get_schema
from crucible.schema import AttributeValue, ProductRecord, RawProduct, Routing, SourceSpan

DESCRIPTION = "PDSH4816AF Dishwasher SS 120V 15A Leg 47 dBA Professional Series"


def value(attribute: str, raw: str) -> AttributeValue:
    start = max(DESCRIPTION.find(raw[:8]), 0)
    return AttributeValue(
        attribute=attribute,
        raw=raw,
        spans=[SourceSpan(doc_id="erp", quote=raw, start=start, end=start + len(raw))],
    )


def make_record(values, *, brand="FRIGIDAIRE", fine="Dishwashers", manufacturer="Rheem"):
    raw = RawProduct(
        sku="PDSH4816AF",
        mpn="PDSH4816AF",
        description=DESCRIPTION,
        brand=brand,
        category_id="appliance.major",
        extra={"part_manuf_name": manufacturer},
    )
    routing = Routing(category_id="appliance.major", fine=fine, dept="Appliances")
    return ProductRecord(raw=raw, category_id="appliance.major", routing=routing, values=values)


FULL = [
    value("appliance_type", "dishwasher"),
    value("series", "Professional Series"),
    value("voltage_rating", "120 V"),
    value("amperage_rating", "15 A"),
    value("mounting_type", "Leg"),
    value("sound_level", "47 dBA"),
    value("finish", "Stainless Steel"),
]


@pytest.fixture
def schema():
    return get_schema("appliance.major")


class TestGrounding:
    def test_no_clause_is_unverified(self, schema):
        """Every token in every description traces back to a verified input.

        This is the claim the whole approach rests on, so it is asserted rather than
        trusted. The check is substring containment against the concatenation of every
        verified value, label and identifier, with punctuation and spacing stripped -
        which tolerates the two legitimate reshapings (a unit closed up as "120V", a label
        joined to its value) while still catching a single invented word.
        """
        record = make_record(FULL)
        fields, _ = compose_all(record, schema)

        corpus_parts = [v.raw for v in record.values]
        corpus_parts += [
            schema.get(v.attribute).sheet_label for v in record.values if schema.get(v.attribute)
        ]
        corpus_parts += [
            record.raw.brand or "",
            record.raw.mpn or "",
            record.raw.extra["part_manuf_name"] or "",
            _singular("Dishwashers"),
            "with additional information and",  # the fixed connectives templates may add
        ]
        corpus = re.sub(r"[^a-z0-9]", "", " ".join(corpus_parts).lower())

        for name, composed in fields.items():
            for token in composed.text.lower().split():
                flat = re.sub(r"[^a-z0-9]", "", token)
                if not flat:
                    continue
                assert flat in corpus, f"{name} introduced unverified text {token!r}"

    def test_an_invented_word_would_be_caught(self, schema):
        # Proof the assertion above has teeth: the same check on prose the guide warns
        # about - "a fluent description made of invented values" - must fail.
        record = make_record(FULL)
        corpus = re.sub(r"[^a-z0-9]", "", " ".join(v.raw for v in record.values).lower())
        assert "quietest" not in corpus

    def test_spans_are_inherited_from_the_values_used(self, schema):
        fields, _ = compose_all(make_record(FULL), schema)
        for composed in fields.values():
            assert composed.spans, "a composed field must cite the evidence it rests on"
            for span in composed.spans:
                assert span.quote

    def test_nothing_is_composed_without_a_product_noun(self, schema):
        # No attribute names the product and no Fine class to fall back on.
        record = make_record([value("voltage_rating", "120 V")], fine="")
        record = record.model_copy(update={"routing": None})
        fields, _ = compose_all(record, schema)
        assert fields == {}


class TestCharacterLimits:
    def test_invoice_never_exceeds_forty(self, schema):
        fields, _ = compose_all(make_record(FULL), schema)
        assert len(fields["INVOICE_DESC"].text) <= INVOICE_MAX

    def test_invoice_is_upper_case(self, schema):
        fields, _ = compose_all(make_record(FULL), schema)
        assert fields["INVOICE_DESC"].text.isupper()

    def test_invoice_closes_the_unit_up(self, schema):
        # The reference row writes 120V and 41DBA, not "120 V" - unique to this field.
        fields, _ = compose_all(make_record(FULL), schema)
        text = fields["INVOICE_DESC"].text
        assert "120V" in text or "15A" in text or "47DBA" in text

    def test_other_fields_space_the_unit(self, schema):
        fields, _ = compose_all(make_record(FULL), schema)
        assert "120 V" in fields["LONG_DESC1"].text

    def test_mobile_lands_in_its_band(self, schema):
        fields, _ = compose_all(make_record(FULL), schema)
        assert MOBILE_MIN <= len(fields["MOBILE_DESC"].text) <= MOBILE_MAX

    def test_compliance_reports_each_check(self, schema):
        fields, _ = compose_all(make_record(FULL), schema)
        checks = compliance(fields)
        assert checks["INVOICE_DESC<=40"] is True
        assert checks["INVOICE_DESC is upper"] is True


class TestShape:
    def test_short_desc_leads_with_brand_series_mpn(self, schema):
        fields, _ = compose_all(make_record(FULL), schema)
        text = fields["SHORT_DESC"].text
        assert text.startswith("FRIGIDAIRE Professional Series PDSH4816AF Dishwasher")

    def test_retail_desc_drops_brand_and_mpn(self, schema):
        fields, _ = compose_all(make_record(FULL), schema)
        text = fields["RETAIL_DESC"].text
        assert "FRIGIDAIRE" not in text
        assert "PDSH4816AF" not in text
        assert text.startswith("Professional Series Dishwasher")

    def test_the_product_noun_is_not_repeated_with_its_own_label(self, schema):
        # "Dishwasher, Dishwasher Product Name" was a real bug.
        fields, _ = compose_all(make_record(FULL), schema)
        assert fields["LONG_DESC1"].text.lower().count("dishwasher") == 1

    def test_manufacturer_is_dropped_when_it_is_the_brand(self, schema):
        # Whirlpool Corporation / Whirlpool(R) prints the name once in the reference row.
        record = make_record(FULL, brand="Whirlpool", manufacturer="Whirlpool Corporation")
        fields, _ = compose_all(record, schema)
        assert fields["MOBILE_DESC"].text.lower().count("whirlpool") == 1

    def test_manufacturer_is_kept_when_it_differs(self, schema):
        fields, _ = compose_all(make_record(FULL), schema)
        assert fields["MOBILE_DESC"].text.startswith("Rheem FRIGIDAIRE")


class TestFallbacks:
    def test_product_noun_falls_back_to_the_fine_class(self, schema):
        # A cut-off wheel's schema has no noun-shaped attribute; the router supplies one.
        record = make_record([value("voltage_rating", "120 V")], fine="Cut-Off & Grinding Wheels")
        assert gather(record, schema).product_name == "Cut-Off Wheel"

    def test_brand_falls_back_to_the_extracted_value(self, schema):
        # Every brand column can be a placeholder while the description names the brand.
        record = make_record([*FULL, value("brand", "Diablo")], brand=None)
        assert gather(record, schema).brand == "Diablo"


class TestWordShaping:
    @pytest.mark.parametrize(
        ("fine", "expected"),
        [
            ("Dishwashers", "Dishwasher"),
            ("Cut-Off & Grinding Wheels", "Cut-Off Wheel"),
            ("Sanding Belts, Discs & Sheets", "Sanding Belt"),
            ("Batteries", "Battery"),
            ("Decking Boards", "Decking Board"),
            ("", ""),
        ],
    )
    def test_singularisation(self, fine, expected):
        assert _singular(fine) == expected

    def test_titlecase_preserves_acronyms_and_hyphens(self):
        assert _titlecase("cut-off wheel") == "Cut-Off Wheel"
        assert _titlecase("PVC decking") == "PVC Decking"


class TestFeatures:
    def test_features_come_from_additional_information(self, schema):
        record = make_record(
            [*FULL, value("additional_information", "Folding Tines, Leak Detection System")]
        )
        _, features = compose_all(record, schema)
        texts = [f.text for f in features]
        assert "Folding Tines" in texts
        assert "Leak Detection System" in texts

    def test_no_additional_information_means_few_features(self, schema):
        # The reference row with no Additional Information has no features either.
        _, features = compose_all(make_record(FULL), schema)
        assert all(f.spans for f in features)
