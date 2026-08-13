"""Routing evidence: the rule engine, its fixtures, and arbitration."""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine.config_loader import ConfigBundle, RuleFixture
from engine.evidence import (
    ALTERNATIVE_CONFIDENCE,
    CONTESTED_CONFIDENCE,
    RULE_CONFIDENCE,
    RoutingEngine,
    build_context,
)
from engine.evidence.context import MAX_PAYLOAD_DEPTH
from schemas.config import RoutingRule
from schemas.evidence import RoutingSource
from tests.factories import make_envelope, make_extractions


def _fixtures(config: ConfigBundle) -> list[RuleFixture]:
    return config.routing.fixtures


def _rule(
    rule_id: str, unit_id: str, priority: int, *, value: str = "x"
) -> RoutingRule:
    return RoutingRule(
        rule_id=rule_id,
        unit_id=unit_id,
        predicate={"field": "probe", "op": "eq", "value": value},
        fixtures=["none"],
        priority=priority,
    )


def test_every_rule_declares_at_least_one_fixture(config: ConfigBundle) -> None:
    for rule in config.routing.rules:
        assert rule.fixtures, f"rule {rule.rule_id} ships without a fixture"


def test_rule_fixtures_still_fire(config: ConfigBundle) -> None:
    """CI runs each rule against its own fixtures (RoutingRule.fixtures)."""
    engine = RoutingEngine(config.routing.rules)
    for fixture in _fixtures(config):
        fired = {rule.rule_id for rule in engine.matching_rules(fixture.context())}
        assert set(fixture.expect_rule_ids) <= fired, (
            f"fixture {fixture.fixture_id} no longer fires {fixture.expect_rule_ids}, "
            f"fired: {sorted(fired)}"
        )


def test_rule_fixtures_declare_the_arbitration_winner(config: ConfigBundle) -> None:
    """Where a fixture names a unit, arbitration must actually pick it."""
    engine = RoutingEngine(config.routing.rules)
    for fixture in _fixtures(config):
        if fixture.expect_unit_id is None:
            continue
        outcome = engine.arbitrate(fixture.context())
        assert outcome.winner_unit_id == fixture.expect_unit_id, (
            f"fixture {fixture.fixture_id} expected {fixture.expect_unit_id}, "
            f"arbitration picked {outcome.winner_unit_id}"
        )


def test_suggestions_are_grouped_per_unit(config: ConfigBundle) -> None:
    """Rules agreeing on a unit produce one suggestion carrying all their ids."""
    engine = RoutingEngine(config.routing.rules)
    context = {
        "procedure_hint": "altersrente",
        "procedure_id": "altersrente",
        "channel": "fit_connect",
        "payload.antrag.rentenart": "regelaltersrente",
        "payload.antrag.rentenbeginn": "2026-11-01",
    }
    suggestions = engine.suggest(context)
    assert len(suggestions) == 1
    assert suggestions[0].unit_id == "Referat_312_Renten"
    assert sorted(suggestions[0].rule_ids) == [
        "rule_altersrente_hint",
        "rule_altersrente_rentenart",
        "rule_altersrente_verfahren",
    ]
    assert suggestions[0].confidence == RULE_CONFIDENCE
    assert suggestions[0].source is RoutingSource.RULE


def test_no_rule_hit_yields_no_suggestion(config: ConfigBundle) -> None:
    engine = RoutingEngine(config.routing.rules)
    outcome = engine.arbitrate({"procedure_hint": "bauantrag", "channel": "email"})
    assert outcome.suggestions == []
    assert outcome.winner_unit_id is None
    assert outcome.unresolved is False


def test_build_context_exposes_hint_channel_payload_and_extractions() -> None:
    context = build_context(
        make_envelope(
            {"antrag": {"rentenart": "regelaltersrente", "auslandsbezug": True}},
            procedure_hint="altersrente",
        ),
        make_extractions({"rentenart": "regelaltersrente"}),
        procedure_id="altersrente",
        procedure_source="hint",
    )
    assert context == {
        "procedure_hint": "altersrente",
        "channel": "fit_connect",
        "payload.antrag.rentenart": "regelaltersrente",
        "payload.antrag.auslandsbezug": "true",
        "procedure_id": "altersrente",
        "procedure_source": "hint",
        "extraction.rentenart": "regelaltersrente",
    }


def test_the_payload_namespace_skips_lists_and_empty_values() -> None:
    context = build_context(
        make_envelope(
            {
                "antrag": {"rentenart": "", "anlagen": ["a", "b"], "seiten": 3},
                "leer": {},
            }
        ),
        make_extractions({}),
    )
    assert "payload.antrag.anlagen" not in context
    assert "payload.antrag.rentenart" not in context
    assert context["payload.antrag.seiten"] == "3"


def test_unknown_field_in_a_rule_never_fires() -> None:
    engine = RoutingEngine(
        [
            RoutingRule(
                rule_id="broken",
                unit_id="Referat_390_Sonstiges",
                predicate={"field": "gibtsnicht", "op": "eq", "value": "x"},
                fixtures=["none"],
            )
        ]
    )
    assert engine.suggest({"procedure_hint": "altersrente"}) == []


def test_rules_referencing_unknown_units_are_rejected_at_load(
    config: ConfigBundle,
) -> None:
    """A rule may only route to a unit the taxonomy knows."""
    from engine.config_loader import ConfigError, _check_units_exist

    with pytest.raises(ConfigError, match="unknown unit"):
        _check_units_exist(
            [_rule("ghost", "Referat_999_Nirgendwo", 10)], config.taxonomy.nodes
        )


# ------------------------------------------------------------ arbitration ---


def test_lower_priority_number_wins_and_the_loser_is_recorded() -> None:
    engine = RoutingEngine(
        [_rule("rule_b", "Unit_B", 50), _rule("rule_a", "Unit_A", 10)]
    )
    outcome = engine.arbitrate({"probe": "x"})
    assert outcome.winner_unit_id == "Unit_A"
    assert outcome.unresolved is False
    assert [candidate.unit_id for candidate in outcome.conflicts] == ["Unit_B"]
    assert [suggestion.confidence for suggestion in outcome.suggestions] == [
        RULE_CONFIDENCE,
        ALTERNATIVE_CONFIDENCE,
    ]


def test_equal_priorities_on_different_units_are_an_unresolved_conflict() -> None:
    engine = RoutingEngine(
        [_rule("rule_b", "Unit_B", 20), _rule("rule_a", "Unit_A", 20)]
    )
    outcome = engine.arbitrate({"probe": "x"})
    assert outcome.unresolved is True
    assert outcome.winner_unit_id == "Unit_A", "ties break on rule_id, not file order"
    assert [suggestion.confidence for suggestion in outcome.suggestions] == [
        CONTESTED_CONFIDENCE,
        ALTERNATIVE_CONFIDENCE,
    ]
    assert CONTESTED_CONFIDENCE < 0.9, "a contested unit must not qualify for tier 1/2"


def test_equal_priorities_on_the_same_unit_are_not_a_conflict() -> None:
    """Two rules agreeing is agreement, however loudly they agree."""
    engine = RoutingEngine(
        [_rule("rule_b", "Unit_A", 20), _rule("rule_a", "Unit_A", 20)]
    )
    outcome = engine.arbitrate({"probe": "x"})
    assert outcome.unresolved is False
    assert outcome.conflicts == ()
    assert outcome.suggestions[0].confidence == RULE_CONFIDENCE
    assert outcome.suggestions[0].rule_ids == ["rule_a", "rule_b"]


def test_the_arbitration_payload_names_every_candidate() -> None:
    engine = RoutingEngine(
        [_rule("rule_b", "Unit_B", 20), _rule("rule_a", "Unit_A", 10)]
    )
    payload = engine.arbitrate({"probe": "x"}).as_payload()
    assert payload["winner_unit_id"] == "Unit_A"
    assert payload["unresolved"] is False
    assert payload["candidates"] == [
        {"unit_id": "Unit_A", "rule_ids": ["rule_a"], "priority": 10},
        {"unit_id": "Unit_B", "rule_ids": ["rule_b"], "priority": 20},
    ]


@given(seed=st.integers(min_value=0, max_value=10_000))
def test_arbitration_is_invariant_under_rule_shuffling(seed: int) -> None:
    """The whole point of explicit priorities: file order stops mattering."""
    rules = [
        _rule("rule_c", "Unit_C", 20),
        _rule("rule_a", "Unit_A", 20),
        _rule("rule_b", "Unit_B", 10),
        _rule("rule_d", "Unit_A", 50),
    ]
    shuffled = list(rules)
    random.Random(seed).shuffle(shuffled)
    baseline = RoutingEngine(rules).arbitrate({"probe": "x"})
    other = RoutingEngine(shuffled).arbitrate({"probe": "x"})
    assert other.candidates == baseline.candidates
    assert other.unresolved == baseline.unresolved
    assert [s.model_dump() for s in other.suggestions] == [
        s.model_dump() for s in baseline.suggestions
    ]


@given(
    priorities=st.lists(
        # The contract floors priority at 0 (schemas.config.RoutingRule).
        st.integers(min_value=0, max_value=100),
        min_size=1,
        max_size=6,
    )
)
def test_the_winner_always_holds_the_strictly_highest_confidence(
    priorities: list[int],
) -> None:
    """The decision plane picks max(confidence); that must be the winner."""
    engine = RoutingEngine(
        [
            _rule(f"rule_{index:02d}", f"Unit_{index:02d}", priority)
            for index, priority in enumerate(priorities)
        ]
    )
    outcome = engine.arbitrate({"probe": "x"})
    best = max(outcome.suggestions, key=lambda suggestion: suggestion.confidence)
    assert best.unit_id == outcome.winner_unit_id
    assert all(
        suggestion.confidence < best.confidence
        for suggestion in outcome.suggestions[1:]
    )


def test_a_part_without_a_structured_payload_contributes_nothing() -> None:
    envelope = make_envelope({"antrag": {"rentenart": "regelaltersrente"}})
    envelope.parts[0].structured_payload = None
    assert build_context(envelope, make_extractions({})) == {
        "procedure_hint": "altersrente",
        "channel": "fit_connect",
        "procedure_id": None,
        "procedure_source": None,
    }


def test_the_payload_flattener_stops_at_the_depth_limit() -> None:
    """Deeper keys are simply absent; a rule over them fails like any unknown."""
    deep: dict[str, object] = {"wert": "tief"}
    for _ in range(MAX_PAYLOAD_DEPTH + 2):
        deep = {"n": deep}
    context = build_context(make_envelope(deep), make_extractions({}))
    assert not any(key.endswith("wert") for key in context)


# --------------------------------- part 03b: statusfeststellung and C-9 ---


def test_an_auslandsbezug_does_not_move_a_statusfeststellung(
    config: ConfigBundle,
) -> None:
    """Par. 7a SGB IV knows no Auslandssonderzustaendigkeit.

    The Clearingstelle is bundesweit exklusiv zustaendig (BT-Drs. 21/1059,
    Antwort zu Frage 23), so there is deliberately NO priority-10 rule for this
    procedure. This asserts the absence: an item that would reroute an
    Altersrente to Referat 318 stays with Referat 340.
    """
    engine = RoutingEngine(config.routing.rules)
    context = {
        "procedure_hint": "statusfeststellung",
        "procedure_id": "statusfeststellung",
        "channel": "fit_connect",
        "payload.antrag.antragsart": "feststellung_nach_aufnahme",
        "payload.antrag.taetigkeit_bezeichnung": "Montage im Ausland",
        "payload.antrag.auslandsbezug": "ja",
        "payload.auftraggeber.firmenname": "Grenzwerk Anlagenbau GmbH",
    }
    outcome = engine.arbitrate(context)
    assert outcome.winner_unit_id == "Referat_340_Clearingstelle"
    assert outcome.unresolved is False
    assert outcome.conflicts == ()
    assert "rule_auslandsbezug" not in {
        rule.rule_id for rule in engine.matching_rules(context)
    }


def test_a_widerspruch_routes_to_the_widerspruchsstelle(config: ConfigBundle) -> None:
    """Compliance backlog C-9, config half; xx-0005's divergence closed."""
    engine = RoutingEngine(config.routing.rules)
    outcome = engine.arbitrate(
        {"procedure_hint": "widerspruch", "procedure_id": None, "channel": "email"}
    )
    assert outcome.winner_unit_id == "Widerspruchsstelle_360"
    assert [suggestion.confidence for suggestion in outcome.suggestions] == [
        RULE_CONFIDENCE
    ]


# ------------------------- ADR-016: conflicts as contract-shaped evidence ---


def test_a_single_candidate_is_not_a_conflict() -> None:
    engine = RoutingEngine([_rule("rule_a", "Unit_A", 20)])
    assert engine.arbitrate({"probe": "x"}).as_conflicts() == []


def test_a_resolved_conflict_names_the_priority_that_settled_it() -> None:
    engine = RoutingEngine(
        [_rule("rule_b", "Unit_B", 50), _rule("rule_a", "Unit_A", 10)]
    )
    conflicts = engine.arbitrate({"probe": "x"}).as_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].unit_ids == ["Unit_A", "Unit_B"]
    assert conflicts[0].resolved_by == "priority"
    assert conflicts[0].detail is not None
    assert "Prioritaet 10" in conflicts[0].detail


def test_an_unresolved_conflict_says_so_on_the_record() -> None:
    engine = RoutingEngine(
        [_rule("rule_b", "Unit_B", 20), _rule("rule_a", "Unit_A", 20)]
    )
    conflicts = engine.arbitrate({"probe": "x"}).as_conflicts()
    assert conflicts[0].resolved_by == "unresolved"
    assert conflicts[0].unit_ids == ["Unit_A", "Unit_B"]
