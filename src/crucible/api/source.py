"""Where a certification session gets its products.

The session was originally hardwired to the generated corpus, which was fine when the
generated corpus was the only thing there was. It is no longer: the submission is about a
real distributor catalog, and a dashboard that certifies ball valves while the input file
contains sanding belts is showing the machinery working on a domain nobody asked about.

Two sources, and the difference between them is the honest part
---------------------------------------------------------------
`SYNTHETIC_SOURCE` is the generated valve/bearing/fastener corpus. Its answer key is exact,
its attributes are quantity-heavy by construction, and the verifiers therefore apply to
nearly everything. It is where the machinery looks best - AUROC 0.992 - and it is a
different domain from the evaluation data.

`CatalogSource.from_csv` is the real thing: the actual input file, routed, extracted, and
labelled the only way unlabelled data can be. It is where the machinery looks worse -
AUROC 0.599 - because a real catalog is mostly nominal attributes and the signals are
genuinely weaker there.

Both are worth showing. Showing only the first would be a demo of the wrong catalog;
showing only the second would hide that the approach works when the evidence is there.
What must never happen is showing either without saying which one it is, which is why
`label` and `caveat` are required fields rather than optional decoration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from crucible.corpus.faults import inject_all
from crucible.corpus.generate import GoldRecord
from crucible.ontology import GENERIC_CATEGORY_ID, generic_schema, load_all
from crucible.schema import ProductRecord

logger = logging.getLogger(__name__)


@dataclass
class CatalogSource:
    """Records and their answer key, plus the provenance a viewer has to be told."""

    records: list[ProductRecord]
    gold: dict[str, GoldRecord]
    label: str
    caveat: str
    simulated: bool = True
    from_cache: bool = False
    n_products: int = 0
    faults: list = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "caveat": self.caveat,
            "simulated": self.simulated,
            "nProducts": self.n_products,
            "fromCache": self.from_cache,
        }

    @classmethod
    def from_csv(
        cls,
        path: Path,
        limit: int | None = 150,
        model: str = "qwen3-vl:8b",
        fault_rate: float = 0.12,
        seed: int = 20260820,
    ) -> CatalogSource:
        """Build a session source from the real input file.

        The labelling is the same construction `pipeline.run_catalog` uses, and carries the
        same caveat: the clean extraction becomes a pseudo-reference, and injected faults
        supply the error labels. The pseudo-reference contains the extractor's own
        mistakes, so a value it consistently misreads is labelled correct and any verifier
        that flags it is scored as a false alarm.

        That noise runs in one direction only - it deflates AUROC and inflates realised
        error. It cannot manufacture a guarantee that does not hold, which is the property
        that makes it acceptable to show at all.
        """
        from crucible.enrich import enrich

        logger.info("building catalog source from %s (limit=%s)", path.name, limit)
        result = enrich(path, limit=limit, model=model)

        schemas = dict(load_all())
        schemas[GENERIC_CATEGORY_ID] = generic_schema()

        gold = {
            record.sku: GoldRecord(
                raw=record.raw,
                truth={v.attribute: v.raw for v in record.values},
                category_id=record.category_id or GENERIC_CATEGORY_ID,
                clean_description=record.raw.description,
                recoverable={v.attribute for v in record.values},
            )
            for record in result.records
        }

        damaged, faults = inject_all(result.records, schemas, rate=fault_rate, seed=seed)

        return cls(
            records=damaged,
            gold=gold,
            label=f"Real catalog — {path.name}",
            caveat=(
                "Real distributor products, routed and extracted from the supplied input "
                "file. Error labels come from injected faults, not hand-checking, and the "
                "reference is the system's own clean extraction — so these figures are a "
                "conservative floor on what the verifiers can do, not a clean read."
            ),
            simulated=True,
            from_cache=False,
            n_products=result.stats.products,
            faults=faults,
        )


#: Marker for the default path. The session builds the synthetic corpus itself when no
#: source is supplied; this exists so the API can still say *which* catalog is on screen.
SYNTHETIC_SOURCE = CatalogSource(
    records=[],
    gold={},
    label="Generated corpus — ball valves, bearings, hex screws",
    # The banner already prints "SIMULATED"; the caveat says what that means here rather
    # than repeating the word.
    caveat=(
        "These products are generated, not real, and the error labels are "
        "injected rather than hand-checked. The domain is also not the one in the supplied "
        "input file: this corpus is quantity-heavy by construction, so the physical "
        "verifiers apply to nearly every value and the numbers are correspondingly better "
        "than the real catalog achieves."
    ),
    simulated=True,
)
