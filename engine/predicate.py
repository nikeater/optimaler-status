"""The one predicate primitive of the system.

Two very different callers share it, which is why it lives at engine root:

* ``engine.evidence`` interprets ``RoutingRule.predicate`` and the per-procedure
  clear-cut criteria as a small AST over evidence values,
* ``engine.decide`` uses only :func:`compare` to evaluate decision-table
  conditions.

The decision plane deliberately imports the comparison primitive and nothing
else: evaluation of the AST is evidence-plane work, its boolean result travels
into the decision table as an ordinary qualifying field (ADR-007).

AST shape (YAML/JSON):

    {all: [node, ...]}
    {any: [node, ...]}
    {field: "<name>", op: "<op>", value: <literal>}

Evaluation rules, all defensive by design:

* an unknown field, or a field whose value is ``None``, makes the comparison
  ``False`` - never an exception, so bad config degrades toward more oversight
  rather than toward a crash or an accidental clearance;
* ordering operators need numbers on both sides, otherwise ``False``;
* ``eq``/``ne`` never conflate booleans with numbers (``True != 1`` here).

Malformed AST *structure* is a different class of problem and raises
:class:`PredicateError` at config load time, where an operator can fix it.

**Two operators live here rather than in the contract** (:class:`TextOp`,
ADR-020). ``contains`` and ``matches`` exist for the ``text.*`` namespace that
part 05 added: a normalized letter is one long string, and ``in`` compares in
the wrong direction for it (``payload.x in "literal"`` asks whether the value is
part of the literal, not whether the letter mentions the word). They are engine
vocabulary rather than schema, because ``RoutingRule.predicate`` is an opaque
mapping in the contract and a config vocabulary can grow without a contract
change. Both are CASE-INSENSITIVE: German capitalizes nouns wherever they fall,
and a rule that stopped firing because a word moved to the start of a sentence
would be a rule nobody could reason about.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache

from schemas.config import Op

Context = Mapping[str, object]

_ALL_KEY = "all"
_ANY_KEY = "any"
_COMPARISON_KEYS = frozenset({"field", "op", "value"})


class TextOp(StrEnum):
    """Operators over free text; engine vocabulary, not contract (ADR-020)."""

    CONTAINS = "contains"
    MATCHES = "matches"


#: Every operator a predicate may name, contract ones and engine ones.
AnyOp = Op | TextOp


class PredicateError(ValueError):
    """Raised when a predicate is structurally malformed (a config bug)."""


@dataclass(frozen=True)
class Comparison:
    """Leaf node: one field tested against one literal."""

    field: str
    op: AnyOp
    value: object

    def evaluate(self, context: Context) -> bool:
        left = context.get(self.field)
        if left is None:
            return False
        return compare(self.op, left, self.value)


@dataclass(frozen=True)
class AllOf:
    """Conjunction; an empty list is vacuously true."""

    nodes: tuple[PredicateNode, ...]

    def evaluate(self, context: Context) -> bool:
        return all(node.evaluate(context) for node in self.nodes)


@dataclass(frozen=True)
class AnyOf:
    """Disjunction; an empty list is false."""

    nodes: tuple[PredicateNode, ...]

    def evaluate(self, context: Context) -> bool:
        return any(node.evaluate(context) for node in self.nodes)


PredicateNode = Comparison | AllOf | AnyOf


def parse_predicate(raw: object) -> PredicateNode:
    """Parse a YAML/JSON predicate into an AST node.

    Raises:
        PredicateError: if the structure is not one of the three node shapes.
    """
    if not isinstance(raw, Mapping):
        raise PredicateError(
            f"predicate node must be a mapping, got {type(raw).__name__}"
        )
    keys = set(raw.keys())
    if _ALL_KEY in keys or _ANY_KEY in keys:
        if len(keys) != 1:
            raise PredicateError(
                f"'{_ALL_KEY}'/'{_ANY_KEY}' nodes take no other keys, "
                f"got {sorted(keys)}"
            )
        key = _ALL_KEY if _ALL_KEY in keys else _ANY_KEY
        children = raw[key]
        if not isinstance(children, Sequence) or isinstance(children, str | bytes):
            raise PredicateError(f"'{key}' must hold a list of nodes")
        nodes = tuple(parse_predicate(child) for child in children)
        return AllOf(nodes) if key == _ALL_KEY else AnyOf(nodes)
    if not keys >= _COMPARISON_KEYS:
        raise PredicateError(
            f"unknown predicate node with keys {sorted(keys)}; "
            f"expected 'all', 'any' or field/op/value"
        )
    if keys - _COMPARISON_KEYS:
        raise PredicateError(
            f"unexpected keys in comparison node: {sorted(keys - _COMPARISON_KEYS)}"
        )
    field = raw["field"]
    if not isinstance(field, str):
        raise PredicateError(f"'field' must be a string, got {type(field).__name__}")
    op_raw = raw["op"]
    if not isinstance(op_raw, str):
        raise PredicateError(f"'op' must be a string, got {type(op_raw).__name__}")
    op = parse_op(op_raw)
    if op is TextOp.MATCHES:
        # Compile at LOAD time, so a broken regular expression is a startup
        # error an operator can fix rather than a rule that silently never
        # fires. Same reason the whole predicate is parsed rather than
        # shape-checked.
        _compiled(raw["value"])
    return Comparison(field=field, op=op, value=raw["value"])


def parse_op(name: str) -> AnyOp:
    """Resolve an operator name across both vocabularies."""
    try:
        return Op(name)
    except ValueError:
        pass
    try:
        return TextOp(name)
    except ValueError as exc:
        allowed = [item.value for item in Op] + [item.value for item in TextOp]
        raise PredicateError(f"unknown operator '{name}'; allowed: {allowed}") from exc


def evaluate(node: PredicateNode, context: Context) -> bool:
    """Evaluate an AST node against a flat context mapping."""
    return node.evaluate(context)


def compare(op: AnyOp, left: object, right: object) -> bool:
    """Evaluate ``left <op> right``; never raises, unknown shapes are False."""
    if isinstance(op, TextOp):
        return _compare_text(op, left, right)
    if op is Op.EQ:
        return _equal(left, right)
    if op is Op.NE:
        return not _equal(left, right)
    if op is Op.IN:
        return _contained(left, right)
    left_number = _as_number(left)
    right_number = _as_number(right)
    if left_number is None or right_number is None:
        return False
    if op is Op.GT:
        return left_number > right_number
    if op is Op.GE:
        return left_number >= right_number
    if op is Op.LT:
        return left_number < right_number
    return left_number <= right_number


def _compare_text(op: TextOp, left: object, right: object) -> bool:
    """``contains`` and ``matches``, both case-insensitive, both never raising.

    Non-string operands are False rather than an error, exactly like every other
    operator here: a text rule pointed at a number is a config mistake that must
    degrade toward "no signal", never toward a crash and never toward a hit.
    """
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    if op is TextOp.CONTAINS:
        return right.casefold() in left.casefold()
    pattern = _cached(right)
    return pattern is not None and pattern.search(left) is not None


def _compiled(pattern: object) -> re.Pattern[str]:
    """Compile a ``matches`` pattern at LOAD time; a broken one is a config bug."""
    if not isinstance(pattern, str):
        raise PredicateError(
            f"op 'matches' needs a string pattern, got {type(pattern).__name__}"
        )
    compiled = _cached(pattern)
    if compiled is None:
        raise PredicateError(f"op 'matches' pattern {pattern!r} is not a valid regex")
    return compiled


@lru_cache(maxsize=256)
def _cached(pattern: str) -> re.Pattern[str] | None:
    """Compile once per distinct pattern; None when it does not compile.

    None rather than an exception, because this is also the evaluation path and
    evaluation never raises. Load-time validation turns the None into a loud
    error in the one place where somebody can still fix the file.
    """
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


def _equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        # A boolean only ever equals a boolean: `rule_hit == 1` must not pass.
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    left_number = _as_number(left)
    right_number = _as_number(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return bool(left == right)


def _contained(left: object, right: object) -> bool:
    if isinstance(right, list | tuple | set | frozenset):
        return any(_equal(left, item) for item in right)
    if isinstance(right, str) and isinstance(left, str):
        return left in right
    return False


def _as_number(value: object) -> float | None:
    """Coerce to float for ordering; booleans and non-numeric text stay out."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None
