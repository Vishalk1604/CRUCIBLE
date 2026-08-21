"""Tests for model-based extraction.

Everything here runs against a fake client. The real model is slow, nondeterministic
across versions, and absent on CI, so tests that need it would either be skipped or
flaky - and the behaviour worth pinning down is not what the model says but how this
module handles what it says: the wrong-field quirk, empty responses, unparseable output,
and values that cannot be grounded.
"""

import json

import pytest

from crucible.extract.llm import (
    LLMExtractor,
    build_format,
    build_prompt,
    merge,
)
from crucible.extract.rules import ERP_DOC_ID
from crucible.extract.rules import extract as rule_extract
from crucible.ontology import get_schema
from crucible.schema import AttributeValue, ProductRecord, RawProduct

VALVE = "valve.ball"
DESCRIPTION = "1/2 SS BALL VLV 600WOG SCRD FP PTFE"


class FakeClient:
    """Stands in for the ollama module, returning a scripted response."""

    def __init__(self, content=None, thinking=None, raises=None):
        self.content = content
        self.thinking = thinking
        self.raises = raises
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return {"message": {"content": self.content or "", "thinking": self.thinking or ""}}


def raw(description=DESCRIPTION) -> RawProduct:
    return RawProduct(sku="V-1", description=description, category_id=VALVE)


def extractor(**kwargs) -> LLMExtractor:
    return LLMExtractor(client=FakeClient(**kwargs))


class TestRequestShape:
    def test_thinking_is_disabled(self):
        # Not a preference. With thinking on, the model exhausts its prediction budget
        # reasoning and returns empty content, so leaving it enabled breaks extraction
        # outright as well as making it 61x slower.
        ex = extractor(content="{}")
        ex.propose(raw(), get_schema(VALVE))
        assert ex._client.calls[0]["think"] is False

    def test_decoding_is_deterministic(self):
        ex = extractor(content="{}")
        ex.propose(raw(), get_schema(VALVE))
        assert ex._client.calls[0]["options"]["temperature"] == 0.0

    def test_format_constrains_to_schema_attributes(self):
        schema = get_schema(VALVE)
        fmt = build_format(schema)
        assert set(fmt["properties"]) == {s.name for s in schema.attributes}

    def test_no_attribute_is_required(self):
        # Requiring them would force the model to invent values to satisfy the grammar.
        assert build_format(get_schema(VALVE))["required"] == []

    def test_attributes_are_typed_as_strings(self):
        # Numeric typing would move unit handling into the decoder, where a failure is an
        # unrecoverable generation error instead of a value the verifiers can reject.
        fmt = build_format(get_schema(VALVE))
        assert all(p["type"] == "string" for p in fmt["properties"].values())

    def test_prompt_names_the_attributes_and_forbids_guessing(self):
        prompt = build_prompt(DESCRIPTION, get_schema(VALVE))
        assert "body_material" in prompt
        assert "Do not guess" in prompt
        assert DESCRIPTION in prompt


class TestResponseFieldQuirk:
    def test_reads_the_answer_from_content(self):
        ex = extractor(content=json.dumps({"body_material": "SS"}))
        values = ex.propose(raw(), get_schema(VALVE))
        assert [v.raw for v in values] == ["SS"]

    def test_reads_the_answer_from_thinking_when_content_is_empty(self):
        # This is the observed behaviour of qwen3-vl:8b with think=False: the JSON lands
        # in `thinking` and `content` is empty. Reading only `content` fails every call.
        ex = extractor(content="", thinking=json.dumps({"body_material": "SS"}))
        values = ex.propose(raw(), get_schema(VALVE))
        assert [v.raw for v in values] == ["SS"]

    def test_prefers_content_when_both_are_populated(self):
        ex = extractor(
            content=json.dumps({"body_material": "SS"}),
            thinking=json.dumps({"body_material": "BRS"}),
        )
        assert [v.raw for v in ex.propose(raw(), get_schema(VALVE))] == ["SS"]


class TestGrounding:
    def test_values_carry_a_span_into_the_description(self):
        ex = extractor(content=json.dumps({"body_material": "SS"}))
        value = ex.propose(raw(), get_schema(VALVE))[0]
        assert value.is_grounded
        span = value.spans[0]
        assert span.doc_id == ERP_DOC_ID
        assert DESCRIPTION[span.start : span.end] == span.quote

    def test_discards_values_absent_from_the_source(self):
        # A hallucinated value with a fabricated citation is worse than no value, and
        # cheaper to prevent here than to detect downstream.
        ex = extractor(content=json.dumps({"body_material": "titanium"}))
        assert ex.propose(raw(), get_schema(VALVE)) == []
        assert ex.stats.values_ungrounded == 1

    def test_tolerates_reformatting_of_a_present_value(self):
        # "600 WOG" for source text "600WOG" is correct and merely respaced; discarding
        # it would throw away a right answer over whitespace.
        ex = extractor(content=json.dumps({"pressure_rating": "600 WOG"}))
        values = ex.propose(raw(), get_schema(VALVE))
        assert len(values) == 1

    def test_ignores_attributes_outside_the_schema(self):
        ex = extractor(content=json.dumps({"invented_attribute": "SS"}))
        assert ex.propose(raw(), get_schema(VALVE)) == []

    def test_ignores_non_string_values(self):
        ex = extractor(content=json.dumps({"body_material": 42}))
        assert ex.propose(raw(), get_schema(VALVE)) == []

    def test_skips_empty_values(self):
        ex = extractor(content=json.dumps({"body_material": "   "}))
        assert ex.propose(raw(), get_schema(VALVE)) == []


class TestFailureHandling:
    def test_empty_response_yields_nothing_and_is_counted(self):
        ex = extractor(content="", thinking="")
        assert ex.propose(raw(), get_schema(VALVE)) == []
        assert ex.stats.empty_responses == 1

    def test_unparseable_response_yields_nothing_and_is_counted(self):
        ex = extractor(content="here you go: {broken")
        assert ex.propose(raw(), get_schema(VALVE)) == []
        assert ex.stats.parse_failures == 1

    def test_non_object_json_is_a_parse_failure(self):
        ex = extractor(content=json.dumps(["SS"]))
        assert ex.propose(raw(), get_schema(VALVE)) == []
        assert ex.stats.parse_failures == 1

    def test_transport_error_does_not_stop_a_catalog_run(self):
        # One unreachable call must not abort enrichment of every remaining SKU.
        ex = extractor(raises=ConnectionError("ollama down"))
        assert ex.propose(raw(), get_schema(VALVE)) == []

    def test_stats_summarise_a_run(self):
        ex = extractor(content=json.dumps({"body_material": "SS", "port_type": "nope"}))
        ex.propose(raw(), get_schema(VALVE))
        assert ex.stats.values_proposed == 2
        assert ex.stats.values_kept == 1
        assert "grounded" in ex.stats.summary()


class TestMerge:
    def test_rules_win_contested_attributes(self):
        # Rules hold the code tables; the model was measured returning unexpanded codes
        # where the tables return the full term.
        rules = rule_extract(raw())
        model = ProductRecord(
            raw=raw(),
            category_id=VALVE,
            values=[AttributeValue(attribute="body_material", raw="SS")],
        )
        merged = merge(rules, model)
        body = next(v for v in merged.values if v.attribute == "body_material")
        assert body.raw == "316 stainless steel"

    def test_model_fills_attributes_rules_missed(self):
        rules = ProductRecord(raw=raw(), category_id=VALVE, values=[])
        model = ProductRecord(
            raw=raw(),
            category_id=VALVE,
            values=[AttributeValue(attribute="body_material", raw="SS")],
        )
        assert len(merge(rules, model).values) == 1

    def test_merged_evidence_is_deduplicated(self):
        rules = rule_extract(raw())
        merged = merge(rules, rule_extract(raw()))
        assert len({d.doc_id for d in merged.evidence}) == len(merged.evidence)


@pytest.mark.parametrize("description", ["", "   ", "???"])
def test_degenerate_descriptions_do_not_raise(description):
    ex = extractor(content=json.dumps({"body_material": "SS"}))
    ex.propose(raw(description), get_schema(VALVE))
