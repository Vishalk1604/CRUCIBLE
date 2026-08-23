"""Tests for the fallback schema used when routing establishes no category.

The evaluation set will contain products this catalog has never modelled. The design
question is what to do with them, and the answer here is: extract what can be extracted,
export a valid row, and leave the classification columns blank — rather than fail the
record or guess a department.

The consequence to hold onto is that generic values are *worth less*, and the system has
to say so rather than hide it. Because every generic attribute is TEXT, the dimensional
and constraint verifiers abstain on all of them, and because `LearnedScorer` encodes
applicability separately from trust, that abstention lowers what can be certified instead
of passing for approval. Non-negotiable #3 is what makes this safe.
"""

import pytest

from crucible.assay.constraints import ConstraintVerifier
from crucible.assay.dimensional import DimensionalVerifier
from crucible.ontology import (
    GENERIC_CATEGORY_ID,
    generic_schema,
    load_all,
    resolve,
)
from crucible.schema import AttributeValue, ProductRecord, RawProduct, SourceSpan, ValueKind


def value(attribute: str, raw: str) -> AttributeValue:
    return AttributeValue(
        attribute=attribute,
        raw=raw,
        spans=[SourceSpan(doc_id="erp", quote=raw, start=0, end=len(raw))],
    )


class TestGenericSchema:
    def test_it_is_not_a_shipped_category(self):
        # Kept out of data/ontology/ on purpose: load_all() should mean "the categories
        # this distributor has modelled", and the invariants that apply to those should
        # not have to be weakened to accommodate the fallback.
        assert GENERIC_CATEGORY_ID not in load_all()

    def test_every_attribute_is_text(self):
        # Nothing here declares a dimension, so nothing here can be checked
        # dimensionally. Saying so is more honest than inventing a dimension to make a
        # verifier fire.
        assert all(a.kind is ValueKind.TEXT for a in generic_schema().attributes)

    def test_it_declares_no_constraints(self):
        assert generic_schema().constraints == []

    def test_it_carries_delivery_labels(self):
        # A generic row still exports, so its attribute labels still print.
        assert all(a.label for a in generic_schema().attributes)

    def test_its_template_is_ordered(self):
        orders = [a.order for a in generic_schema().template()]
        assert orders == sorted(o for o in orders if o is not None)

    def test_it_is_cached_and_stable(self):
        assert generic_schema() is generic_schema()


class TestResolve:
    @pytest.mark.parametrize("category_id", [None, "", GENERIC_CATEGORY_ID, "never.modelled"])
    def test_unknown_categories_resolve_to_generic(self, category_id):
        # The point of resolve() over get_schema(): an unseen product must not raise.
        assert resolve(category_id).category_id == GENERIC_CATEGORY_ID

    def test_known_categories_resolve_to_themselves(self):
        for category_id in load_all():
            assert resolve(category_id).category_id == category_id

    def test_resolve_never_raises_on_an_unknown_id(self):
        from crucible.ontology import SchemaError, get_schema

        # get_schema is strict by design; resolve is the tolerant path for real input.
        with pytest.raises(SchemaError):
            get_schema("never.modelled")
        assert resolve("never.modelled") is generic_schema()


class TestVerifiersAbstainRatherThanApprove:
    def _record(self) -> ProductRecord:
        raw = RawProduct(
            sku="ZX-9",
            description="ZX-9 Widget Assembly, Blue, 6pc",
            category_id=GENERIC_CATEGORY_ID,
        )
        return ProductRecord(
            raw=raw,
            category_id=GENERIC_CATEGORY_ID,
            values=[value("product_name", "Widget Assembly"), value("color", "Blue")],
        )

    def test_dimensional_abstains_on_generic_values(self):
        schema, record = generic_schema(), self._record()
        for item in record.values:
            signal = DimensionalVerifier().verify(item, schema.get(item.attribute), record)
            assert signal.applicable is False

    def test_constraint_abstains_on_generic_values(self):
        schema, record = generic_schema(), self._record()
        verifier = ConstraintVerifier(schema)
        for item in record.values:
            signal = verifier.verify(item, schema.get(item.attribute), record)
            assert signal.applicable is False

    def test_abstention_is_not_recorded_as_trust(self):
        # The distinction non-negotiable #3 exists to protect. If an abstaining verifier
        # returned a neutral-but-applicable score, generic rows would auto-publish on the
        # strength of checks nobody performed.
        schema, record = generic_schema(), self._record()
        item = record.values[0]
        signal = DimensionalVerifier().verify(item, schema.get(item.attribute), record)
        assert signal.applicable is False
        assert not signal.is_hard_failure
