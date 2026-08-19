"""Tests for the core data model.

These focus on the guards, not the getters. Every guard here encodes a design rule that
the rest of the pipeline is allowed to assume, so if one of these tests goes green when
it should be red, a downstream verifier is silently being handed something it cannot check.
"""

import pytest
from pydantic import ValidationError

from crucible.schema import (
    AttributeSpec,
    AttributeValue,
    CategorySchema,
    EvidenceDoc,
    EvidenceKind,
    NormalizedValue,
    ProductRecord,
    RawProduct,
    SourceSpan,
    ValueKind,
)


class TestSourceSpan:
    def test_accepts_character_range(self):
        span = SourceSpan(doc_id="d1", quote="600 WOG", start=10, end=17)
        assert span.doc_id == "d1"

    def test_accepts_page_locus_for_pdfs(self):
        span = SourceSpan(doc_id="d1", quote="600 WOG", page=3, bbox=(0.1, 0.2, 0.3, 0.4))
        assert span.page == 3

    def test_rejects_span_with_no_address(self):
        # A span that points nowhere cannot be verified, so it must not be constructible.
        with pytest.raises(ValidationError, match="character range"):
            SourceSpan(doc_id="d1", quote="600 WOG")

    def test_rejects_inverted_range(self):
        with pytest.raises(ValidationError, match="empty or inverted"):
            SourceSpan(doc_id="d1", quote="x", start=20, end=10)


class TestAttributeSpec:
    def test_quantity_requires_dimension(self):
        # Without a dimension the dimensional verifier has nothing to check against,
        # which would let "thread pitch: 4.2 kg" through.
        with pytest.raises(ValidationError, match="dimension"):
            AttributeSpec(name="bore", kind=ValueKind.QUANTITY)

    def test_nominal_requires_vocabulary(self):
        with pytest.raises(ValidationError, match="vocabulary"):
            AttributeSpec(name="body_material", kind=ValueKind.NOMINAL)

    def test_text_needs_neither(self):
        spec = AttributeSpec(name="notes", kind=ValueKind.TEXT)
        assert spec.dimension is None


class TestCategorySchema:
    def test_rejects_duplicate_attributes(self):
        dup = AttributeSpec(name="bore", kind=ValueKind.QUANTITY, dimension="[length]")
        with pytest.raises(ValidationError, match="duplicate attributes"):
            CategorySchema(category_id="c1", name="Ball Valves", attributes=[dup, dup])

    def test_lookup_by_name(self):
        schema = CategorySchema(
            category_id="c1",
            name="Ball Valves",
            attributes=[AttributeSpec(name="bore", kind=ValueKind.QUANTITY, dimension="[length]")],
        )
        assert schema.get("bore") is not None
        assert schema.get("nonexistent") is None
        assert schema.attribute_names == ["bore"]


class TestNormalizedValue:
    def test_renders_quantity_with_the_unit_symbol(self):
        # A reviewer scanning a queue should see "12.7 mm", not "12.7 millimeter".
        v = NormalizedValue(kind=ValueKind.QUANTITY, magnitude=12.7, unit="millimeter")
        assert v.render() == "12.7 mm"

    def test_renders_range(self):
        v = NormalizedValue(kind=ValueKind.RANGE, low=-20, high=120, unit="degC")
        assert v.render().startswith("-20 to 120 ")

    def test_renders_unknown_unit_unchanged_rather_than_failing(self):
        # Display must never be a failure path.
        v = NormalizedValue(kind=ValueKind.QUANTITY, magnitude=5, unit="widgets")
        assert "5" in v.render()

    def test_renders_missing_data_without_crashing(self):
        # Review queues must be able to display a half-extracted value.
        assert NormalizedValue(kind=ValueKind.QUANTITY).render() == "-"
        assert NormalizedValue(kind=ValueKind.RANGE, low=1).render() == "-"


class TestAttributeValue:
    def test_value_without_spans_is_ungrounded(self):
        v = AttributeValue(attribute="bore", raw="1/2 in")
        assert v.is_grounded is False

    def test_value_with_span_is_grounded(self):
        v = AttributeValue(
            attribute="bore",
            raw="1/2 in",
            spans=[SourceSpan(doc_id="d1", quote="1/2 in", start=0, end=6)],
        )
        assert v.is_grounded is True


class TestProductRecord:
    def test_lookups(self):
        record = ProductRecord(
            raw=RawProduct(sku="V-1", description="1/2 SS BALL VLV 600WOG SCRD"),
            evidence=[EvidenceDoc(doc_id="d1", kind=EvidenceKind.DATASHEET_PDF)],
            values=[AttributeValue(attribute="bore", raw="1/2 in")],
        )
        assert record.sku == "V-1"
        assert record.value_for("bore") is not None
        assert record.value_for("missing") is None
        assert record.evidence_for("d1").trust_prior == 1.00
        assert record.evidence_for("nope") is None
