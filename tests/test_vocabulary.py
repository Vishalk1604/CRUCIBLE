"""Tests for the vocabulary verifier."""

from __future__ import annotations

import pytest

from crucible.assay.vocabulary import VocabularyVerifier
from crucible.ontology import get_schema
from crucible.schema import (
    AttributeSpec,
    AttributeValue,
    ProductRecord,
    RawProduct,
    SourceSpan,
    ValueKind,
)

MATERIALS = ["aluminum", "composite", "PVC", "steel", "wood"]


def check(raw: str, vocabulary=None, kind=ValueKind.NOMINAL, name="material"):
    quantity_like = kind in (ValueKind.QUANTITY, ValueKind.RANGE)
    spec = AttributeSpec(
        name=name,
        kind=kind,
        # QUANTITY/RANGE must declare a dimension - the schema validator enforces it, and
        # supplying one here keeps this helper building specs the real loader would accept.
        dimension="[length]" if quantity_like else None,
        canonical_unit="millimeter" if quantity_like else None,
        vocabulary=vocabulary
        if vocabulary is not None
        else (MATERIALS if kind is ValueKind.NOMINAL else None),
    )
    value = AttributeValue(
        attribute=name,
        raw=raw,
        spans=[SourceSpan(doc_id="erp", quote=raw or "x", start=0, end=1)],
    )
    record = ProductRecord(raw=RawProduct(sku="S", description=f"a {raw} thing"), values=[value])
    return VocabularyVerifier().verify(value, spec, record)


class TestMembership:
    def test_declared_term_passes(self):
        signal = check("aluminum")
        assert signal.applicable
        assert signal.trust == 1.0

    def test_case_and_spacing_do_not_matter(self):
        for variant in ("Aluminum", "ALUMINUM", "  aluminum  "):
            assert check(variant).trust == 1.0

    def test_term_outside_the_vocabulary_fails_hard(self):
        signal = check("brushed nickel")
        assert signal.applicable
        assert signal.trust == 0.0
        assert signal.is_hard_failure

    def test_failure_lists_what_is_allowed(self):
        # A reviewer must be able to fix the value without opening the schema.
        detail = check("brushed nickel").detail
        assert "aluminum" in detail
        assert "composite" in detail


class TestNearMisses:
    def test_qualified_term_is_doubted_not_failed(self):
        # "316 stainless steel" against a "stainless steel" vocabulary: the extra wording
        # may be a real distinction or may be noise from the description.
        signal = check("316 stainless steel", vocabulary=["stainless steel", "brass"])
        assert signal.applicable
        assert 0.0 < signal.trust < 1.0

    def test_spelling_variant_is_doubted(self):
        signal = check("aluminium")  # British spelling against an American vocabulary
        assert 0.0 < signal.trust < 1.0

    def test_ambiguous_overlap_gets_no_benefit_of_the_doubt(self):
        # "steel" is contained by both entries, so there is no single intended term.
        signal = check("steel", vocabulary=["stainless steel", "carbon steel"])
        assert signal.trust < 1.0

    def test_a_genuinely_different_term_is_not_rescued_by_similarity(self):
        assert check("wood plastic composite decking board").trust < 1.0


class TestAbstention:
    """Non-negotiable #3: abstention must never read as approval."""

    def test_abstains_on_quantities(self):
        signal = check("5", kind=ValueKind.QUANTITY, name="width")
        assert not signal.applicable
        assert signal.trust == 0.0
        assert "not a controlled term" in signal.detail

    def test_abstains_on_free_text(self):
        signal = check("Professional Series", kind=ValueKind.TEXT, name="series")
        assert not signal.applicable

    def test_abstains_on_empty_values(self):
        assert not check("   ").applicable

    def test_abstains_rather_than_raising_without_a_vocabulary(self):
        # ontology.py rejects this at load, so it can only arise from a code-built schema.
        # A verifier must never be the thing that stops a catalog run.
        spec = AttributeSpec.model_construct(
            name="material", kind=ValueKind.NOMINAL, vocabulary=None
        )
        value = AttributeValue(
            attribute="material",
            raw="steel",
            spans=[SourceSpan(doc_id="erp", quote="steel", start=0, end=5)],
        )
        record = ProductRecord(raw=RawProduct(sku="S", description="steel"), values=[value])
        signal = VocabularyVerifier().verify(value, spec, record)
        assert not signal.applicable


class TestAgainstRealSchemas:
    @pytest.mark.parametrize(
        ("category", "attribute", "good", "bad"),
        [
            ("abrasive.cutoff_disc", "material_application", "metal", "drywall"),
            ("decking.railing", "material", "aluminum", "titanium"),
            ("powertool.cordless", "tool_type", "impact wrench", "lathe"),
            ("appliance.major", "appliance_type", "dishwasher", "toaster"),
        ],
    )
    def test_shipped_vocabularies_accept_and_reject(self, category, attribute, good, bad):
        schema = get_schema(category)
        spec = schema.get(attribute)
        assert spec is not None, f"{category}.{attribute} is missing"

        def signal_for(raw):
            value = AttributeValue(
                attribute=attribute,
                raw=raw,
                spans=[SourceSpan(doc_id="erp", quote=raw, start=0, end=1)],
            )
            record = ProductRecord(raw=RawProduct(sku="S", description=raw), values=[value])
            return VocabularyVerifier().verify(value, spec, record)

        assert signal_for(good).trust == 1.0
        assert signal_for(bad).trust < 1.0


class TestCoverage:
    def test_it_speaks_where_the_physical_verifiers_cannot(self):
        # The whole reason this verifier exists: nominal attributes are the majority of a
        # real catalog and dimensional/constraint correctly abstain on all of them.
        schema = get_schema("abrasive.cutoff_disc")
        nominal = [a for a in schema.attributes if a.kind is ValueKind.NOMINAL]
        assert nominal, "the showcase category should carry nominal attributes"
        for spec in nominal:
            value = AttributeValue(
                attribute=spec.name,
                raw=spec.vocabulary[0],
                spans=[SourceSpan(doc_id="erp", quote="x", start=0, end=1)],
            )
            record = ProductRecord(raw=RawProduct(sku="S", description="x"), values=[value])
            assert VocabularyVerifier().verify(value, spec, record).applicable
