"""Tests for the local web app.

The session is stubbed rather than built. A real one needs Ollama and a harvest, which
would make these tests slow, machine-dependent, and skipped exactly where they are most
wanted. What is worth pinning here is the contract the page relies on: field names,
refusal behaviour, and that an infeasible alpha is reported as such instead of being
quietly rendered as zero automation.

TestClient is deliberately *not* used as a context manager. Entering it runs the app's
lifespan, which builds a real CertificationSession - the first version of this file did
that and sat firing inference at Ollama until it timed out, while competing with a
harvest for the same GPU. Without the context manager the lifespan never runs, which is
what the stub is for.
"""

import pytest
from fastapi.testclient import TestClient

from crucible.api import app as app_module
from crucible.api.session import DialResult


def dial(alpha=0.05, feasible=True) -> DialResult:
    return DialResult(
        alpha=alpha,
        feasible=feasible,
        reason="" if feasible else "not enough separation at this risk level",
        threshold=0.4 if feasible else None,
        automation_rate=0.78 if feasible else 0.0,
        n_auto_published=780 if feasible else 0,
        n_review=220 if feasible else 1000,
        n_total=1000,
        realized_error=0.017 if feasible else 0.0,
        certified_bound=0.019 if feasible else None,
        baseline_error=0.31,
    )


class StubSession:
    def __init__(self, feasible=True):
        self.feasible = feasible

    def certify_at(self, alpha):
        return dial(alpha, self.feasible)

    def sweep(self, alphas):
        return [dial(a, self.feasible) for a in alphas]

    def review_queue(self, alpha, limit=25):
        return [
            {
                "sku": "V-1",
                "category": "valve.ball",
                "attribute": "bore",
                "extracted": "520 mm",
                "expected": "25 mm",
                "isError": True,
                "nonconformity": 0.91,
                "signals": [
                    {
                        "verifier": "coherence",
                        "trust": 0.1,
                        "applicable": True,
                        "detail": "far from median",
                    }
                ],
            }
        ][:limit]

    def stats(self):
        return {
            "model": "qwen3-vl:8b",
            "nProducts": 600,
            "nValues": 3000,
            "nTest": 1000,
            "auroc": 0.908,
            "baselineError": 0.31,
            "verifiers": ["coherence", "constraint", "dimensional"],
            "simulatedCorpus": True,
        }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "get_session", lambda: StubSession())
    yield TestClient(app_module.create_app())


@pytest.fixture
def refusing_client(monkeypatch):
    monkeypatch.setattr(app_module, "get_session", lambda: StubSession(feasible=False))
    yield TestClient(app_module.create_app())


class TestSerialisation:
    def test_dial_result_uses_the_field_names_the_page_reads(self):
        payload = dial().to_dict()
        for key in (
            "alpha",
            "feasible",
            "automationRate",
            "nAutoPublished",
            "nReview",
            "nTotal",
            "realizedError",
            "certifiedBound",
            "baselineError",
        ):
            assert key in payload, f"page reads {key!r} and it is missing"

    def test_infeasible_reports_a_null_bound_not_a_zero(self):
        # Rendering "certified 0.0%" for a refusal would read as a perfect guarantee
        # rather than as no guarantee at all.
        payload = dial(feasible=False).to_dict()
        assert payload["certifiedBound"] is None
        assert payload["feasible"] is False


class TestRoutes:
    def test_stats(self, client):
        body = client.get("/api/stats").json()
        assert body["auroc"] == 0.908
        assert body["simulatedCorpus"] is True

    def test_certify(self, client):
        body = client.get("/api/certify?alpha=0.02").json()
        assert body["alpha"] == 0.02
        assert body["feasible"] is True

    def test_sweep_covers_every_dial_stop(self, client):
        body = client.get("/api/sweep").json()
        assert body["stops"] == app_module.DIAL_STOPS
        assert len(body["results"]) == len(app_module.DIAL_STOPS)

    def test_review(self, client):
        body = client.get("/api/review?alpha=0.02&limit=5").json()
        assert body["items"][0]["attribute"] == "bore"
        assert body["items"][0]["signals"][0]["verifier"] == "coherence"

    def test_index_is_served(self, client):
        assert client.get("/").status_code == 200


class TestRefusal:
    def test_refusal_is_reported_with_a_reason(self, refusing_client):
        body = refusing_client.get("/api/certify?alpha=0.001").json()
        assert body["feasible"] is False
        assert body["reason"]
        assert body["certifiedBound"] is None


class TestValidation:
    @pytest.mark.parametrize("alpha", [0, 1, 1.5, -0.1])
    def test_alpha_outside_the_open_unit_interval_is_rejected(self, client, alpha):
        assert client.get(f"/api/certify?alpha={alpha}").status_code == 422

    @pytest.mark.parametrize("limit", [0, 500])
    def test_review_limit_is_bounded(self, client, limit):
        assert client.get(f"/api/review?limit={limit}").status_code == 422


def test_unbuilt_session_returns_service_unavailable(monkeypatch):
    # Requests arriving while the harvest is still running must not look like an
    # internal error.
    monkeypatch.setattr(app_module, "_session", None)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        app_module.get_session()
    assert excinfo.value.status_code == 503
