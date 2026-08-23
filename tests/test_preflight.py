"""Tests for the readiness check.

Everything here is offline. A preflight check that needed a live Ollama to test would be
untestable on CI and, worse, would only be exercised on exactly the healthy machines where
it never fires.
"""

from __future__ import annotations

import json
import urllib.error
from io import BytesIO

import pytest

from crucible.preflight import (
    MIN_GPU_SHARE,
    ModelPlacement,
    PreflightError,
    check_ollama,
    loaded_models,
    reachable,
)

GB = 1_000_000_000


def fake_urlopen(payload, *, fail=False):
    """Stand in for urllib.request.urlopen with a canned /api/ps body."""

    def _open(url, timeout=None):  # noqa: ARG001
        if fail:
            raise urllib.error.URLError("connection refused")

        class _Response(BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.close()
                return False

        return _Response(json.dumps(payload).encode())

    return _open


def ps_body(name="qwen3-vl:8b", size=6.2 * GB, vram=None):
    return {"models": [{"name": name, "size": int(size), "size_vram": int(vram or size)}]}


class TestPlacement:
    def test_fully_resident_is_gpu_bound(self):
        placement = ModelPlacement("m", 6 * GB, 6 * GB)
        assert placement.gpu_share == 1.0
        assert placement.is_gpu_bound

    def test_the_documented_healthy_spill_still_passes(self):
        # 6.2 GB on an 8 GB card spills ~11% to CPU and runs at full useful speed. The
        # threshold must not reject the machine this project was built on.
        placement = ModelPlacement("qwen3-vl:8b", 6.2 * GB, 0.87 * 6.2 * GB)
        assert placement.is_gpu_bound
        assert "13%/87% CPU/GPU" in placement.describe()

    def test_cpu_fallback_is_not_gpu_bound(self):
        placement = ModelPlacement("qwen3-vl:8b", 6.2 * GB, 0.02 * 6.2 * GB)
        assert not placement.is_gpu_bound
        assert "98%/2% CPU/GPU" in placement.describe()

    def test_zero_size_does_not_divide_by_zero(self):
        assert ModelPlacement("m", 0, 0).gpu_share == 0.0

    def test_threshold_boundary(self):
        size = 10 * GB
        assert ModelPlacement("m", size, MIN_GPU_SHARE * size).is_gpu_bound
        assert not ModelPlacement("m", size, (MIN_GPU_SHARE - 0.01) * size).is_gpu_bound


class TestReachability:
    def test_unreachable_server_is_reported(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen({}, fail=True))
        assert not reachable()

    def test_reachable_server(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen({}))
        assert reachable()

    def test_loaded_models_returns_empty_when_unreachable(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen({}, fail=True))
        assert loaded_models() == []

    def test_entries_without_a_size_are_skipped(self, monkeypatch):
        body = {"models": [{"name": "broken", "size": 0}, {"name": "ok", "size": GB}]}
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(body))
        assert [p.name for p in loaded_models()] == ["ok"]


class TestCheck:
    def test_missing_server_refuses_with_the_fix(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen({}, fail=True))
        with pytest.raises(PreflightError) as excinfo:
            check_ollama("qwen3-vl:8b")
        message = str(excinfo.value)
        assert "ollama serve" in message
        # It must say why a blank sheet would be the danger, not just that a port is shut.
        assert "abstention" in message

    def test_cpu_fallback_refuses_and_says_what_to_do(self, monkeypatch):
        body = ps_body(vram=0.02 * 6.2 * GB)
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(body))
        with pytest.raises(PreflightError) as excinfo:
            check_ollama("qwen3-vl:8b")
        message = str(excinfo.value)
        assert "nvidia-smi" in message  # names the diagnostic command
        assert "--no-preflight" in message  # names the escape hatch
        assert "98%/2% CPU/GPU" in message  # names the actual measurement

    def test_healthy_placement_passes(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(ps_body(vram=0.87 * 6.2 * GB)))
        assert "87% CPU/GPU" in check_ollama("qwen3-vl:8b")

    def test_cold_server_with_nothing_loaded_passes(self, monkeypatch):
        # Ollama loads on first use; refusing here would make the day's first run
        # impossible.
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen({"models": []}))
        assert "no model resident" in check_ollama("qwen3-vl:8b")

    def test_require_gpu_false_tolerates_cpu(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(ps_body(vram=0.02 * 6.2 * GB)))
        assert check_ollama("qwen3-vl:8b", require_gpu=False)

    def test_a_different_model_being_loaded_is_not_a_failure(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(ps_body(name="llama3:8b")))
        assert "loaded:" in check_ollama("qwen3-vl:8b")

    def test_worst_placement_wins_when_several_are_loaded(self, monkeypatch):
        body = {
            "models": [
                {"name": "qwen3-vl:8b", "size": int(6 * GB), "size_vram": int(6 * GB)},
                {"name": "qwen3-vl:2b", "size": int(2 * GB), "size_vram": 0},
            ]
        }
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen(body))
        with pytest.raises(PreflightError):
            check_ollama("qwen3-vl")
