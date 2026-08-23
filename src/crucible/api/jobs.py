"""Running an enrichment as a background job, so the browser can watch it happen.

A thousand-row catalog is about twenty-four minutes of local inference. There is no
version of that which fits inside an HTTP request, and a progress bar that sits at an
unchanging percentage for twenty minutes teaches a user that the system is broken even
when it is working perfectly.

So the work runs on a worker thread and the page polls for what has finished. Rows arrive
as they are produced rather than at the end, which does two things: it makes a long run
feel like progress instead of a hang, and it means a user who only wanted to see the shape
of the output can stop reading after ten rows without waiting for the other nine hundred
and ninety.

Threads, not a task queue
-------------------------
This is a single-user local tool. Celery, Redis and a worker pool would be the right answer
for a real deployment and are entirely the wrong answer for something that has to start
with `uv run crucible-app` on a laptop with no network. The GIL is not a problem here
because the thread spends its life blocked on Ollama's socket.

Jobs live in memory and die with the process. That is a deliberate limit, not an oversight:
persisting them would mean a database, and the delivery file is already the durable
artifact.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from crucible.emit.rows import DeliveryRow, EmitPolicy, FillMode

logger = logging.getLogger(__name__)

#: How many products a run defaults to. A judge opening this cold will not wait
#: twenty-four minutes to find out whether it works, and 50 products is about ninety
#: seconds - long enough to watch, short enough to sit through. The full catalog stays one
#: click away, and the row count is always stated so the slice is never mistaken for the
#: whole file.
DEFAULT_PREVIEW_ROWS = 50

#: Rows kept in memory for the browser to render. The delivery file holds everything; this
#: is only what the results table shows.
MAX_STREAMED_ROWS = 400


class JobState(StrEnum):
    QUEUED = "queued"
    PREFLIGHT = "preflight"
    RUNNING = "running"
    CERTIFYING = "certifying"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Job:
    """One enrichment run, observable while it happens."""

    id: str
    filename: str
    input_path: Path
    limit: int | None
    total: int = 0
    processed: int = 0
    state: JobState = JobState.QUEUED
    message: str = "waiting to start"
    error: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None

    rows: list[DeliveryRow] = field(default_factory=list)
    preview: list[dict[str, Any]] = field(default_factory=list)
    result: Any = None
    outputs: dict[str, Path] = field(default_factory=dict)

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def elapsed(self) -> float:
        return (self.finished_at or time.monotonic()) - self.started_at

    @property
    def eta_seconds(self) -> float | None:
        """Seconds remaining, from the rate actually observed so far.

        None until a few products are done: an estimate drawn from one sample is worse
        than no estimate, because a user believes a number and does not believe a dash.
        """
        if self.processed < 3 or self.total <= 0 or self.state is JobState.DONE:
            return None
        rate = self.elapsed / self.processed
        return max(0.0, rate * (self.total - self.processed))

    def snapshot(self) -> dict[str, Any]:
        """What the browser polls for."""
        with self._lock:
            return {
                "id": self.id,
                "filename": self.filename,
                "state": self.state.value,
                "message": self.message,
                "error": self.error,
                "processed": self.processed,
                "total": self.total,
                "elapsed": round(self.elapsed, 1),
                "etaSeconds": None if self.eta_seconds is None else round(self.eta_seconds),
                "rows": list(self.preview),
                "outputs": sorted(self.outputs),
                "stats": self._stats(),
            }

    def _stats(self) -> dict[str, Any] | None:
        if self.result is None:
            return None
        s = self.result.stats
        return {
            "products": s.products,
            "routed": s.routed,
            "generic": s.generic,
            "valuesExtracted": s.values_extracted,
            "cellsPopulated": s.cells_populated,
            "secondsPerProduct": round(s.seconds / s.products, 2) if s.products else None,
        }


class JobRunner:
    """Owns the running jobs. One instance per process, held by the FastAPI app."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(
        self,
        input_path: Path,
        filename: str,
        limit: int | None = DEFAULT_PREVIEW_ROWS,
        model: str = "qwen3-vl:8b",
    ) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], filename=filename, input_path=input_path, limit=limit)
        with self._lock:
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, model), daemon=True).start()
        return job

    # -- the worker ---------------------------------------------------------

    def _run(self, job: Job, model: str) -> None:
        try:
            self._set(job, JobState.PREFLIGHT, "checking the model is reachable")
            from crucible.preflight import PreflightError, check_ollama

            try:
                placement = check_ollama(model)
            except PreflightError as exc:
                self._fail(job, str(exc))
                return
            logger.info("job %s preflight: %s", job.id, placement)

            from crucible.ingest import read_products

            products = read_products(job.input_path, limit=job.limit)
            with job._lock:  # noqa: SLF001 - the runner owns this job
                job.total = len(products)
            self._set(job, JobState.RUNNING, f"enriching {len(products)} products")

            from crucible.enrich import enrich

            def progress(index: int, total: int, sku: str) -> None:
                with job._lock:  # noqa: SLF001
                    job.processed = index
                    job.message = f"{sku}"

            result = enrich(
                job.input_path,
                limit=job.limit,
                policy=EmitPolicy(fill_mode=FillMode.GROUNDED),
                model=model,
                progress=progress,
                on_row=lambda row, record: self._stream(job, row, record),
            )

            self._set(job, JobState.CERTIFYING, "writing the delivery file")
            job.result = result
            job.rows = result.rows
            self._write_outputs(job, result)
            self._set(job, JobState.DONE, f"{len(result.rows)} products enriched")
            with job._lock:  # noqa: SLF001
                job.finished_at = time.monotonic()

        except Exception as exc:  # noqa: BLE001 - a failed job must report, not vanish
            logger.exception("job %s failed", job.id)
            self._fail(job, f"{type(exc).__name__}: {exc}")

    def _stream(self, job: Job, row: DeliveryRow, record: Any) -> None:
        """Push one finished row to the browser's view of the run."""
        with job._lock:  # noqa: SLF001
            if len(job.preview) >= MAX_STREAMED_ROWS:
                return
            job.preview.append(_row_summary(row, record))

    def _write_outputs(self, job: Job, result: Any) -> None:
        from crucible.emit.writer import write_csv, write_evidence, write_xlsx

        out = self.output_root / job.id
        out.mkdir(parents=True, exist_ok=True)

        write_csv(result.rows, out / "delivery.csv")
        job.outputs["csv"] = out / "delivery.csv"

        try:
            write_xlsx(result.rows, out / "delivery.xlsx")
            job.outputs["xlsx"] = out / "delivery.xlsx"
        except RuntimeError as exc:  # openpyxl absent - CSV still stands
            logger.warning("xlsx export skipped for job %s: %s", job.id, exc)

        write_evidence(result.evidence_pairs(), out / "evidence.csv")
        job.outputs["evidence"] = out / "evidence.csv"

    # -- state transitions --------------------------------------------------

    def _set(self, job: Job, state: JobState, message: str) -> None:
        with job._lock:  # noqa: SLF001
            job.state = state
            job.message = message

    def _fail(self, job: Job, error: str) -> None:
        with job._lock:  # noqa: SLF001
            job.state = JobState.FAILED
            job.error = error
            job.message = "failed"
            job.finished_at = time.monotonic()


def _row_summary(row: DeliveryRow, record: Any) -> dict[str, Any]:
    """One delivery row, shaped for the results table.

    Carries the attribute grid as label/value/uom triples including the empty ones,
    because a label with no value is the thing the table most needs to show.
    """
    from crucible.emit.columns import ATTRIBUTE_SLOTS, attribute_columns

    cells = row.cells
    attributes = []
    for slot in range(1, ATTRIBUTE_SLOTS + 1):
        label_col, value_col, uom_col = attribute_columns(slot)
        label = cells.get(label_col)
        if label is None:
            continue
        value = cells.get(value_col)
        uom = cells.get(uom_col)
        attributes.append(
            {
                "label": label.value,
                "value": value.value if value else "",
                "uom": uom.value if uom else "",
                "quote": " | ".join(s.quote for s in (value.spans if value else ()) if s.quote),
            }
        )

    def text(column: str) -> str:
        cell = cells.get(column)
        return cell.value if cell else ""

    return {
        "sku": row.sku,
        "description": record.raw.description if record is not None else "",
        "dept": text("Dept"),
        "klass": text("Class"),
        "fine": text("Fine"),
        "classpath": text("Classpath"),
        "brand": text("BRAND_NAME"),
        "populated": row.populated,
        "attributes": attributes,
        "filled": sum(1 for a in attributes if a["value"]),
        "total": len(attributes),
    }
