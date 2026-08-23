"""Tests for category schema loading.

The load-time validation here exists because of a specific silent-failure mode: a
constraint that references an attribute the schema does not declare will cause the
constraint verifier to abstain on every single record. Nothing crashes, no error is
logged, and the catalog reports itself perfectly clean because nothing was ever checked.
That is the worst possible outcome for a system whose product is trustworthiness, so it
has to fail at load.
"""

import pytest

from crucible.ontology import SchemaError, fingerprint, get_schema, load_all, load_schema


class TestShippedSchemas:
    def test_every_shipped_category_loads(self):
        # A subset assertion rather than an equality one. This was pinned to exactly the
        # three original categories, which made adding a category to the ontology a test
        # failure - precisely backwards for a system whose whole point is that categories
        # are data a product manager can add. The invariants asserted below are the
        # valuable part and they apply to every category, however many there are.
        schemas = load_all()
        assert {"valve.ball", "fastener.hex_cap_screw", "bearing.ball"} <= set(schemas)
        assert len(schemas) >= 3

    def test_category_ids_match_their_filenames(self):
        # A schema whose id disagrees with its filename loads fine and is then addressed
        # by an id nobody can find on disk.
        from crucible.ontology import ONTOLOGY_DIR

        for path in ONTOLOGY_DIR.glob("*.yaml"):
            assert load_schema(path).category_id == path.stem

    def test_delivery_labels_are_unique_within_a_category(self):
        # Two attributes sharing an ATTRIBUTE_LABEL would print the same header twice in
        # the delivery sheet and make the second unreadable to any importer.
        for category_id, schema in load_all().items():
            labels = [a.sheet_label for a in schema.attributes]
            dupes = sorted({label for label in labels if labels.count(label) > 1})
            assert not dupes, f"{category_id} repeats delivery labels: {dupes}"

    def test_every_declared_dimension_resolves_in_pint(self):
        # A typo'd dimension is invisible until a value arrives: with a canonical_unit
        # present the verifier resolves the unit instead, so the bad dimension string is
        # never exercised - right up until someone removes the canonical_unit and the
        # fallback path raises in production.
        from crucible.schema import ValueKind
        from crucible.units import registry

        for schema in load_all().values():
            for attr in schema.attributes:
                if attr.kind not in (ValueKind.QUANTITY, ValueKind.RANGE):
                    continue
                registry().get_dimensionality(attr.dimension)
                registry().Unit(attr.canonical_unit)

    def test_declared_dimension_agrees_with_canonical_unit(self):
        # The two must describe the same physics. "[length]" with canonical_unit "psi"
        # would load, and then every value of that attribute would fail dimensionally
        # while the schema looked fine.
        from crucible.schema import ValueKind
        from crucible.units import registry

        for category_id, schema in load_all().items():
            for attr in schema.attributes:
                if attr.kind not in (ValueKind.QUANTITY, ValueKind.RANGE):
                    continue
                declared = registry().get_dimensionality(attr.dimension)
                from_unit = registry().Unit(attr.canonical_unit).dimensionality
                assert declared == from_unit, (
                    f"{category_id}.{attr.name}: dimension {attr.dimension!r} and "
                    f"canonical_unit {attr.canonical_unit!r} disagree"
                )

    def test_attribute_template_order_is_stable(self):
        # ATTRIBUTE_LABEL n is a positional contract. If ordering were unstable, an
        # unrelated edit would shuffle a customer's columns and make every downstream
        # diff of the catalog meaningless.
        for schema in load_all().values():
            first = [a.name for a in schema.template()]
            assert first == [a.name for a in schema.template()]
            orders = [a.order for a in schema.template() if a.order is not None]
            assert orders == sorted(orders)

    def test_shipped_categories_declare_constraints(self):
        # A category with no constraints gets no benefit from the constraint verifier,
        # which would quietly remove one of the five signals.
        for category_id, schema in load_all().items():
            assert schema.constraints, f"{category_id} declares no constraints"

    def test_shipped_categories_declare_required_attributes(self):
        for category_id, schema in load_all().items():
            required = [a for a in schema.attributes if a.required]
            assert required, f"{category_id} marks nothing as required"

    def test_quantity_attributes_carry_canonical_units(self):
        # Without a canonical unit there is nothing to normalize into, so cross-attribute
        # constraints would compare inches against millimetres.
        from crucible.schema import ValueKind

        for category_id, schema in load_all().items():
            for attr in schema.attributes:
                if attr.kind in (ValueKind.QUANTITY, ValueKind.RANGE):
                    assert attr.canonical_unit, f"{category_id}.{attr.name} has no canonical unit"


class TestValidation:
    def test_rejects_constraint_naming_an_undeclared_attribute(self, tmp_path):
        # The silent-failure guard. Without this, the verifier abstains on everything.
        path = tmp_path / "bad.yaml"
        path.write_text(
            """
category_id: bad.category
name: Bad
attributes:
  - name: bore
    kind: quantity
    dimension: "[length]"
    canonical_unit: millimeter
constraints:
  - bore <= body_diameter
""",
            encoding="utf-8",
        )
        with pytest.raises(SchemaError, match="undeclared"):
            load_schema(path)

    def test_rejects_malformed_constraint_expression(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(
            """
category_id: bad.category
name: Bad
attributes:
  - name: bore
    kind: quantity
    dimension: "[length]"
    canonical_unit: millimeter
constraints:
  - "__import__('os').system('x')"
""",
            encoding="utf-8",
        )
        with pytest.raises(SchemaError):
            load_schema(path)

    def test_rejects_invalid_yaml(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("category_id: [unclosed\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="not valid YAML"):
            load_schema(path)

    def test_rejects_non_mapping_document(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(SchemaError, match="mapping"):
            load_schema(path)

    def test_rejects_quantity_attribute_without_dimension(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text(
            """
category_id: bad.category
name: Bad
attributes:
  - name: bore
    kind: quantity
constraints: []
""",
            encoding="utf-8",
        )
        with pytest.raises(SchemaError, match="not a valid category schema"):
            load_schema(path)


class TestLookup:
    def test_get_by_id(self):
        schema = get_schema("valve.ball")
        assert schema.name == "Ball Valves"
        assert "bore" in schema.attribute_names

    def test_unknown_category_lists_the_alternatives(self):
        with pytest.raises(SchemaError, match="available"):
            get_schema("valve.gate")

    def test_missing_directory(self, tmp_path):
        with pytest.raises(SchemaError, match="not found"):
            load_all(tmp_path / "nope")

    def test_empty_directory(self, tmp_path):
        with pytest.raises(SchemaError, match="no category definitions"):
            load_all(tmp_path)


class TestFingerprint:
    def test_is_stable_across_calls(self):
        schema = get_schema("valve.ball")
        assert fingerprint(schema) == fingerprint(schema)

    def test_differs_between_categories(self):
        assert fingerprint(get_schema("valve.ball")) != fingerprint(get_schema("bearing.ball"))

    def test_changes_when_a_constraint_changes(self):
        # A certificate names the schema it was issued against. Editing a constraint
        # afterwards changes what "verified" meant, and the fingerprint must show it.
        schema = get_schema("valve.ball")
        edited = schema.model_copy(update={"constraints": [*schema.constraints, "bore > 0"]})
        assert fingerprint(schema) != fingerprint(edited)
