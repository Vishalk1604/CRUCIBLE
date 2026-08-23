"""Checking a nominal value against the terms its category actually allows.

Why this verifier exists, measured rather than assumed
------------------------------------------------------
On the original synthetic corpus - ball valves, bearings, hex screws - the scorer reached
AUROC 0.928. Re-run against the real distributor catalog it collapsed to **0.532**, which
is a coin flip. The cause was not a worse model or harder products. It was coverage:

    dimensional   applied to 37.8% of values
    constraint    applied to 37.7%
    identity      applied to  5.7%

Roughly **62% of real values had no verifier opinion at all**, so their feature vector was
all zeros and the scorer had nothing to separate them with. The synthetic corpus was
quantity-heavy by construction; a building-products catalog is not. Its attributes are
mostly *nominal* - material, finish, mounting type, wheel type, drive style - and both
physical verifiers correctly abstain on every one of them.

Abstention was the right behaviour and it was still a hole. The fix is not to make the
existing verifiers guess, it is to add one that can genuinely speak about nominal values.

What it checks
--------------
A NOMINAL attribute declares a closed vocabulary; `ontology.py` refuses to load one that
does not. So the check is exact and external: **is this term in the list a human authored
for this category?** No model is consulted, and the answer does not depend on anything the
proposer said about its own confidence.

This is the cheapest verifier in the system and it catches the single most common failure
of a constrained-decoding extractor: a plausible-sounding term that is not a member of the
set - "brushed nickel" for a wheel type, "PVC" where only aluminium and composite exist.
The value looks right, reads right, and is not a thing this category sells.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from crucible.assay.base import Verifier
from crucible.schema import AttributeSpec, AttributeValue, ProductRecord, ValueKind
from crucible.verdict import VerifierSignal

#: A near-miss this close to a real term is a formatting or inflection difference
#: ("stainless steel" vs "Stainless Steel", "aluminum" vs "aluminium") rather than a
#: different claim, so it is doubted rather than failed.
_NEAR_MISS = 0.86

#: Trust for a term that is close to a vocabulary entry but not equal to one. Low, because
#: the vocabulary is closed and a value outside it cannot be published as though it were
#: inside - but not zero, because the difference is often the extractor's spelling rather
#: than the extractor's understanding.
_NEAR_MISS_TRUST = 0.35


def _fold(term: str) -> str:
    """Compare terms the way a catalogue would: case- and space-insensitive."""
    return " ".join((term or "").casefold().split())


class VocabularyVerifier(Verifier):
    """Checks that a nominal value is a member of its attribute's declared vocabulary."""

    name = "vocabulary"
    version = "0.1.0"

    def _check(
        self,
        value: AttributeValue,
        spec: AttributeSpec,
        record: ProductRecord,
    ) -> VerifierSignal:
        if spec.kind is not ValueKind.NOMINAL:
            return self.abstain(f"{spec.name} is {spec.kind.value}, not a controlled term")
        if not spec.vocabulary:
            # ontology.py rejects this at load, so reaching here means a schema was built
            # in code rather than loaded. Abstain rather than assert: a verifier must not
            # be the thing that stops a catalog run.
            return self.abstain(f"{spec.name} declares no vocabulary to check against")

        claimed = _fold(value.raw)
        if not claimed:
            return self.abstain(f"{spec.name} is empty")

        allowed = {_fold(term): term for term in spec.vocabulary}

        if claimed in allowed:
            return self.ok(f"{value.raw!r} is a declared term for {spec.name}")

        # A term that contains, or is contained by, exactly one allowed term is almost
        # always a qualifier the source carried along ("316 stainless steel" against
        # "stainless steel"). More than one match is genuinely ambiguous and gets no
        # benefit of the doubt.
        contained = [
            term for folded, term in allowed.items() if folded in claimed or claimed in folded
        ]
        if len(contained) == 1:
            return self.doubt(
                _NEAR_MISS_TRUST,
                f"{value.raw!r} is not exactly a declared term for {spec.name} but overlaps "
                f"{contained[0]!r}; the extra wording may be a real distinction or may be "
                "noise carried from the description",
            )

        best_term, best_score = "", 0.0
        for folded, term in allowed.items():
            score = SequenceMatcher(None, folded, claimed).ratio()
            if score > best_score:
                best_term, best_score = term, score

        if best_score >= _NEAR_MISS:
            return self.doubt(
                _NEAR_MISS_TRUST,
                f"{value.raw!r} is {best_score:.0%} similar to {best_term!r} but is not that "
                f"term; {spec.name} accepts only its declared vocabulary",
            )

        return self.fail(
            f"{value.raw!r} is not a term {spec.name} accepts. This category allows "
            f"{_render(spec.vocabulary)}. A plausible-sounding value outside the "
            "vocabulary is exactly the failure a confidence score does not catch."
        )


def _render(vocabulary: list[str], limit: int = 6) -> str:
    """List the allowed terms without burying the reader in a long enumeration."""
    shown = ", ".join(repr(term) for term in vocabulary[:limit])
    remaining = len(vocabulary) - limit
    return f"{shown} (+{remaining} more)" if remaining > 0 else shown
