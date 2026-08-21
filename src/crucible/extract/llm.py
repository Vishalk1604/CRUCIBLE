"""Model-based extraction: tier one of the cascade.

Tier zero handles what pattern matching can resolve. This handles the rest: descriptions
whose codes are absent from the tables, vendor-specific shorthand, and free text where
the structure has to be inferred rather than matched.

The model is a proposer, not an authority. Everything it returns goes to the verifiers on
exactly the same terms as a rule-extracted value, and a value it cannot ground in the
source is discarded here rather than passed on to be rejected later.

Three findings from bringing up qwen3-vl:8b, recorded because each one cost real time
------------------------------------------------------------------------------------
**Thinking mode must be off.** With it enabled the model spent 4048 tokens reasoning about
a six-field extraction, hit the prediction limit, and returned *empty content* - so it was
not merely slow, it was silently producing nothing. Disabling it took one extraction from
122.7s to 2.0s, a 61x difference that decides whether a 1500-SKU corpus takes fifty
minutes or fifty hours.

**The answer arrives in the wrong field.** With `think=False`, this model's template
routes the JSON into the response's `thinking` field and leaves `content` empty. Reading
only `content` yields an empty string and a JSON parse error on every call. So both fields
are checked, content first. This is a quirk of one model plus one Ollama version, not a
stable contract, which is why it is isolated in `_payload` rather than assumed throughout.

**It does not expand abbreviations.** Asked to expand trade shorthand, it returned `SS`,
`600WOG`, `SCRD` - the input codes unchanged. The rule extractor does this better because
it holds the actual code tables. That is the cascade ordering justifying itself: rules
first because they are better *and* cheaper on the cases they cover, model second for the
cases they do not.

Grounding
---------
The model is not asked for character offsets. Models are unreliable at reporting them and
a wrong offset is worse than none, since it fabricates provenance. Instead each returned
value is located in the source text directly, and a value that cannot be found is dropped.
That is strict - it discards correct inferences that paraphrase the source - but the
alternative is admitting values whose citation does not support them, which is the exact
failure the entailment verifier exists to catch and which is cheaper to prevent than to
detect.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from crucible.extract.rules import ERP_DOC_ID
from crucible.schema import (
    AttributeValue,
    CategorySchema,
    EvidenceDoc,
    EvidenceKind,
    ProductRecord,
    RawProduct,
    SourceSpan,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3-vl:8b"
EXTRACTOR_NAME = "llm-qwen3vl8b"

#: Deterministic decoding. Extraction has a right answer; sampling only adds variance for
#: the ensemble-disagreement verifier to then flag as uncertainty.
DEFAULT_OPTIONS = {"temperature": 0.0}


@dataclass
class ExtractionStats:
    """Per-run counters, kept because silent degradation is the failure mode here."""

    calls: int = 0
    empty_responses: int = 0
    parse_failures: int = 0
    values_proposed: int = 0
    values_ungrounded: int = 0
    seconds: float = 0.0

    @property
    def values_kept(self) -> int:
        return self.values_proposed - self.values_ungrounded

    def summary(self) -> str:
        return (
            f"{self.calls} calls in {self.seconds:.1f}s; "
            f"{self.values_kept}/{self.values_proposed} values grounded, "
            f"{self.empty_responses} empty, {self.parse_failures} unparseable"
        )


def build_format(schema: CategorySchema) -> dict[str, Any]:
    """JSON schema for grammar-constrained decoding.

    Every attribute is a string and none are required. Typing them as numbers would push
    unit handling into the decoder, where a failure is an unrecoverable generation error
    rather than a value the dimensional verifier can examine and reject. Leaving them
    optional lets the model omit what the text does not support, which is the behaviour
    we want - the alternative is forcing it to invent a value to satisfy the grammar.
    """
    return {
        "type": "object",
        "properties": {spec.name: {"type": "string"} for spec in schema.attributes},
        "required": [],
    }


def build_prompt(description: str, schema: CategorySchema) -> str:
    lines = [
        "Extract product attributes from this industrial distributor short description.",
        "Return only values the text supports. Omit any attribute the text does not state.",
        "Do not guess.",
        "",
        f"Category: {schema.name}",
        "Attributes:",
    ]
    for spec in schema.attributes:
        hint = f" ({spec.description})" if spec.description else ""
        lines.append(f"  - {spec.name}{hint}")
    lines += ["", f'Description: "{description}"']
    return "\n".join(lines)


def _payload(message: Any) -> str:
    """Pull the JSON out of a response message.

    Checks `content` first, then `thinking`. See the module docstring: with `think=False`
    this model routes its answer into `thinking` and leaves `content` empty, so reading
    only the documented field fails on every call.
    """
    for key in ("content", "thinking"):
        value = message.get(key) if hasattr(message, "get") else getattr(message, key, None)
        if value and value.strip():
            return value.strip()
    return ""


def _ground(attribute: str, raw_value: str, description: str) -> AttributeValue | None:
    """Locate a proposed value in the source, or discard it.

    Matching is case-insensitive on the whole value first, then on its longest token. The
    token fallback exists because a model returning `600 WOG` for the text `600WOG` is
    right about the value and merely reformatted it, and discarding that would throw away
    a correct extraction over whitespace.
    """
    if not raw_value or not raw_value.strip():
        return None

    value = raw_value.strip()
    haystack = description.lower()

    candidates = [value]
    tokens = sorted(value.split(), key=len, reverse=True)
    candidates.extend(t for t in tokens if len(t) >= 2)
    candidates.append(value.replace(" ", ""))

    for candidate in candidates:
        index = haystack.find(candidate.lower())
        if index >= 0:
            quote = description[index : index + len(candidate)]
            return AttributeValue(
                attribute=attribute,
                raw=value,
                spans=[
                    SourceSpan(
                        doc_id=ERP_DOC_ID,
                        quote=quote,
                        start=index,
                        end=index + len(candidate),
                    )
                ],
                proposer=EXTRACTOR_NAME,
            )
    return None


class LLMExtractor:
    """Extracts attributes with a local model served by Ollama."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        client: Any | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.options = {**DEFAULT_OPTIONS, **(options or {})}
        self._client = client
        self.stats = ExtractionStats()

    @property
    def client(self) -> Any:
        # Imported lazily so the package stays importable, and the test suite runnable,
        # on a machine with no Ollama installed.
        if self._client is None:
            import ollama

            self._client = ollama
        return self._client

    def propose(self, raw: RawProduct, schema: CategorySchema) -> list[AttributeValue]:
        """Ask the model for values, keeping only those groundable in the description."""
        import time

        started = time.monotonic()
        self.stats.calls += 1

        try:
            response = self.client.chat(
                model=self.model,
                messages=[{"role": "user", "content": build_prompt(raw.description, schema)}],
                format=build_format(schema),
                options=self.options,
                # See module docstring: 61x faster, and with it enabled the model
                # exhausts its budget reasoning and returns nothing at all.
                think=False,
            )
        except Exception:
            logger.exception("extraction call failed for %s", raw.sku)
            return []
        finally:
            self.stats.seconds += time.monotonic() - started

        payload = _payload(response["message"])
        if not payload:
            self.stats.empty_responses += 1
            logger.warning("empty response for %s", raw.sku)
            return []

        try:
            proposed = json.loads(payload)
        except json.JSONDecodeError:
            self.stats.parse_failures += 1
            logger.warning("unparseable response for %s: %r", raw.sku, payload[:120])
            return []

        if not isinstance(proposed, dict):
            self.stats.parse_failures += 1
            return []

        values: list[AttributeValue] = []
        for attribute, value in proposed.items():
            if schema.get(attribute) is None or not isinstance(value, str):
                continue
            self.stats.values_proposed += 1
            grounded = _ground(attribute, value, raw.description)
            if grounded is None:
                self.stats.values_ungrounded += 1
                continue
            values.append(grounded)

        return values

    def extract(self, raw: RawProduct, schema: CategorySchema) -> ProductRecord:
        return ProductRecord(
            raw=raw,
            category_id=schema.category_id,
            evidence=[
                EvidenceDoc(doc_id=ERP_DOC_ID, kind=EvidenceKind.ERP_RECORD, text=raw.description)
            ],
            values=self.propose(raw, schema),
        )


def merge(primary: ProductRecord, secondary: ProductRecord) -> ProductRecord:
    """Combine two extractions, preferring the first for any contested attribute.

    The cascade calls this with rules first. Rules win ties because on the codes they
    cover they are both more accurate and free, and because the model was measured
    returning unexpanded codes where the tables return the full term.
    """
    seen = {value.attribute for value in primary.values}
    merged = list(primary.values)
    merged.extend(v for v in secondary.values if v.attribute not in seen)

    evidence = {doc.doc_id: doc for doc in secondary.evidence}
    evidence.update({doc.doc_id: doc for doc in primary.evidence})

    return primary.model_copy(update={"values": merged, "evidence": list(evidence.values())})
