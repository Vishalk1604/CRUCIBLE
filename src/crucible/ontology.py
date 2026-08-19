"""Loading category schemas from disk.

Schemas are YAML rather than Python because they are the part of the system a product
manager should be able to change, and because the roadmap has them being mined from
clean catalog data rather than hand-authored. Neither is possible if a category is a
class definition.

Loading validates eagerly. A schema whose constraints reference attributes it does not
declare would fail silently at run time - the constraint verifier would abstain on every
record and the catalog would look cleaner than it is. Better to refuse at load.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import yaml

from crucible.assay.constraints import ConstraintError, referenced_attributes
from crucible.schema import CategorySchema

#: Where the shipped category definitions live, relative to the repository root.
ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "data" / "ontology"


class SchemaError(ValueError):
    """Raised when a category definition is malformed or internally inconsistent."""


def load_schema(path: Path) -> CategorySchema:
    """Read and validate one category definition."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SchemaError(f"{path.name} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise SchemaError(f"{path.name} must contain a mapping at the top level")

    try:
        schema = CategorySchema.model_validate(raw)
    except Exception as exc:
        raise SchemaError(f"{path.name} is not a valid category schema: {exc}") from exc

    _validate_constraints(schema, path.name)
    return schema


def _validate_constraints(schema: CategorySchema, source: str) -> None:
    """Check every constraint parses and references only declared attributes.

    A constraint naming an attribute that does not exist is worse than a crash: the
    verifier abstains on every record, and the catalog reports itself clean because
    nothing was ever checked.
    """
    declared = set(schema.attribute_names)
    for expression in schema.constraints:
        try:
            referenced = referenced_attributes(expression)
        except ConstraintError as exc:
            raise SchemaError(f"{source}: {exc}") from exc

        unknown = sorted(referenced - declared)
        if unknown:
            raise SchemaError(
                f"{source}: constraint {expression!r} references undeclared "
                f"attribute(s) {unknown}. A constraint that names a missing attribute "
                "silently disables itself, so this must fail at load rather than run time."
            )


@lru_cache(maxsize=1)
def load_all(directory: Path | None = None) -> dict[str, CategorySchema]:
    """Load every category definition, keyed by category_id."""
    root = directory or ONTOLOGY_DIR
    if not root.is_dir():
        raise SchemaError(f"ontology directory not found: {root}")

    schemas: dict[str, CategorySchema] = {}
    for path in sorted(root.glob("*.yaml")):
        schema = load_schema(path)
        if schema.category_id in schemas:
            raise SchemaError(f"duplicate category_id {schema.category_id!r} in {path.name}")
        schemas[schema.category_id] = schema

    if not schemas:
        raise SchemaError(f"no category definitions found in {root}")
    return schemas


def get_schema(category_id: str, directory: Path | None = None) -> CategorySchema:
    """Fetch one category by id, with a message that lists the alternatives."""
    schemas = load_all(directory)
    if category_id not in schemas:
        raise SchemaError(f"unknown category {category_id!r}; available: {sorted(schemas)}")
    return schemas[category_id]


def fingerprint(schema: CategorySchema) -> str:
    """A stable hash of a schema's checkable content.

    Recorded on the certificate so a guarantee can be tied to the exact attribute
    definitions and constraints in force when it was issued. Editing a constraint after
    the fact changes what "verified" meant, and the fingerprint makes that visible.
    """
    payload = schema.model_dump_json(include={"category_id", "attributes", "constraints"})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
