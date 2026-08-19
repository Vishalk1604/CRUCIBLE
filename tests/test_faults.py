"""Tests for fault injection.

This module is a test fixture, so its tests are about fidelity rather than correctness of
output: does it corrupt at the rate it claims, does it preserve the answer key, and does
it keep the spans attached so entailment checking has something to catch.
"""

import pytest

from crucible.corpus.faults import (
    DEFAULT_MIX,
    FaultInjector,
    FaultType,
    fault_mix,
    inject_all,
)
from crucible.corpus.generate import generate_corpus
from crucible.extract.rules import extract
from crucible.ontology import get_schema, load_all
from crucible.schema import AttributeValue, ProductRecord, RawProduct


def corpus_records(n=120):
    return [extract(g.raw) for g in generate_corpus(n)]


class TestInjectionRate:
    def test_corrupts_near_the_requested_rate(self):
        records = corpus_records()
        _, faults = inject_all(records, load_all(), rate=0.12)
        total = sum(len(r.values) for r in records)
        assert 0.08 <= len(faults) / total <= 0.14

    def test_zero_rate_corrupts_nothing(self):
        records = corpus_records(20)
        damaged, faults = inject_all(records, load_all(), rate=0.0)
        assert faults == []
        assert [v.raw for r in damaged for v in r.values] == [
            v.raw for r in records for v in r.values
        ]

    def test_rejects_out_of_range_rate(self):
        with pytest.raises(ValueError, match=r"\[0, 1\]"):
            FaultInjector(rate=1.5)


class TestFaultFidelity:
    def test_corrupted_values_actually_differ(self):
        records = corpus_records()
        _, faults = inject_all(records, load_all(), rate=0.2)
        assert faults
        for fault in faults:
            assert fault.original != fault.corrupted

    def test_answer_key_matches_what_changed(self):
        # The fault log is the detection answer key. If it disagrees with the records,
        # every detection number computed from it is wrong.
        records = corpus_records(60)
        damaged, faults = inject_all(records, load_all(), rate=0.2)

        logged = {(f.sku, f.attribute): f.corrupted for f in faults}
        clean = {(r.sku, v.attribute): v.raw for r in records for v in r.values}

        for record in damaged:
            for value in record.values:
                key = (record.sku, value.attribute)
                if key in logged:
                    assert value.raw == logged[key]
                else:
                    assert value.raw == clean[key]

    def test_spans_survive_corruption(self):
        # A corrupted value must keep claiming the same source. That is precisely the
        # situation entailment checking exists to catch: a citation that is present and
        # does not support the value.
        records = corpus_records(40)
        damaged, faults = inject_all(records, load_all(), rate=0.3)
        corrupted_keys = {(f.sku, f.attribute) for f in faults}
        for record in damaged:
            for value in record.values:
                if (record.sku, value.attribute) in corrupted_keys:
                    assert value.is_grounded

    def test_original_records_are_not_mutated(self):
        records = corpus_records(30)
        before = [v.raw for r in records for v in r.values]
        inject_all(records, load_all(), rate=0.5)
        after = [v.raw for r in records for v in r.values]
        assert before == after


class TestFaultTypes:
    def test_every_declared_type_can_occur(self):
        records = corpus_records(300)
        _, faults = inject_all(records, load_all(), rate=0.4)
        seen = {f.fault for f in faults}
        missing = set(DEFAULT_MIX) - seen
        assert not missing, f"fault types never produced: {sorted(missing)}"

    def test_dimension_swap_produces_a_wrong_dimension(self):
        schema = get_schema("bearing.ball")
        injector = FaultInjector(rate=1.0, mix={FaultType.DIMENSION_SWAP: 1.0}, seed=1)
        record = ProductRecord(
            raw=RawProduct(sku="B-1", description="BRG BALL 6205"),
            category_id="bearing.ball",
            values=[AttributeValue(attribute="bore_diameter", raw="25 mm")],
        )
        _, faults = injector.inject(record, schema)
        assert faults
        assert "mm" not in faults[0].corrupted

    def test_vocabulary_drift_only_hits_nominal_attributes(self):
        records = corpus_records(200)
        _, faults = inject_all(records, load_all(), rate=0.4)
        schemas = load_all()
        for fault in faults:
            if fault.fault is FaultType.VOCABULARY_DRIFT:
                record = next(r for r in records if r.sku == fault.sku)
                spec = schemas[record.category_id].get(fault.attribute)
                assert spec.kind.value == "nominal"


class TestFaultMix:
    def test_reports_proportions_summing_to_one(self):
        records = corpus_records(150)
        _, faults = inject_all(records, load_all(), rate=0.2)
        mix = fault_mix(faults)
        assert sum(mix.values()) == pytest.approx(1.0)

    def test_empty_input(self):
        assert fault_mix([]) == {}

    def test_achieved_mix_is_reported_not_assumed(self):
        # The achieved mix differs from the declared one because applicability varies by
        # attribute kind. This test documents that rather than pretending otherwise: it
        # is why detection must be measured per fault type, never in aggregate.
        records = corpus_records(200)
        _, faults = inject_all(records, load_all(), rate=0.2)
        achieved = fault_mix(faults)
        assert achieved[FaultType.ATTRIBUTE_SWAP] > DEFAULT_MIX[FaultType.ATTRIBUTE_SWAP], (
            "attribute swap is the only fault applicable to every value, so it is "
            "expected to be over-represented relative to its declared weight"
        )


class TestDeterminism:
    def test_same_seed_reproduces_the_same_faults(self):
        records = corpus_records(50)
        _, a = inject_all(records, load_all(), rate=0.2, seed=3)
        _, b = inject_all(records, load_all(), rate=0.2, seed=3)
        assert a == b

    def test_different_seeds_differ(self):
        records = corpus_records(50)
        _, a = inject_all(records, load_all(), rate=0.2, seed=1)
        _, b = inject_all(records, load_all(), rate=0.2, seed=2)
        assert a != b
