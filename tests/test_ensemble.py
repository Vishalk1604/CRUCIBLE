"""Tests for ensemble disagreement.

The property this verifier exists for is continuity. The other three emit about three
discrete trust levels each, which is why the risk-coverage frontier has roughly twelve
rungs. A test here asserts that this one produces many distinct values across varied
input, because a verifier that also emitted three levels would not fix the problem it
was built for.
"""

import pytest

from crucible.assay.ensemble import (
    MIN_TRUST,
    EnsembleIndex,
    EnsembleVerifier,
    build_index,
    mean_pairwise_similarity,
)
from crucible.ontology import get_schema
from crucible.schema import AttributeValue, ProductRecord, RawProduct

VALVE = "valve.ball"
SCHEMAS = {VALVE: get_schema(VALVE)}
SPEC = get_schema(VALVE).get("body_material")


def record(sku: str, **values: str) -> ProductRecord:
    return ProductRecord(
        raw=RawProduct(sku=sku, description="1/2 SS BALL VLV"),
        category_id=VALVE,
        values=[AttributeValue(attribute=a, raw=v) for a, v in values.items()],
    )


def index_from(*passes: list[ProductRecord]) -> EnsembleIndex:
    return build_index(list(passes), SCHEMAS)


class TestSimilarity:
    def test_identical_values_score_one(self):
        assert mean_pairwise_similarity(["brass", "brass", "brass"]) == 1.0

    def test_single_value_scores_one(self):
        assert mean_pairwise_similarity(["brass"]) == 1.0

    def test_unrelated_values_score_low(self):
        assert mean_pairwise_similarity(["brass", "PTFE"]) < 0.4

    def test_is_continuous_not_stepped(self):
        # The reason this verifier exists. Exact-match agreement over three samples
        # gives four possible values and would not smooth the frontier.
        pools = [
            ["316 stainless steel", "316 stainless stee", "316 stainless st"],
            ["brass", "brass", "bras"],
            ["PTFE", "PTF", "PT"],
            ["carbon steel", "carbon", "steel"],
            ["25 mm", "25 m", "2 mm"],
        ]
        scores = {round(mean_pairwise_similarity(p), 4) for p in pools}
        assert len(scores) == len(pools), "similarity is collapsing to a few levels"

    def test_case_and_whitespace_are_ignored(self):
        assert mean_pairwise_similarity([" Brass ", "brass"]) == 1.0


class TestIndex:
    def test_collects_values_across_passes(self):
        idx = index_from([record("V-1", body_material="BRS")], [record("V-1", body_material="BRS")])
        assert len(idx.get("V-1", "body_material")) == 2

    def test_normalises_on_the_way_in(self):
        # Otherwise this would mostly measure whether the model expanded an abbreviation
        # the same way twice, which is formatting rather than confidence.
        idx = index_from([record("V-1", body_material="BRS")])
        assert idx.get("V-1", "body_material") == ["brass"]

    def test_ignores_unknown_categories(self):
        rec = record("V-1", body_material="BRS")
        rec.category_id = "nope"
        assert len(build_index([[rec]], SCHEMAS)) == 0

    def test_ignores_attributes_outside_the_schema(self):
        rec = record("V-1")
        rec.values.append(AttributeValue(attribute="not_a_thing", raw="x"))
        assert len(build_index([[rec]], SCHEMAS)) == 0


class TestVerification:
    def test_stable_value_is_trusted(self):
        idx = index_from(
            [record("V-1", body_material="brass")], [record("V-1", body_material="brass")]
        )
        signal = EnsembleVerifier(idx).verify(
            AttributeValue(attribute="body_material", raw="brass"), SPEC, record("V-1")
        )
        assert signal.applicable
        assert signal.trust == 1.0

    def test_agreement_survives_differing_abbreviation(self):
        # "BRS" and "brass" are the same answer; counting them as disagreement would
        # measure formatting.
        idx = index_from([record("V-1", body_material="BRS")])
        signal = EnsembleVerifier(idx).verify(
            AttributeValue(attribute="body_material", raw="brass"), SPEC, record("V-1")
        )
        assert signal.trust == 1.0

    def test_unstable_value_is_doubted(self):
        idx = index_from(
            [record("V-1", body_material="brass")], [record("V-1", body_material="PTFE")]
        )
        signal = EnsembleVerifier(idx).verify(
            AttributeValue(attribute="body_material", raw="carbon steel"), SPEC, record("V-1")
        )
        assert signal.applicable
        assert signal.trust < 0.5

    def test_never_hard_fails(self):
        # Instability is evidence of guessing, not proof of error - a model can be
        # unstable about a value that is right - so this must never bypass the
        # calibrated threshold.
        idx = index_from([record("V-1", body_material="zzzzzzzz")])
        signal = EnsembleVerifier(idx).verify(
            AttributeValue(attribute="body_material", raw="qqqqqqqq"), SPEC, record("V-1")
        )
        assert signal.trust >= MIN_TRUST
        assert signal.trust > 0.0

    def test_abstains_when_no_resample_exists(self):
        signal = EnsembleVerifier(EnsembleIndex()).verify(
            AttributeValue(attribute="body_material", raw="brass"), SPEC, record("V-1")
        )
        assert not signal.applicable

    def test_detail_names_what_the_others_said(self):
        idx = index_from([record("V-1", body_material="PTFE")])
        signal = EnsembleVerifier(idx).verify(
            AttributeValue(attribute="body_material", raw="brass"), SPEC, record("V-1")
        )
        assert "PTFE" in signal.detail

    @pytest.mark.parametrize("bad", ["", "   ", "?"])
    def test_never_raises(self, bad):
        idx = index_from([record("V-1", body_material="brass")])
        assert (
            EnsembleVerifier(idx).verify(
                AttributeValue(attribute="body_material", raw=bad), SPEC, record("V-1")
            )
            is not None
        )
