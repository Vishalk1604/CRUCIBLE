"""Local web app: the risk dial, served from this machine.

Runs entirely on localhost against the local Ollama. Nothing about a catalog leaves the
building, which is the deployment story as much as it is a demo convenience - a
distributor's unreleased pricing and specifications are exactly the data they cannot send
to a hosted API.

The session is built once at startup and held. Requests only re-threshold it, so the dial
responds in milliseconds while the underlying extraction took a quarter of an hour.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from crucible.api.jobs import DEFAULT_PREVIEW_ROWS, JobRunner
from crucible.api.session import CertificationSession

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

#: Where uploads and their outputs live. Under the repo rather than a temp dir so a run's
#: delivery file survives long enough for someone to download it twice.
UPLOAD_ROOT = Path(__file__).resolve().parents[3] / "runs" / "web"

#: The catalog shipped with the project, so a visitor with no file can still see it work.
SAMPLE_INPUT = Path(__file__).resolve().parents[3] / "Unihack_ Sample Dataset - Input.csv"

#: Risk levels offered by the dial. Spans the range where the answer actually changes:
#: below the lowest nothing can be certified, above the highest almost everything is.
DIAL_STOPS = [0.005, 0.01, 0.02, 0.03, 0.05, 0.07, 0.10, 0.15]

_session: CertificationSession | None = None


def get_session() -> CertificationSession:
    if _session is None:
        raise HTTPException(status_code=503, detail="session still building")
    return _session


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _session
    logger.info("building certification session (cached harvest loads instantly)")
    _session = CertificationSession(n_per_category=app.state.n_per_category)
    logger.info("ready")
    yield
    _session = None


def create_app(n_per_category: int = 200) -> FastAPI:
    app = FastAPI(title="Crucible", lifespan=lifespan)
    app.state.n_per_category = n_per_category
    app.state.jobs = JobRunner(output_root=UPLOAD_ROOT / "runs")

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        return get_session().stats()

    @app.get("/api/certify")
    def certify(alpha: float = Query(0.05, gt=0.0, lt=1.0)) -> dict[str, Any]:
        return get_session().certify_at(alpha).to_dict()

    @app.get("/api/sweep")
    def sweep() -> dict[str, Any]:
        """The risk-coverage curve, and the evidence the guarantee holds.

        Returned as one payload so the chart and the dial never disagree about what a
        given alpha buys.
        """
        session = get_session()
        return {
            "stops": DIAL_STOPS,
            "results": [r.to_dict() for r in session.sweep(DIAL_STOPS)],
        }

    @app.get("/api/review")
    def review(
        alpha: float = Query(0.05, gt=0.0, lt=1.0), limit: int = Query(25, ge=1, le=200)
    ) -> dict[str, Any]:
        return {"items": get_session().review_queue(alpha, limit=limit)}

    # Shared stylesheet and any page assets. Mounted rather than routed one file at a
    # time so adding a page does not mean adding a route for its CSS.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # ---- pages -----------------------------------------------------------

    @app.get("/")
    def landing() -> FileResponse:
        return FileResponse(STATIC_DIR / "landing.html")

    @app.get("/signin")
    def signin() -> FileResponse:
        return FileResponse(STATIC_DIR / "signin.html")

    @app.get("/app")
    def workspace() -> FileResponse:
        return FileResponse(STATIC_DIR / "app.html")

    @app.get("/certify")
    def certify_page() -> FileResponse:
        # The original dial view, kept as its own page rather than deleted: it is the
        # calibrated-guarantee story, and it runs on a corpus with an answer key.
        return FileResponse(STATIC_DIR / "index.html")

    # ---- enrichment jobs ---------------------------------------------------

    @app.get("/api/sample")
    def sample_info() -> dict[str, Any]:
        """The bundled catalog, so a visitor with no file of their own can still run it."""
        path = SAMPLE_INPUT
        if not path.exists():
            return {"available": False}
        from crucible.ingest import read_products

        return {
            "available": True,
            "filename": path.name,
            "rows": len(read_products(path)),
            "defaultLimit": DEFAULT_PREVIEW_ROWS,
        }

    @app.post("/api/jobs")
    async def create_job(
        file: UploadFile | None = File(default=None),
        limit: int | None = Query(default=DEFAULT_PREVIEW_ROWS, ge=1),
        use_sample: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Start an enrichment. Either an uploaded CSV, or the bundled sample."""
        if use_sample:
            if not SAMPLE_INPUT.exists():
                raise HTTPException(status_code=404, detail="no sample catalog bundled")
            path, name = SAMPLE_INPUT, SAMPLE_INPUT.name
        else:
            if file is None or not file.filename:
                raise HTTPException(status_code=400, detail="no file uploaded")
            if not file.filename.lower().endswith((".csv", ".txt")):
                raise HTTPException(
                    status_code=400,
                    detail="expected a .csv export. Excel files must be saved as CSV first.",
                )
            UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
            path = UPLOAD_ROOT / f"{uuid.uuid4().hex[:12]}-{Path(file.filename).name}"
            path.write_bytes(await file.read())
            name = file.filename

        job = app.state.jobs.submit(path, filename=name, limit=limit)
        return {"id": job.id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return job.snapshot()

    @app.get("/api/jobs/{job_id}/download/{kind}")
    def job_download(job_id: str, kind: str) -> FileResponse:
        job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        path = job.outputs.get(kind)
        if path is None or not path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"no {kind!r} output for this run; it may still be processing",
            )
        media = {
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "csv": "text/csv",
            "evidence": "text/csv",
        }.get(kind, "application/octet-stream")
        return FileResponse(path, media_type=media, filename=path.name)

    return app


app = create_app()


def main() -> None:
    """Entry point for `crucible-app`."""
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Bound to localhost deliberately. This serves catalog data and should not be
    # reachable from the network without a considered decision to expose it.
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
