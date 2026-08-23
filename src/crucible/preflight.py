"""Checking that the machine can actually do the work, before spending an hour finding out.

This module exists because of a specific evening. Extraction quietly slowed from 2.4 s to
8.6 s per product and three consecutive calibration runs were lost - one to a wedged
Ollama, two to inference that had silently fallen back to the CPU because a game was
holding most of the VRAM. Nothing in the output said anything was wrong. The runs simply
took three and a half times longer and then died.

The failure mode is the same one this whole project is about: **the artifact looks fine
while the thing that produced it was broken.** A run at CPU speed produces identical rows
to a run on the GPU, just far too slowly to finish, and a run against a dead Ollama
produces well-formed rows that are entirely blank. Neither announces itself.

So: check first, refuse early, and say what to do about it. Forty minutes of GPU time is
worth thirty seconds of preflight.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

#: Below this share of the model resident in VRAM, inference is CPU-bound enough that a
#: catalog run is not worth starting. Not 1.0: a 6.2 GB model on an 8 GB card normally
#: spills about 11% and still runs at full useful speed, which is the documented healthy
#: state for this project's hardware.
MIN_GPU_SHARE = 0.60

DEFAULT_HOST = "http://127.0.0.1:11434"


class PreflightError(RuntimeError):
    """Raised when the environment cannot support the run that was requested."""


@dataclass(frozen=True)
class ModelPlacement:
    """Where a loaded model actually lives."""

    name: str
    size_bytes: int
    vram_bytes: int

    @property
    def gpu_share(self) -> float:
        return self.vram_bytes / self.size_bytes if self.size_bytes else 0.0

    @property
    def is_gpu_bound(self) -> bool:
        return self.gpu_share >= MIN_GPU_SHARE

    def describe(self) -> str:
        cpu = 1.0 - self.gpu_share
        return (
            f"{self.name}: {self.size_bytes / 1e9:.1f} GB, {cpu:.0%}/{self.gpu_share:.0%} CPU/GPU"
        )


def reachable(host: str = DEFAULT_HOST, timeout: float = 5.0) -> bool:
    """Whether an Ollama server answers at all."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout):  # noqa: S310
            return True
    except (urllib.error.URLError, OSError):
        return False


def loaded_models(host: str = DEFAULT_HOST, timeout: float = 5.0) -> list[ModelPlacement]:
    """Models currently resident, and how much of each sits in VRAM.

    Returns an empty list when nothing is loaded, which is not an error: Ollama loads on
    first use, so a cold server legitimately reports nothing.
    """
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return []

    placements = []
    for entry in payload.get("models") or []:
        size = int(entry.get("size") or 0)
        if size <= 0:
            continue
        placements.append(
            ModelPlacement(
                name=str(entry.get("name") or entry.get("model") or "unknown"),
                size_bytes=size,
                vram_bytes=int(entry.get("size_vram") or 0),
            )
        )
    return placements


def check_ollama(
    model: str | None = None,
    host: str = DEFAULT_HOST,
    require_gpu: bool = True,
) -> str:
    """Refuse the run if Ollama is absent, or if the model has fallen back to the CPU.

    Returns a human-readable status line when everything is fine. Raises `PreflightError`
    with an actionable message when it is not - actionable meaning it names the command to
    run, not merely the condition that failed.

    A cold server with nothing loaded passes: placement cannot be judged before the model
    is loaded, and refusing to start would make the first run of the day impossible.
    """
    if not reachable(host, timeout=5.0):
        raise PreflightError(
            f"no Ollama server at {host}. Start it with `ollama serve` (on Windows the "
            "tray app alone does not always bring the API up), then re-run. Without it "
            "every extraction returns nothing and the delivery file would be blank rows "
            "that look like careful abstention."
        )

    placements = loaded_models(host)
    if not placements:
        return "Ollama reachable; no model resident yet (it loads on first use)."

    relevant = [p for p in placements if model is None or p.name.startswith(model.split(":")[0])]
    if not relevant:
        return f"Ollama reachable; loaded: {', '.join(p.describe() for p in placements)}"

    worst = min(relevant, key=lambda p: p.gpu_share)
    if worst.is_gpu_bound or not require_gpu:
        return worst.describe()

    raise PreflightError(
        f"{worst.describe()} - the model has fallen back to the CPU, which runs this "
        f"pipeline roughly 3.5x slower and will not finish a catalog in reasonable time.\n"
        "Almost always this means something else is holding VRAM. Check with:\n"
        "    nvidia-smi --query-compute-apps=pid,process_name --format=csv\n"
        "Close whatever is on the card (games and browsers are the usual culprits), then "
        "restart Ollama so it reloads onto the GPU. Pass --no-preflight to run anyway."
    )
