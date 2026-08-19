"""The verifier interface.

Every verifier answers one question about one proposed value, and answers it
*independently* of the model that proposed the value. That independence is the whole
point: the literature is clear that an LLM asked to check its own work mostly agrees
with itself, and that useful correction requires feedback from external tools. So a
verifier here is a tool — a unit algebra, a constraint solver, a statistical model of
the catalog — not a second opinion from the same source.

Verifiers must be cheap enough to run on every value of a million-SKU catalog, and must
never raise: a verifier that crashes on malformed input is a verifier that stops the
line. Anything unexpected becomes a low-trust signal with an explanatory detail.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from crucible.schema import AttributeSpec, AttributeValue, ProductRecord
from crucible.verdict import VerifierSignal


class Verifier(ABC):
    """Base class for the assay stage.

    Subclasses implement `_check`. The public `verify` wraps it so that an unexpected
    exception degrades to an abstention rather than taking down the run.
    """

    #: Stable identifier, recorded in the certificate so a guarantee can be traced back
    #: to the exact set of verifiers that produced it.
    name: str = "verifier"
    version: str = "0.1.0"

    @abstractmethod
    def _check(
        self,
        value: AttributeValue,
        spec: AttributeSpec,
        record: ProductRecord,
    ) -> VerifierSignal:
        """Form an opinion. May raise; `verify` contains the damage."""

    def verify(
        self,
        value: AttributeValue,
        spec: AttributeSpec,
        record: ProductRecord,
    ) -> VerifierSignal:
        try:
            return self._check(value, spec, record)
        except Exception as exc:  # noqa: BLE001 - a broken verifier must not stop the run
            return self.abstain(f"{self.name} errored on {value.attribute!r}: {exc}")

    # -- helpers for building signals, so subclasses stay readable ----------------------

    def ok(self, detail: str = "") -> VerifierSignal:
        return VerifierSignal(verifier=self.name, trust=1.0, applicable=True, detail=detail)

    def fail(self, detail: str) -> VerifierSignal:
        """A hard contradiction. Bypasses the calibrated threshold entirely."""
        return VerifierSignal(verifier=self.name, trust=0.0, applicable=True, detail=detail)

    def doubt(self, trust: float, detail: str) -> VerifierSignal:
        """Partial confidence. Feeds the calibrated scorer like any other signal."""
        return VerifierSignal(
            verifier=self.name, trust=max(0.0, min(1.0, trust)), applicable=True, detail=detail
        )

    def abstain(self, detail: str = "") -> VerifierSignal:
        """No opinion. Kept distinct from mild approval so fusion is not misled."""
        return VerifierSignal(verifier=self.name, trust=0.0, applicable=False, detail=detail)
