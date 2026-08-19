"""Tests for the cross-attribute constraint verifier.

Two things are being tested: that jointly-impossible records are caught, and that the
expression evaluator cannot be turned into an execution vector. Category schemas are
data, and data that arrives from outside must never be executable.
"""

import pytest

from crucible.assay.constraints import (
    ConstraintError,
    ConstraintVerifier,
    build_environment,
    parse_constraint,
    referenced_attributes,
)
from crucible.schema import (
    AttributeSpec,
    AttributeValue,
    CategorySchema,
    ProductRecord,
    RawProduct,
    ValueKind,
)


def length(name: str) -> AttributeSpec:
    return AttributeSpec(
        name=name, kind=ValueKind.QUANTITY, dimension="[length]", canonical_unit="millimeter"
    )


VALVE_SCHEMA = CategorySchema(
    category_id="valve.ball",
    name="Ball Valves",
    attributes=[length("bore"), length("body_diameter"), length("face_to_face")],
    constraints=["bore <= body_diameter", "body_diameter <= face_to_face"],
)


def valve(**raw_values: str) -> ProductRecord:
    return ProductRecord(
        raw=RawProduct(sku="V-1", description="1/2 SS BALL VLV 600WOG SCRD"),
        category_id="valve.ball",
        values=[AttributeValue(attribute=k, raw=v) for k, v in raw_values.items()],
    )


VERIFIER = ConstraintVerifier(VALVE_SCHEMA)


def check(attribute: str, record: ProductRecord):
    spec = VALVE_SCHEMA.get(attribute)
    assert spec is not None
    return VERIFIER.verify(record.value_for(attribute), spec, record)


class TestViolations:
    def test_catches_the_jointly_impossible_record(self):
        # The demo case: every value is individually plausible, the record is not.
        signal = check("bore", valve(bore="200 mm", body_diameter="15 mm"))
        assert signal.is_hard_failure
        assert "bore <= body_diameter" in signal.detail

    def test_reports_the_actual_numbers_not_just_the_rule(self):
        # A reviewer can act on "bore=200 mm, body_diameter=15 mm". They cannot act on
        # the name of a rule alone.
        signal = check("bore", valve(bore="200 mm", body_diameter="15 mm"))
        assert "bore=200 mm" in signal.detail
        assert "body_diameter=15 mm" in signal.detail

    def test_compares_across_units_after_normalization(self):
        # 1/2 inch is 12.7 mm, so this record is fine despite the larger-looking number.
        # Comparing raw magnitudes instead of normalized ones would flag it wrongly.
        signal = check("bore", valve(bore='1/2"', body_diameter="15 mm"))
        assert signal.trust == 1.0

    def test_catches_violation_that_only_appears_after_conversion(self):
        # 2 inches is 50.8 mm, which exceeds a 15 mm body. Raw-magnitude comparison
        # (2 <= 15) would wave this through.
        signal = check("bore", valve(bore='2"', body_diameter="15 mm"))
        assert signal.is_hard_failure


class TestSatisfied:
    def test_passes_a_coherent_record(self):
        signal = check("bore", valve(bore="10 mm", body_diameter="15 mm", face_to_face="60 mm"))
        assert signal.trust == 1.0
        assert "hold" in signal.detail


class TestAbstention:
    def test_abstains_when_no_constraint_mentions_the_attribute(self):
        schema = CategorySchema(
            category_id="c", name="C", attributes=[length("weight")], constraints=[]
        )
        record = ProductRecord(
            raw=RawProduct(sku="X", description="x"),
            values=[AttributeValue(attribute="weight", raw="2 mm")],
        )
        signal = ConstraintVerifier(schema).verify(
            record.value_for("weight"), schema.get("weight"), record
        )
        assert signal.applicable is False

    def test_abstains_when_co_referenced_attribute_is_missing(self):
        # A constraint cannot be evidence against a value when half its inputs are
        # unknown. Failing here would punish incomplete extraction as if it were error.
        signal = check("bore", valve(bore="200 mm"))
        assert signal.applicable is False
        assert signal.is_hard_failure is False

    def test_doubts_when_only_some_constraints_could_be_evaluated(self):
        # bore <= body_diameter holds; body_diameter <= face_to_face cannot be checked.
        signal = check("body_diameter", valve(bore="10 mm", body_diameter="15 mm"))
        assert 0.0 < signal.trust < 1.0
        assert signal.applicable


class TestExpressionSafety:
    @pytest.mark.parametrize(
        "expression",
        [
            "__import__('os').system('echo pwned')",
            "open('/etc/passwd').read()",
            "().__class__.__bases__",
            "[x for x in range(10)]",
            "lambda: 1",
        ],
    )
    def test_rejects_anything_that_is_not_arithmetic(self, expression):
        with pytest.raises(ConstraintError):
            parse_constraint(expression)

    def test_rejects_arbitrary_function_calls(self):
        with pytest.raises(ConstraintError, match="constraints are data, not code"):
            parse_constraint("eval('1+1') > 0")

    def test_allows_the_small_whitelist_of_maths_functions(self):
        assert parse_constraint("abs(bore - body_diameter) < 5") is not None
        assert parse_constraint("max(bore, 1) <= body_diameter") is not None

    def test_rejects_malformed_syntax(self):
        with pytest.raises(ConstraintError, match="cannot parse"):
            parse_constraint("bore <= ")


class TestExpressionParsing:
    def test_extracts_referenced_attributes(self):
        assert referenced_attributes("bore <= body_diameter") == {"bore", "body_diameter"}

    def test_ignores_function_names(self):
        assert referenced_attributes("abs(bore - 1) < body_diameter") == {"bore", "body_diameter"}

    def test_supports_chained_and_boolean_forms(self):
        assert parse_constraint("0 < bore <= body_diameter") is not None
        assert parse_constraint("bore > 0 and body_diameter > 0") is not None


class TestEnvironment:
    def test_normalizes_values_into_canonical_units(self):
        env, display = build_environment(valve(bore='1/2"', body_diameter="15 mm"), VALVE_SCHEMA)
        assert env["bore"] == pytest.approx(12.7)
        assert env["body_diameter"] == pytest.approx(15.0)
        assert display["bore"] == "12.7 mm"

    def test_omits_values_that_cannot_be_normalized(self):
        env, _ = build_environment(valve(bore="see chart"), VALVE_SCHEMA)
        assert "bore" not in env

    def test_ignores_attributes_absent_from_the_schema(self):
        record = ProductRecord(
            raw=RawProduct(sku="V-1", description="x"),
            values=[AttributeValue(attribute="not_in_schema", raw="5 mm")],
        )
        env, _ = build_environment(record, VALVE_SCHEMA)
        assert env == {}


class TestRobustness:
    def test_never_raises_on_a_malformed_schema_constraint(self):
        schema = CategorySchema(
            category_id="c",
            name="C",
            attributes=[length("bore")],
            constraints=["bore <= "],  # malformed on purpose
        )
        record = ProductRecord(
            raw=RawProduct(sku="X", description="x"),
            values=[AttributeValue(attribute="bore", raw="5 mm")],
        )
        signal = ConstraintVerifier(schema).verify(
            record.value_for("bore"), schema.get("bore"), record
        )
        assert 0.0 <= signal.trust <= 1.0
