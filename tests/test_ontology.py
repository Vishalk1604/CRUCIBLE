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
        schemas = load_all()
        assert set(schemas) == {"valve.ball", "fastener.hex_cap_screw", "bearing.ball"}

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
