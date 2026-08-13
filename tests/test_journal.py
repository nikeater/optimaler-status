"""Journal: append-only, monotonic, and identical across both backends."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from engine.journal import (
    InMemoryJournalStore,
    JournalStore,
    JsonlJournalStore,
    SequenceConflictError,
    derive_case_state,
    emit,
    make_event,
)
from engine.journal.store import DuplicateEventError
from schemas.events import Event, EventType
from tests.factories import FIXED_NOW, TEST_VERSIONS

CASE = "case-s1-0001"


@pytest.fixture(params=["memory", "jsonl"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> JournalStore:
    """Both backends must behave identically."""
    if request.param == "memory":
        return InMemoryJournalStore()
    return JsonlJournalStore(tmp_path / "journal")


def _event(sequence: int, event_type: EventType = EventType.RECEIVED) -> Event:
    return make_event(
        case_id=CASE,
        event_type=event_type,
        sequence=sequence,
        versions=TEST_VERSIONS,
        payload={"n": sequence},
        occurred_at=FIXED_NOW,
    )


def test_append_and_read_round_trip(store: JournalStore) -> None:
    store.append(_event(0))
    store.append(_event(1, EventType.EXTRACTED))
    events = store.read(CASE)
    assert [event.sequence for event in events] == [0, 1]
    assert [event.type for event in events] == [
        EventType.RECEIVED,
        EventType.EXTRACTED,
    ]
    assert events[0].versions.schema_version == TEST_VERSIONS.schema_version


def test_unknown_case_reads_empty(store: JournalStore) -> None:
    assert store.read("case-does-not-exist") == []
    assert store.next_sequence("case-does-not-exist") == 0


def test_stale_sequence_is_rejected(store: JournalStore) -> None:
    """Optimistic concurrency: a writer working from stale state loses."""
    store.append(_event(0))
    with pytest.raises(SequenceConflictError):
        store.append(_event(0))
    with pytest.raises(SequenceConflictError):
        store.append(_event(5))
    assert len(store.read(CASE)) == 1


def test_duplicate_event_id_is_rejected(store: JournalStore) -> None:
    event = _event(0)
    store.append(event)
    replayed = event.model_copy(update={"sequence": 1})
    with pytest.raises(DuplicateEventError):
        store.append(replayed)


def test_emit_assigns_the_next_sequence(store: JournalStore) -> None:
    for _ in range(3):
        emit(
            store,
            case_id=CASE,
            event_type=EventType.RECEIVED,
            versions=TEST_VERSIONS,
            occurred_at=FIXED_NOW,
        )
    assert [event.sequence for event in store.read(CASE)] == [0, 1, 2]
    assert store.next_sequence(CASE) == 3


def test_case_ids_are_listed(store: JournalStore) -> None:
    store.append(_event(0))
    assert store.case_ids() == [CASE]


def test_events_are_never_mutated_in_place(store: JournalStore) -> None:
    """Reading must not hand out a handle onto the store's own list."""
    store.append(_event(0))
    events = store.read(CASE)
    events.clear()
    assert len(store.read(CASE)) == 1


def test_jsonl_store_survives_a_reopen(tmp_path: Path) -> None:
    directory = tmp_path / "journal"
    first = JsonlJournalStore(directory)
    first.append(_event(0))
    second = JsonlJournalStore(directory)
    assert second.next_sequence(CASE) == 1
    assert second.read(CASE)[0].payload == {"n": 0}


def test_jsonl_store_rejects_unsafe_case_ids(tmp_path: Path) -> None:
    store = JsonlJournalStore(tmp_path / "journal")
    with pytest.raises(ValueError, match="filesystem-safe"):
        store.read("../../etc/passwd")


def test_notified_events_must_be_informational(store: JournalStore) -> None:
    """Contract check exercised through the store helper (ADR-005)."""
    with pytest.raises(ValueError, match="informational_only"):
        make_event(
            case_id=CASE,
            event_type=EventType.NOTIFIED,
            sequence=0,
            versions=TEST_VERSIONS,
        )


def test_derive_case_state_folds_the_pipeline_events() -> None:
    events = [
        make_event(
            case_id=CASE,
            event_type=EventType.RECEIVED,
            sequence=0,
            versions=TEST_VERSIONS,
            payload={
                "envelope_id": "env-1",
                "channel": "fit_connect",
                "procedure_hint": "altersrente",
            },
            occurred_at=FIXED_NOW,
        ),
        make_event(
            case_id=CASE,
            event_type=EventType.EXTRACTED,
            sequence=1,
            versions=TEST_VERSIONS,
            payload={"fields": ["geburtsdatum"], "discarded_count": 1},
            occurred_at=FIXED_NOW,
        ),
        make_event(
            case_id=CASE,
            event_type=EventType.EVIDENCE_ASSEMBLED,
            sequence=2,
            versions=TEST_VERSIONS,
            payload={
                "routing": [{"unit_id": "Referat_312_Renten"}],
                "completeness_verdict": "incomplete",
                "gaps": [{"requirement_id": "versicherungsnummer"}],
                "clear_cut": True,
            },
            occurred_at=FIXED_NOW,
        ),
        make_event(
            case_id=CASE,
            event_type=EventType.TIER_DECIDED,
            sequence=3,
            versions=TEST_VERSIONS,
            payload={
                "tier": 2,
                "pre_downgrade_tier": 2,
                "reasons": [{"kind": "qualified"}],
                "downgrades": [
                    {"rule_id": "downgrade_anomaly_flagged", "fired": False}
                ],
            },
            occurred_at=FIXED_NOW,
        ),
        make_event(
            case_id=CASE,
            event_type=EventType.ROUTED,
            sequence=4,
            versions=TEST_VERSIONS,
            payload={"unit_id": "Referat_312_Renten"},
            occurred_at=FIXED_NOW,
        ),
    ]
    state = derive_case_state(CASE, events)
    assert state.event_count == 5
    assert state.procedure_hint == "altersrente"
    assert state.extracted_fields == ["geburtsdatum"]
    assert state.discarded_count == 1
    assert state.completeness_verdict == "incomplete"
    assert state.clear_cut is True
    assert state.tier == 2
    assert state.routed_unit_id == "Referat_312_Renten"
    assert state.last_event_type is EventType.ROUTED
    assert isinstance(state.received_at, datetime)


def test_derive_case_state_ignores_malformed_payloads() -> None:
    """A rendering bug must not be able to take down the audit trail."""
    event = make_event(
        case_id=CASE,
        event_type=EventType.TIER_DECIDED,
        sequence=0,
        versions=TEST_VERSIONS,
        payload={"tier": "zwei", "reasons": "kaputt", "downgrades": None},
        occurred_at=FIXED_NOW,
    )
    state = derive_case_state(CASE, [event])
    assert state.tier is None
    assert state.reasons == []
    assert state.shadow_downgrades == []


def test_store_satisfies_the_protocol(store: JournalStore) -> None:
    assert isinstance(store, JournalStore)
    assert callable(store.append)
