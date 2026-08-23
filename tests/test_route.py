"""Tests for taxonomy loading and category routing.

The routing tests use descriptions lifted verbatim from the 1000-row sample rather than
invented ones, because the failure modes here are all specific to how this catalog writes
things: colour names that collide with hardware nouns ("Castle Gate" on a deck board),
brands that span several categories (Milwaukee sells both the grinder and the wheel), and
trade shorthand that carries no sentence structure at all.

The single most important assertion in this file is that a generic routing leaves Dept,
Class and Fine empty. Those are published columns. A guessed department is indisputable
once it is in a spreadsheet, and nothing downstream can tell it apart from a known one.
"""

from pathlib import Path

import pytest

from crucible.ingest import read_products, to_raw_product
from crucible.route import (
    GENERIC_CATEGORY_ID,
    CascadeRouter,
    LexicalRouter,
    TaxonomyError,
    fingerprint,
    load_taxonomy,
)
from crucible.route.taxonomy import DEFAULT_TAXONOMY

SAMPLE = Path(__file__).resolve().parents[1] / "Unihack_ Sample Dataset - Input.csv"


def product(description: str, **extra: str):
    row = {"Mfg_Part_Num": "T1", "Part_Desc": description, **extra}
    return to_raw_product(row, 0)


@pytest.fixture(scope="module")
def router() -> CascadeRouter:
    return CascadeRouter()


class TestTaxonomyFile:
    def test_the_shipped_taxonomy_loads(self):
        assert load_taxonomy()

    def test_every_node_declares_all_four_classification_columns(self):
        # These four are published. A node missing one would export a blank cell that
        # looks like an abstention but is really a gap in configuration.
        for node in load_taxonomy():
            assert node.dept and node.klass and node.fine and node.classpath

    def test_classpath_is_a_delimited_path(self):
        for node in load_taxonomy():
            assert ">" in node.classpath, f"{node.category_id} classpath is not a path"

    def test_category_ids_are_unique(self):
        ids = [n.category_id for n in load_taxonomy()]
        assert len(set(ids)) == len(ids)

    def test_fingerprint_is_stable_and_order_independent(self):
        nodes = load_taxonomy()
        assert fingerprint(nodes) == fingerprint(list(reversed(nodes)))

    def test_fingerprint_changes_when_a_rule_changes(self):
        nodes = load_taxonomy()
        mutated = [n.model_copy(update={"keywords": [*n.keywords, "zzz"]}) for n in nodes]
        assert fingerprint(nodes) != fingerprint(mutated)


class TestTaxonomyValidation:
    def _write(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "t.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_rejects_a_node_with_no_matching_terms(self, tmp_path):
        # Dead configuration that reads as coverage.
        path = self._write(
            tmp_path,
            "- category_id: a.b\n  dept: D\n  klass: K\n  fine: F\n  classpath: A>B\n",
        )
        with pytest.raises(TaxonomyError, match="no matching terms"):
            load_taxonomy(path)

    def test_rejects_a_pattern_that_does_not_compile(self, tmp_path):
        path = self._write(
            tmp_path,
            "- category_id: a.b\n  dept: D\n  klass: K\n  fine: F\n  classpath: A>B\n"
            '  patterns: ["(unclosed"]\n',
        )
        with pytest.raises(TaxonomyError, match="does not compile"):
            load_taxonomy(path)

    def test_rejects_a_term_that_is_both_evidence_and_disqualifier(self, tmp_path):
        path = self._write(
            tmp_path,
            "- category_id: a.b\n  dept: D\n  klass: K\n  fine: F\n  classpath: A>B\n"
            "  keywords: [led]\n  negative: [led]\n",
        )
        with pytest.raises(TaxonomyError, match="both"):
            load_taxonomy(path)

    def test_rejects_generic_as_a_node(self, tmp_path):
        path = self._write(
            tmp_path,
            f"- category_id: {GENERIC_CATEGORY_ID}\n  dept: D\n  klass: K\n  fine: F\n"
            "  classpath: A>B\n  keywords: [x]\n",
        )
        with pytest.raises(TaxonomyError, match="fallback"):
            load_taxonomy(path)

    def test_rejects_duplicate_category_ids(self, tmp_path):
        node = (
            "- category_id: a.b\n  dept: D\n  klass: K\n  fine: F\n"
            "  classpath: A>B\n  keywords: [x]\n"
        )
        with pytest.raises(TaxonomyError, match="duplicate"):
            load_taxonomy(self._write(tmp_path, node * 2))

    def test_reports_a_missing_file(self, tmp_path):
        with pytest.raises(TaxonomyError, match="not found"):
            load_taxonomy(tmp_path / "absent.yaml")


class TestRoutingRealDescriptions:
    """Descriptions copied verbatim out of the sample dataset."""

    @pytest.mark.parametrize(
        ("description", "expected"),
        [
            ("1x6-20' Weathered Teak Grooved - Vintage Azek PVC Decking", "decking.board"),
            ("1nx6-20' Tide Pool Sq Edge - Trex Enhance Basics Decking", "decking.board"),
            ("1x12-12' Whiskey Barrel - Trex Select 2.0 Fascia", "decking.board"),
            ("S21354 8W Led T9 Med 27k", "lamp.led"),
            ("574392 40W Led B11 Med 27k 3pk", "lamp.led"),
            ('49-94-0013 Milw 5"x.045"x7/8" Metal Cut Off Disc', "abrasive.cutoff_disc"),
            ('DBD090094101F Diablo 9" - Metal Cut-Off Disc', "abrasive.cutoff_disc"),
            ('DCB518ASTS06G Diablo 1/2"x18" - Sanding Belt 6pc', "abrasive.coated"),
            ("KDTS324SPS Kitchen Aid Dishwasher SS", "appliance.major"),
            ("EUF21CDBW Element 21CF Freezer - Upright", "appliance.major"),
            ("65-771R3 Nuvo Highbay Light", "luminaire.fixture"),
            ("45297BK Kichler Wall Lt", "luminaire.fixture"),
            ("M200G-21L Milw M12 Gray - Heated Hoodie Kit L", "apparel.heated"),
            ('2563P-20 Milw M12 1/2" Stubby - Impact Wrench w/Pin Detent', "powertool.cordless"),
            ("6' Black Select Classic Horiz - Rail w/Rnd Black Alum Baluster", "decking.railing"),
            ("R00GFNT100K 15A Outlet Br", "electrical.device"),
        ],
    )
    def test_routes_to_the_expected_category(self, router, description, expected):
        assert router.route(product(description)).category_id == expected

    def test_a_colour_named_gate_is_still_a_deck_board(self, router):
        # "Castle Gate" is a finish, not hardware. This was a real misroute.
        routing = router.route(product("1x12-12' Castle Gate - Landmark Azek PVC Fascia"))
        assert routing.category_id == "decking.board"

    def test_a_milwaukee_wheel_is_an_abrasive_not_a_cordless_tool(self, router):
        # Brand narrows a catalog; it does not name a product.
        routing = router.route(product('49-94-0033 Milw 7"x1/16"x7/8" Metal Cut Off Disc'))
        assert routing.category_id == "abrasive.cutoff_disc"


class TestAbstention:
    def test_unrecognised_products_route_to_generic(self, router):
        routing = router.route(product("ZX-9 Widget Assembly Frobnicator"))
        assert routing.category_id == GENERIC_CATEGORY_ID

    def test_generic_routings_invent_no_classification(self, router):
        # The assertion this file exists for.
        routing = router.route(product("ZX-9 Widget Assembly Frobnicator"))
        assert routing.dept is None
        assert routing.klass is None
        assert routing.fine is None
        assert routing.classpath is None
        assert routing.is_generic

    def test_generic_routings_carry_zero_confidence(self, router):
        assert router.route(product("qqqq zzzz")).confidence == 0.0

    def test_a_tie_abstains_rather_than_picking_the_higher_score(self):
        # Two nodes, identical evidence. Silently preferring one would be a guess
        # reported as a classification.
        from crucible.route.taxonomy import TaxonomyNode

        nodes = [
            TaxonomyNode(
                category_id=f"tie.{i}",
                dept="D",
                klass="K",
                fine="F",
                classpath="A>B",
                strong=["widget"],
            )
            for i in (1, 2)
        ]
        routing = CascadeRouter(nodes=nodes).route(product("A widget"))
        assert routing.category_id == GENERIC_CATEGORY_ID
        assert routing.method == "ambiguous"

    def test_ambiguous_routings_still_report_what_was_considered(self, router):
        from crucible.route.taxonomy import TaxonomyNode

        nodes = [
            TaxonomyNode(
                category_id=f"tie.{i}",
                dept="D",
                klass="K",
                fine="F",
                classpath="A>B",
                strong=["widget"],
            )
            for i in (1, 2)
        ]
        routing = CascadeRouter(nodes=nodes).route(product("A widget"))
        # Refusing is not the same as having nothing to say.
        assert {c for c, _ in routing.runners_up} == {"tie.1", "tie.2"}


class TestGrounding:
    def test_every_span_quotes_text_that_is_really_there(self, router):
        raw = product("1x6-20' Weathered Teak Grooved - Vintage Azek PVC Decking")
        from crucible.ingest import erp_text

        text = erp_text(raw)
        routing = router.route(raw)
        assert routing.spans
        for span in routing.spans:
            assert span.quote
            assert text[span.start : span.end] == span.quote

    def test_a_term_does_not_match_inside_a_longer_word(self):
        # "led" is a keyword of lamp.led and "Assembled" contains it. Without word
        # boundaries the router cites a substring of an unrelated word as its reason,
        # and a deck rail panel picks up evidence for being a light bulb.
        candidates = LexicalRouter().route(product("Assembled Black Rail Panel"))
        quoted = {span.quote.casefold() for c in candidates for span in c.spans}
        assert "led" not in quoted
        assert not any(c.category_id == "lamp.led" for c in candidates)


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample dataset not present")
class TestAgainstTheRealSample:
    def test_routes_every_row_without_raising(self):
        router = CascadeRouter()
        routings = router.route_all(read_products(SAMPLE))
        assert len(routings) == 1000

    def test_classifies_a_clear_majority(self):
        # Not a target to be gamed - a regression guard. If a taxonomy edit halves
        # coverage, that should fail here rather than be discovered in an export.
        router = CascadeRouter()
        router.route_all(read_products(SAMPLE))
        assert router.stats.coverage > 0.60

    def test_no_routed_row_is_missing_a_classification(self):
        router = CascadeRouter()
        for routing in router.route_all(read_products(SAMPLE)):
            if routing.category_id != GENERIC_CATEGORY_ID:
                assert routing.dept and routing.klass and routing.fine and routing.classpath

    def test_every_routed_category_exists_in_the_taxonomy(self):
        known = {n.category_id for n in load_taxonomy(DEFAULT_TAXONOMY)} | {GENERIC_CATEGORY_ID}
        router = CascadeRouter()
        for routing in router.route_all(read_products(SAMPLE)):
            assert routing.category_id in known
