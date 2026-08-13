"""The review package's refusal branches, one by one.

`tests/test_review_flows.py` drives the happy paths through the real app. This
file exercises the edges: the arguments a caller can get wrong, the payloads a
journal can carry after a partial write, and the states a projection has to
survive without raising. Every branch here is either a refusal that protects the
append-only journal or a degradation that keeps a rendering bug from taking the
audit trail down, which is why they are worth their own tests rather than being
left to coverage arithmetic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from engine.config_loader import ConfigBundle
from engine.dispatch import (
    DispatchFacts,
    build_stub_xml,
    dispatch_dir,
    stub_filename,
    write_stub,
)
from engine.draft.bekanntgabe import response_deadline
from engine.draft.store import DraftRecord
from engine.journal import InMemoryJournalStore, emit
from engine.review import (
    CLEARING_QUEUE,
    ReviewActionError,
    ReviewIndex,
    build_index,
    build_queue,
    confirm_case,
    escalate_case,
    override_case,
    queue_census,
    review_metrics,
    review_state,
)
from engine.review.metrics import MIN_UNIT_ITEMS, TOO_FEW, UnitReview
from engine.review.queues import queue_ids
from engine.review.state import OVERRIDE_TIER, OVERRIDE_UNIT
from schemas.common import VersionStamp
from schemas.events import Actor, ActorKind, EventType

UNIT = "Referat_312_Renten"
NOW = datetime(2026, 3, 4, 9, 0, tzinfo=UTC)


def _journal_with_decision(
    *, tier: int = 2, unit_id: str | None = UNIT, case_id: str = "case-unit-test"
) -> InMemoryJournalStore:
    """The minimum journal a review action needs: received, decided, routed."""
    store = InMemoryJournalStore()
    versions = VersionStamp(schema_version="0.1.0")
    emit(
        store,
        case_id=case_id,
        event_type=EventType.RECEIVED,
        versions=versions,
        occurred_at=NOW - timedelta(days=2),
        payload={"envelope_id": "env-unit-test", "channel": "email"},
    )
    emit(
        store,
        case_id=case_id,
        event_type=EventType.TIER_DECIDED,
        versions=versions,
        occurred_at=NOW - timedelta(days=2),
        payload={"envelope_id": "env-unit-test", "tier": tier, "reasons": []},
    )
    if unit_id is not None:
        emit(
            store,
            case_id=case_id,
            event_type=EventType.ROUTED,
            versions=versions,
            occurred_at=NOW - timedelta(days=2),
            payload={"unit_id": unit_id, "tier": tier},
        )
    return store


# ------------------------------------------------------------- refusals ----


def test_an_action_on_no_events_is_refused(config: ConfigBundle) -> None:
    with pytest.raises(ReviewActionError, match="no events"):
        confirm_case([], config=config, journal=InMemoryJournalStore(), unit_id=UNIT)


def test_an_action_without_a_unit_is_refused(config: ConfigBundle) -> None:
    """An unattributed confirmation proves nothing about human involvement."""
    store = _journal_with_decision()
    with pytest.raises(ReviewActionError, match="unit-scoped"):
        confirm_case(
            store.read("case-unit-test"), config=config, journal=store, unit_id="   "
        )
    assert len(store.read("case-unit-test")) == 3


def test_an_unknown_override_field_is_refused(config: ConfigBundle) -> None:
    """The pool stays a labelled set only while the labels are a closed set."""
    store = _journal_with_decision()
    with pytest.raises(ReviewActionError, match="unknown override field"):
        override_case(
            store.read("case-unit-test"),
            config=config,
            journal=store,
            unit_id=UNIT,
            field="priority",
            to_value="hoch",
            reason="dringend",
        )


def test_an_override_that_changes_nothing_is_refused(config: ConfigBundle) -> None:
    store = _journal_with_decision()
    with pytest.raises(ReviewActionError, match="would not change"):
        override_case(
            store.read("case-unit-test"),
            config=config,
            journal=store,
            unit_id=UNIT,
            field=OVERRIDE_UNIT,
            to_value=UNIT,
            reason="unveraendert",
        )


def test_a_tier_override_records_the_machine_answer(config: ConfigBundle) -> None:
    store = _journal_with_decision(tier=2)
    override_case(
        store.read("case-unit-test"),
        config=config,
        journal=store,
        unit_id=UNIT,
        field=OVERRIDE_TIER,
        to_value=1,
        reason="Unterlagen lagen bereits vor",
        now=NOW,
    )
    payload = store.read("case-unit-test")[-1].payload
    assert payload["from"] == 2
    assert payload["to"] == 1
    assert payload["machine_tier"] == 2
    state = review_state("case-unit-test", store.read("case-unit-test"))
    assert state.tier == 1
    assert state.machine_tier == 2
    assert state.tier_changed is True
    assert state.escalated is False


def test_escalating_a_tier_three_item_is_refused(config: ConfigBundle) -> None:
    store = _journal_with_decision(tier=3)
    with pytest.raises(ReviewActionError, match="already in full human review"):
        escalate_case(
            store.read("case-unit-test"), config=config, journal=store, unit_id=UNIT
        )
    assert len(store.read("case-unit-test")) == 3


# --------------------------------------------------------- dispatch paths ----


def test_confirming_without_a_draft_store_says_why_nothing_was_sent(
    config: ConfigBundle,
) -> None:
    store = _journal_with_decision()
    outcome = confirm_case(
        store.read("case-unit-test"),
        config=config,
        journal=store,
        unit_id=UNIT,
        now=NOW,
    )
    assert outcome.facts is None
    assert outcome.dispatch_skipped == "kein Entwurfsspeicher angebunden"
    assert outcome.event.payload["dispatched"] is False
    assert outcome.event.payload["dispatch_skipped"] == outcome.dispatch_skipped


def test_a_caseworker_can_confirm_without_dispatching(config: ConfigBundle) -> None:
    """Deselecting dispatch is a real state, not an error."""
    store = _journal_with_decision()
    outcome = confirm_case(
        store.read("case-unit-test"),
        config=config,
        journal=store,
        unit_id=UNIT,
        dispatch=False,
        now=NOW,
    )
    assert outcome.dispatch_skipped == "Versand vom Bearbeiter abgewaehlt"


def test_the_stub_filename_is_a_pure_function_of_case_and_draft() -> None:
    first = stub_filename("case-a", "draft-1")
    assert first == stub_filename("case-a", "draft-1")
    assert first != stub_filename("case-a", "draft-2")
    assert first != stub_filename("case-b", "draft-1")
    assert first.startswith("case-a-") and first.endswith(".xml")


def test_the_stub_writer_refuses_an_unsafe_identifier(tmp_path) -> None:
    facts = _facts(case_id="../escape")
    with pytest.raises(ValueError, match="filesystem-safe"):
        write_stub(
            tmp_path,
            facts=facts,
            xml="<x/>",
            format_id="xdomea_shaped_stub_v0",
        )


def test_a_prepared_decision_stub_carries_no_deadline_element() -> None:
    """A Bewilligungsentwurf asks for nothing, so it has no response window."""
    xml = build_stub_xml(
        _facts(kind="prepared_decision"), format_id="xdomea_shaped_stub_v0"
    )
    assert "Fristablauf" not in xml
    assert "Versanddatum" in xml


def test_a_nachforderung_stub_carries_the_deadline_and_its_basis() -> None:
    deadline = response_deadline(date(2026, 3, 4), window_days=30)
    xml = build_stub_xml(_facts(deadline=deadline), format_id="xdomea_shaped_stub_v0")
    assert f"<Fristablauf>{deadline.deadline.isoformat()}</Fristablauf>" in xml
    assert "par. 37 Abs. 2 SGB X" in xml


def test_the_dispatch_directory_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("EINGANGSLOTSE_DISPATCH_DIR", raising=False)
    assert dispatch_dir() is None
    monkeypatch.setenv("EINGANGSLOTSE_DISPATCH_DIR", str(tmp_path))
    assert dispatch_dir() == tmp_path


def _facts(
    *,
    case_id: str = "case-unit-test",
    kind: str = "nachforderung",
    deadline: object = None,
) -> DispatchFacts:
    return DispatchFacts(
        case_id=case_id,
        envelope_id="env-unit-test",
        draft_id="draft-1",
        draft_kind=kind,
        template_id="nachforderung_v1",
        unit_id=UNIT,
        procedure_id="altersrente",
        dispatch_shape="postal",
        dispatch_channel="email",
        dispatched_at=datetime(2026, 3, 4, 9, 0),
        dispatch_date=date(2026, 3, 4),
        deadline=deadline,  # type: ignore[arg-type]
        land="[Platzhalter]",
        holiday_count=0,
    )


def test_the_facts_timestamp_is_normalized_to_utc_when_naive() -> None:
    """A naive clock in a test must not produce an ambiguous journal entry."""
    payload = _facts().as_payload()
    assert isinstance(payload["dispatched_at"], str)
    assert payload["dispatched_at"].endswith("+00:00")


# -------------------------------------------------------------- projections --


def test_a_malformed_override_payload_degrades_instead_of_raising() -> None:
    """The part-01 discipline: a rendering bug may not kill the case view."""
    store = _journal_with_decision()
    emit(
        store,
        case_id="case-unit-test",
        event_type=EventType.OVERRIDDEN,
        versions=VersionStamp(schema_version="0.1.0"),
        actor=Actor(kind=ActorKind.CASEWORKER, unit_id=UNIT),
        occurred_at=NOW,
        payload={"field": OVERRIDE_UNIT, "to": None, "reason": ""},
    )
    emit(
        store,
        case_id="case-unit-test",
        event_type=EventType.OVERRIDDEN,
        versions=VersionStamp(schema_version="0.1.0"),
        actor=Actor(kind=ActorKind.CASEWORKER, unit_id=UNIT),
        occurred_at=NOW,
        payload={"field": OVERRIDE_TIER, "to": "drei"},
    )
    state = review_state("case-unit-test", store.read("case-unit-test"))
    # A null unit reads as "no unit" (the clearing queue), a non-integer tier
    # is ignored - neither raises, and both are visible in the override list.
    assert state.unit_id is None
    assert state.tier == 2
    assert len(state.overrides) == 2


def test_a_case_with_no_received_event_still_has_a_queue_row(
    config: ConfigBundle,
) -> None:
    """An age it cannot compute reads 'unbekannt', not a crash and not zero."""
    store = InMemoryJournalStore()
    emit(
        store,
        case_id="case-truncated",
        event_type=EventType.TIER_DECIDED,
        versions=VersionStamp(schema_version="0.1.0"),
        occurred_at=NOW,
        payload={"tier": 3, "reasons": []},
    )
    queue = build_queue(build_index(store), unit_id=None, now=NOW, config=config.queues)
    assert queue.count == 1
    row = queue.rows[0]
    assert row.age_hours is None
    assert row.age_label == "unbekannt"
    assert row.over_budget is False


@pytest.mark.parametrize(
    ("hours", "expected"),
    [(0.25, "15 Min."), (5.0, "5.0 Std."), (100.0, "4.2 Tage")],
)
def test_the_age_label_reads_as_a_human_reads_it(
    hours: float, expected: str, config: ConfigBundle
) -> None:
    store = _journal_with_decision()
    moment = NOW - timedelta(days=2) + timedelta(hours=hours)
    queue = build_queue(
        build_index(store), unit_id=UNIT, now=moment, config=config.queues
    )
    assert queue.rows[0].age_label == expected


def test_queues_work_without_a_queue_config(config: ConfigBundle) -> None:
    """A deployment with no config/queues/ gets queues and no clocks."""
    store = _journal_with_decision()
    queue = build_queue(build_index(store), unit_id=UNIT, now=NOW, config=None)
    assert queue.count == 1
    assert queue.rows[0].flags == ()
    assert queue.rows[0].budget_hours is None
    assert queue.note == ""
    del config


def test_the_clearing_queue_is_always_listed(config: ConfigBundle) -> None:
    store = _journal_with_decision()
    ids = queue_ids(build_index(store), config=config.queues)
    assert ids == [UNIT, CLEARING_QUEUE]
    empty = queue_ids(ReviewIndex(), config=config.queues)
    assert empty == [CLEARING_QUEUE]


def test_a_confirmed_case_leaves_every_queue(config: ConfigBundle) -> None:
    store = _journal_with_decision()
    confirm_case(
        store.read("case-unit-test"),
        config=config,
        journal=store,
        unit_id=UNIT,
        now=NOW,
    )
    index = build_index(store)
    assert index.open_states() == []
    assert build_queue(index, unit_id=UNIT, now=NOW, config=config.queues).count == 0
    assert index.get("case-unit-test") is not None
    assert index.get("case-nope") is None
    assert [state.case_id for state in index.by_unit(UNIT)] == ["case-unit-test"]


def test_a_reha_item_without_a_received_event_says_the_clock_cannot_run(
    config: ConfigBundle,
) -> None:
    store = InMemoryJournalStore()
    emit(
        store,
        case_id="case-reha-truncated",
        event_type=EventType.ROUTED,
        versions=VersionStamp(schema_version="0.1.0"),
        occurred_at=NOW,
        payload={"unit_id": "Referat_320_Reha", "tier": 3},
    )
    queue = build_queue(
        build_index(store),
        unit_id="Referat_320_Reha",
        now=NOW,
        config=config.queues,
    )
    flag = next(f for f in queue.rows[0].flags if f.flag_id == "reha_frist")
    assert "nicht im Journal" in flag.detail


# ------------------------------------------------------------------ metrics --


def test_a_unit_at_the_floor_starts_reporting_rates() -> None:
    """The five-confirmation floor, from both sides."""
    below = UnitReview(unit_id=UNIT, confirmed=MIN_UNIT_ITEMS - 1)
    at = UnitReview(
        unit_id=UNIT,
        confirmed=MIN_UNIT_ITEMS,
        confirmed_without_edit=3,
        latencies_seconds=(10.0, 20.0, 30.0),
    )
    assert below.reportable is False
    assert below.confirm_without_edit_rate is None
    assert below.override_rate is None
    assert below.median_seconds_to_confirm is None
    assert below.as_payload()["suppressed_reason"] == TOO_FEW
    assert at.reportable is True
    assert at.confirm_without_edit_rate == pytest.approx(0.6)
    assert at.override_rate == pytest.approx(0.0)
    assert at.median_seconds_to_confirm == pytest.approx(20.0)
    assert at.as_payload()["suppressed_reason"] is None


def test_a_unit_with_confirmations_but_no_latency_reports_none() -> None:
    unit = UnitReview(unit_id=UNIT, confirmed=MIN_UNIT_ITEMS, latencies_seconds=())
    assert unit.median_seconds_to_confirm is None


def test_metrics_over_an_empty_index_are_zeros_and_notes() -> None:
    payload = review_metrics(ReviewIndex(), now=NOW).as_payload()
    assert payload["open_items"] == 0
    assert payload["units"] == []
    assert payload["backlog"] == []
    assert any("BPersVG" in note for note in payload["notes"])


def test_an_unrouted_case_belongs_to_no_unit_metric(config: ConfigBundle) -> None:
    """A case nobody owns cannot be attributed to a unit's confirm rate."""
    store = _journal_with_decision(unit_id=None, tier=3)
    metrics = review_metrics(build_index(store), now=NOW, config=config.queues)
    assert metrics.units == ()
    assert metrics.open_items == 1
    assert [row.tier for row in metrics.backlog] == [3]


def test_the_census_counts_and_refuses_to_report_ages(config: ConfigBundle) -> None:
    store = _journal_with_decision()
    census = queue_census(build_index(store), config=config.queues)
    assert census["open_items"] == 1
    assert census["by_unit"] == {UNIT: 1}
    assert census["by_tier"] == {"2": 1}
    assert census["clearing_queue"] == 0
    assert "Nur Anzahlen" in census["note"]
    assert not any("hours" in key or "age" in key for key in census)


def test_the_census_works_without_a_queue_config() -> None:
    store = _journal_with_decision()
    census = queue_census(build_index(store), config=None)
    assert census["widerspruch_frist_laeuft"] == 0
    assert census["reha_par14_clock"] == 0


# ------------------------------------------------- the remaining edges ----


def test_confirming_a_case_with_no_draft_of_its_own_says_so(
    config: ConfigBundle,
) -> None:
    """A draft store is attached, and this case simply owes no letter."""
    from engine.draft import InMemoryDraftStore

    store = _journal_with_decision(tier=3)
    outcome = confirm_case(
        store.read("case-unit-test"),
        config=config,
        journal=store,
        unit_id=UNIT,
        drafts=InMemoryDraftStore(),
        now=NOW,
    )
    assert outcome.dispatch_skipped == "kein Entwurf zu diesem Vorgang"
    assert outcome.draft is None


def test_the_par66_rerender_needs_the_vault_and_says_why(
    config: ConfigBundle,
) -> None:
    """The block goes into a letter addressed to a person; no vault, no letter."""
    from engine.draft import InMemoryDraftStore

    store = _journal_with_decision()
    drafts = InMemoryDraftStore()
    drafts.save(_nachforderung_record())
    with pytest.raises(ReviewActionError, match="no identity vault"):
        confirm_case(
            store.read("case-unit-test"),
            config=config,
            journal=store,
            unit_id=UNIT,
            drafts=drafts,
            vault=None,
            rechtsfolgenhinweis=True,
            now=NOW,
        )


def test_the_par66_rerender_refuses_when_the_case_facts_are_gone(
    config: ConfigBundle,
) -> None:
    """A stored draft whose source event the journal no longer explains."""
    from engine.draft import InMemoryDraftStore
    from engine.redact import InMemoryVaultStore

    store = _journal_with_decision()
    drafts = InMemoryDraftStore()
    drafts.save(_nachforderung_record(source_event_id="an-event-nobody-wrote"))
    with pytest.raises(ReviewActionError, match="no owed draft matches"):
        confirm_case(
            store.read("case-unit-test"),
            config=config,
            journal=store,
            unit_id=UNIT,
            drafts=drafts,
            vault=InMemoryVaultStore(),
            rechtsfolgenhinweis=True,
            now=NOW,
        )


def test_a_confirmation_without_a_decision_reports_no_latency(
    config: ConfigBundle,
) -> None:
    """P-6's latency needs a decision timestamp; a truncated journal has none."""
    store = InMemoryJournalStore()
    emit(
        store,
        case_id="case-no-decision",
        event_type=EventType.ROUTED,
        versions=VersionStamp(schema_version="0.1.0"),
        occurred_at=NOW,
        payload={"unit_id": UNIT, "tier": 2},
    )
    outcome = confirm_case(
        store.read("case-no-decision"),
        config=config,
        journal=store,
        unit_id=UNIT,
        now=NOW,
    )
    assert outcome.event.payload["seconds_since_decision"] is None
    metrics = review_metrics(build_index(store), now=NOW, config=config.queues)
    unit = next(row for row in metrics.units if row.unit_id == UNIT)
    assert unit.latencies_seconds == ()


def test_metrics_count_escalations_and_reroutes_separately(
    config: ConfigBundle,
) -> None:
    store = _journal_with_decision(tier=1)
    override_case(
        store.read("case-unit-test"),
        config=config,
        journal=store,
        unit_id=UNIT,
        field=OVERRIDE_UNIT,
        to_value="Referat_318_Auslandsrenten",
        reason="Auslandsbezug",
        now=NOW,
    )
    escalate_case(
        store.read("case-unit-test"),
        config=config,
        journal=store,
        unit_id="Referat_318_Auslandsrenten",
        now=NOW,
    )
    metrics = review_metrics(build_index(store), now=NOW, config=config.queues)
    unit = next(
        row for row in metrics.units if row.unit_id == "Referat_318_Auslandsrenten"
    )
    assert unit.overridden == 1
    assert unit.escalated == 1
    assert unit.rerouted == 1
    assert unit.confirmed == 0


def test_a_case_with_no_tier_is_left_out_of_the_backlog(
    config: ConfigBundle,
) -> None:
    """P-10 counts tiers; an item the decision plane never reached has none."""
    store = InMemoryJournalStore()
    emit(
        store,
        case_id="case-no-tier",
        event_type=EventType.RECEIVED,
        versions=VersionStamp(schema_version="0.1.0"),
        occurred_at=NOW,
        payload={"envelope_id": "env-no-tier", "channel": "email"},
    )
    metrics = review_metrics(build_index(store), now=NOW, config=config.queues)
    assert metrics.open_items == 1
    assert metrics.backlog == ()


def test_the_anomaly_and_escalation_flags_render_with_their_words(
    config: ConfigBundle,
) -> None:
    """Both are 'attention', and both say what they mean in a sentence."""
    store = _journal_with_decision(tier=1)
    emit(
        store,
        case_id="case-unit-test",
        event_type=EventType.ANOMALY_SCORED,
        versions=VersionStamp(schema_version="0.1.0"),
        occurred_at=NOW,
        payload={"score": 0.91, "flagged": True, "mode": "log_only", "reasons": []},
    )
    escalate_case(
        store.read("case-unit-test"), config=config, journal=store, unit_id=UNIT
    )
    queue = build_queue(build_index(store), unit_id=UNIT, now=NOW, config=config.queues)
    flags = {flag.flag_id: flag for flag in queue.rows[0].flags}
    assert flags["anomaly"].tone == "attention"
    assert "kein Befund ueber eine Person" in flags["anomaly"].detail
    assert flags["escalated"].tone == "attention"
    assert "P-4" in flags["escalated"].detail


def _nachforderung_record(source_event_id: str = "") -> DraftRecord:
    """A stored Nachforderung for the case ``_journal_with_decision`` builds."""
    return DraftRecord(
        draft_id="draft-unit-test",
        case_id="case-unit-test",
        envelope_id="env-unit-test",
        kind="nachforderung",
        template_id="nachforderung_v1",
        procedure_id="altersrente",
        tier=2,
        requirement_ids=["rentenbeginn"],
        subject="Nachforderung",
        body="Sehr geehrte Damen und Herren,",
        resolved_tokens=0,
        distinct_tokens=0,
        response_window_days=30,
        rechtsfolgenhinweis=False,
        source_event_id=source_event_id or "an-event-nobody-wrote",
        drafting_version="drafting_v1",
        created_at=NOW,
    )
