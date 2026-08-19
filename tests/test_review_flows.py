"""The human half, end to end through the real app.

Every test in this file drives the actual FastAPI application over HTTP:
ingest, then queue, then case view, then a form POST. That matters more here
than anywhere else in the suite, because the thing under test is not a
function - it is whether a caseworker's click appends the right event and
nothing else.

Four invariants get their own tests rather than a shared assertion, because
each of them is a different way the system could stop being what it claims:

1. **Append-only.** Every action adds exactly one event (two when a par. 66
   opt-in re-renders the letter) and changes none.
2. **The routing answer is the ROUTED event.** A queue never rebuilds a unit
   from the evidence, which can carry suggestions from sources the agency has
   not admitted (the part-06 finding).
3. **A sampled case is not a suspicious one** (ADR-025), in either journal
   shape.
4. **The deadline is stamped at dispatch**, from the dispatch date, with
   weekends and injected holidays shifting it (par. 37 Abs. 2, par. 26 Abs. 3
   SGB X).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.review import build_case_view, build_overview, build_queue_view
from engine.config_loader import ConfigBundle
from engine.dispatch import DISPATCH_DIR_ENV, stub_filename
from engine.draft import InMemoryDraftStore, draft_case
from engine.draft.bekanntgabe import response_deadline
from engine.draft.projection import facts_from
from engine.journal import InMemoryJournalStore
from engine.notify import InMemoryOutbox
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore, text_seal_detector
from engine.review import (
    CLEARING_QUEUE,
    ReviewActionError,
    build_index,
    build_queue,
    confirm_case,
    escalate_case,
    override_case,
    review_metrics,
    review_state,
)
from engine.review.state import OVERRIDE_TIER, OVERRIDE_UNIT
from schemas.events import Actor, ActorKind, Event, EventType

#: Two units that exist in the shipped taxonomy. One acts, one receives.
UNIT = "Referat_312_Renten"
OTHER_UNIT = "Referat_318_Auslandsrenten"
CLEARING_UNIT = "Referat_390_Sonstiges"

#: Gold items chosen for what they DO, not for coverage: a complete tier-1
#: item, an incomplete tier-2 item that owes a Nachforderung, a Widerspruch, a
#: Reha item and an unroutable one for the clearing queue.
TIER1_ITEM = "ar-0001-regelaltersrente-vollstaendig"
TIER2_ITEM = "ar-0011-ohne-rentenbeginn"
WIDERSPRUCH_ITEM = "xx-0005-widerspruch"
REHA_ITEM = "xx-0003-reha-antrag"
UNROUTABLE_ITEM = "xx-0002-ohne-verfahrenskennung"

INGESTED_AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
REVIEWED_AT = datetime(2026, 3, 4, 9, 0, tzinfo=UTC)


@pytest.fixture
def drafts() -> InMemoryDraftStore:
    return InMemoryDraftStore()


@pytest.fixture
def journal() -> InMemoryJournalStore:
    return InMemoryJournalStore()


@pytest.fixture
def vault() -> InMemoryVaultStore:
    return InMemoryVaultStore()


@pytest.fixture
def client(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
) -> Iterator[TestClient]:
    app = create_app(
        config=config,
        journal=journal,
        vault=vault,
        text_detector=text_seal_detector(with_ner=False),
        outbox=InMemoryOutbox(),
        drafts=drafts,
    )
    with TestClient(app) as test_client:
        yield test_client


def submission(gold_v4_dir: Path, item_id: str) -> dict[str, Any]:
    return json.loads((gold_v4_dir / f"{item_id}.json").read_text(encoding="utf-8"))


def ingest(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
    item_id: str,
    *,
    now: datetime = INGESTED_AT,
) -> str:
    """One item through both planes and the drafting fold, at a fixed time."""
    result = run_pipeline(
        submission(gold_v4_dir, item_id),
        config=config,
        journal=journal,
        vault=vault,
        now=now,
        text_detector=text_seal_detector(with_ner=False),
    )
    draft_case(
        journal.read(result.decision.case_id),
        config=config,
        journal=journal,
        vault=vault,
        drafts=drafts,
        facts=facts_from(result.extractions),
        now=now,
    )
    return result.decision.case_id


# --------------------------------------------------------- queue projections --


def test_queues_read_the_routed_event_and_never_the_evidence(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """The part-06 finding, as a queue invariant.

    ``EvidenceRecord.routing`` can hold a suggestion from a source the agency
    has not admitted. The queue an item lands in must be the one the DECISION
    plane chose, which the ROUTED event records, and nothing else.
    """
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER1_ITEM)
    state = review_state(case_id, journal.read(case_id))
    routed = [
        event for event in journal.read(case_id) if event.type is EventType.ROUTED
    ]
    assert len(routed) == 1
    assert state.machine_unit_id == routed[0].payload["unit_id"]
    index = build_index(journal)
    queue = build_queue(index, unit_id=UNIT, now=REVIEWED_AT, config=config.queues)
    assert [row.case_id for row in queue.rows] == [case_id]


def test_an_unroutable_item_lands_in_the_clearing_queue(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """C-10: an item nobody owns still has an owner (par. 16 Abs. 2 SGB I)."""
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, UNROUTABLE_ITEM)
    queue = build_queue(
        build_index(journal), unit_id=None, now=REVIEWED_AT, config=config.queues
    )
    assert queue.queue_id == CLEARING_QUEUE
    assert [row.case_id for row in queue.rows] == [case_id]
    flags = {flag.flag_id for row in queue.rows for flag in row.flags}
    assert "clearing_sla" in flags


def test_the_widerspruch_flag_shows_the_clock_and_no_admissibility_text(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """C-9's remaining half, and the line it may not cross.

    Routing a Widerspruch is a Realakt. The flag may show WHEN it arrived and
    HOW, and it may not say a word about Zulaessigkeit, Fristwahrung or
    Begruendetheit - those belong to the Widerspruchsausschuss.
    """
    ingest(config, journal, vault, drafts, gold_v4_dir, WIDERSPRUCH_ITEM)
    queue = build_queue(
        build_index(journal),
        unit_id="Widerspruchsstelle_360",
        now=REVIEWED_AT,
        config=config.queues,
    )
    assert queue.rows
    flag = next(
        flag
        for row in queue.rows
        for flag in row.flags
        if flag.flag_id == "widerspruch"
    )
    assert flag.label == "Frist laeuft"
    assert INGESTED_AT.isoformat() in flag.detail
    assert "fit_connect" in flag.detail or "email" in flag.detail
    # The Aktenzeichen is reported as PRESENT or ABSENT and never as a value:
    # it is sealed identity data and a queue page is not on the re-hydration
    # surface.
    assert "Aktenzeichen" in flag.detail
    # The disclaimer is verbatim, and it is the ONLY place the legal words
    # appear. Everything the flag asserts on its own account is the arrival
    # time, the channel and the presence of a file reference.
    disclaimer = (
        "Diese Anzeige trifft KEINE Aussage zu Zulaessigkeit, Fristwahrung "
        "oder Begruendetheit (par. 84, par. 85 SGG) - die Zuordnung ist ein "
        "Realakt."
    )
    assert disclaimer in flag.detail
    stated = flag.detail.replace(disclaimer, "").lower()
    for forbidden in ("zulaessig", "unzulaessig", "fristwahrung", "begruendet"):
        assert forbidden not in stated


def test_the_reha_clock_counts_two_weeks_from_the_eingangszeitpunkt(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """C-10: par. 14 Abs. 1 SGB IX, with an injected clock on both sides."""
    ingest(config, journal, vault, drafts, gold_v4_dir, REHA_ITEM)
    assert config.queues is not None
    due = INGESTED_AT + timedelta(days=config.queues.reha.weiterleitung_days)
    index = build_index(journal)
    inside = build_queue(
        index,
        unit_id="Referat_320_Reha",
        now=INGESTED_AT + timedelta(days=1),
        config=config.queues,
    )
    outside = build_queue(
        index,
        unit_id="Referat_320_Reha",
        now=due + timedelta(days=3),
        config=config.queues,
    )
    assert inside.rows and outside.rows
    early = next(f for f in inside.rows[0].flags if f.flag_id == "reha_frist")
    late = next(f for f in outside.rows[0].flags if f.flag_id == "reha_frist")
    assert due.date().isoformat() in early.detail
    assert early.tone == "neutral"
    assert "abgelaufen" in late.detail
    assert late.tone == "attention"
    # The clock states the norm and stops there: par. 14 Abs. 2 SGB IX turns a
    # missed period into own responsibility for the case, and that is a legal
    # finding a queue may not make.
    assert "par. 14 Abs. 2 SGB IX trifft diese Anzeige keine Aussage" in late.detail


def test_queue_clocks_never_hide_or_reorder_anything(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """Display-only means the row set does not depend on the clock."""
    for item in (TIER1_ITEM, TIER2_ITEM):
        ingest(config, journal, vault, drafts, gold_v4_dir, item)
    index = build_index(journal)
    early = build_queue(index, unit_id=UNIT, now=INGESTED_AT, config=config.queues)
    late = build_queue(
        index, unit_id=UNIT, now=INGESTED_AT + timedelta(days=400), config=config.queues
    )
    assert {row.case_id for row in early.rows} == {row.case_id for row in late.rows}
    assert early.over_budget_count == 0
    assert late.over_budget_count == len(late.rows)


# ------------------------------------------------------------- confirm flow --


def test_confirm_appends_exactly_one_event_and_rewrites_nothing(
    client: TestClient,
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER1_ITEM)
    before = journal.read(case_id)
    response = client.post(
        f"/review/case/{case_id}/confirm",
        data={"unit": UNIT, "dispatch": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    after = journal.read(case_id)
    assert after[: len(before)] == before, "no past event may change"
    assert len(after) == len(before) + 1
    confirmed = after[-1]
    assert confirmed.type is EventType.CONFIRMED
    assert confirmed.actor == Actor(kind=ActorKind.CASEWORKER, unit_id=UNIT)
    assert confirmed.payload["confirmed_tier"] == 1
    assert confirmed.payload["draft_edited"] is False


def test_a_second_confirmation_is_refused_rather_than_written(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER1_ITEM)
    confirm_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        unit_id=UNIT,
        drafts=drafts,
        vault=vault,
        now=REVIEWED_AT,
    )
    count = len(journal.read(case_id))
    with pytest.raises(ReviewActionError, match="already confirmed"):
        confirm_case(
            journal.read(case_id),
            config=config,
            journal=journal,
            unit_id=UNIT,
            drafts=drafts,
            vault=vault,
            now=REVIEWED_AT,
        )
    assert len(journal.read(case_id)) == count


def test_confirm_stamps_the_absolute_deadline_from_the_dispatch_date(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """C-6's open half: the relative window becomes a date at dispatch."""
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER2_ITEM)
    outcome = confirm_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        unit_id=UNIT,
        drafts=drafts,
        vault=vault,
        now=REVIEWED_AT,
    )
    assert outcome.facts is not None
    assert outcome.facts.deadline is not None
    assert config.drafting is not None
    expected = response_deadline(
        REVIEWED_AT.date(),
        window_days=config.drafting.response_window_days,
        holidays=frozenset(),
    )
    assert outcome.facts.deadline == expected
    payload = outcome.event.payload["dispatch"]
    assert isinstance(payload, dict)
    assert payload["deadline"]["deadline"] == expected.deadline.isoformat()
    assert payload["deadline"]["basis"] == "par. 37 Abs. 2 SGB X, par. 26 Abs. 3 SGB X"


@pytest.mark.parametrize(
    ("dispatch_day", "expect_shift"),
    [
        # 4 March 2026 is a Wednesday: +4 days is Sunday 8 March, which shifts.
        (date(2026, 3, 4), True),
        # 2 March 2026 is a Monday: +4 days is Friday 6 March, no shift.
        (date(2026, 3, 2), False),
    ],
)
def test_the_bekanntgabe_fiction_shifts_off_a_weekend(
    dispatch_day: date, expect_shift: bool, config: ConfigBundle
) -> None:
    assert config.drafting is not None
    deadline = response_deadline(
        dispatch_day,
        window_days=config.drafting.response_window_days,
        holidays=frozenset(),
    )
    assert ("bekanntgabe" in deadline.shifted) is expect_shift
    assert deadline.bekanntgabe_date.weekday() < 5
    assert deadline.deadline.weekday() < 5


def test_an_injected_holiday_moves_the_deadline_further_out(
    config: ConfigBundle,
) -> None:
    """The Land holiday set is a config value, and this is what it does.

    The shipped file has an EMPTY list on purpose - German holidays are
    Land-specific and this repository cannot cite which apply where a letter is
    served - so the behaviour is tested with an injected set rather than with
    an invented table.
    """
    assert config.drafting is not None
    assert config.dispatch is not None
    assert config.dispatch.holidays == [], "the shipped holiday set stays empty"
    plain = response_deadline(date(2026, 3, 2), window_days=30, holidays=frozenset())
    with_holiday = response_deadline(
        date(2026, 3, 2), window_days=30, holidays=frozenset({plain.deadline})
    )
    assert with_holiday.deadline > plain.deadline
    assert "deadline" in with_holiday.shifted


def test_the_par66_opt_in_rewrites_the_letter_and_journals_the_new_one(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """C-6 / part 08: the block is a per-case caseworker decision.

    Recording the opt-in while dispatching the letter drafting prepared
    WITHOUT the block would be a journal that disagrees with the post office.
    """
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER2_ITEM)
    prepared = drafts.records(case_id)[0]
    assert prepared.rechtsfolgenhinweis is False
    before = len(journal.read(case_id))
    outcome = confirm_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        unit_id=UNIT,
        drafts=drafts,
        vault=vault,
        rechtsfolgenhinweis=True,
        now=REVIEWED_AT,
    )
    assert outcome.draft is not None
    assert outcome.draft.rechtsfolgenhinweis is True
    assert outcome.draft.draft_id != prepared.draft_id
    assert "66" in outcome.draft.body
    # One DRAFTED for the new letter, one CONFIRMED. Nothing replaced.
    after = journal.read(case_id)
    assert len(after) == before + 2
    assert after[-2].type is EventType.DRAFTED
    assert after[-2].payload["supersedes_draft_id"] == prepared.draft_id
    assert after[-1].payload["rechtsfolgenhinweis"] is True
    assert drafts.records(case_id)[0].draft_id == prepared.draft_id


def test_the_par66_opt_in_does_nothing_to_a_prepared_decision(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """A Bewilligungsentwurf asks for nothing; par. 66 has nothing to attach to."""
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER1_ITEM)
    before = len(journal.read(case_id))
    outcome = confirm_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        unit_id=UNIT,
        drafts=drafts,
        vault=vault,
        rechtsfolgenhinweis=True,
        now=REVIEWED_AT,
    )
    assert len(journal.read(case_id)) == before + 1
    assert outcome.event.payload["rechtsfolgenhinweis"] is False
    assert outcome.event.payload["rechtsfolgenhinweis_requested"] is True


def test_confirm_writes_the_handover_stub_and_names_it_by_digest(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
    tmp_path: Path,
) -> None:
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER2_ITEM)
    outcome = confirm_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        unit_id=UNIT,
        drafts=drafts,
        vault=vault,
        now=REVIEWED_AT,
        dispatch_root=tmp_path,
    )
    assert outcome.stub is not None
    assert outcome.facts is not None
    assert outcome.stub.path.name == stub_filename(case_id, outcome.facts.draft_id)
    xml = outcome.stub.path.read_text(encoding="utf-8")
    assert 'konform="false"' in xml
    assert "Kein konformer xdomea-Nachrichtensatz" in xml
    assert case_id in xml
    # The body stays in the draft store: the exception list is two members long.
    assert outcome.draft is not None
    assert outcome.draft.body[:40] not in xml
    export = outcome.event.payload["export"]
    assert isinstance(export, dict)
    assert export["sha256"] == outcome.stub.sha256


def test_the_dispatch_directory_is_optional(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No out-directory is a normal state: the facts are journaled anyway."""
    monkeypatch.delenv(DISPATCH_DIR_ENV, raising=False)
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER2_ITEM)
    outcome = confirm_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        unit_id=UNIT,
        drafts=drafts,
        vault=vault,
        now=REVIEWED_AT,
    )
    assert outcome.stub is None
    assert outcome.facts is not None
    assert "export" not in outcome.event.payload


# ---------------------------------------------------------------- overrides --


def test_a_reroute_moves_the_case_and_keeps_the_machine_answer(
    client: TestClient,
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER1_ITEM)
    client.post(
        f"/review/case/{case_id}/override",
        data={
            "unit": UNIT,
            "field": OVERRIDE_UNIT,
            "to": OTHER_UNIT,
            "reason": "Auslandssachverhalt aus der Akte erkennbar",
        },
        follow_redirects=False,
    )
    state = review_state(case_id, journal.read(case_id))
    assert state.unit_id == OTHER_UNIT
    assert state.machine_unit_id == UNIT, "the machine's answer never moves"
    assert state.rerouted is True
    assert state.open is True, "a correction does not close a case"
    index = build_index(journal)
    assert not build_queue(
        index, unit_id=UNIT, now=REVIEWED_AT, config=config.queues
    ).rows
    assert build_queue(
        index, unit_id=OTHER_UNIT, now=REVIEWED_AT, config=config.queues
    ).rows


def test_an_override_without_a_reason_is_refused(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER1_ITEM)
    count = len(journal.read(case_id))
    with pytest.raises(ReviewActionError, match="reason in words"):
        override_case(
            journal.read(case_id),
            config=config,
            journal=journal,
            unit_id=UNIT,
            field=OVERRIDE_UNIT,
            to_value=OTHER_UNIT,
            reason="   ",
        )
    assert len(journal.read(case_id)) == count


def test_escalation_is_one_click_and_needs_no_written_justification(
    client: TestClient,
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """P-4, and the deliberate asymmetry with the other two corrections.

    Escalating only ever ADDS oversight. A form that refused to submit without
    a justification would put friction in front of the safe direction, which is
    the same reasoning the one-way valve encodes.
    """
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER1_ITEM)
    response = client.post(
        f"/review/case/{case_id}/escalate",
        data={"unit": UNIT, "reason": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    state = review_state(case_id, journal.read(case_id))
    assert state.tier == 3
    assert state.machine_tier == 1
    assert state.escalated is True
    reason = journal.read(case_id)[-1].payload["reason"]
    assert isinstance(reason, str)
    assert "88 Abs. 5 Nr. 3 AO" in reason


def test_an_item_already_in_full_review_cannot_be_escalated(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, UNROUTABLE_ITEM)
    with pytest.raises(ReviewActionError, match="already in full human review"):
        escalate_case(
            journal.read(case_id), config=config, journal=journal, unit_id=CLEARING_UNIT
        )


def test_a_correction_after_confirmation_is_refused(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER1_ITEM)
    confirm_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        unit_id=UNIT,
        drafts=drafts,
        vault=vault,
        now=REVIEWED_AT,
    )
    with pytest.raises(ReviewActionError, match="confirmed"):
        override_case(
            journal.read(case_id),
            config=config,
            journal=journal,
            unit_id=UNIT,
            field=OVERRIDE_TIER,
            to_value=3,
            reason="zu spaet",
        )


# ------------------------------------------------------ ADR-025, both shapes --


def _sampled_event(case_id: str, template: Event, kind: str) -> Event:
    """A TIER_DECIDED whose reasons carry an audit draw in ONE of the shapes."""
    payload = dict(template.payload)
    payload["reasons"] = [
        {
            "kind": kind,
            "rule_id": "audit_sample",
            "detail": "Zufallsstichprobe der Qualitaetssicherung",
        }
    ]
    return template.model_copy(update={"payload": payload})


@pytest.mark.parametrize("kind", ["sampled", "downgraded"])
def test_a_sampled_case_reads_as_sampled_in_either_journal_shape(
    kind: str,
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """ADR-025: journals written before the migration still read correctly."""
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER1_ITEM)
    events = journal.read(case_id)
    decided = next(e for e in events if e.type is EventType.TIER_DECIDED)
    rewritten = [
        _sampled_event(case_id, event, kind) if event is decided else event
        for event in events
    ]
    state = review_state(case_id, rewritten)
    assert state.sampled is True
    assert state.flagged is False
    queue = build_queue(
        build_index(_StubStore({case_id: rewritten})),
        unit_id=UNIT,
        now=REVIEWED_AT,
        config=config.queues,
    )
    flag = next(f for row in queue.rows for f in row.flags if f.flag_id == "sampled")
    assert flag.tone == "neutral", "a draw may never wear the alarm tone"
    assert "Kein Auffaelligkeitsbefund" in flag.detail
    assert "auffaellig" not in flag.label.lower()


class _StubStore:
    """A journal store over a fixed dict, for the both-shapes rewrite above."""

    def __init__(self, events: dict[str, list[Event]]) -> None:
        self._events = events

    def read(self, case_id: str) -> list[Event]:
        return list(self._events.get(case_id, []))

    def case_ids(self) -> list[str]:
        return sorted(self._events)


# ------------------------------------------------------------------ metrics --


def test_p6_reports_no_rate_for_a_unit_with_too_few_confirmations(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """A rate over two cases is a number about two cases, and about people."""
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER1_ITEM)
    confirm_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        unit_id=UNIT,
        drafts=drafts,
        vault=vault,
        now=REVIEWED_AT,
    )
    metrics = review_metrics(
        build_index(journal), now=REVIEWED_AT, config=config.queues
    )
    unit = next(row for row in metrics.units if row.unit_id == UNIT)
    assert unit.confirmed == 1
    assert unit.confirm_without_edit_rate is None
    assert unit.as_payload()["suppressed_reason"] == "zu wenige Vorgaenge"


def test_p10_reports_the_oldest_open_item_per_tier(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    ingest(config, journal, vault, drafts, gold_v4_dir, TIER1_ITEM)
    ingest(
        config,
        journal,
        vault,
        drafts,
        gold_v4_dir,
        TIER2_ITEM,
        now=INGESTED_AT - timedelta(days=5),
    )
    metrics = review_metrics(
        build_index(journal), now=REVIEWED_AT, config=config.queues
    )
    tiers = {row.tier: row for row in metrics.backlog}
    assert tiers[1].oldest_hours == pytest.approx(48.0)
    assert tiers[2].oldest_hours == pytest.approx(168.0)
    assert tiers[2].budget_hours == 72
    assert tiers[2].over_budget is True


def test_the_time_to_confirm_is_journal_derived_and_not_telemetry(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER1_ITEM)
    outcome = confirm_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        unit_id=UNIT,
        drafts=drafts,
        vault=vault,
        now=REVIEWED_AT,
    )
    assert outcome.event.payload["seconds_since_decision"] == pytest.approx(
        (REVIEWED_AT - INGESTED_AT).total_seconds()
    )


# -------------------------------------------------------------- the pages --


def test_the_three_pages_render_and_carry_their_honesty_notes(
    client: TestClient,
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER2_ITEM)
    overview = client.get("/review")
    queue = client.get(f"/review/queue/{UNIT}")
    case = client.get(f"/review/case/{case_id}?unit={UNIT}")
    for response in (overview, queue, case):
        assert response.status_code == 200
        assert "Rollenwahl ist eine Demo-Funktion ohne Anmeldung" in response.text
        assert "docs/accessibility-selfcheck.md" in response.text
    assert "par. 16 Abs. 2 SGB I" in client.get(f"/review/queue/{CLEARING_QUEUE}").text
    # The case view names the admitted answer and labels the ranking log-only.
    assert "Das ist die Antwort der Entscheidungsebene" in case.text
    assert "Bekanntgabefiktion" in case.text or "par. 37 Abs. 2 SGB X" in case.text


def test_the_draft_section_is_gated_by_the_unit_picker(
    client: TestClient,
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER2_ITEM)
    letter = drafts.records(case_id)[0].body
    without = client.get(f"/review/case/{case_id}")
    assert letter[:60] not in without.text
    with_unit = client.get(f"/review/case/{case_id}?unit={UNIT}")
    assert letter[:60] in with_unit.text
    # A unit the taxonomy does not know is not a role.
    assert (
        letter[:60] not in client.get(f"/review/case/{case_id}?unit=Referat_999").text
    )


def test_the_picker_says_which_unit_is_acting_and_the_unit_survives_a_click(
    client: TestClient,
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """Part 17: the picker worked and looked broken, so it now says so.

    Traced in a browser first. The parameter round-trips, `resolve_unit`
    accepts it, and the draft section really does unlock - but submitting the
    form re-rendered a page whose only visible difference was which option the
    `<select>` had marked, and the tables deliberately do not move because any
    unit may read any queue (ADR-026). Nothing on the page named the unit that
    was now acting, so pressing the button looked like pressing nothing.

    Two things are pinned here. The page states the acting unit in words on
    all three screens, and every link that leads onward or back carries the
    unit, so an adopted unit does not fall off on the next click.
    """
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER2_ITEM)
    name = next(node.name for node in config.taxonomy.nodes if node.unit_id == UNIT)

    pages = {
        "overview": f"/review?unit={UNIT}",
        "queue": f"/review/queue/{UNIT}?unit={UNIT}",
        "case": f"/review/case/{case_id}?unit={UNIT}",
    }
    for where, path in pages.items():
        body = client.get(path).text
        assert 'id="acting-unit"' in body, where
        assert "Aktive Einheit" in body, where
        assert name in body, where
        assert UNIT in body, where

    # With NO unit the page says what choosing one would unlock, and does not
    # state a non-fact as a fact.
    for where, path in (
        ("overview", "/review"),
        ("queue", f"/review/queue/{UNIT}"),
        ("case", f"/review/case/{case_id}"),
    ):
        body = client.get(path).text
        assert 'id="acting-unit"' in body, where
        assert "Aktive Einheit" not in body, where
        assert "Entwürfe" in body, where

    # Every onward and backward link keeps the unit.
    overview = client.get(f"/review?unit={UNIT}").text
    assert f'href="/review/queue/{UNIT}?unit={UNIT}"' in overview
    queue = client.get(f"/review/queue/{UNIT}?unit={UNIT}").text
    assert f'href="/review/case/{case_id}?unit={UNIT}"' in queue
    assert f'href="/review?unit={UNIT}"' in queue
    case = client.get(f"/review/case/{case_id}?unit={UNIT}").text
    assert f'href="/review?unit={UNIT}"' in case
    assert f"/review/queue/{UNIT}?unit={UNIT}" in case

    # The tour's highlight survives adopting a unit: the picker is a GET form,
    # so anything not carried as a hidden field is dropped on submit.
    marked = client.get(f"/review/queue/{UNIT}?unit={UNIT}&highlight={case_id}").text
    assert f'<input type="hidden" name="highlight" value="{case_id}">' in marked
    assert "highlight" not in client.get(f"/review/queue/{UNIT}?unit={UNIT}").text

    # The overview and case pickers target the page they are on, so submitting
    # them stays. The QUEUE picker submits to the switch route and names its
    # origin queue, because on that page the button navigates to the adopted
    # unit's own queue (user direction 2026-08-19) - the redirect itself is
    # pinned in the test below this one.
    overview = client.get(f"/review?unit={UNIT}").text
    assert 'action="/review"' in overview
    assert 'action="/review/queue"' in queue
    assert f'<input type="hidden" name="origin" value="{UNIT}">' in queue
    assert f'action="/review/case/{case_id}"' in case


def test_taking_over_a_unit_on_a_queue_page_lands_on_that_units_queue(
    client: TestClient,
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """The queue picker navigates; the switch route decides where to.

    Part 17 made the picker say what it did; it still did not go anywhere,
    and a reader who took over Referat 312 while looking at another unit's
    queue stayed on that other unit's queue. On a queue page the question
    behind the choice is "what is this unit's work", so the form submits to
    `/review/queue` and lands on the adopted unit's own queue (user direction
    2026-08-19). Everything else about ADR-026 is unchanged: every queue
    stays readable by every unit through the overview's links, and the
    overview and case pickers still stay put.
    """
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER2_ITEM)
    other = next(node.unit_id for node in config.taxonomy.nodes if node.unit_id != UNIT)

    # Adopting a unit lands on ITS queue, acting as it.
    moved = client.get(
        f"/review/queue?unit={other}&origin={UNIT}", follow_redirects=False
    )
    assert moved.status_code == 303
    assert moved.headers["location"] == f"/review/queue/{other}?unit={other}"
    landed = client.get(moved.headers["location"])
    assert landed.status_code == 200
    assert "Aktive Einheit" in landed.text

    # The tour's highlight travels only when the destination IS the origin
    # queue - anywhere else it would mark no row and the page would explain
    # the absence with a sentence that is not what happened.
    same = client.get(
        f"/review/queue?unit={UNIT}&origin={UNIT}&highlight={case_id}",
        follow_redirects=False,
    )
    assert (
        same.headers["location"]
        == f"/review/queue/{UNIT}?unit={UNIT}&highlight={case_id}"
    )
    elsewhere = client.get(
        f"/review/queue?unit={other}&origin={UNIT}&highlight={case_id}",
        follow_redirects=False,
    )
    assert elsewhere.headers["location"] == f"/review/queue/{other}?unit={other}"

    # Clearing the choice returns to the queue the form was on, without a
    # unit - including the clearing queue, which is not a taxonomy unit.
    cleared = client.get(f"/review/queue?origin={UNIT}", follow_redirects=False)
    assert cleared.headers["location"] == f"/review/queue/{UNIT}"
    clearing = client.get("/review/queue?origin=__clearing__", follow_redirects=False)
    assert clearing.headers["location"] == "/review/queue/__clearing__"

    # A unit the taxonomy does not know is not a role (the draft gate's own
    # rule), so it clears rather than navigates; with no origin either, the
    # only honest destination left is the overview.
    unknown = client.get(
        f"/review/queue?unit=Referat_999&origin={UNIT}", follow_redirects=False
    )
    assert unknown.headers["location"] == f"/review/queue/{UNIT}"
    homeless = client.get("/review/queue?unit=Referat_999", follow_redirects=False)
    assert homeless.headers["location"] == "/review"


def test_an_unknown_case_is_a_404_and_not_an_empty_page(
    client: TestClient,
) -> None:
    assert client.get("/review/case/case-does-not-exist").status_code == 404
    assert (
        client.post(
            "/review/case/case-does-not-exist/confirm",
            data={"unit": UNIT},
            follow_redirects=False,
        ).status_code
        == 404
    )


def test_the_view_builders_take_an_injected_clock(
    config: ConfigBundle,
    journal: InMemoryJournalStore,
    vault: InMemoryVaultStore,
    drafts: InMemoryDraftStore,
    gold_v4_dir: Path,
) -> None:
    """Nothing on these pages reads the wall clock; the tests pin that."""
    case_id = ingest(config, journal, vault, drafts, gold_v4_dir, TIER1_ITEM)
    overview = build_overview(journal, config=config, unit_id=UNIT, now=REVIEWED_AT)
    queue = build_queue_view(
        journal, config=config, queue_id=UNIT, unit_id=UNIT, now=REVIEWED_AT
    )
    case = build_case_view(
        journal,
        config=config,
        case_id=case_id,
        unit_id=UNIT,
        drafts=drafts,
        now=REVIEWED_AT,
    )
    assert overview.now == queue.now == REVIEWED_AT
    assert case is not None and case.now == REVIEWED_AT
