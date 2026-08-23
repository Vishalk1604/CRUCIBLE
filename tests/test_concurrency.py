"""Tests for concurrent enrichment.

Concurrency is worth nothing if it changes the answer. These tests are almost entirely
about that: the same input must produce the same rows in the same order regardless of how
many products were in flight, because a delivery file whose row order depends on which HTTP
response returned first diffs differently on every run against identical input.

They run with `use_llm=False` so no GPU is needed - the concern here is the scheduling, not
the extraction.
"""

from __future__ import annotations

import csv

import pytest

from crucible.enrich import DEFAULT_CONCURRENCY, enrich

HEADER = ["Mfg_Part_Num", "Part_Desc", "E1_Brand", "Unilog_Brand", "DIB_Brand", "Part_Manuf"]
ROWS = [
    [
        "DCB518ASTS06G",
        'Diablo 1/2"x18" - Sanding Belt 6pc',
        "-- Unbranded --",
        "",
        "Diablo",
        "Freud Inc (2435)",
    ],
    [
        "49-94-0013",
        'Milw 5"x.045"x7/8" Metal Cut Off Disc',
        "-- Unbranded --",
        "",
        "",
        "Milwaukee (4031)",
    ],
    ["S21354", "8W Led T9 Med 27k", "-- Unbranded --", "", "Satco", "Satco Prod Inc (5573)"],
    [
        "KDTS324SPS",
        "Kitchen Aid Dishwasher SS",
        "-- Unbranded --",
        "",
        "",
        "Appliance Dealers (APPDE)",
    ],
    [
        "1x6-20",
        "Weathered Teak Grooved - Vintage Azek PVC Decking",
        "TREX",
        "",
        "",
        "Parksite (6151)",
    ],
    [
        "2563P-20",
        'Milw M12 1/2" Stubby - Impact Wrench',
        "-- Unbranded --",
        "",
        "",
        "Milwaukee (4031)",
    ],
    ["65-771R3", "Nuvo Highbay Light", "-- Unbranded --", "", "", "Satco Prod Inc (5573)"],
    [
        "M200G-21L",
        "Milw M12 Gray - Heated Hoodie Kit L",
        "-- Unbranded --",
        "",
        "",
        "Milwaukee (4031)",
    ],
]


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "input.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(ROWS)
    return path


def skus(result):
    return [record.sku for record in result.records]


class TestOrderIsPreserved:
    """The property that makes concurrency safe to ship."""

    @pytest.mark.parametrize("workers", [1, 2, 4, 8, 16])
    def test_output_order_matches_input_order(self, catalog, workers):
        result = enrich(catalog, use_llm=False, concurrency=workers)
        assert skus(result) == [row[0] for row in ROWS]

    def test_concurrent_output_is_identical_to_sequential(self, catalog):
        sequential = enrich(catalog, use_llm=False, concurrency=1)
        parallel = enrich(catalog, use_llm=False, concurrency=8)

        assert skus(sequential) == skus(parallel)
        assert [r.as_dict() for r in sequential.rows] == [r.as_dict() for r in parallel.rows]

    def test_more_workers_than_products_is_harmless(self, catalog):
        result = enrich(catalog, use_llm=False, concurrency=64)
        assert len(result.rows) == len(ROWS)


class TestCallbacks:
    def test_rows_are_streamed_in_order(self, catalog):
        seen: list[str] = []
        enrich(
            catalog,
            use_llm=False,
            concurrency=4,
            on_row=lambda row, record: seen.append(record.sku),
        )
        assert seen == [row[0] for row in ROWS]

    def test_progress_counts_up_without_gaps_or_repeats(self, catalog):
        counts: list[int] = []
        enrich(
            catalog,
            use_llm=False,
            concurrency=4,
            progress=lambda index, total, sku: counts.append(index),
        )
        assert counts == list(range(1, len(ROWS) + 1))

    def test_progress_total_is_the_whole_catalog(self, catalog):
        totals: set[int] = set()
        enrich(
            catalog,
            use_llm=False,
            concurrency=4,
            progress=lambda index, total, sku: totals.add(total),
        )
        assert totals == {len(ROWS)}


class TestStats:
    @pytest.mark.parametrize("workers", [1, 4])
    def test_counters_agree_across_concurrency(self, catalog, workers):
        result = enrich(catalog, use_llm=False, concurrency=workers)
        assert result.stats.products == len(ROWS)
        assert result.stats.routed + result.stats.generic == len(ROWS)

    def test_shared_counters_are_not_lost_to_races(self, catalog):
        # Every product must be counted exactly once even with heavy contention.
        result = enrich(catalog, use_llm=False, concurrency=16)
        assert result.stats.products == len(result.rows) == len(ROWS)


class TestDefaults:
    def test_default_concurrency_is_modest(self):
        # An 8 GB card holding a 6.2 GB model has little room; the default should overlap
        # I/O without assuming the server will grant many parallel slots.
        assert 1 < DEFAULT_CONCURRENCY <= 8

    def test_single_worker_still_works(self, catalog):
        result = enrich(catalog, use_llm=False, concurrency=1)
        assert len(result.rows) == len(ROWS)
