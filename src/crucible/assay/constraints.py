"""Cross-attribute constraint verifier.

The dimensional verifier checks each value in isolation. This one checks whether the
values are consistent *with each other* — the failure mode that produces catalog entries
which are individually plausible and jointly impossible:

    a 1/2 inch ball valve with a 200 mm bore
    a fastener whose thread length exceeds its overall length
    a bearing whose inner diameter exceeds its outer diameter
    a valve rated to 600 psi with a 150 psi flange

No per-value confidence score can catch these, because every value on its own looks
fine. Only a check that reads the record as a whole can.

Constraints are expression strings carried on the category schema, so a category can be
authored, shipped, or mined from clean catalog data without touching code:

    "bore <= body_diameter"
    "thread_length <= overall_length"
    "inner_diameter < outer_diameter"

They are evaluated over *normalized* values, so a comparison between an inch dimension
and a millimetre dimension is meaningful rather than nonsense. Evaluation uses a
restricted AST walker rather than eval(), because catalog schemas are data and data from
outside must never be executable.
"""

from __future__ import annotations

import ast
import operator
from functools import lru_cache
from typing import Any

from crucible.assay.base import Verifier
from crucible.assay.dimensional import normalize
from crucible.schema import AttributeSpec, AttributeValue, CategorySchema, ProductRecord, ValueKind
from crucible.verdict import VerifierSignal


class ConstraintError(ValueError):
    """Raised when a constraint expression is malformed or uses forbidden syntax."""


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}

_CMP_OPS = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}

#: The only callables a constraint may use. Deliberately tiny.
_FUNCTIONS = {"abs": abs, "min": min, "max": max, "round": round}


@lru_cache(maxsize=512)
def parse_constraint(expression: str) -> ast.Expression:
    """Parse and validate a constraint expression.

    Cached: the same handful of expressions are evaluated once per product across a
    whole catalog, and re-parsing them each time would dominate the verifier's cost.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ConstraintError(f"cannot parse constraint {expression!r}: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
                raise ConstraintError(
                    f"constraint {expression!r} calls something other than "
                    f"{sorted(_FUNCTIONS)}; constraints are data, not code"
                )
            continue
        if isinstance(
            node,
            ast.Expression
            | ast.Compare
            | ast.BoolOp
            | ast.UnaryOp
            | ast.BinOp
            | ast.Name
            | ast.Constant
            | ast.Load
            | ast.And
            | ast.Or
            | ast.Not
            | ast.USub
            | ast.UAdd,
        ):
            continue
        if isinstance(node, tuple(_BIN_OPS) + tuple(_CMP_OPS)):
            continue
        raise ConstraintError(
            f"constraint {expression!r} uses unsupported syntax {type(node).__name__}"
        )
    return tree


def referenced_attributes(expression: str) -> set[str]:
    """Which attribute names a constraint reads. Used to decide whether it applies."""
    tree = parse_constraint(expression)
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in _FUNCTIONS
    }


def _eval(node: ast.AST, env: dict[str, Any]) -> Any:
    """Evaluate a validated constraint node against an attribute environment."""
    match node:
        case ast.Expression():
            return _eval(node.body, env)
        case ast.Constant():
            return node.value
        case ast.Name():
            if node.id not in env:
                raise KeyError(node.id)
            return env[node.id]
        case ast.UnaryOp():
            operand = _eval(node.operand, env)
            if isinstance(node.op, ast.Not):
                return not operand
            return -operand if isinstance(node.op, ast.USub) else +operand
        case ast.BinOp():
            return _BIN_OPS[type(node.op)](_eval(node.left, env), _eval(node.right, env))
        case ast.BoolOp():
            values = [_eval(v, env) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        case ast.Compare():
            left = _eval(node.left, env)
            for op, comparator in zip(node.ops, node.comparators, strict=True):
                right = _eval(comparator, env)
                if not _CMP_OPS[type(op)](left, right):
                    return False
                left = right
            return True
        case ast.Call():
            return _FUNCTIONS[node.func.id](*[_eval(a, env) for a in node.args])  # type: ignore[attr-defined]
    raise ConstraintError(f"unsupported node {type(node).__name__}")


def build_environment(
    record: ProductRecord, schema: CategorySchema
) -> tuple[dict[str, float], dict[str, str]]:
    """Collect the record's normalized magnitudes, keyed by attribute name.

    Returns the numeric environment plus a display map of rendered values, so a
    violation can be reported with the actual numbers rather than just the expression.
    Attributes that cannot be normalized are simply absent; a constraint that needs
    them will abstain rather than guess.
    """
    env: dict[str, float] = {}
    display: dict[str, str] = {}

    for value in record.values:
        attr_spec = schema.get(value.attribute)
        if attr_spec is None:
            continue

        normalized = value.normalized or normalize(value, attr_spec)
        if normalized is None:
            continue

        if normalized.kind is ValueKind.QUANTITY and normalized.magnitude is not None:
            env[value.attribute] = normalized.magnitude
            display[value.attribute] = normalized.render()
        elif normalized.kind is ValueKind.BOOLEAN and normalized.flag is not None:
            env[value.attribute] = normalized.flag
            display[value.attribute] = normalized.render()

    return env, display


class ConstraintVerifier(Verifier):
    """Evaluates a category's physical constraints over the record as a whole."""

    name = "constraint"
    version = "0.1.0"

    def __init__(self, schema: CategorySchema) -> None:
        self.schema = schema

    def _check(
        self,
        value: AttributeValue,
        spec: AttributeSpec,
        record: ProductRecord,
    ) -> VerifierSignal:
        applicable = [
            c for c in self.schema.constraints if value.attribute in referenced_attributes(c)
        ]
        if not applicable:
            return self.abstain(f"no constraint in {self.schema.category_id} mentions {spec.name}")

        env, display = build_environment(record, self.schema)

        violations: list[str] = []
        skipped = 0
        for expression in applicable:
            try:
                holds = _eval(parse_constraint(expression), env)
            except KeyError:
                # A co-referenced attribute is missing or unnormalizable. Abstaining on
                # this constraint is right: a constraint cannot be evidence against a
                # value when half its inputs are unknown.
                skipped += 1
                continue
            except ConstraintError as exc:
                return self.abstain(f"malformed constraint {expression!r}: {exc}")

            if not holds:
                violations.append(f"{expression} [{self._witness(expression, display)}]")

        if violations:
            return self.fail(
                f"{spec.name} violates "
                + "; ".join(violations)
                + ". Values that are individually plausible but jointly impossible "
                "cannot be published at any confidence level."
            )

        evaluated = len(applicable) - skipped
        if evaluated == 0:
            return self.abstain(
                f"all {len(applicable)} constraint(s) on {spec.name} need attributes "
                "that are missing from this record"
            )
        if skipped:
            return self.doubt(
                0.7,
                f"{evaluated} of {len(applicable)} constraint(s) on {spec.name} held; "
                f"{skipped} could not be evaluated for lack of co-referenced values",
            )
        return self.ok(f"all {evaluated} constraint(s) on {spec.name} hold")

    @staticmethod
    def _witness(expression: str, display: dict[str, str]) -> str:
        """Render the actual values behind a violated constraint.

        'bore <= body_diameter' tells a reviewer what rule broke. 'bore=200 mm,
        body_diameter=15 mm' tells them why, which is the part they can act on.
        """
        names = sorted(referenced_attributes(expression) & display.keys())
        return ", ".join(f"{n}={display[n]}" for n in names) or "values unavailable"
