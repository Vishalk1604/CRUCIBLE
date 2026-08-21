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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from crucible.api.session import CertificationSession

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

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

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

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
