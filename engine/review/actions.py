"""What a caseworker's click does: appends an event. Never anything else.

Three actions, one discipline. Confirm writes CONFIRMED, re-route and tier
change write OVERRIDDEN, escalate writes OVERRIDDEN with the escalation field.
Nothing in this module updates a payload, rewrites a decision or deletes an
event - correcting an item means appending the correction next to what was
wrong, which is ADR-008 becoming a screen.

The actor is a UNIT and the contract makes it one: ``Actor`` has ``kind`` and
``unit_id`` and no field for a person. That is the BPersVG line (par. 80 Abs. 1
Nr. 21 - performance and behaviour monitoring is co-determined), and it is why
every metric in this part is aggregate by construction rather than by policy.

**Confirm-and-dispatch, phase-0.** Confirming a prepared letter is the moment
the dispatch facts become real, so this is where they are stamped:

* ``dispatched_at`` from an injectable clock,
* the channel SHAPE part 08 recorded per case (C-8: postal, qualified
  electronic, status event),
* and for a Nachforderung the ABSOLUTE deadline, computed now from the dispatch
  date by ``engine/draft/bekanntgabe.py`` with the Land holiday set from
  ``config/dispatch/dispatch_v1.yaml``. The letter stated the window relatively
  because a waiting draft has no dispatch date; this is where it gets one.

**The par. 66 Abs. 3 opt-in re-renders the letter.** Recording "the caseworker
opted in" on the CONFIRMED payload while dispatching the letter drafting
prepared WITHOUT the block would be a journal that disagrees with the post. So
an opt-in builds the letter again with the block, stores it as its own draft
record and journals its own DRAFTED event; the CONFIRMED payload names which
draft was actually dispatched. Opting in is per case and per caseworker, the
config carries no switch that could default it on, and the block never covers a
requirement C-7 softened.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from engine.config_loader import ConfigBundle
from engine.dispatch import (
    DispatchFacts,
    DispatchStub,
    build_stub_xml,
    write_stub,
)
from engine.draft.bekanntgabe import ResponseDeadline, response_deadline
from engine.draft.letters import (
    KIND_NACHFORDERUNG,
    DraftingError,
    DraftRequest,
    RenderedDraft,
    build_letter,
)
from engine.draft.projection import OwedDraft, owed_drafts
from engine.draft.rehydrate import RehydrationError, Rehydrator
from engine.draft.store import DraftRecord, DraftStore, draft_id_for
from engine.journal.store import JournalStore, emit
from engine.redact.vault import VaultStore
from engine.review.state import (
    ESCALATION_TIER,
    OVERRIDE_ESCALATION,
    OVERRIDE_FIELDS,
    OVERRIDE_TIER,
    OVERRIDE_UNIT,
    ReviewState,
    review_state,
)
from schemas.events import Actor, ActorKind, Event, EventType

#: Suffix that distinguishes the re-rendered par. 66 letter from the one
#: drafting prepared. Both stay in the store: the journal has to be able to
#: show that a caseworker changed the letter, and deleting the first one would
#: be exactly the rewrite this architecture refuses.
RECHTSFOLGEN_DRAFT_SUFFIX = "-par66"

#: What an escalation says when the caseworker adds nothing. Escalating ADDS
#: oversight, and demanding a written justification before somebody may ask for
#: more human review is friction pointing the wrong way - the same asymmetry
#: the one-way valve encodes. Re-routes and tier changes DO require a reason.
ESCALATION_DEFAULT_REASON = (
    "Manuelle Eskalation zur vollstaendigen Pruefung durch die bearbeitende "
    "Einheit (P-4, par. 88 Abs. 5 Nr. 3 AO analog)."
)


class ReviewActionError(RuntimeError):
    """A review action that may not be journaled, with the reason why."""


@dataclass(frozen=True)
class ConfirmOutcome:
    """Everything one confirmation produced."""

    event: Event
    state: ReviewState
    draft: DraftRecord | None = None
    draft_event: Event | None = None
    facts: DispatchFacts | None = None
    stub: DispatchStub | None = None
    #: Why nothing was dispatched, when nothing was. Named rather than left to
    #: be inferred from a missing key: "this case had no letter to send" and
    #: "the letter could not be built" are different facts.
    dispatch_skipped: str = ""


def confirm_case(
    events: Sequence[Event],
    *,
    config: ConfigBundle,
    journal: JournalStore,
    unit_id: str,
    drafts: DraftStore | None = None,
    vault: VaultStore | None = None,
    draft_edited: bool = False,
    rechtsfolgenhinweis: bool = False,
    dispatch: bool = True,
    note: str = "",
    now: datetime | None = None,
    dispatch_root: Path | str | None = None,
) -> ConfirmOutcome:
    """Journal the human's confirmation, and the dispatch facts it produces."""
    state = review_state(_case_id(events), list(events))
    if state.confirmed:
        raise ReviewActionError(
            f"case {state.case_id} is already confirmed; a second confirmation "
            f"would be a rewrite, and this journal only appends"
        )
    moment = now or datetime.now(UTC)
    prepared = _prepare_dispatch(
        events,
        state=state,
        config=config,
        journal=journal,
        drafts=drafts,
        vault=vault,
        unit_id=unit_id,
        rechtsfolgenhinweis=rechtsfolgenhinweis,
        dispatch=dispatch,
        moment=moment,
        dispatch_root=dispatch_root,
    )
    event = emit(
        journal,
        case_id=state.case_id,
        event_type=EventType.CONFIRMED,
        versions=config.version_stamp(),
        actor=_actor(unit_id),
        occurred_at=moment,
        # A confirmed letter is the opposite of an informational Realakt: a
        # human took responsibility for it, which is the whole of ADR-003.
        informational_only=False,
        template_id=prepared.draft.template_id if prepared.draft else None,
        payload={
            "envelope_id": state.case.envelope_id,
            "confirmed_tier": state.tier,
            "machine_tier": state.machine_tier,
            "unit_id": state.unit_id,
            "machine_unit_id": state.machine_unit_id,
            # P-6's two inputs. "Edited" is the caseworker's own statement that
            # they changed the prepared text; the latency is derived from the
            # journal's own timestamps and needs no telemetry to compute.
            "draft_edited": bool(draft_edited),
            "seconds_since_decision": _seconds_since_decision(state, moment),
            # C-6 / part 08: the par. 66 Abs. 3 block is a per-case decision
            # and the payload records which way it went, both ways.
            "rechtsfolgenhinweis": bool(
                prepared.draft.rechtsfolgenhinweis if prepared.draft else False
            ),
            "rechtsfolgenhinweis_requested": bool(rechtsfolgenhinweis),
            "rechtsfolgenhinweis_source": "caseworker_opt_in",
            "overridden": bool(state.overrides),
            "sampled": state.sampled,
            "note": note.strip(),
            "dispatched": prepared.facts is not None,
            **({"dispatch": prepared.facts.as_payload()} if prepared.facts else {}),
            **({"export": prepared.stub.as_payload()} if prepared.stub else {}),
            **(
                {"dispatch_skipped": prepared.dispatch_skipped}
                if prepared.dispatch_skipped
                else {}
            ),
        },
    )
    return ConfirmOutcome(
        event=event,
        state=state,
        draft=prepared.draft,
        draft_event=prepared.draft_event,
        facts=prepared.facts,
        stub=prepared.stub,
        dispatch_skipped=prepared.dispatch_skipped,
    )


def override_case(
    events: Sequence[Event],
    *,
    config: ConfigBundle,
    journal: JournalStore,
    unit_id: str,
    field: str,
    to_value: object,
    reason: str,
    now: datetime | None = None,
) -> Event:
    """Journal one correction: old value, new value, and why. Never an edit."""
    state = review_state(_case_id(events), list(events))
    if state.confirmed:
        raise ReviewActionError(
            f"case {state.case_id} is confirmed; a correction after "
            f"confirmation is a new decision, not an override of this one"
        )
    if field not in OVERRIDE_FIELDS:
        raise ReviewActionError(
            f"unknown override field {field!r}; a correction is one of "
            f"{sorted(OVERRIDE_FIELDS)} so the training pool stays labelled"
        )
    text = reason.strip()
    if not text:
        raise ReviewActionError(
            "an override needs a reason in words; the correction pool is "
            "training data and an unexplained label teaches the wrong thing"
        )
    from_value = state.unit_id if field == OVERRIDE_UNIT else state.tier
    if from_value == to_value:
        raise ReviewActionError(
            f"override would not change {field}: it is already {to_value!r}"
        )
    return emit(
        journal,
        case_id=state.case_id,
        event_type=EventType.OVERRIDDEN,
        versions=config.version_stamp(),
        actor=_actor(unit_id),
        occurred_at=now,
        payload={
            "envelope_id": state.case.envelope_id,
            "field": field,
            "from": from_value,
            "to": to_value,
            "reason": text,
            "machine_tier": state.machine_tier,
            "machine_unit_id": state.machine_unit_id,
            "sampled": state.sampled,
        },
    )


def escalate_case(
    events: Sequence[Event],
    *,
    config: ConfigBundle,
    journal: JournalStore,
    unit_id: str,
    reason: str = "",
    now: datetime | None = None,
) -> Event:
    """P-4: one click moves an item to full human review, as an OVERRIDDEN.

    The reason is optional here and mandatory everywhere else, and the
    asymmetry is deliberate: an escalation only ever ADDS oversight, so a form
    that refused to submit without a justification would be putting friction in
    front of the safe direction. What the caseworker writes is kept when they
    write it; the default sentence names the norm when they do not.

    An item already in full review cannot be escalated, and the refusal is the
    same principle as the audit sample's: writing a correction for a move that
    did not happen would be a lie in the audit trail. The UI hides the button
    on a tier-3 case; this is the belt that goes with that brace.
    """
    state = review_state(_case_id(events), list(events))
    if state.tier == ESCALATION_TIER:
        raise ReviewActionError(
            f"case {state.case_id} is already in full human review (tier "
            f"{ESCALATION_TIER}); escalating it would journal a move that did "
            f"not happen"
        )
    return override_case(
        events,
        config=config,
        journal=journal,
        unit_id=unit_id,
        field=OVERRIDE_ESCALATION,
        to_value=ESCALATION_TIER,
        reason=reason.strip() or ESCALATION_DEFAULT_REASON,
        now=now,
    )


@dataclass(frozen=True)
class _Prepared:
    draft: DraftRecord | None = None
    draft_event: Event | None = None
    facts: DispatchFacts | None = None
    stub: DispatchStub | None = None
    dispatch_skipped: str = ""


def _prepare_dispatch(
    events: Sequence[Event],
    *,
    state: ReviewState,
    config: ConfigBundle,
    journal: JournalStore,
    drafts: DraftStore | None,
    vault: VaultStore | None,
    unit_id: str,
    rechtsfolgenhinweis: bool,
    dispatch: bool,
    moment: datetime,
    dispatch_root: Path | str | None,
) -> _Prepared:
    """The letter that goes out, the facts that go with it, and the stub."""
    if not dispatch:
        return _Prepared(dispatch_skipped="Versand vom Bearbeiter abgewaehlt")
    if drafts is None:
        return _Prepared(dispatch_skipped="kein Entwurfsspeicher angebunden")
    stored = drafts.records(state.case_id)
    if not stored:
        return _Prepared(dispatch_skipped="kein Entwurf zu diesem Vorgang")
    record = stored[-1]
    draft_event: Event | None = None
    # Only a Nachforderung can carry the block: par. 66 Abs. 1 SGB I is about
    # a Mitwirkungspflicht, and a prepared decision asks for nothing. Opting in
    # on one is a no-op that must not manufacture a second identical letter.
    wants_block = (
        rechtsfolgenhinweis
        and record.kind == KIND_NACHFORDERUNG
        and not record.rechtsfolgenhinweis
    )
    if wants_block:
        record, draft_event = _redraft_with_rechtsfolgenhinweis(
            events,
            config=config,
            journal=journal,
            drafts=drafts,
            vault=vault,
            base=record,
            moment=moment,
        )
    facts = _facts_for(
        record, state=state, config=config, unit_id=unit_id, moment=moment
    )
    return _Prepared(
        draft=record,
        draft_event=draft_event,
        facts=facts,
        stub=_write_stub(facts, config=config, dispatch_root=dispatch_root),
    )


def _facts_for(
    record: DraftRecord,
    *,
    state: ReviewState,
    config: ConfigBundle,
    unit_id: str,
    moment: datetime,
) -> DispatchFacts:
    holidays = config.dispatch.holiday_set() if config.dispatch else frozenset()
    deadline: ResponseDeadline | None = None
    if record.kind == KIND_NACHFORDERUNG and record.response_window_days:
        deadline = response_deadline(
            moment.date(),
            window_days=record.response_window_days,
            holidays=holidays,
        )
    channel = state.case.channel
    shape = config.drafting.channel(channel) if config.drafting else None
    return DispatchFacts(
        case_id=state.case_id,
        envelope_id=state.case.envelope_id or "",
        draft_id=record.draft_id,
        draft_kind=record.kind,
        template_id=record.template_id,
        unit_id=state.unit_id or unit_id,
        procedure_id=record.procedure_id,
        dispatch_shape=(shape.dispatch if shape is not None else "unbekannt"),
        dispatch_channel=channel,
        dispatched_at=moment,
        dispatch_date=moment.date(),
        deadline=deadline,
        land=config.dispatch.land if config.dispatch else "",
        holiday_count=len(holidays),
    )


def _write_stub(
    facts: DispatchFacts,
    *,
    config: ConfigBundle,
    dispatch_root: Path | str | None,
) -> DispatchStub | None:
    """The handover file, when an out-directory and a format are configured."""
    if dispatch_root is None or config.dispatch is None:
        return None
    format_id = config.dispatch.export.format_id
    return write_stub(
        dispatch_root,
        facts=facts,
        xml=build_stub_xml(facts, format_id=format_id),
        format_id=format_id,
    )


def _redraft_with_rechtsfolgenhinweis(
    events: Sequence[Event],
    *,
    config: ConfigBundle,
    journal: JournalStore,
    drafts: DraftStore,
    vault: VaultStore | None,
    base: DraftRecord,
    moment: datetime,
) -> tuple[DraftRecord, Event | None]:
    """Build the SAME letter again, this time with the par. 66 Abs. 3 block."""
    if vault is None:
        raise ReviewActionError(
            "the par. 66 Abs. 3 block needs the letter re-rendered and the "
            "letter is addressed to a person; no identity vault was passed"
        )
    owed = [
        item
        for item in owed_drafts(events, config=config, include_drafted=True)
        if item.source_event_id == base.source_event_id
    ]
    if not owed:
        raise ReviewActionError(
            f"no owed draft matches {base.draft_id}; the par. 66 block cannot "
            f"be added to a letter whose case facts are no longer derivable"
        )
    rendered = _build(owed[0], config=config, vault=vault)
    record = base.model_copy(
        update={
            "draft_id": draft_id_for(base.source_event_id, rendered.template_id)
            + RECHTSFOLGEN_DRAFT_SUFFIX,
            "subject": rendered.subject,
            "body": rendered.body,
            "resolved_tokens": rendered.resolved_tokens,
            "distinct_tokens": rendered.distinct_tokens,
            "token_kinds": dict(rendered.token_kinds),
            "rechtsfolgenhinweis": rendered.rechtsfolgenhinweis,
            "created_at": moment,
        }
    )
    if not drafts.save(record):
        return record, None
    event = emit(
        journal,
        case_id=record.case_id,
        event_type=EventType.DRAFTED,
        versions=config.version_stamp(),
        occurred_at=moment,
        informational_only=False,
        template_id=record.template_id,
        payload={
            "envelope_id": record.envelope_id,
            "source_event_id": record.source_event_id,
            "source_event_type": EventType.TIER_DECIDED.value,
            **record.summary(),
            "dispatched": False,
            "supersedes_draft_id": base.draft_id,
            "reason": "par. 66 Abs. 3 SGB I: Rechtsfolgenhinweis vom Bearbeiter "
            "einzelfallbezogen ausgewaehlt",
        },
    )
    return record, event


def _build(
    item: OwedDraft, *, config: ConfigBundle, vault: VaultStore
) -> RenderedDraft:
    try:
        return build_letter(
            DraftRequest(
                case_id=item.case_id,
                envelope_id=item.envelope_id,
                kind=item.kind,
                tier=item.tier,
                vault_ref=item.vault_ref,
                procedure_id=item.procedure_id,
                channel_id=item.channel_id,
                unit_id=item.unit_id,
                received_at=item.received_at,
                gaps=item.gaps,
            ),
            config=config,
            record=Rehydrator(vault).record(item.vault_ref),
            rechtsfolgenhinweis=True,
        )
    except (RehydrationError, DraftingError) as error:
        raise ReviewActionError(
            f"the par. 66 letter could not be built: {error}"
        ) from error


def _seconds_since_decision(state: ReviewState, moment: datetime) -> float | None:
    """P-6's latency input: how long the item waited for its human.

    Decision to confirmation, both timestamps off the journal. NOT "how long
    the caseworker looked at the screen" - measuring that needs per-session
    telemetry about a person, which is exactly what C-4 and the unit-scoped
    Actor rule out. What this measures is queue dwell, the metrics module says
    so, and the rubber-stamp signal it pairs with is the confirm-without-edit
    rate.
    """
    decided = state.decided_at
    if decided is None:
        return None
    return max((moment - decided).total_seconds(), 0.0)


def _actor(unit_id: str) -> Actor:
    unit = unit_id.strip()
    if not unit:
        raise ReviewActionError(
            "a review action needs the unit acting; the journal's Actor is "
            "unit-scoped and an unattributed confirmation proves nothing"
        )
    return Actor(kind=ActorKind.CASEWORKER, unit_id=unit)


def _case_id(events: Sequence[Event]) -> str:
    if not events:
        raise ReviewActionError("no events: there is no case to act on")
    return events[0].case_id


__all__ = [
    "ESCALATION_DEFAULT_REASON",
    "OVERRIDE_ESCALATION",
    "OVERRIDE_TIER",
    "OVERRIDE_UNIT",
    "ConfirmOutcome",
    "ReviewActionError",
    "confirm_case",
    "escalate_case",
    "override_case",
]
