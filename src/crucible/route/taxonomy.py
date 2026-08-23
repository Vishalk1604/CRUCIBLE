"""Loading the classification taxonomy.

Validated at load for the same reason `ontology.py` validates constraints at load: the
failure mode is silence. A node whose regex does not compile, or whose `category_id`
matches no schema, does not crash anything - it simply never wins, or wins and then
routes into a schema that does not exist. Either way the catalog still exports, still
looks complete, and is wrong in a way that surfaces only when someone checks a column
nobody checks.

The taxonomy is separate from the ontology on purpose. A `CategorySchema` says what
attributes a ball valve has; a `TaxonomyNode` says how to recognise one and where it sits
in the merchandising hierarchy. Distributors reorganise their hierarchy far more often
than physics reorganises valves, and keeping the two apart means a reclassification does
not invalidate an extraction cache.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

TAXONOMY_DIR = Path(__file__).resolve().parents[3] / "data" / "taxonomy"
DEFAULT_TAXONOMY = TAXONOMY_DIR / "unilog.yaml"

# The routing outcome for a product nothing recognised. Not a category: a declaration
# that no category was established, which is why it carries no dept/klass/fine.
GENERIC_CATEGORY_ID = "generic"

STRONG_WEIGHT = 2.5
KEYWORD_WEIGHT = 1.0
PATTERN_WEIGHT = 2.0
NEGATIVE_PENALTY = 4.0


class TaxonomyError(ValueError):
    """Raised when a taxonomy file cannot be trusted to route with."""


class TaxonomyNode(BaseModel):
    """One classification target: how to recognise it, and what it is called."""

    category_id: str
    dept: str
    klass: str
    fine: str
    classpath: str
    unspsc: str | None = None
    strong: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    negative: list[str] = Field(default_factory=list)

    @field_validator("patterns")
    @classmethod
    def _patterns_compile(cls, value: list[str]) -> list[str]:
        for pattern in value:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"pattern {pattern!r} does not compile: {exc}") from exc
        return value

    @property
    def compiled(self) -> list[re.Pattern[str]]:
        return [re.compile(p, re.IGNORECASE) for p in self.patterns]

    def terms(self) -> set[str]:
        """Every literal term, for embedding text and for duplicate detection."""
        return {t.casefold() for t in (*self.strong, *self.keywords)}


def _validate(nodes: list[TaxonomyNode], source: str) -> None:
    seen: set[str] = set()
    for node in nodes:
        if node.category_id in seen:
            raise TaxonomyError(f"{source}: duplicate category_id {node.category_id!r}")
        seen.add(node.category_id)

        if node.category_id == GENERIC_CATEGORY_ID:
            raise TaxonomyError(
                f"{source}: {GENERIC_CATEGORY_ID!r} is the fallback and cannot be a node"
            )
        if not (node.strong or node.keywords or node.patterns):
            # A node with no evidence can never win, so it is dead configuration that
            # reads as coverage. Refuse it rather than let it inflate the taxonomy.
            raise TaxonomyError(f"{source}: {node.category_id!r} declares no matching terms")

        overlap = {t.casefold() for t in node.negative} & node.terms()
        if overlap:
            raise TaxonomyError(
                f"{source}: {node.category_id!r} lists {sorted(overlap)} as both "
                "evidence and disqualifier"
            )


def load_taxonomy(path: Path | None = None) -> list[TaxonomyNode]:
    """Read and validate a taxonomy file."""
    target = path or DEFAULT_TAXONOMY
    if not target.exists():
        raise TaxonomyError(f"taxonomy file not found: {target}")

    try:
        raw: Any = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TaxonomyError(f"{target.name}: {exc}") from exc

    if not isinstance(raw, list) or not raw:
        raise TaxonomyError(f"{target.name}: expected a non-empty list of nodes")

    nodes: list[TaxonomyNode] = []
    for entry in raw:
        try:
            nodes.append(TaxonomyNode.model_validate(entry))
        except Exception as exc:
            got = entry.get("category_id", "<unnamed>") if isinstance(entry, dict) else "<invalid>"
            raise TaxonomyError(f"{target.name}: node {got!r}: {exc}") from exc

    _validate(nodes, target.name)
    return nodes


@lru_cache(maxsize=4)
def load_cached(path: Path | None = None) -> tuple[TaxonomyNode, ...]:
    """Cached load, keyed by path rather than size-1, so tests can alternate files."""
    return tuple(load_taxonomy(path))


def fingerprint(nodes: tuple[TaxonomyNode, ...] | list[TaxonomyNode]) -> str:
    """Stable digest of the routing rules, for cache keys on derived artifacts."""
    blob = "\x00".join(
        node.model_dump_json() for node in sorted(nodes, key=lambda n: n.category_id)
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
