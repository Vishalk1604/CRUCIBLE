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
import json
from functools import lru_cache
from pathlib import Path

import yaml

from crucible.assay.constraints import ConstraintError, referenced_attributes
from crucible.schema import AttributeSpec, CategorySchema, ValueKind

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


#: The category id used when routing could not establish one. Not a category: a record
#: that no category was established.
GENERIC_CATEGORY_ID = "generic"

#: Attributes asked of any product, regardless of what it turns out to be. Deliberately
#: few and deliberately all TEXT - see `generic_schema`.
_GENERIC_ATTRIBUTES: tuple[tuple[str, str, str], ...] = (
    ("product_name", "Product Name", "The noun that names the product, e.g. Dishwasher"),
    ("brand", "Brand", "Manufacturer or marque as written in the source"),
    ("material", "Material", "Primary material, if the source states one"),
    ("color", "Color", "Colour or finish, if the source states one"),
    ("size", "Size", "Overall size exactly as written, including its unit"),
    ("quantity", "Quantity", "Pack or selling quantity, e.g. 6pc, 3pk"),
)


@lru_cache(maxsize=1)
def generic_schema() -> CategorySchema:
    """The schema for a product nothing recognised.

    Built in code rather than shipped as YAML on purpose. `load_all()` should keep meaning
    "the categories this distributor has actually modelled", and the `test_ontology`
    invariants - every category declares constraints, required attributes, canonical units -
    should keep applying to all of them. A generic fallback satisfies none of those and
    would have to be special-cased out of every one, which weakens the checks for the real
    categories to accommodate the one that is not.

    Every attribute is TEXT, which means the dimensional and constraint verifiers abstain
    on all of them. That is the correct outcome, not a limitation: nothing here declares a
    dimension, so there is nothing to check dimensionally, and saying so is more honest
    than inventing a dimension to make a verifier fire. Because `LearnedScorer` encodes
    applicability separately from trust, generic values simply certify worse - less could
    be checked, so less is claimed - and the review queue says exactly that.
    """
    return CategorySchema(
        category_id=GENERIC_CATEGORY_ID,
        name="Uncategorised Product",
        attributes=[
            AttributeSpec(
                name=name,
                kind=ValueKind.TEXT,
                label=label,
                description=description,
                order=index,
            )
            for index, (name, label, description) in enumerate(_GENERIC_ATTRIBUTES, start=1)
        ],
        constraints=[],
    )


def resolve(category_id: str | None, directory: Path | None = None) -> CategorySchema:
    """The schema to use for a category id, falling back to generic.

    Unlike `get_schema` this never raises on an unknown id. That is the point: the
    evaluation set will contain products this catalog has never modelled, and the right
    response is to extract what can be extracted and export a valid row with blank
    classification columns - not to fail the record.
    """
    if not category_id or category_id == GENERIC_CATEGORY_ID:
        return generic_schema()
    schemas = load_all(directory)
    return schemas.get(category_id) or generic_schema()


#: Fields that describe how an attribute is *presented*, not what is *checked* about it.
#: Excluded from the fingerprint - see `fingerprint`.
_PRESENTATION = frozenset({"label", "display_uom", "order"})


def fingerprint(schema: CategorySchema) -> str:
    """A stable hash of a schema's checkable content.

    Recorded on the certificate so a guarantee can be tied to the exact attribute
    definitions and constraints in force when it was issued. Editing a constraint after
    the fact changes what "verified" meant, and the fingerprint makes that visible.

    **Presentation fields are excluded on purpose.** `label`, `display_uom` and `order`
    describe how a value is printed on a delivery sheet, not what was checked about it.
    Renaming a column heading from "Arbor Size" to "Arbor Diameter" changes nothing a
    verifier examined, so it must not invalidate a guarantee - nor a harvest cache, which
    keys on this hash and costs ~25 minutes of inference to rebuild. Including them once
    did exactly that, silently, and the next `crucible-app` launch would have re-extracted
    the whole corpus before serving its first request.
    """
    payload = json.dumps(
        {
            "category_id": schema.category_id,
            "constraints": list(schema.constraints),
            "attributes": [
                {k: v for k, v in attr.model_dump(mode="json").items() if k not in _PRESENTATION}
                for attr in schema.attributes
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
