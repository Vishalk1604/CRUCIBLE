"""Crucible routing stage: assigning a category to a product that arrived without one."""

from crucible.route.router import (
    MARGIN,
    SCORE_FLOOR,
    CascadeRouter,
    LexicalRouter,
    RouteCandidate,
    RouterStats,
)
from crucible.route.taxonomy import (
    GENERIC_CATEGORY_ID,
    TaxonomyError,
    TaxonomyNode,
    fingerprint,
    load_cached,
    load_taxonomy,
)

__all__ = [
    "GENERIC_CATEGORY_ID",
    "MARGIN",
    "SCORE_FLOOR",
    "CascadeRouter",
    "LexicalRouter",
    "RouteCandidate",
    "RouterStats",
    "TaxonomyError",
    "TaxonomyNode",
    "fingerprint",
    "load_cached",
    "load_taxonomy",
]
