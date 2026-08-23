"""Assigning a category to a product that arrived without one.

The input has no category column. Everything downstream needs one: the schema decides
which attributes are even asked for, the constraint verifier needs declared relationships,
and four of the delivery sheet's columns are the classification itself. So routing is not
a preprocessing convenience, it is the step that determines how much of the rest of the
system can function.

The cascade, and why it is shaped like the extractor
---------------------------------------------------
Lexical first, then embedding, then the model, then refusal - the same ordering, for the
same reasons, as `extract/rules.py` before `extract/llm.py`. Pattern matching is free,
deterministic, auditable, and on this catalog it is also *better* than the model on the
cases it covers, because "1x6-20' Weathered Teak Grooved" is a format, not a sentence.
The model earns its place only on the residue that has no format.

Refusal is a routing outcome
----------------------------
When no tier is confident the product routes to `generic`, which leaves Dept, Class, Fine
and Classpath blank. That is deliberate and it is the same principle as everywhere else
in this system: a guessed department is worse than an absent one, because a guess is
indistinguishable from knowledge once it is in a spreadsheet. Generic rows still export,
still extract, and still certify - they simply certify worse, because two of the four
verifiers have no schema to check against and correctly abstain.

What the scores mean
--------------------
A node scores by summing evidence: decisive terms, supporting terms, and format patterns,
minus a penalty for terms that rule it out. Two thresholds then decide whether the winner
is trusted: an absolute floor (did anything match at all) and a margin (did it beat the
runner-up clearly). The margin matters more than the floor here - "Milw M12 Heated Hoodie
Kit" matches both the cordless-tool and heated-apparel nodes strongly, and a system that
silently picks the higher of two near-equal scores is guessing while reporting certainty.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from crucible.ingest.csv_source import erp_text
from crucible.route.taxonomy import (
    GENERIC_CATEGORY_ID,
    KEYWORD_WEIGHT,
    NEGATIVE_PENALTY,
    PATTERN_WEIGHT,
    STRONG_WEIGHT,
    TaxonomyNode,
    load_cached,
)
from crucible.schema import RawProduct, Routing, SourceSpan

logger = logging.getLogger(__name__)

ROUTE_DOC_ID = "erp"

# A winner must clear this, or nothing matched meaningfully. One supporting keyword
# (weight 1.0) is not a classification; a decisive term or a pattern plus support is.
SCORE_FLOOR = 2.5

# ...and must beat the runner-up by this, or the two are not distinguishable on evidence.
MARGIN = 1.5


@dataclass(frozen=True)
class RouteCandidate:
    """One node's case for a product, with the evidence that made it."""

    category_id: str
    score: float
    method: str
    spans: list[SourceSpan] = field(default_factory=list)
    node: TaxonomyNode | None = None

    @property
    def matched(self) -> list[str]:
        return [s.quote for s in self.spans]


def _span(text: str, needle: str) -> SourceSpan | None:
    """Locate a matched term in the evidence text, as a citable span.

    Word-boundary anchored: without it "led" matches inside "assembled" and the routing
    cites a substring of an unrelated word as its reason.
    """
    match = re.search(rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])", text, re.IGNORECASE)
    if match is None:
        return None
    return SourceSpan(
        doc_id=ROUTE_DOC_ID,
        quote=text[match.start() : match.end()],
        start=match.start(),
        end=match.end(),
    )


class LexicalRouter:
    """Weighted term and pattern matching over the description."""

    method = "lexical"

    def __init__(self, nodes: Sequence[TaxonomyNode] | None = None) -> None:
        self.nodes = list(nodes) if nodes is not None else list(load_cached())

    def _score(self, node: TaxonomyNode, text: str) -> RouteCandidate:
        score = 0.0
        spans: list[SourceSpan] = []

        for term in node.negative:
            if _span(text, term) is not None:
                score -= NEGATIVE_PENALTY

        for term in node.strong:
            hit = _span(text, term)
            if hit is not None:
                score += STRONG_WEIGHT
                spans.append(hit)

        for term in node.keywords:
            hit = _span(text, term)
            if hit is not None:
                score += KEYWORD_WEIGHT
                spans.append(hit)

        for pattern in node.compiled:
            match = pattern.search(text)
            if match is not None:
                score += PATTERN_WEIGHT
                spans.append(
                    SourceSpan(
                        doc_id=ROUTE_DOC_ID,
                        quote=match.group(0),
                        start=match.start(),
                        end=match.end(),
                    )
                )

        return RouteCandidate(node.category_id, score, self.method, spans, node)

    def route(self, raw: RawProduct) -> list[RouteCandidate]:
        """Every node's score, best first. Non-positive scores are dropped."""
        text = erp_text(raw)
        scored = [self._score(node, text) for node in self.nodes]
        return sorted((c for c in scored if c.score > 0), key=lambda c: (-c.score, c.category_id))


@dataclass
class RouterStats:
    """Where routings came from. Reported rather than inferred from output."""

    total: int = 0
    by_method: dict[str, int] = field(default_factory=dict)
    by_category: dict[str, int] = field(default_factory=dict)
    ambiguous: int = 0

    def record(self, routing: Routing) -> None:
        self.total += 1
        self.by_method[routing.method] = self.by_method.get(routing.method, 0) + 1
        self.by_category[routing.category_id] = self.by_category.get(routing.category_id, 0) + 1

    @property
    def coverage(self) -> float:
        """Fraction routed to a real category rather than the generic fallback."""
        if not self.total:
            return 0.0
        return 1.0 - self.by_category.get(GENERIC_CATEGORY_ID, 0) / self.total

    def summary(self) -> str:
        generic = self.by_category.get(GENERIC_CATEGORY_ID, 0)
        return (
            f"{self.total} products, {self.coverage:.1%} classified, "
            f"{generic} generic, {self.ambiguous} ambiguous"
        )


class CascadeRouter:
    """Lexical, then optional escalation, then honest refusal."""

    def __init__(
        self,
        nodes: Sequence[TaxonomyNode] | None = None,
        taxonomy_path: Path | None = None,
        escalate: object | None = None,
        score_floor: float = SCORE_FLOOR,
        margin: float = MARGIN,
    ) -> None:
        if nodes is None:
            nodes = load_cached(taxonomy_path)
        self.nodes = list(nodes)
        self.lexical = LexicalRouter(self.nodes)
        self.escalate = escalate
        self.score_floor = score_floor
        self.margin = margin
        self.stats = RouterStats()

    def _generic(self, reason: str, candidates: Sequence[RouteCandidate]) -> Routing:
        return Routing(
            category_id=GENERIC_CATEGORY_ID,
            confidence=0.0,
            method=reason,
            runners_up=[(c.category_id, round(c.score, 2)) for c in candidates[:3]],
        )

    def _from_candidate(
        self, candidate: RouteCandidate, runners: Sequence[RouteCandidate]
    ) -> Routing:
        node = candidate.node
        assert node is not None  # candidates always carry their node
        top = candidate.score
        second = runners[0].score if runners else 0.0
        # Confidence blends "did it match strongly" with "did it beat the alternative".
        # Either alone is misleading: a huge score with a tied runner-up is a coin flip,
        # and a clear win on one weak keyword is not knowledge.
        margin_part = min(1.0, (top - second) / max(self.margin * 2, 1e-9))
        strength_part = min(1.0, top / (self.score_floor * 2))
        return Routing(
            category_id=node.category_id,
            dept=node.dept,
            klass=node.klass,
            fine=node.fine,
            classpath=node.classpath,
            unspsc=node.unspsc,
            confidence=round(0.5 * margin_part + 0.5 * strength_part, 3),
            method=candidate.method,
            spans=candidate.spans[:6],
            runners_up=[(c.category_id, round(c.score, 2)) for c in runners[:2]],
        )

    def route(self, raw: RawProduct) -> Routing:
        candidates = self.lexical.route(raw)

        if not candidates or candidates[0].score < self.score_floor:
            routing = self._generic("unmatched", candidates)
            self.stats.record(routing)
            return routing

        top, rest = candidates[0], candidates[1:]
        second = rest[0].score if rest else 0.0

        if top.score - second < self.margin:
            self.stats.ambiguous += 1
            resolved = self._resolve_ambiguity(raw, candidates)
            if resolved is None:
                routing = self._generic("ambiguous", candidates)
                self.stats.record(routing)
                return routing
            top = resolved
            rest = [c for c in candidates if c.category_id != top.category_id]

        routing = self._from_candidate(top, rest)
        self.stats.record(routing)
        return routing

    def _resolve_ambiguity(
        self, raw: RawProduct, candidates: Sequence[RouteCandidate]
    ) -> RouteCandidate | None:
        """Hand a tie to the next tier, if one is configured."""
        if self.escalate is None:
            return None
        try:
            return self.escalate.route(raw, candidates)  # type: ignore[attr-defined]
        except Exception:
            # An escalation failure must degrade to generic, never to a guess.
            logger.exception("escalation failed for %s", raw.sku)
            return None

    def route_all(self, raws: Sequence[RawProduct]) -> list[Routing]:
        return [self.route(raw) for raw in raws]
