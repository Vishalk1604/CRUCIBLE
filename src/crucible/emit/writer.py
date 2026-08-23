"""Writing the delivery file, and the evidence sidecar that explains it.

Two outputs, deliberately separate. The delivery file is what a distributor loads into a
PIM: 252 columns, nothing else, no annotations. The sidecar is what a reviewer opens when
they want to know why a cell says what it says - one row per populated cell, with its
provenance, the quoted source text, every verifier's opinion, and whether it was certified.

Keeping them apart matters. Interleaving confidence columns into the delivery file would
break the format contract; omitting them entirely would make "explainable output" a claim
rather than a deliverable.

Excel note that is not optional
-------------------------------
The trade writes dimensions as fractions: 1/2, 50-1/4, 7/8. Excel reads `1/2` as a date
and `50-1/4` as a date too, and once it has done so the original text is gone. Every
delivery column is therefore written as an explicit text cell. This is why the XLSX path
exists at all rather than telling everyone to open the CSV - Excel corrupts the CSV on
open, silently, and the corruption looks like a data-quality problem with our extractor.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from crucible.emit.columns import DELIVERY_COLUMNS
from crucible.emit.rows import DeliveryRow

#: Sidecar header. Wider than the delivery format because its job is explanation.
EVIDENCE_COLUMNS: tuple[str, ...] = (
    "SKU",
    "Column",
    "Attribute",
    "Value",
    "UOM",
    "Provenance",
    "Certified",
    "Nonconformity",
    "Source Quote",
    "Verifier Signals",
)


def write_csv(rows: Iterable[DeliveryRow], path: Path) -> int:
    """Write the delivery file as CSV. Returns the number of rows written.

    QUOTE_MINIMAL with CRLF, matching the reference sheet. Blanks are written as truly
    empty fields - never "N/A", "None" or "nan", each of which is a string that a
    downstream system will faithfully store as a product attribute.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(DELIVERY_COLUMNS),
            quoting=csv.QUOTE_MINIMAL,
            extrasaction="raise",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_dict())
            count += 1
    return count


def write_xlsx(rows: Iterable[DeliveryRow], path: Path) -> int:
    """Write the delivery file as XLSX, with every cell forced to text.

    Uses openpyxl's write-only mode so a 1000-row export does not build the whole workbook
    in memory. The import is local so that the CSV path keeps working on a machine without
    openpyxl installed - an export that produces one of the two formats beats an ImportError
    at the top of the module that produces neither.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "writing .xlsx needs openpyxl; install it with `uv sync --extra api`, "
            "or export CSV instead"
        ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Delivery")

    sheet.append([_text_cell(sheet, column, WriteOnlyCell) for column in DELIVERY_COLUMNS])

    count = 0
    for row in rows:
        values = row.as_dict()
        sheet.append(
            [_text_cell(sheet, values[column], WriteOnlyCell) for column in DELIVERY_COLUMNS]
        )
        count += 1

    workbook.save(path)
    return count


def _text_cell(sheet: Any, value: str, write_only_cell: Any) -> Any:
    """A cell Excel will not reinterpret.

    number_format "@" is the text format. Without it `1/2` becomes 2 January and `50-1/4`
    becomes a 1950 date, which turns a correct extraction into a visible data error that
    the reviewer will blame on the extractor.
    """
    cell = write_only_cell(sheet, value=value)
    cell.number_format = "@"
    return cell


def write_evidence(
    records: Sequence[tuple[DeliveryRow, dict[str, list[tuple[str, float, bool, str]]]]],
    path: Path,
) -> int:
    """Write the sidecar: one row per populated cell, with its full justification.

    `records` pairs each delivery row with a mapping from attribute name to that
    attribute's verifier signals, as (verifier, trust, applicable, detail) tuples.

    Passthrough cells are included even though their provenance is trivial, because a
    reviewer auditing a row wants the whole row accounted for; a sidecar that silently
    omits some cells invites the question of what else it omitted.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(EVIDENCE_COLUMNS)
        for row, signals in records:
            for column in DELIVERY_COLUMNS:
                cell = row.cells.get(column)
                if cell is None:
                    continue
                writer.writerow(
                    (
                        row.sku,
                        cell.column,
                        cell.attribute or "",
                        cell.value,
                        "",
                        cell.provenance.value,
                        "yes" if cell.certified else "no",
                        "" if cell.nonconformity is None else f"{cell.nonconformity:.4f}",
                        _quote_spans(cell),
                        _render_signals(signals.get(cell.attribute or "", [])),
                    )
                )
                count += 1
    return count


def _quote_spans(cell: Any) -> str:
    """The source text a cell rests on, as the reviewer would want to read it."""
    quotes = [s.quote for s in cell.spans if s.quote]
    return " | ".join(dict.fromkeys(quotes))


def _render_signals(signals: Sequence[tuple[str, float, bool, str]]) -> str:
    """Verifier opinions as one readable field.

    Abstentions are rendered as "abstained" rather than as a trust number, because a
    verifier that did not look and a verifier that looked and was satisfied must never
    read the same way. That distinction is a non-negotiable of this project and it has to
    survive into the artifact a human actually opens.
    """
    parts = []
    for verifier, trust, applicable, detail in signals:
        if not applicable:
            parts.append(
                f"{verifier}: abstained ({detail})" if detail else f"{verifier}: abstained"
            )
        else:
            parts.append(
                f"{verifier}: {trust:.2f} ({detail})" if detail else f"{verifier}: {trust:.2f}"
            )
    return " ; ".join(parts)
