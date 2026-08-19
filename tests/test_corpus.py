"""Tests for the labelled corpus.

The load-bearing test here is `TestGoldRecordsAreCoherent`: every generated product must
satisfy its own category's constraints. If the corpus contains physically impossible
products, the constraint verifier fires on correct data, those become false errors in
calibration, and the resulting guarantee is calibrated against a broken answer key.

A corpus that lies is worse than no corpus, because the failure is invisible - the
numbers still come out, they are just wrong.
"""

import random

import pytest

from crucible.assay.constraints import ConstraintVerifier
from crucible.corpus.generate import (
    DEFAULT_FIELD_WIDTH,
    GENERATORS,
    Token,
    assemble,
    generate_corpus,
)
from crucible.ontology import get_schema
from crucible.schema import AttributeValue, ProductRecord


def as_record(gold) -> ProductRecord:
    """Rebuild a ProductRecord from the answer key, as if extraction were perfect."""
    return ProductRecord(
        raw=gold.raw,
        category_id=gold.category_id,
        values=[AttributeValue(attribute=k, raw=v) for k, v in gold.truth.items()],
    )


class TestGoldRecordsAreCoherent:
    """Perfect extraction of a gold record must produce zero constraint violations."""

    @pytest.mark.parametrize("category_id", sorted(GENERATORS))
    def test_no_generated_product_violates_its_own_constraints(self, category_id):
        schema = get_schema(category_id)
        verifier = ConstraintVerifier(schema)

        failures = []
        for gold in generate_corpus(120, seed=99):
            if gold.category_id != category_id:
                continue
            record = as_record(gold)
            for value in record.values:
                spec = schema.get(value.attribute)
                if spec is None:
                    continue
                signal = verifier.verify(value, spec, record)
                if signal.is_hard_failure:
                    failures.append(f"{gold.raw.sku}: {signal.detail}")

        assert not failures, (
            f"{len(failures)} generated {category_id} product(s) violate their own "
            f"constraints; the answer key is not physically coherent:\n" + "\n".join(failures[:5])
        )

    def test_every_truth_attribute_exists_in_the_schema(self):
        # A truth key with no matching attribute is scored against nothing and silently
        # disappears from the error rate.
        for gold in generate_corpus(40, seed=5):
            schema = get_schema(gold.category_id)
            declared = set(schema.attribute_names)
            unknown = set(gold.truth) - declared
            assert not unknown, f"{gold.raw.sku} has truth keys absent from schema: {unknown}"

    def test_nominal_truth_values_come_from_the_declared_vocabulary(self):
        # If the corpus emits a term the schema does not list, a correct extraction would
        # be scored as wrong.
        from crucible.schema import ValueKind

        for gold in generate_corpus(40, seed=6):
            schema = get_schema(gold.category_id)
            for name, value in gold.truth.items():
                spec = schema.get(name)
                if spec and spec.kind is ValueKind.NOMINAL:
                    assert value in (spec.vocabulary or []), (
                        f"{gold.raw.sku}: {name}={value!r} is not in the declared vocabulary"
                    )


class TestRecoverability:
    def test_recoverable_is_a_subset_of_truth(self):
        for gold in generate_corpus(40, seed=11):
            assert gold.recoverable <= set(gold.truth)

    def test_scorable_excludes_facts_the_input_cannot_support(self):
        # Bearing load ratings never appear in a short description. Scoring a model on
        # them would measure clairvoyance rather than extraction.
        for gold in generate_corpus(40, seed=12):
            if gold.category_id == "bearing.ball":
                assert "dynamic_load_rating" not in gold.scorable()
                assert "static_load_rating" not in gold.scorable()

    def test_something_is_always_recoverable(self):
        # A record with an empty answer key contributes nothing to calibration.
        for gold in generate_corpus(60, seed=13):
            assert gold.recoverable, f"{gold.raw.sku} has an empty scorable answer key"

    def test_bore_is_recoverable_only_with_size_and_port(self):
        # Bore follows from nominal size plus port type. It is inferable exactly when
        # both of its inputs survived, which is a piece of real domain reasoning.
        for gold in generate_corpus(60, seed=14):
            if gold.category_id == "valve.ball" and "bore" in gold.recoverable:
                assert {"nominal_size", "port_type"} <= gold.recoverable


class TestAssembly:
    def test_respects_the_field_width(self):
        for gold in generate_corpus(60, seed=15):
            assert len(gold.raw.description) <= DEFAULT_FIELD_WIDTH

    def test_dropped_tokens_forfeit_their_attributes(self):
        rng = random.Random(0)
        tokens = [
            Token.of("SHORT", "kept"),
            Token.of("X" * 100, "dropped"),
        ]
        description, survived = assemble(tokens, rng, field_width=20)
        assert "kept" in survived
        assert "dropped" not in survived
        assert "X" * 100 not in description

    def test_later_short_token_still_fits_after_a_dropped_long_one(self):
        rng = random.Random(0)
        tokens = [
            Token.of("A" * 15, "first"),
            Token.of("B" * 50, "toolong"),
            Token.of("C", "last"),
        ]
        _, survived = assemble(tokens, rng, field_width=20)
        assert survived == {"first", "last"}

    def test_output_is_uppercase(self):
        for gold in generate_corpus(20, seed=16):
            assert gold.raw.description == gold.raw.description.upper()


class TestDeterminism:
    def test_same_seed_gives_identical_corpus(self):
        a = generate_corpus(30, seed=42)
        b = generate_corpus(30, seed=42)
        assert [r.raw.description for r in a] == [r.raw.description for r in b]
        assert [r.truth for r in a] == [r.truth for r in b]

    def test_different_seeds_differ(self):
        a = generate_corpus(30, seed=1)
        b = generate_corpus(30, seed=2)
        assert [r.raw.description for r in a] != [r.raw.description for r in b]

    def test_rejects_nonpositive_size(self):
        with pytest.raises(ValueError, match="positive"):
            generate_corpus(0)


class TestScale:
    def test_produces_enough_values_to_calibrate(self):
        # Certifying 2% error at 95% confidence needs roughly 150 clean points per
        # category. The corpus has to clear that comfortably or the guarantee is
        # unavailable no matter how good the verifiers are.
        from crucible.certify.conformal import required_sample_size

        needed = required_sample_size(0.02, 0.05)
        corpus = generate_corpus(400)
        for category_id in GENERATORS:
            scorable = sum(len(g.recoverable) for g in corpus if g.category_id == category_id)
            assert scorable > needed * 3, f"{category_id} yields only {scorable} scorable values"
