"""Tests for the evaluation harness.

The most important tests here are the ones about *reporting*, not about arithmetic. Two
labelled rows can produce a percentage as readily as two hundred can, and a percentage
detached from its sample size is how a worked example gets quoted as a rate.
"""

from __future__ import annotations

import csv

import pytest

from crucible.emit.rows import DeliveryRow, EmittedCell, Provenance
from crucible.evaluate import (
    INDICATIVE_MINIMUM,
    SCORED_COLUMNS,
    Accuracy,
    Compliance,
    VocabularyReport,
    check_limits,
    check_vocabulary,
    compare_rows,
    format_report,
    load_truth,
    normalise,
    truth_as_input,
)
from crucible.ontology import get_schema
from crucible.schema import AttributeValue, ProductRecord, RawProduct, SourceSpan


def row(**cells) -> DeliveryRow:
    delivery = DeliveryRow(sku=cells.get("Mfg_Part_Num", "X"))
    for column, value in cells.items():
        delivery.cells[column] = EmittedCell(
            column=column, value=value, provenance=Provenance.DERIVED
        )
    return delivery


class TestNormalisation:
    def test_folds_case_whitespace_and_trademark_symbols(self):
        assert normalise("FRIGIDAIRE®  Series") == normalise("frigidaire series")

    def test_does_not_fold_a_fraction_into_a_decimal(self):
        # The guide is explicit that fractions are the required form, so 50-1/4 and 50.25
        # are a real disagreement rather than a formatting difference.
        assert normalise("50-1/4 in") != normalise("50.25 in")


class TestAccuracy:
    def test_exact_and_normalised_are_reported_separately(self):
        ours = [row(Mfg_Part_Num="ABC", BRAND_NAME="frigidaire")]
        truth = [{"Mfg_Part_Num": "ABC", "BRAND_NAME": "FRIGIDAIRE®"}]
        accuracy = compare_rows(ours, truth)

        assert accuracy.fields["Mfg_Part_Num"].exact == 1
        assert accuracy.fields["BRAND_NAME"].exact == 0
        assert accuracy.fields["BRAND_NAME"].normalised == 1

    def test_a_blank_in_the_answer_key_is_not_scored(self):
        # The delivery sheet leaves cells blank on purpose; scoring ourselves against a
        # blank would reward filling it.
        accuracy = compare_rows([row(BRAND_NAME="Anything")], [{"BRAND_NAME": ""}])
        assert accuracy.fields["BRAND_NAME"].compared == 0
        assert accuracy.fields["BRAND_NAME"].truth_blank == 1

    def test_our_blank_against_a_real_answer_counts_as_a_miss(self):
        accuracy = compare_rows([row()], [{"BRAND_NAME": "FRIGIDAIRE"}])
        assert accuracy.fields["BRAND_NAME"].compared == 1
        assert accuracy.fields["BRAND_NAME"].exact == 0
        assert accuracy.fields["BRAND_NAME"].we_left_blank == 1

    def test_every_scored_column_is_a_real_delivery_column(self):
        from crucible.emit.columns import DELIVERY_COLUMNS

        for column in SCORED_COLUMNS:
            assert column in DELIVERY_COLUMNS


class TestSampleSizeHonesty:
    """A percentage from two rows must not be presentable as a rate."""

    def test_small_samples_are_flagged_as_not_indicative(self):
        assert not Accuracy(n_rows=2).is_indicative
        assert not Accuracy(n_rows=INDICATIVE_MINIMUM - 1).is_indicative
        assert Accuracy(n_rows=INDICATIVE_MINIMUM).is_indicative

    def test_a_small_sample_carries_a_caveat(self):
        caveat = Accuracy(n_rows=2).caveat()
        assert "2 labelled row" in caveat
        assert "rather than rates" in caveat

    def test_an_adequate_sample_needs_no_caveat(self):
        assert Accuracy(n_rows=200).caveat() == ""

    def test_the_report_prints_the_sample_size_next_to_the_percentage(self):
        ours = [row(Mfg_Part_Num="ABC")]
        accuracy = compare_rows(ours, [{"Mfg_Part_Num": "ABC"}])
        text = "\n".join(format_report(accuracy, Compliance(), VocabularyReport()))
        assert "over 1 labelled row(s)" in text
        assert "!" in text  # the caveat is rendered, not merely available


class TestLimits:
    def test_invoice_over_forty_characters_fails(self):
        long_line = "D" * 41
        report = check_limits([row(INVOICE_DESC=long_line)])
        assert report.rate("INVOICE_DESC <= 40 chars") == 0.0

    def test_invoice_must_be_upper_case(self):
        report = check_limits([row(INVOICE_DESC="dishwasher 120v")])
        assert report.rate("INVOICE_DESC upper case") == 0.0

    def test_mobile_outside_its_band_fails(self):
        report = check_limits([row(MOBILE_DESC="too short")])
        assert report.rate("MOBILE_DESC 60-80 chars") == 0.0

    def test_a_closed_up_unit_outside_the_receipt_line_fails(self):
        # House rule from the guide: "24 in", never "24in" - except on INVOICE_DESC.
        report = check_limits([row(SHORT_DESC="Board 24in Wide")])
        assert report.rate("SHORT_DESC spaces its units") == 0.0

    def test_a_spaced_unit_passes(self):
        report = check_limits([row(SHORT_DESC="Board 24 in Wide")])
        assert report.rate("SHORT_DESC spaces its units") == 1.0

    def test_empty_fields_are_not_counted_as_failures(self):
        # A product with no description should not drag compliance down; it simply has
        # nothing to comply with.
        assert check_limits([row()]).checks == {}


class TestVocabulary:
    def make_record(self, attribute: str, raw: str):
        value = AttributeValue(
            attribute=attribute,
            raw=raw,
            spans=[SourceSpan(doc_id="erp", quote=raw, start=0, end=len(raw))],
        )
        return ProductRecord(
            raw=RawProduct(sku="S", description=raw, category_id="abrasive.cutoff_disc"),
            category_id="abrasive.cutoff_disc",
            values=[value],
        )

    @pytest.fixture
    def schemas(self):
        return {"abrasive.cutoff_disc": get_schema("abrasive.cutoff_disc")}

    def test_a_declared_term_counts_as_in_vocabulary(self, schemas):
        report = check_vocabulary([self.make_record("material_application", "metal")], schemas)
        assert report.in_vocabulary == 1
        assert report.rate == 1.0

    def test_an_undeclared_term_is_counted_and_shown(self, schemas):
        report = check_vocabulary([self.make_record("material_application", "drywall")], schemas)
        assert report.out_of_vocabulary == 1
        assert report.examples[0][2] == "drywall"

    def test_casing_is_not_treated_as_a_violation(self, schemas):
        report = check_vocabulary([self.make_record("material_application", "Metal")], schemas)
        assert report.in_vocabulary == 1

    def test_quantities_are_not_vocabulary_checked(self, schemas):
        report = check_vocabulary([self.make_record("disc_diameter", '5"')], schemas)
        assert report.total == 0

    def test_the_report_names_what_it_measured_against(self):
        text = "\n".join(format_report(Accuracy(), Compliance(), VocabularyReport()))
        # Never "LOV compliance": that would assert conformance to a standard never seen.
        assert "not the client's LOV" in text
        assert "LOV compliance" not in text


class TestLoading:
    def test_reads_enriched_rows_from_a_delivery_sheet(self, tmp_path):
        path = tmp_path / "truth.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Mfg_Part_Num", "Part_Desc", "SHORT_DESC"])
            writer.writerow(["A1", "widget", "A Widget"])
            writer.writerow(["", "", ""])  # trailing blank line
        rows = load_truth(path)
        assert len(rows) == 1
        assert rows[0]["SHORT_DESC"] == "A Widget"

    def test_rejects_a_file_that_is_not_a_delivery_sheet(self, tmp_path):
        path = tmp_path / "wrong.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="does not look like a delivery sheet"):
            load_truth(path)

    def test_input_columns_are_recovered_from_the_labelled_rows(self):
        truth = [{"Mfg_Part_Num": "A1", "Part_Desc": "widget", "SHORT_DESC": "ignored"}]
        recovered = truth_as_input(truth)
        assert recovered[0]["Mfg_Part_Num"] == "A1"
        assert "SHORT_DESC" not in recovered[0]

    def test_the_supplied_expected_output_sheet_loads(self):
        from pathlib import Path

        path = Path("Unihack_ Expected Output - Delivery Format.csv")
        if not path.exists():
            pytest.skip("delivery sheet not present")
        rows = load_truth(path)
        assert len(rows) >= 2
        assert rows[0]["Mfg_Part_Num"]
