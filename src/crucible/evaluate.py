"""Measuring the output against the three things the client said judges look for.

> *"Field-level accuracy against the 200 known-good rows, character-limit compliance, and
> percentage of values found in the LOV are all simple, credible metrics. Judges will look
> for them."*

All three are implemented here, with one honest adjustment each for the data we actually
have.

**Field-level accuracy.** We were given 2 fully enriched rows, not 200. Two rows is a
worked example, not a sample: it can show that a field is built the right *way*, and it
cannot show how often that is true. Every report therefore prints `n` next to the
percentage, and `Accuracy.is_indicative` is False below ten rows so a caller cannot quietly
present it as a rate. A metric that hides its own sample size is worse than no metric.

**Character-limit compliance.** Fully measurable at any scale, because the limits are
properties of the format rather than of the answer key. This is the strongest of the three
for us: it runs over all 1,000 products and means exactly what it says.

**Vocabulary compliance.** The client's 161,000-row LOV was not published with the sample
pack, so this measures values against the *per-category controlled vocabularies this project
authors*, which is a different and weaker claim. It is reported under that name throughout.
Calling it "LOV compliance" would assert conformance to a standard we have never seen, and
the guide is explicit that inventing conformance is the one thing that scores zero.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from crucible.emit.compose import INVOICE_MAX, MOBILE_MAX, MOBILE_MIN
from crucible.emit.rows import DeliveryRow
from crucible.schema import CategorySchema, ProductRecord, ValueKind

#: Columns whose content this pipeline is designed to produce. Accuracy is reported over
#: these rather than over all 252, because scoring ourselves on distributor-internal SKUs
#: and manufacturer image filenames we deliberately never emit would measure the brief's
#: scope, not our output.
SCORED_COLUMNS: tuple[str, ...] = (
    "Mfg_Part_Num",
    "MANUFACTURER_PART_NUMBER",
    "Part_Desc",
    "BRAND_NAME",
    "MANUFACTURER_NAME",
    "Dept",
    "Class",
    "Fine",
    "Classpath",
    "Product Name",
    "SHORT_DESC",
    "RETAIL_DESC",
    "LONG_DESC1",
    "MOBILE_DESC",
    "INVOICE_DESC",
)

#: Below this many labelled rows, a percentage is an anecdote wearing a number.
INDICATIVE_MINIMUM = 10


@dataclass
class FieldScore:
    """How one column did across the labelled rows."""

    column: str
    compared: int = 0
    exact: int = 0
    normalised: int = 0
    we_left_blank: int = 0
    truth_blank: int = 0

    @property
    def exact_rate(self) -> float:
        return self.exact / self.compared if self.compared else 0.0

    @property
    def normalised_rate(self) -> float:
        return self.normalised / self.compared if self.compared else 0.0


@dataclass
class Accuracy:
    """Field-level accuracy, with its sample size attached so it cannot be quoted alone."""

    fields: dict[str, FieldScore] = field(default_factory=dict)
    n_rows: int = 0

    @property
    def is_indicative(self) -> bool:
        """Whether the sample supports quoting these as rates rather than as examples."""
        return self.n_rows >= INDICATIVE_MINIMUM

    @property
    def overall_exact(self) -> float:
        compared = sum(f.compared for f in self.fields.values())
        return sum(f.exact for f in self.fields.values()) / compared if compared else 0.0

    @property
    def overall_normalised(self) -> float:
        compared = sum(f.compared for f in self.fields.values())
        return sum(f.normalised for f in self.fields.values()) / compared if compared else 0.0

    def caveat(self) -> str:
        if self.is_indicative:
            return ""
        return (
            f"Only {self.n_rows} labelled row(s) were available, so these are worked "
            "examples rather than rates. They show whether a field is built the right way; "
            "they cannot show how often it is."
        )


@dataclass
class Compliance:
    """Character-limit results. Measurable at full scale and means what it says."""

    checks: dict[str, tuple[int, int]] = field(default_factory=dict)  # name -> (passed, total)

    def rate(self, name: str) -> float:
        passed, total = self.checks.get(name, (0, 0))
        return passed / total if total else 0.0

    def record(self, name: str, passed: bool) -> None:
        p, t = self.checks.get(name, (0, 0))
        self.checks[name] = (p + int(passed), t + 1)


@dataclass
class VocabularyReport:
    """Share of nominal values drawn from their category's controlled vocabulary.

    Named for what it is. Without the client's LOV this measures conformance to vocabularies
    this project authored, which is a real and useful check and is *not* the client's
    standard.
    """

    in_vocabulary: int = 0
    out_of_vocabulary: int = 0
    examples: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.in_vocabulary + self.out_of_vocabulary

    @property
    def rate(self) -> float:
        return self.in_vocabulary / self.total if self.total else 0.0


# --------------------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------------------


def normalise(text: str) -> str:
    """Fold the differences that are formatting rather than fact.

    Case, whitespace and the ®/™ symbols the client's brand list carries but our input never
    does. Deliberately does *not* fold punctuation inside values: `50-1/4` and `50.25` are a
    genuine disagreement about how a dimension is written, and the guide is explicit that
    the fraction form is the required one.
    """
    cleaned = (text or "").replace("®", "").replace("™", "").replace("�", "")
    return re.sub(r"\s+", " ", cleaned).strip().casefold()


def compare_rows(ours: Sequence[DeliveryRow], truth: Sequence[dict[str, str]]) -> Accuracy:
    """Score our rows against the labelled ones, column by column."""
    accuracy = Accuracy(n_rows=min(len(ours), len(truth)))

    for row, expected in zip(ours, truth, strict=False):
        produced = row.as_dict()
        for column in SCORED_COLUMNS:
            want = (expected.get(column) or "").strip()
            got = (produced.get(column) or "").strip()
            score = accuracy.fields.setdefault(column, FieldScore(column=column))

            if not want:
                score.truth_blank += 1
                continue  # the answer key has nothing to check against

            score.compared += 1
            if not got:
                score.we_left_blank += 1
                continue
            if got == want:
                score.exact += 1
                score.normalised += 1
            elif normalise(got) == normalise(want):
                score.normalised += 1

    return accuracy


def check_limits(rows: Sequence[DeliveryRow]) -> Compliance:
    """Character-limit compliance over any number of rows."""
    report = Compliance()
    for row in rows:
        cells = row.as_dict()

        invoice = cells.get("INVOICE_DESC", "")
        if invoice:
            report.record("INVOICE_DESC <= 40 chars", len(invoice) <= INVOICE_MAX)
            report.record("INVOICE_DESC upper case", invoice.isupper())

        mobile = cells.get("MOBILE_DESC", "")
        if mobile:
            report.record("MOBILE_DESC 60-80 chars", MOBILE_MIN <= len(mobile) <= MOBILE_MAX)

        for column in ("SHORT_DESC", "LONG_DESC1", "RETAIL_DESC"):
            text = cells.get(column, "")
            if text:
                # House rule from the guide: a space between number and unit everywhere
                # except the receipt line. "24in" is a violation; "24 in" is correct.
                report.record(
                    f"{column} spaces its units", not re.search(r"\d(?:in|ft|mm|cm)\b", text)
                )

    return report


def check_vocabulary(
    records: Sequence[ProductRecord], schemas: dict[str, CategorySchema]
) -> VocabularyReport:
    """Share of nominal values that are members of their declared vocabulary."""
    report = VocabularyReport()

    for record in records:
        schema = schemas.get(record.category_id or "")
        if schema is None:
            continue
        for value in record.values:
            spec = schema.get(value.attribute)
            if spec is None or spec.kind is not ValueKind.NOMINAL or not spec.vocabulary:
                continue
            allowed = {normalise(term) for term in spec.vocabulary}
            if normalise(value.raw) in allowed:
                report.in_vocabulary += 1
            else:
                report.out_of_vocabulary += 1
                if len(report.examples) < 20:
                    report.examples.append((record.sku, value.attribute, value.raw))

    return report


# --------------------------------------------------------------------------------------
# Loading the answer key
# --------------------------------------------------------------------------------------


def load_truth(path: Path) -> list[dict[str, str]]:
    """Read the enriched rows from a delivery-format sheet.

    Accepts the Expected Output file directly: its header is the 252-column contract and any
    row beneath it that carries content is a worked example.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise ValueError(f"{path.name} is empty")

    header = rows[0]
    missing = [c for c in ("Mfg_Part_Num", "Part_Desc") if c not in header]
    if missing:
        raise ValueError(f"{path.name} does not look like a delivery sheet; missing {missing}")

    return [
        dict(zip(header, row, strict=False))
        for row in rows[1:]
        if any(cell.strip() for cell in row)
    ]


def truth_as_input(truth: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    """The six input columns extracted back out of a labelled row.

    The delivery sheet carries the original input columns alongside the enriched ones, so
    the same file supplies both the question and the answer.
    """
    columns = ("Mfg_Part_Num", "Part_Desc", "E1_Brand", "Unilog_Brand", "DIB_Brand", "Part_Manuf")
    return [{c: row.get(c, "") for c in columns} for row in truth]


def format_report(
    accuracy: Accuracy, limits: Compliance, vocabulary: VocabularyReport
) -> list[str]:
    """The three metrics as lines a human reads, sample sizes attached."""
    out: list[str] = []

    out.append("FIELD-LEVEL ACCURACY")
    if accuracy.n_rows:
        out.append(
            f"  {accuracy.overall_exact:.0%} exact, {accuracy.overall_normalised:.0%} "
            f"normalised, over {accuracy.n_rows} labelled row(s)"
        )
        if not accuracy.is_indicative:
            out.append(f"  ! {accuracy.caveat()}")
        for score in accuracy.fields.values():
            if score.compared:
                out.append(
                    f"    {score.column:<26} {score.exact}/{score.compared} exact, "
                    f"{score.normalised}/{score.compared} normalised"
                )
    else:
        out.append("  no labelled rows supplied")

    out.append("")
    out.append("CHARACTER-LIMIT COMPLIANCE")
    if limits.checks:
        for name, (passed, total) in limits.checks.items():
            out.append(f"  {name:<32} {passed}/{total} = {passed / total:.0%}")
    else:
        out.append("  nothing to check")

    out.append("")
    out.append("CONTROLLED-VOCABULARY COMPLIANCE")
    out.append(
        f"  {vocabulary.in_vocabulary}/{vocabulary.total} nominal values in vocabulary "
        f"= {vocabulary.rate:.0%}"
    )
    out.append(
        "  ! Measured against the per-category vocabularies this project authors, not the "
        "client's LOV, which was not supplied."
    )
    for sku, attribute, raw in vocabulary.examples[:5]:
        out.append(f"    outside: {sku} {attribute} = {raw!r}")

    return out
