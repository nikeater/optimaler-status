"""The predicate AST: operators, nesting, and its defensive failure mode."""

from __future__ import annotations

import pytest

from engine.predicate import (
    AllOf,
    AnyOf,
    Comparison,
    PredicateError,
    compare,
    evaluate,
    parse_predicate,
)
from schemas.config import Op

CONTEXT = {
    "procedure_hint": "altersrente",
    "channel": "fit_connect",
    "extraction.rentenart": "regelaltersrente",
    "extraction.rentenbeginn": "2026-11-01",
    "extraction.auslandsbezug": "nein",
}


@pytest.mark.parametrize(
    ("op", "left", "right", "expected"),
    [
        (Op.EQ, "a", "a", True),
        (Op.EQ, "a", "b", False),
        (Op.EQ, 1, 1.0, True),
        (Op.EQ, True, True, True),
        (Op.EQ, True, 1, False),  # a boolean never equals a number
        (Op.EQ, 1, True, False),
        (Op.NE, "a", "b", True),
        (Op.GT, 2, 1, True),
        (Op.GT, 1, 2, False),
        (Op.GE, 1, 1, True),
        (Op.LT, 1, 2, True),
        (Op.LE, 2, 1, False),
        (Op.GT, "zwei", 1, False),  # non-numeric ordering is False, not an error
        (Op.GE, True, 0, False),  # booleans never order
        (Op.IN, "a", ["a", "b"], True),
        (Op.IN, "c", ["a", "b"], False),
        (Op.IN, "ren", "regelaltersrente", True),
        (Op.IN, "a", 3, False),
    ],
)
def test_compare_operator_semantics(
    op: Op, left: object, right: object, expected: bool
) -> None:
    assert compare(op, left, right) is expected


def test_parse_and_evaluate_all_node() -> None:
    node = parse_predicate(
        {
            "all": [
                {"field": "procedure_hint", "op": "eq", "value": "altersrente"},
                {"field": "extraction.auslandsbezug", "op": "eq", "value": "nein"},
            ]
        }
    )
    assert isinstance(node, AllOf)
    assert evaluate(node, CONTEXT) is True


def test_all_node_fails_when_one_child_fails() -> None:
    node = parse_predicate(
        {
            "all": [
                {"field": "procedure_hint", "op": "eq", "value": "altersrente"},
                {"field": "extraction.auslandsbezug", "op": "eq", "value": "ja"},
            ]
        }
    )
    assert node.evaluate(CONTEXT) is False


def test_any_node_needs_one_hit() -> None:
    node = parse_predicate(
        {
            "any": [
                {"field": "procedure_hint", "op": "eq", "value": "reha"},
                {
                    "field": "extraction.rentenart",
                    "op": "eq",
                    "value": "regelaltersrente",
                },
            ]
        }
    )
    assert isinstance(node, AnyOf)
    assert node.evaluate(CONTEXT) is True


def test_nested_nodes_evaluate() -> None:
    node = parse_predicate(
        {
            "all": [
                {"field": "channel", "op": "eq", "value": "fit_connect"},
                {
                    "any": [
                        {"field": "procedure_hint", "op": "eq", "value": "reha"},
                        {"field": "procedure_hint", "op": "eq", "value": "altersrente"},
                    ]
                },
            ]
        }
    )
    assert node.evaluate(CONTEXT) is True


def test_empty_all_is_true_and_empty_any_is_false() -> None:
    assert parse_predicate({"all": []}).evaluate(CONTEXT) is True
    assert parse_predicate({"any": []}).evaluate(CONTEXT) is False


def test_unknown_field_fails_the_condition_without_raising() -> None:
    """The defensive rule: missing evidence can never satisfy a condition."""
    node = parse_predicate({"field": "extraction.gibtsnicht", "op": "eq", "value": "x"})
    assert node.evaluate(CONTEXT) is False
    negated = parse_predicate(
        {"field": "extraction.gibtsnicht", "op": "ne", "value": "x"}
    )
    assert negated.evaluate(CONTEXT) is False


def test_presence_is_tested_with_ne_null() -> None:
    present = parse_predicate(
        {"field": "extraction.rentenbeginn", "op": "ne", "value": None}
    )
    absent = parse_predicate({"field": "extraction.fehlt", "op": "ne", "value": None})
    assert present.evaluate(CONTEXT) is True
    assert absent.evaluate(CONTEXT) is False


def test_none_valued_field_fails_like_an_absent_one() -> None:
    node = parse_predicate({"field": "procedure_hint", "op": "eq", "value": None})
    assert node.evaluate({"procedure_hint": None}) is False


@pytest.mark.parametrize(
    "raw",
    [
        "not a mapping",
        {"all": {"field": "x", "op": "eq", "value": 1}},
        {"all": [], "any": []},
        {"all": [], "field": "x"},
        {"field": "x", "op": "eq"},
        {"field": "x", "op": "eq", "value": 1, "extra": True},
        {"field": 3, "op": "eq", "value": 1},
        {"field": "x", "op": 7, "value": 1},
        {"field": "x", "op": "matches", "value": 1},
        {"all": ["not a node"]},
    ],
)
def test_malformed_predicates_raise_at_parse_time(raw: object) -> None:
    """Structure errors are config bugs and must surface at load, loudly."""
    with pytest.raises(PredicateError):
        parse_predicate(raw)


def test_comparison_node_keeps_its_parts() -> None:
    node = parse_predicate({"field": "procedure_hint", "op": "eq", "value": "x"})
    assert node == Comparison(field="procedure_hint", op=Op.EQ, value="x")
