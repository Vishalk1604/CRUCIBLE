"""Tests for the identity verifier.

The two typo cases are real pairs taken from the sample catalog, not invented ones. A
verifier tested only against synthetic errors tends to encode the shape of the errors its
author imagined.
"""

from __future__ import annotations

import pytest

from crucible.assay.identity import (
    IDENTITY_ATTRIBUTES,
    IdentityVerifier,
    canonical,
    fold_confusables,
)
from crucible.schema import (
    AttributeSpec,
    AttributeValue,
    ProductRecord,
    RawProduct,
    SourceSpan,
    ValueKind,
)


def check(claim: str, mpn: str, description: str, attribute: str = "part_number"):
    spec = AttributeSpec(name=attribute, kind=ValueKind.TEXT)
    raw = RawProduct(sku=mpn, mpn=mpn, description=description)
    value = AttributeValue(
        attribute=attribute,
        raw=claim,
        spans=[SourceSpan(doc_id="erp", quote=claim, start=0, end=len(claim))],
    )
    record = ProductRecord(raw=raw, values=[value])
    return IdentityVerifier().verify(value, spec, record)


class TestNormalisation:
    def test_canonical_strips_formatting_only(self):
        assert canonical("174-0CSB3-15W") == "1740CSB315W"
        assert canonical("dcb518asts06g") == "DCB518ASTS06G"

    def test_fold_collapses_confusable_glyphs(self):
        assert fold_confusables("174-OCSB3-15A") == fold_confusables("174-0C5B3-15A")
        assert fold_confusables("IO") == fold_confusables("10")


class TestExactMatch:
    def test_matching_part_number_is_trusted(self):
        signal = check("DCB518ASTS06G", "DCB518ASTS06G", "DCB518ASTS06G Diablo Sanding Belt")
        assert signal.applicable
        assert signal.trust == 1.0

    def test_formatting_differences_do_not_count_as_errors(self):
        signal = check("dcb-518-asts06g", "DCB518ASTS06G", "DCB518ASTS06G Diablo Sanding Belt")
        assert signal.trust == 1.0


class TestRealTypos:
    """Both pairs occur in the provided sample dataset."""

    def test_transposition_is_doubted_not_accepted(self):
        signal = check("55226BKFLU", "55226BKLFU", "55226BKLFU Kichler Fixture")
        assert signal.applicable
        assert 0.0 < signal.trust < 1.0
        assert "transposition" in signal.detail or "folding" in signal.detail

    def test_zero_oh_confusion_is_doubted(self):
        signal = check("174-OCSB3-15A", "174-0CSB3-15W", "174-0CSB3-15W Lamp")
        assert 0.0 < signal.trust < 1.0

    def test_the_detail_names_what_differs(self):
        # A reviewer has to be able to act on this without opening the code.
        signal = check("55226BKFLU", "55226BKLFU", "55226BKLFU Kichler Fixture")
        assert "Mfg_Part_Num" in signal.detail


class TestFabrication:
    def test_unsupported_part_number_fails_hard(self):
        signal = check("XYZ-99999", "DCB518ASTS06G", "DCB518ASTS06G Diablo Sanding Belt")
        assert signal.applicable
        assert signal.trust == 0.0
        assert signal.is_hard_failure

    def test_brand_absent_from_source_fails(self):
        signal = check("Bosch", "DCB518", "DCB518 Diablo Sanding Belt", attribute="brand")
        assert signal.trust == 0.0

    def test_brand_present_in_source_passes(self):
        signal = check("Diablo", "DCB518", "DCB518 Diablo Sanding Belt", attribute="brand")
        assert signal.trust == 1.0


class TestAbstention:
    """Abstention must stay distinct from approval - non-negotiable #3."""

    def test_abstains_on_non_identity_attributes(self):
        signal = check("120", "DCB518", "DCB518 Sanding Belt", attribute="voltage_rating")
        assert not signal.applicable
        assert signal.trust == 0.0
        assert "not an identity claim" in signal.detail

    def test_abstains_on_empty_value(self):
        spec = AttributeSpec(name="part_number", kind=ValueKind.TEXT)
        raw = RawProduct(sku="X", mpn="X", description="something")
        value = AttributeValue(
            attribute="part_number",
            raw="   ",
            spans=[SourceSpan(doc_id="erp", quote="x", start=0, end=1)],
        )
        signal = IdentityVerifier().verify(value, spec, ProductRecord(raw=raw, values=[value]))
        assert not signal.applicable

    @pytest.mark.parametrize("attribute", sorted(IDENTITY_ATTRIBUTES))
    def test_every_declared_identity_attribute_is_handled(self, attribute):
        # None of them may fall through to an abstention when a real claim is present.
        signal = check("DCB518", "DCB518", "DCB518 Diablo Sanding Belt", attribute=attribute)
        assert signal.applicable, f"{attribute} abstained on a checkable claim"


class TestRobustness:
    def test_never_raises_on_hostile_input(self):
        for claim in ("", "   ", "!!!", "\x00", "é" * 200, '1/2"x18"'):
            signal = check(claim or "x", "DCB518", "DCB518 Belt")
            assert 0.0 <= signal.trust <= 1.0
