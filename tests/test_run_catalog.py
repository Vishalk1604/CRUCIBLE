"""Tests for calibrating on the real catalog.

`run_catalog` is the one path in the project that needs a GPU to do anything useful, so
these tests cover its contract and its failure mode rather than its numbers. The numbers
belong in `docs/RESULTS.md`, produced by an actual run.
"""

from __future__ import annotations

import csv

import pytest

from crucible.pipeline import run_catalog

HEADER = ["Mfg_Part_Num", "Part_Desc", "E1_Brand", "Unilog_Brand", "DIB_Brand", "Part_Manuf"]
ROWS = [
    [
        "DCB518ASTS06G",
        'DCB518ASTS06G Diablo 1/2"x18" - Sanding Belt 6pc',
        "-- Unbranded --",
        "",
        "Diablo",
        "Freud Inc (2435)",
    ],
    ["S21354", "S21354 8W Led T9 Med 27k", "-- Unbranded --", "", "Satco", "Satco Prod Inc (5573)"],
    [
        "2563P-20",
        '2563P-20 Milw M12 1/2" Stubby - Impact Wrench',
        "-- Unbranded --",
        "",
        "",
        "Milwaukee Accessory (4031)",
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


class TestFailureMode:
    def test_refuses_clearly_when_nothing_is_extractable(self, catalog):
        # Rules-only on this domain produces nothing: the code tables are for valves,
        # bearings and screws. The error has to say so rather than surfacing as an
        # empty-sequence exception three frames deeper.
        with pytest.raises(RuntimeError, match="no scorable values"):
            run_catalog(catalog, use_llm=False)

    def test_the_message_names_both_likely_causes(self, catalog):
        with pytest.raises(RuntimeError) as excinfo:
            run_catalog(catalog, use_llm=False)
        message = str(excinfo.value)
        assert "use_llm" in message
        assert "schema" in message


class TestContract:
    def test_signature_defaults_are_conservative(self):
        import inspect

        params = inspect.signature(run_catalog).parameters
        # alpha defaults higher than run()'s 2% because the pseudo-reference introduces
        # label noise; promising 2% off noisy labels would be the wrong default.
        assert params["alpha"].default == 0.05
        assert params["delta"].default == 0.05
        assert params["use_llm"].default is True

    def test_results_from_this_path_are_labelled_simulated(self):
        # Non-negotiable #6. The labels come from injected faults, so anything this
        # produces must announce itself as simulated wherever it is shown.
        source = run_catalog.__doc__ or ""
        assert "SIMULATED" in source or "simulated" in source
