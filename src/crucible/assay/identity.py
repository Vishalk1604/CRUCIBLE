"""Cross-checking identity claims against a second, independent channel.

The other verifiers reason about physics: is this a length, is it smaller than that other
length. None of them can say anything about a part number, which is an arbitrary string
whose only truth condition is whether it matches the manufacturer's. That is a real gap,
because identity errors are the most expensive kind in a distribution catalog - a wrong
dimension gets caught at the counter, a wrong part number ships the wrong product.

The opening this dataset provides
---------------------------------
`Part_Desc` redundantly repeats `Mfg_Part_Num` on **676 of 1000** rows:

    Mfg_Part_Num: DCB518ASTS06G
    Part_Desc:    DCB518ASTS06G Diablo 1/2"x18" - Sanding Belt 6pc

Two channels carrying the same fact, populated by different processes at different times.
That redundancy is what makes this a verifier rather than a second opinion: comparing them
is comparing two independent sources, exactly the external feedback the assay stage is
built on, and no model is consulted at any point.

Confusables
-----------
Part numbers are transcribed by humans and OCR'd from labels, so the error distribution is
not random - it is dominated by glyph confusion. Both of these are real pairs from this
catalog:

    55226BKLFU   vs   55226BKFLU     (transposition)
    174-0CSB3-15W vs  174-OCSB3-15A  (zero/oh, plus a genuine suffix difference)

The second is the interesting case and the reason this verifier reports `doubt` rather
than `fail` on a fold match. `0` vs `O` is almost certainly a transcription artifact of one
part; `W` vs `A` at the end is almost certainly a different product. Collapsing both into
one verdict would either flag thousands of harmless glyph variants or wave through genuine
mismatches, so the fold is applied and *named* in the detail, leaving the reviewer to see
which channel differs and decide.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from crucible.assay.base import Verifier
from crucible.ingest import erp_text
from crucible.schema import AttributeSpec, AttributeValue, ProductRecord
from crucible.verdict import VerifierSignal

#: Attributes whose value is an identity claim rather than a measurement. Everything else
#: gets an abstention: this verifier has nothing to say about a wattage.
IDENTITY_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "part_number",
        "mpn",
        "manufacturer_part_number",
        "model",
        "model_number",
        "catalog_number",
        "sku",
        "series",
        "brand",
        "product_name",
    }
)

#: Glyph pairs that a human or an OCR pass confuses. Folded to a single representative so
#: that `0CSB3` and `OCSB3` compare equal, and the fact that the fold was needed is
#: reported rather than hidden.
_CONFUSABLES = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "I": "1",
        "l": "1",
        "i": "1",
        "S": "5",
        "s": "5",
        "B": "8",
        "Z": "2",
        "z": "2",
    }
)

#: Below this, two strings are not variants of each other, they are different strings.
_SIMILARITY_FLOOR = 0.82

#: Trust for a value that matches only after folding confusables. Deliberately low but
#: non-zero: usually a transcription artifact, occasionally a genuinely different product.
_FOLD_TRUST = 0.3

#: Trust for a brand or product name found in the source but not as an exact token.
_SOFT_TRUST = 0.6


def canonical(text: str) -> str:
    """Strip everything that is formatting rather than identity."""
    return re.sub(r"[^A-Za-z0-9]", "", text or "").upper()


def fold_confusables(text: str) -> str:
    """Collapse glyphs that humans and scanners interchange."""
    return canonical(text).translate(_CONFUSABLES)


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


class IdentityVerifier(Verifier):
    """Checks identity-bearing values against the other channel that carries them."""

    name = "identity"
    version = "0.1.0"

    def _check(
        self,
        value: AttributeValue,
        spec: AttributeSpec,
        record: ProductRecord,
    ) -> VerifierSignal:
        if spec.name not in IDENTITY_ATTRIBUTES:
            return self.abstain(f"{spec.name} is not an identity claim")

        claimed = (value.raw or "").strip()
        if not claimed:
            return self.abstain(f"{spec.name} is empty")

        haystack = erp_text(record.raw)
        if not haystack.strip():
            return self.abstain("no source text to check the claim against")

        # Brand and product name are prose, not codes: a substring match in the source is
        # the right test, and the part-number machinery below would be nonsense on them.
        if spec.name in ("brand", "product_name", "series"):
            return self._check_prose(claimed, haystack, spec)

        return self._check_code(claimed, record, haystack, spec)

    def _check_prose(self, claimed: str, haystack: str, spec: AttributeSpec) -> VerifierSignal:
        if claimed.casefold() in haystack.casefold():
            return self.ok(f"{spec.name} {claimed!r} appears verbatim in the source")

        folded_claim, folded_hay = canonical(claimed), canonical(haystack)
        if folded_claim and folded_claim in folded_hay:
            return self.doubt(
                _SOFT_TRUST,
                f"{spec.name} {claimed!r} appears in the source only after normalising "
                "punctuation and spacing",
            )
        return self.fail(
            f"{spec.name} {claimed!r} does not appear in the source text at all; "
            "it was not read from this record"
        )

    def _check_code(
        self,
        claimed: str,
        record: ProductRecord,
        haystack: str,
        spec: AttributeSpec,
    ) -> VerifierSignal:
        """Compare a part-number-shaped claim against both channels that carry one."""
        channels: list[tuple[str, str]] = []
        if record.raw.mpn:
            channels.append(("Mfg_Part_Num", record.raw.mpn))
        if record.raw.sku and record.raw.sku != record.raw.mpn:
            channels.append(("SKU", record.raw.sku))

        target = canonical(claimed)
        if not target:
            return self.abstain(f"{spec.name} {claimed!r} has no comparable characters")

        # Exact agreement with either channel, or an exact appearance in the description.
        for channel, other in channels:
            if canonical(other) == target:
                return self.ok(f"{spec.name} matches {channel} exactly")
        if target in canonical(haystack):
            return self.ok(f"{spec.name} {claimed!r} appears exactly in the source text")

        # Agreement only after folding confusable glyphs.
        folded_target = fold_confusables(claimed)
        for channel, other in channels:
            if fold_confusables(other) == folded_target:
                return self.doubt(
                    _FOLD_TRUST,
                    f"{spec.name} {claimed!r} matches {channel} {other!r} only after folding "
                    "confusable characters (0/O, 1/I/l, 5/S, 8/B); one of the two channels "
                    "has a transcription error and this does not say which",
                )
        if folded_target and folded_target in fold_confusables(haystack):
            return self.doubt(
                _FOLD_TRUST,
                f"{spec.name} {claimed!r} appears in the source only after folding confusable "
                "characters; the source and the claim disagree on at least one glyph",
            )

        # Near-miss: close enough to be a corrupted copy, not close enough to accept.
        best_channel, best_score = "", 0.0
        for channel, other in channels:
            score = similarity(fold_confusables(other), folded_target)
            if score > best_score:
                best_channel, best_score = channel, score
        if best_score >= _SIMILARITY_FLOOR:
            return self.doubt(
                _FOLD_TRUST,
                f"{spec.name} {claimed!r} is {best_score:.0%} similar to {best_channel} but not "
                "equal even after folding; likely a transposition",
            )

        return self.fail(
            f"{spec.name} {claimed!r} matches neither the part number nor the description "
            f"(closest channel {best_score:.0%}); a part number nothing supports "
            "must not be published"
        )
