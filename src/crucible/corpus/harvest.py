"""Running the real extractor over the corpus, once, and keeping the result.

Everything the pipeline reports so far rests on injected faults. That was necessary -
the rule extractor is circular against this corpus and produces no errors to calibrate
against - but it means no number yet describes a system rather than a scenario.

The model produces real errors. On the first three valves it read a pressure rating as a
nominal size and put a port code in the bore field, unprompted. Those are the attribute
swaps the fault injector was imitating, arriving on their own, which makes them a
legitimate basis for calibration in a way injected faults never were.

Why this is a separate module with a cache
------------------------------------------
At roughly 1.8 seconds per SKU a 1500-product corpus is about forty-five minutes. That is
fine once and intolerable per test run, per ablation, per threshold sweep. So extraction
is separated from everything downstream and written to disk, letting the expensive step
happen once while calibration, fusion and certification iterate freely against the
result.

The cache is keyed on everything that changes the output - model, prompt, schema, corpus
seed and size. A stale cache silently scoring an old model's mistakes would be a
particularly bad failure here, because the numbers would look entirely plausible.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from crucible.corpus.generate import GoldRecord, generate_corpus
from crucible.extract.llm import EXTRACTOR_NAME, LLMExtractor, build_prompt, merge
from crucible.extract.rules import extract as rule_extract
from crucible.ontology import fingerprint, load_all
from crucible.schema import ProductRecord

logger = logging.getLogger(__name__)

DEFAULT_CACHE = Path("data/generated/harvest")


@dataclass
class Harvest:
    """Extracted records plus the answer key, ready for scoring."""

    records: list[ProductRecord]
    gold: dict[str, GoldRecord]
    model: str
    seconds: float
    cache_key: str
    from_cache: bool

    @property
    def n_values(self) -> int:
        return sum(len(r.values) for r in self.records)

    def summary(self) -> str:
        source = "cache" if self.from_cache else f"{self.seconds:.0f}s of inference"
        return (
            f"{len(self.records)} products, {self.n_values} values, "
            f"model={self.model}, from {source}"
        )


def _cache_key(
    model: str,
    n_per_category: int,
    seed: int,
    use_rules: bool,
    category_ids: Iterable[str] | None = None,
) -> str:
    """Fingerprint everything that changes the extraction.

    Includes the prompt text and schema fingerprints, not just the model name. Editing a
    prompt without invalidating the cache would score the old prompt's mistakes against
    the new one's expectations, and nothing in the output would look wrong.

    Scoped to the categories the corpus actually uses. Keying on every schema in the
    ontology looks more conservative but is worse: adding an unrelated category would
    invalidate a cache whose contents it cannot affect, and because the session is built
    inside the API lifespan, the next launch would silently spend ~25 minutes
    re-extracting before serving a request.
    """
    schemas = load_all()
    if category_ids is not None:
        wanted = set(category_ids)
        schemas = {k: v for k, v in schemas.items() if k in wanted}
    parts = [
        model,
        str(n_per_category),
        str(seed),
        str(use_rules),
        EXTRACTOR_NAME,
        *sorted(fingerprint(s) for s in schemas.values()),
        *sorted(build_prompt("", s) for s in schemas.values()),
    ]
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:16]


def _write(path: Path, records: list[ProductRecord], seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "seconds": seconds,
                "records": [r.model_dump(mode="json") for r in records],
            }
        ),
        encoding="utf-8",
    )


def _read(path: Path) -> tuple[list[ProductRecord], float] | None:
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        return [ProductRecord.model_validate(r) for r in blob["records"]], blob["seconds"]
    except Exception:
        # A corrupt cache should cost an extraction run, not a crash.
        logger.warning("ignoring unreadable harvest cache at %s", path)
        return None


def harvest(
    model: str = "qwen3-vl:8b",
    n_per_category: int = 200,
    seed: int = 20260820,
    use_rules: bool = True,
    cache_dir: Path | None = None,
    refresh: bool = False,
    progress_every: int = 25,
) -> Harvest:
    """Extract the corpus with the real model, caching the result.

    With `use_rules`, tier zero runs first and the model fills only what it left absent.
    That is the cascade as designed, and it also cuts inference cost, since most values
    on this corpus are resolvable by pattern matching.
    """
    corpus = generate_corpus(n_per_category, seed=seed)
    gold = {g.raw.sku: g for g in corpus}
    schemas = load_all()

    key = _cache_key(model, n_per_category, seed, use_rules, {g.category_id for g in corpus})
    path = (cache_dir or DEFAULT_CACHE) / f"{key}.json"

    if not refresh:
        cached = _read(path)
        if cached is not None:
            records, seconds = cached
            return Harvest(records, gold, model, seconds, key, from_cache=True)

    extractor = LLMExtractor(model=model)
    records: list[ProductRecord] = []
    started = time.monotonic()

    for i, entry in enumerate(corpus, start=1):
        schema = schemas.get(entry.category_id)
        if schema is None:
            continue

        proposed = extractor.extract(entry.raw, schema)
        records.append(merge(rule_extract(entry.raw), proposed) if use_rules else proposed)

        if progress_every and i % progress_every == 0:
            elapsed = time.monotonic() - started
            logger.info(
                "harvested %d/%d (%.1fs, %.2fs/product)", i, len(corpus), elapsed, elapsed / i
            )

    seconds = time.monotonic() - started
    _write(path, records, seconds)
    logger.info("harvest complete: %s", extractor.stats.summary())

    return Harvest(records, gold, model, seconds, key, from_cache=False)


def harvest_sample(
    sample_index: int,
    model: str = "qwen3-vl:8b",
    n_per_category: int = 200,
    seed: int = 20260820,
    temperature: float = 0.7,
    cache_dir: Path | None = None,
    refresh: bool = False,
    progress_every: int = 50,
) -> Harvest:
    """A second opinion: the same corpus extracted again under sampling.

    The ensemble verifier needs the model's answer to vary before its stability means
    anything. At temperature zero every pass is identical and agreement is trivially
    total, so these passes deliberately sample - what survives across them is what the
    model is confident about, and what changes is where it was guessing.

    Each sample gets its own decoding seed and its own cache entry, so passes are
    reproducible individually and can be built up incrementally rather than requiring
    all of them in one run.
    """
    corpus = generate_corpus(n_per_category, seed=seed)
    gold = {g.raw.sku: g for g in corpus}
    schemas = load_all()

    key = _cache_key(
        f"{model}@t{temperature}#s{sample_index}",
        n_per_category,
        seed,
        False,
        {g.category_id for g in corpus},
    )
    path = (cache_dir or DEFAULT_CACHE) / f"sample-{key}.json"

    if not refresh:
        cached = _read(path)
        if cached is not None:
            records, seconds = cached
            return Harvest(records, gold, model, seconds, key, from_cache=True)

    extractor = LLMExtractor(
        model=model,
        options={"temperature": temperature, "seed": seed + sample_index},
    )
    records: list[ProductRecord] = []
    started = time.monotonic()

    for i, entry in enumerate(corpus, start=1):
        schema = schemas.get(entry.category_id)
        if schema is None:
            continue
        records.append(extractor.extract(entry.raw, schema))
        if progress_every and i % progress_every == 0:
            elapsed = time.monotonic() - started
            logger.info("sample %d: %d/%d (%.1fs)", sample_index, i, len(corpus), elapsed)

    seconds = time.monotonic() - started
    _write(path, records, seconds)
    logger.info("sample %d complete: %s", sample_index, extractor.stats.summary())

    return Harvest(records, gold, model, seconds, key, from_cache=False)
