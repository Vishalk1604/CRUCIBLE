"""The catalog run: sparse input CSV in, delivery file out.

This is the spine the whole submission hangs from. Everything else in the project either
feeds it (ingest, routing, extraction, verification) or explains it (the sidecar, the
certificate, the dashboard).

    read_products -> route -> extract -> normalise -> assay -> build_row -> write

The stage that does not appear in that list is the interesting one. There is no step where
a missing value gets filled in to make the sheet look complete, because the delivery format
already specifies otherwise: its own reference rows carry a full label template with values
only where the data supports them.

Progress is reported per product rather than at the end. A 1000-row run is roughly 25
minutes of local inference, and a silent 25 minutes is indistinguishable from a hang.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crucible.assay.base import Verifier
from crucible.assay.constraints import ConstraintVerifier
from crucible.assay.dimensional import DimensionalVerifier
from crucible.assay.identity import IdentityVerifier
from crucible.assay.vocabulary import VocabularyVerifier
from crucible.emit.rows import DeliveryRow, EmitPolicy, build_row
from crucible.extract import llm as llm_extract
from crucible.extract import rules
from crucible.ingest import read_products
from crucible.normalize import normalise_record
from crucible.ontology import GENERIC_CATEGORY_ID, resolve
from crucible.route import CascadeRouter
from crucible.schema import CategorySchema, ProductRecord, RawProduct

logger = logging.getLogger(__name__)


class ExtractionUnavailable(RuntimeError):
    """Raised when the model could not be reached for enough of a run to trust it.

    Deliberately fatal. Every other failure in this pipeline degrades to a blank cell,
    which is the correct response to "we could not establish this value" - but an outage
    is not that. It produces identical blanks while meaning something completely
    different, and a delivery file cannot distinguish the two after the fact. So the run
    stops rather than shipping a sheet whose emptiness misrepresents itself.
    """


#: Verifier signals for one attribute: (verifier, trust, applicable, detail).
SignalRow = tuple[str, float, bool, str]


@dataclass
class EnrichStats:
    """What a run did, in the terms someone would ask about afterwards."""

    products: int = 0
    routed: int = 0
    generic: int = 0
    values_extracted: int = 0
    cells_populated: int = 0
    seconds: float = 0.0

    def summary(self) -> str:
        rate = self.seconds / self.products if self.products else 0.0
        return (
            f"{self.products} products in {self.seconds:.1f}s ({rate:.2f}s each); "
            f"{self.routed} routed to a category, {self.generic} generic; "
            f"{self.values_extracted} values extracted, {self.cells_populated} cells populated"
        )


@dataclass
class EnrichResult:
    """Everything a caller needs to write files and explain them."""

    rows: list[DeliveryRow] = field(default_factory=list)
    records: list[ProductRecord] = field(default_factory=list)
    signals: list[dict[str, list[SignalRow]]] = field(default_factory=list)
    stats: EnrichStats = field(default_factory=EnrichStats)

    def evidence_pairs(self) -> list[tuple[DeliveryRow, dict[str, list[SignalRow]]]]:
        return list(zip(self.rows, self.signals, strict=True))


def build_verifiers(schema: CategorySchema) -> list[Verifier]:
    """The verifiers that need no calibration and no second model.

    Deliberately the cheap two. Coherence needs a corpus to compare against and ensemble
    needs repeated sampling, so both belong to the calibrated path rather than to a plain
    export. Running an export should not require having first run a study.
    """
    return [
        DimensionalVerifier(),
        ConstraintVerifier(schema),
        IdentityVerifier(),
        VocabularyVerifier(),
    ]


def _assay_record(record: ProductRecord, schema: CategorySchema) -> dict[str, list[SignalRow]]:
    """Every verifier's opinion on every value, keyed by attribute."""
    verifiers = build_verifiers(schema)
    signals: dict[str, list[SignalRow]] = {}
    for value in record.values:
        spec = schema.get(value.attribute)
        if spec is None:
            continue
        rows: list[SignalRow] = []
        for verifier in verifiers:
            signal = verifier.verify(value, spec, record)
            rows.append((signal.verifier, signal.trust, signal.applicable, signal.detail))
        signals[value.attribute] = rows
    return signals


#: Products in flight at once. Extraction is one blocking HTTP call per product, so the
#: worker threads spend their lives waiting on a socket and the GIL is irrelevant here.
#:
#: Whether this actually helps depends on the *server*: Ollama serves a fixed number of
#: parallel slots (OLLAMA_NUM_PARALLEL), and on an 8 GB card holding a 6.2 GB model it may
#: well choose one. With one slot, concurrent requests queue and the wall clock is
#: unchanged - so this is measured rather than assumed, and `crucible bench` is the thing
#: that measures it.
DEFAULT_CONCURRENCY = 4


def _process_one(
    raw: RawProduct,
    router: CascadeRouter,
    extractor: Any,
    policy: EmitPolicy,
    lock: threading.Lock,
) -> tuple[DeliveryRow, ProductRecord, dict[str, list[SignalRow]]]:
    """The whole per-product pipeline. Pure with respect to shared state except for `lock`.

    `CascadeRouter` and `LLMExtractor` both keep mutable counters, so every touch of them
    is serialised. The lock is held only around the counter updates, never across the HTTP
    call - holding it there would serialise the one part of this that is worth overlapping.
    """
    with lock:
        routing = router.route(raw)
    schema = resolve(routing.category_id)

    record = rules.extract(raw, routing.category_id)
    if extractor is not None:
        # Rules first, model second: `merge` prefers the primary record for any contested
        # attribute, and on the codes the tables cover rules are both more accurate and free.
        proposed = extractor.propose(raw, schema)
        model_record = record.model_copy(update={"values": proposed})
        record = llm_extract.merge(record, model_record)

    record = record.model_copy(update={"routing": routing, "category_id": routing.category_id})
    record = normalise_record(record, schema)

    signals = _assay_record(record, schema)
    row = build_row(record, schema, policy)
    return row, record, signals


def enrich(
    input_path: Path,
    limit: int | None = None,
    policy: EmitPolicy | None = None,
    model: str | None = None,
    use_llm: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
    on_row: Callable[[DeliveryRow, ProductRecord], None] | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> EnrichResult:
    """Run the full catalog pipeline over an input CSV.

    Products are processed concurrently but **reported in input order**. A delivery file
    whose row order depended on which HTTP response came back first would diff differently
    on every run against identical input, which makes it useless for review.

    `use_llm=False` runs rules-only, which is fast and offline and produces almost nothing
    on this dataset - the rule extractor holds code tables for valves, bearings and screws,
    none of which are in a building-products catalog. It exists so the shape of a run can
    be exercised in a test without a GPU, not as a serious extraction path.
    """
    policy = policy or EmitPolicy()
    products = read_products(input_path, limit=limit)
    router = CascadeRouter()
    extractor = llm_extract.LLMExtractor(model=model) if use_llm and model else None
    if use_llm and extractor is None:
        extractor = llm_extract.LLMExtractor()

    result = EnrichResult()
    started = time.monotonic()
    lock = threading.Lock()
    workers = max(1, min(concurrency, len(products) or 1))

    def record_result(index: int, produced) -> None:
        """Fold one finished product into the result. Called under `lock`."""
        row, record, signals = produced
        result.rows.append(row)
        result.records.append(record)
        result.signals.append(signals)
        result.stats.products += 1
        result.stats.values_extracted += len(record.values)
        result.stats.cells_populated += row.populated
        if record.category_id == GENERIC_CATEGORY_ID:
            result.stats.generic += 1
        else:
            result.stats.routed += 1

    if workers == 1:
        for index, raw in enumerate(products, start=1):
            produced = _process_one(raw, router, extractor, policy, lock)
            record_result(index, produced)
            if on_row is not None:
                on_row(produced[0], produced[1])
            if progress is not None:
                progress(index, len(products), raw.sku)
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="enrich") as pool:
            futures = {
                pool.submit(_process_one, raw, router, extractor, policy, lock): position
                for position, raw in enumerate(products)
            }

            # Results arrive out of order; buffer them and release in input order so the
            # caller sees a stable sequence and the delivery file diffs cleanly.
            pending: dict[int, tuple] = {}
            next_out = 0
            done = 0

            for future in as_completed(futures):
                position = futures[future]
                try:
                    pending[position] = future.result()
                except Exception:
                    logger.exception("product %d failed", position)
                    pending[position] = None

                while next_out in pending:
                    produced = pending.pop(next_out)
                    next_out += 1
                    done += 1
                    if produced is None:
                        continue
                    with lock:
                        record_result(next_out, produced)
                    if on_row is not None:
                        on_row(produced[0], produced[1])
                    if progress is not None:
                        progress(done, len(products), produced[1].sku)

    result.stats.seconds = time.monotonic() - started

    if extractor is not None:
        _refuse_if_unreachable(extractor.stats)

    return result


#: Above this share of unreachable calls, the run is an outage rather than a result.
#: Not zero, because a single transient blip on a 1000-row catalog is survivable and
#: costs one product; not high, because the blanks are indistinguishable afterwards.
UNREACHABLE_LIMIT = 0.05


def _refuse_if_unreachable(stats: llm_extract.ExtractionStats) -> None:
    """Stop a run whose blanks would be an infrastructure fault wearing abstention."""
    if stats.calls == 0 or stats.transport_failures == 0:
        return
    share = stats.transport_failures / stats.calls
    if share <= UNREACHABLE_LIMIT:
        logger.warning(
            "%d of %d extraction calls could not reach the model; those products are "
            "under-populated in this export",
            stats.transport_failures,
            stats.calls,
        )
        return
    raise ExtractionUnavailable(
        f"{stats.transport_failures} of {stats.calls} extraction calls "
        f"({share:.0%}) never reached the model. The blank cells this run would produce "
        "would be an outage, not an assay, and nothing downstream could tell the "
        "difference. Check that Ollama is running (`ollama ps`) and re-run."
    )


def coverage_by_column(rows: Sequence[DeliveryRow]) -> dict[str, int]:
    """How many rows populated each column. The honest picture of an export."""
    counts: dict[str, int] = {}
    for row in rows:
        for column in row.cells:
            counts[column] = counts.get(column, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
