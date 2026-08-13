"""The walking skeleton end to end, on the real config and real fixtures."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from engine.config_loader import ConfigBundle
from engine.evidence import CONTESTED_CONFIDENCE, DerivationSource, HintStatus
from engine.journal import InMemoryJournalStore, derive_case_state
from engine.pipeline import run_pipeline
from schemas.common import Tier
from schemas.decision import ReasonKind
from schemas.events import EventType
from schemas.evidence import CompletenessVerdict
from schemas.evidence import DerivationSource as ContractDerivationSource
from tests.factories import FIXED_NOW, make_anomaly

PIPELINE_EVENTS = [
    EventType.RECEIVED,
    EventType.REDACTED,
    EventType.EXTRACTED,
    EventType.EVIDENCE_ASSEMBLED,
    # Part 09: the shadow scorer runs between evidence and decision, and writes
    # one event whether it flagged the item, cleared it, or fell over. "No
    # ANOMALY_SCORED for this case" therefore means exactly one thing - this
    # agency runs no scorer - which is what an audit trail has to be able to say.
    EventType.ANOMALY_SCORED,
    EventType.TIER_DECIDED,
    EventType.ROUTED,
]


def _payload(gold_dir: Path, name: str) -> dict[str, Any]:
    return json.loads((gold_dir / name).read_text(encoding="utf-8"))


@pytest.fixture
def complete_item(gold_v3_dir: Path) -> dict[str, Any]:
    return _payload(gold_v3_dir, "ar-0001-regelaltersrente-vollstaendig.json")


@pytest.fixture
def incomplete_item(gold_v3_dir: Path) -> dict[str, Any]:
    return _payload(gold_v3_dir, "ar-0010-ohne-versicherungsnummer.json")


@pytest.fixture
def complete_statusfeststellung(gold_v3_dir: Path) -> dict[str, Any]:
    return _payload(gold_v3_dir, "sf-0001-it-beratung-vollstaendig.json")


def test_complete_item_reaches_tier_one(
    complete_item: dict[str, Any], config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    result = run_pipeline(complete_item, config=config, journal=journal, now=FIXED_NOW)
    assert result.decision.tier is Tier.CLEAR_AND_COMPLETE
    assert result.decision.routed_unit_id == "Referat_312_Renten"
    assert result.evidence.completeness.verdict is CompletenessVerdict.COMPLETE
    assert result.extractions.discarded_count == 0
    assert result.clear_cut is True


def test_incomplete_item_reaches_tier_two_with_a_gap(
    incomplete_item: dict[str, Any], config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    result = run_pipeline(
        incomplete_item, config=config, journal=journal, now=FIXED_NOW
    )
    assert result.decision.tier is Tier.INCOMPLETE_BUT_ROUTABLE
    assert result.decision.routed_unit_id == "Referat_312_Renten"
    assert [gap.requirement_id for gap in result.evidence.completeness.gaps] == [
        "versicherungsnummer"
    ]
    assert result.extractions.discarded_count == 1


def test_every_stage_writes_its_event(
    complete_item: dict[str, Any], config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    result = run_pipeline(complete_item, config=config, journal=journal)
    events = journal.read(result.envelope.case_id)
    assert [event.type for event in events] == PIPELINE_EVENTS
    assert [event.sequence for event in events] == [0, 1, 2, 3, 4, 5, 6]
    for event in events:
        assert event.versions.decision_table_version == config.decision_table.version
        assert event.versions.taxonomy_version == config.taxonomy.version


def test_unknown_procedure_is_not_evaluable_and_defaults_to_tier_three(
    config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    """Unknown hint and nothing in the content: no procedure, no verdict."""
    result = run_pipeline(
        {
            "submissionId": "s1-unknown",
            "procedureHint": "bauantrag",
            "data": {"antrag": {"gegenstand": "Carport"}},
        },
        config=config,
        journal=journal,
    )
    assert result.procedure_id is None
    assert result.derivation.source is DerivationSource.NONE
    assert result.derivation.hint_status is HintStatus.UNKNOWN
    assert result.evidence.completeness.verdict is CompletenessVerdict.NOT_EVALUABLE
    assert result.decision.tier is Tier.FULL_HUMAN_REVIEW
    assert result.clear_cut is False


def test_an_unknown_hint_does_not_block_a_content_derivation(
    config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    """The hint names nothing this config knows; the form still does."""
    result = run_pipeline(
        {
            "submissionId": "s1-hint-unknown",
            "procedureHint": "bauantrag",
            "data": {"antrag": {"rentenart": "regelaltersrente"}},
        },
        config=config,
        journal=journal,
    )
    assert result.procedure_id == "altersrente"
    assert result.derivation.source is DerivationSource.CONTENT
    assert result.evidence.completeness.verdict is CompletenessVerdict.INCOMPLETE
    assert result.decision.tier is Tier.INCOMPLETE_BUT_ROUTABLE


def test_a_contested_routing_conflict_costs_the_tier(
    config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    """Two units at the same priority: recorded, de-confidenced, tier 3."""
    result = run_pipeline(
        {
            "submissionId": "s1-contested",
            "data": {
                "antrag": {
                    "rentenart": "regelaltersrente",
                    "rentenbeginn": "2027-03-01",
                    "eintritt_erwerbsminderung": "2025-04-01",
                }
            },
        },
        config=config,
        journal=journal,
    )
    assert result.routing.unresolved is True
    assert [candidate.unit_id for candidate in result.routing.candidates] == [
        "Referat_312_Renten",
        "Referat_316_Erwerbsminderungsrenten",
    ]
    assert result.evidence.routing[0].confidence == CONTESTED_CONFIDENCE
    assert result.decision.routed_unit_id == "Referat_312_Renten"
    assert result.decision.tier is Tier.FULL_HUMAN_REVIEW


def test_evidence_event_records_derivation_and_arbitration(
    config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    """The journal keeps the wider view even now that the record carries both."""
    result = run_pipeline(
        {
            "submissionId": "s1-journal",
            "data": {
                "antrag": {
                    "rentenart": "regelaltersrente",
                    "rentenbeginn": "2027-03-01",
                }
            },
        },
        config=config,
        journal=journal,
    )
    event = next(
        item
        for item in journal.read(result.envelope.case_id)
        if item.type is EventType.EVIDENCE_ASSEMBLED
    )
    procedure = event.payload["procedure"]
    assert isinstance(procedure, dict)
    assert procedure["source"] == "content"
    assert procedure["procedure_id"] == "altersrente"
    assert procedure["candidates"] == ["altersrente"]
    arbitration = event.payload["routing_arbitration"]
    assert isinstance(arbitration, dict)
    assert arbitration["winner_unit_id"] == "Referat_312_Renten"
    assert arbitration["unresolved"] is False
    gaps = event.payload["gaps"]
    assert isinstance(gaps, list)
    assert all(gap["request_text"] for gap in gaps)


def test_unroutable_item_writes_no_routed_event(
    config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    result = run_pipeline(
        {"submissionId": "s1-unroutable", "data": {}},
        config=config,
        journal=journal,
    )
    assert result.decision.routed_unit_id is None
    assert EventType.ROUTED not in {
        event.type for event in journal.read(result.envelope.case_id)
    }


def test_log_only_downgrade_is_journaled_but_not_applied(
    complete_item: dict[str, Any], config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    """What the scorer would have done is recorded; the tier is untouched."""
    result = run_pipeline(
        complete_item,
        config=config,
        journal=journal,
        anomaly=make_anomaly(score=0.97, flagged=True),
        now=FIXED_NOW,
    )
    assert result.decision.tier is Tier.CLEAR_AND_COMPLETE
    events = journal.read(result.envelope.case_id)
    assert EventType.ANOMALY_SCORED in {event.type for event in events}
    decided = next(event for event in events if event.type is EventType.TIER_DECIDED)
    downgrades = decided.payload["downgrades"]
    assert isinstance(downgrades, list)
    assert [entry["fired"] for entry in downgrades] == [True, True]
    assert [entry["applied"] for entry in downgrades] == [False, False]
    assert decided.payload["scorer_mode"] == "log_only"


def test_enforcing_downgrade_moves_the_tier(
    complete_item: dict[str, Any], config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    enforcing = replace(
        config, risk=config.risk.model_copy(update={"scorer_mode": "enforcing"})
    )
    result = run_pipeline(
        complete_item,
        config=enforcing,
        journal=journal,
        anomaly=make_anomaly(score=0.97, flagged=True),
        now=FIXED_NOW,
    )
    assert result.decision.tier is Tier.FULL_HUMAN_REVIEW
    assert result.decision.pre_downgrade_tier is Tier.CLEAR_AND_COMPLETE


def test_case_state_projection_matches_the_decision(
    incomplete_item: dict[str, Any], config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    result = run_pipeline(incomplete_item, config=config, journal=journal)
    state = derive_case_state(
        result.envelope.case_id, journal.read(result.envelope.case_id)
    )
    assert state.tier == int(result.decision.tier)
    assert state.routed_unit_id == result.decision.routed_unit_id
    assert state.procedure_hint == "altersrente"
    assert [gap["requirement_id"] for gap in state.gaps] == ["versicherungsnummer"]


def test_two_items_do_not_share_a_case(
    complete_item: dict[str, Any],
    incomplete_item: dict[str, Any],
    config: ConfigBundle,
    journal: InMemoryJournalStore,
) -> None:
    first = run_pipeline(complete_item, config=config, journal=journal)
    second = run_pipeline(incomplete_item, config=config, journal=journal)
    assert first.envelope.case_id != second.envelope.case_id
    assert len(journal.case_ids()) == 2


# ---------------------------------- the tier-3-by-design shape (par. 7a SGB IV) ---


def test_a_complete_statusfeststellung_defaults_to_tier_three(
    complete_statusfeststellung: dict[str, Any],
    config: ConfigBundle,
    journal: InMemoryJournalStore,
) -> None:
    """Complete, routed, confident - and still tier 3, with a DEFAULTED reason.

    The procedure ships no ``clear_cut`` block at all, so ``procedure.clear_cut``
    resolves to False and the tier-1 row cannot qualify; the verdict is
    ``complete``, so the tier-2 row cannot either. The table default applies,
    which is the intended answer for a par. 7a Abs. 2 S. 1 SGB IV
    Gesamtwuerdigung and not a hole in the config.
    """
    result = run_pipeline(
        complete_statusfeststellung, config=config, journal=journal, now=FIXED_NOW
    )
    assert config.procedures["statusfeststellung"].clear_cut is None
    assert result.procedure_id == "statusfeststellung"
    assert result.evidence.completeness.verdict is CompletenessVerdict.COMPLETE
    assert result.evidence.completeness.gaps == []
    assert result.clear_cut is False
    assert result.decision.routed_unit_id == "Referat_340_Clearingstelle"
    assert max(s.confidence for s in result.evidence.routing) == 1.0
    assert result.decision.tier is Tier.FULL_HUMAN_REVIEW
    assert [reason.kind for reason in result.decision.reasons] == [
        ReasonKind.FAILED,
        ReasonKind.FAILED,
        ReasonKind.DEFAULTED,
    ]
    defaulted = result.decision.reasons[-1]
    assert defaulted.rule_id == "default"
    assert "im Zweifel Tier 3" in defaulted.detail


def test_a_prognoseantrag_with_a_future_beginn_is_not_a_defect(
    gold_v3_dir: Path, config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    """No validator reads the clock; a Beginn in the future is par. 7a Abs. 4a."""
    result = run_pipeline(
        _payload(gold_v3_dir, "sf-0002-prognoseantrag-vor-aufnahme.json"),
        config=config,
        journal=journal,
        now=FIXED_NOW,
    )
    assert result.evidence.completeness.verdict is CompletenessVerdict.COMPLETE
    assert result.decision.tier is Tier.FULL_HUMAN_REVIEW


def test_a_widerspruch_now_routes_to_the_widerspruchsstelle(
    gold_v3_dir: Path, config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    """C-9, config half. Routing is a Realakt: it says nothing about Zulaessigkeit."""
    result = run_pipeline(
        _payload(gold_v3_dir, "xx-0005-widerspruch.json"),
        config=config,
        journal=journal,
        now=FIXED_NOW,
    )
    assert result.decision.routed_unit_id == "Widerspruchsstelle_360"
    assert result.procedure_id is None
    assert result.evidence.completeness.verdict is CompletenessVerdict.NOT_EVALUABLE
    assert result.decision.tier is Tier.FULL_HUMAN_REVIEW


# ------------------------------ ADR-016: derivation and conflicts on the record ---


def test_the_record_carries_the_derivation_outcome(
    complete_statusfeststellung: dict[str, Any],
    config: ConfigBundle,
    journal: InMemoryJournalStore,
) -> None:
    result = run_pipeline(
        complete_statusfeststellung, config=config, journal=journal, now=FIXED_NOW
    )
    derivation = result.evidence.derivation
    assert derivation is not None
    assert derivation.source is ContractDerivationSource.HINT
    assert derivation.candidates == ["statusfeststellung"]
    assert derivation.detail == result.derivation.detail
    assert result.evidence.conflicts == []


def test_the_record_carries_an_unresolved_conflict(
    gold_v3_dir: Path, config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    """xx-0006: two rules of priority 20, two units, nothing to break the tie."""
    result = run_pipeline(
        _payload(gold_v3_dir, "xx-0006-zwei-verfahren-im-formular.json"),
        config=config,
        journal=journal,
        now=FIXED_NOW,
    )
    assert len(result.evidence.conflicts) == 1
    conflict = result.evidence.conflicts[0]
    assert conflict.resolved_by == "unresolved"
    assert conflict.unit_ids == [
        "Referat_312_Renten",
        "Referat_316_Erwerbsminderungsrenten",
    ]
    derivation = result.evidence.derivation
    assert derivation is not None
    assert derivation.source is ContractDerivationSource.NONE
    assert derivation.candidates == ["altersrente", "erwerbsminderungsrente"]


def test_a_resolved_conflict_names_the_priority_that_settled_it(
    gold_v3_dir: Path, config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    """ar-0031: Auslandsbezug (priority 10) beats the Rentenart rule (20)."""
    result = run_pipeline(
        _payload(gold_v3_dir, "ar-0031-auslandsbezug.json"),
        config=config,
        journal=journal,
        now=FIXED_NOW,
    )
    assert len(result.evidence.conflicts) == 1
    conflict = result.evidence.conflicts[0]
    assert conflict.resolved_by == "priority"
    assert conflict.unit_ids[0] == "Referat_318_Auslandsrenten"
    assert result.decision.routed_unit_id == "Referat_318_Auslandsrenten"
