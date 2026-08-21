"""Tests for catalog coherence.

The important property is restraint. An outlier detector that fires on sparse or
genuinely dispersed attributes generates false positives, and every false positive costs
human review time - the exact resource the system exists to conserve. So most of these
tests check that it declines to have an opinion.
"""

import pytest

from crucible.assay.coherence import (
    MIN_SAMPLES,
    CatalogProfile,
    CoherenceVerifier,
    NominalProfile,
    NumericProfile,
    fit,
    spread_report,
)
from crucible.schema import (
    AttributeSpec,
    AttributeValue,
    CategorySchema,
    ProductRecord,
    RawProduct,
    ValueKind,
)

BORE = AttributeSpec(
    name="bore_diameter", kind=ValueKind.QUANTITY, dimension="[length]", canonical_unit="millimeter"
)
SEAL = AttributeSpec(name="seal_type", kind=ValueKind.NOMINAL, vocabulary=["open", "2RS"])
SCHEMA = CategorySchema(category_id="bearing.ball", name="Ball bearing", attributes=[BORE, SEAL])
SCHEMAS = {"bearing.ball": SCHEMA}


def record(sku: str, bore: str | None = None, seal: str | None = None) -> ProductRecord:
    values = []
    if bore is not None:
        values.append(AttributeValue(attribute="bore_diameter", raw=bore))
    if seal is not None:
        values.append(AttributeValue(attribute="seal_type", raw=seal))
    return ProductRecord(
        raw=RawProduct(sku=sku, description="BRG"), category_id="bearing.ball", values=values
    )


def catalog(bores: list[str], seals: list[str] | None = None) -> list[ProductRecord]:
    seals = seals or []
    return [
        record(f"B-{i}", bore=b, seal=seals[i] if i < len(seals) else None)
        for i, b in enumerate(bores)
    ]


class TestFitting:
    def test_uses_robust_statistics(self):
        # A mean would be dragged toward the outlier and then fail to flag it, which is
        # how an outlier detector gets defeated by the outliers it was built to find.
        bores = [f"{v} mm" for v in [25] * 20 + [9999]]
        profile = fit(catalog(bores), SCHEMAS)
        assert profile.numeric[("bearing.ball", "bore_diameter")].median == 25

    def test_counts_nominal_frequencies(self):
        records = catalog(["25 mm"] * 20, seals=["open"] * 15 + ["2RS"] * 5)
        profile = fit(records, SCHEMAS)
        nominal = profile.nominal[("bearing.ball", "seal_type")]
        assert nominal.frequency("open") == pytest.approx(0.75)

    def test_ignores_unparseable_values(self):
        profile = fit(catalog(["see chart"] * 20), SCHEMAS)
        assert ("bearing.ball", "bore_diameter") not in profile.numeric

    def test_ignores_unknown_categories(self):
        rec = record("X-1", bore="25 mm")
        rec.category_id = "not.a.category"
        assert len(fit([rec], SCHEMAS)) == 0


class TestRestraint:
    def test_abstains_below_the_sample_floor(self):
        # A distribution fitted on a handful of values describes those values.
        profile = fit(catalog(["25 mm"] * (MIN_SAMPLES - 1)), SCHEMAS)
        signal = CoherenceVerifier(profile).verify(
            AttributeValue(attribute="bore_diameter", raw="9999 mm"), BORE, record("B-x")
        )
        assert not signal.applicable

    def test_abstains_when_spread_is_too_wide(self):
        # Fastener length legitimately spans orders of magnitude; flagging the tails of
        # a real distribution is noise, not detection.
        bores = [f"{v} mm" for v in [1, 2, 5, 10, 25, 60, 150, 400, 900] * 4]
        profile = fit(catalog(bores), SCHEMAS)
        signal = CoherenceVerifier(profile).verify(
            AttributeValue(attribute="bore_diameter", raw="500 mm"), BORE, record("B-x")
        )
        assert not signal.applicable

    def test_abstains_with_no_variation(self):
        profile = fit(catalog(["25 mm"] * 20), SCHEMAS)
        signal = CoherenceVerifier(profile).verify(
            AttributeValue(attribute="bore_diameter", raw="25 mm"), BORE, record("B-x")
        )
        assert not signal.applicable

    def test_abstains_on_unprofiled_attributes(self):
        signal = CoherenceVerifier(CatalogProfile()).verify(
            AttributeValue(attribute="bore_diameter", raw="25 mm"), BORE, record("B-x")
        )
        assert not signal.applicable

    def test_abstains_when_value_has_no_magnitude(self):
        profile = fit(catalog([f"{v} mm" for v in [20, 25, 30] * 7]), SCHEMAS)
        signal = CoherenceVerifier(profile).verify(
            AttributeValue(attribute="bore_diameter", raw="see chart"), BORE, record("B-x")
        )
        assert not signal.applicable


class TestDetection:
    @pytest.fixture
    def verifier(self):
        # Tight enough to be usable, varied enough to have a scale.
        bores = [f"{v} mm" for v in [24, 25, 26] * 8]
        return CoherenceVerifier(fit(catalog(bores), SCHEMAS))

    def test_accepts_a_typical_value(self, verifier):
        signal = verifier.verify(
            AttributeValue(attribute="bore_diameter", raw="25 mm"), BORE, record("B-x")
        )
        assert signal.applicable
        assert signal.trust == 1.0

    def test_doubts_a_far_outlier(self, verifier):
        signal = verifier.verify(
            AttributeValue(attribute="bore_diameter", raw="520 mm"), BORE, record("B-x")
        )
        assert signal.applicable
        assert signal.trust < 0.5

    def test_outliers_are_doubted_not_rejected(self, verifier):
        # A genuine outlier exists - an unusually large part is unusual and correct - so
        # this lowers trust and lets fusion weigh it, rather than rejecting outright.
        signal = verifier.verify(
            AttributeValue(attribute="bore_diameter", raw="520 mm"), BORE, record("B-x")
        )
        assert signal.trust > 0.0

    def test_detail_explains_the_finding(self, verifier):
        signal = verifier.verify(
            AttributeValue(attribute="bore_diameter", raw="520 mm"), BORE, record("B-x")
        )
        assert "median" in signal.detail


class TestNominalDetection:
    @pytest.fixture
    def verifier(self):
        records = catalog(["25 mm"] * 40, seals=["open"] * 39 + ["2RS"])
        return CoherenceVerifier(fit(records, SCHEMAS))

    def test_accepts_a_common_value(self, verifier):
        signal = verifier.verify(
            AttributeValue(attribute="seal_type", raw="open"), SEAL, record("B-x")
        )
        assert signal.trust == 1.0

    def test_doubts_a_value_never_seen_elsewhere(self, verifier):
        signal = verifier.verify(
            AttributeValue(attribute="seal_type", raw="C3"), SEAL, record("B-x")
        )
        assert signal.applicable
        assert signal.trust < 0.3


class TestProfiles:
    def test_numeric_profile_reports_usability(self):
        assert not NumericProfile(median=25, mad=0, count=100).usable
        assert not NumericProfile(median=25, mad=1, count=2).usable
        assert NumericProfile(median=25, mad=1, count=100).usable

    def test_nominal_profile_handles_empty(self):
        assert NominalProfile().frequency("x") == 0.0

    def test_spread_report_names_abstaining_attributes(self):
        profile = fit(catalog(["25 mm"] * 3), SCHEMAS)
        assert any("too few samples" in line for line in spread_report(profile))


def test_verifier_never_raises():
    # A verifier that crashes on malformed input stops the line.
    verifier = CoherenceVerifier(fit(catalog([f"{v} mm" for v in [24, 25, 26] * 8]), SCHEMAS))
    for bad in ["", "   ", "??", "-", "1e999"]:
        signal = verifier.verify(
            AttributeValue(attribute="bore_diameter", raw=bad), BORE, record("B-x")
        )
        assert signal is not None
