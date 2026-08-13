"""Which drafts does a case owe? A fold over its event list, plus one fetch.

The shape is deliberately the notification worker's (part 07): a pure function
answers "what does this case owe" from the journal alone, subtracts what the
journal already records as drafted, and a second function does the writing. Two
differences, both load-bearing:

* this fold ends in a **vault read** - the one production ``fetch`` there is -
  because a letter carries the applicant's identity and a receipt does not;
* a prepared decision needs the item's extracted VALUES, which the journal
  deliberately does not carry (the EXTRACTED payload records field ids and
  counts). They are handed in by the caller that has them, and a replay that
  cannot supply them reports the case as blocked instead of drafting a letter
  with an empty fact list. A journal that recorded every value would be a
  second copy of the submission, and part 05 was right not to build one.

**The draft policy, deterministic (ruling 7):** tier 2 with gaps owes a
Nachforderung, tier 1 owes a prepared decision, tier 3 owes NOTHING - drafting
for an item a human has not read yet would presume the outcome, and the whole
point of tier 3 is that nobody has decided anything.

**Ordering: save first, journal second**, for ADR-022's reason applied one
artifact further on. A DRAFTED event for a draft nobody can open is a claim the
audit trail cannot support; a saved draft with no event is re-derived on the
next run, saved again (a no-op, by the deterministic draft id) and journaled.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from engine.config_loader import ConfigBundle
from engine.draft.letters import (
    KIND_NACHFORDERUNG,
    KIND_PREPARED_DECISION,
    DraftingError,
    DraftRequest,
    GapSentence,
    RenderedDraft,
    build_letter,
    gap_sentences,
)
from engine.draft.rehydrate import RehydrationError, Rehydrator
from engine.draft.store import DraftRecord, DraftStore, draft_id_for
from engine.journal.store import JournalStore, emit
from engine.redact.vault import VaultStore
from schemas.events import Event, EventType
from schemas.extraction import ExtractionSet


@dataclass(frozen=True)
class OwedDraft:
    """One draft a case owes and that the journal does not record as written."""

    case_id: str
    kind: str
    tier: int
    source_event_id: str
    source_event_at: datetime
    envelope_id: str
    vault_ref: str
    procedure_id: str | None
    channel_id: str | None
    unit_id: str | None
    received_at: datetime | None
    gaps: tuple[GapSentence, ...] = ()


@dataclass(frozen=True)
class BlockedDraft:
    """A draft that was owed and could not be produced, with the reason.

    Reported rather than swallowed and never partially written: ruling 2's "no
    partial output" applies to the whole draft, not only to the substitution.
    """

    case_id: str
    kind: str
    source_event_id: str
    reason: str


@dataclass(frozen=True)
class DraftOutcome:
    """What one drafting run did to one case."""

    case_id: str
    drafts: tuple[DraftRecord, ...] = ()
    events: tuple[Event, ...] = ()
    blocked: tuple[BlockedDraft, ...] = ()
    skipped: int = 0

    @property
    def count(self) -> int:
        """How many drafts this run actually wrote."""
        return len(self.events)


def facts_from(extractions: ExtractionSet) -> dict[str, str]:
    """``field id -> value`` for a prepared decision, from the extraction set.

    One definition, used by the API path and by the eval harness, so the letter
    a demo produces and the letter a metric counts are the same letter. Sealed
    fields arrive here as placeholders and are resolved at re-hydration like
    everything else - the fact list is not a second identity channel.
    """
    return {record.field: record.value for record in extractions.records}


def drafted_source_event_ids(events: Iterable[Event]) -> frozenset[str]:
    """Source event ids the journal already records a DRAFTED for.

    Read defensively for the reason every projection in this repo is: a DRAFTED
    whose payload lost its source id degrades to "not recorded", which re-drafts
    at most one letter into a store that dedupes it anyway. Raising would take
    the case view down over a malformed payload.
    """
    return frozenset(
        source_id
        for event in events
        if event.type is EventType.DRAFTED
        for source_id in [event.payload.get("source_event_id")]
        if isinstance(source_id, str)
    )


def draft_kind_for(tier: int, gaps: Sequence[GapSentence]) -> str | None:
    """The draft policy of ruling 7, in one function.

    Tier 2 without gaps returns None and cannot occur today - the tier-2 row of
    the decision table requires an INCOMPLETE verdict - but a table an agency
    edits could produce it, and a "Nachforderung" that asks for nothing would be
    a letter with a blank list in it.
    """
    if tier == 1:
        return KIND_PREPARED_DECISION
    if tier == 2 and gaps:
        return KIND_NACHFORDERUNG
    return None


def owed_drafts(
    events: Sequence[Event], *, config: ConfigBundle, include_drafted: bool = False
) -> tuple[OwedDraft, ...]:
    """Every draft this event list owes and has not been recorded as writing.

    Pure: no store is read, no clock is called, nothing is written. Order is
    the journal's own sequence order, so a replay produces the same list in the
    same order every time.

    ``include_drafted`` returns the owed items a DRAFTED event already covers.
    Exactly one caller wants that, and it is part 10's confirm step: a
    caseworker who opts into the par. 66 Abs. 3 SGB I block is asking for a
    DIFFERENT letter than the one drafting prepared, and re-deriving what the
    letter is made of has to start from the same fold that produced it. It is
    off by default so a replay can never re-draft what it already wrote.
    """
    drafting = config.drafting
    if drafting is None:
        return ()
    ordered = sorted(events, key=lambda event: event.sequence)
    already = frozenset() if include_drafted else drafted_source_event_ids(ordered)
    state = _fold(ordered)
    owed: list[OwedDraft] = []
    for event in ordered:
        if event.type is not EventType.TIER_DECIDED or event.event_id in already:
            continue
        tier = _as_int(event.payload.get("tier"))
        gaps = gap_sentences(
            state["gaps"], drafting=drafting, procedure_id=state["procedure_id"]
        )
        kind = draft_kind_for(tier, gaps) if tier is not None else None
        if kind is None or not state["vault_ref"]:
            continue
        owed.append(
            OwedDraft(
                case_id=event.case_id,
                kind=kind,
                tier=tier if tier is not None else 3,
                source_event_id=event.event_id,
                source_event_at=event.occurred_at,
                envelope_id=str(event.payload.get("envelope_id") or ""),
                vault_ref=str(state["vault_ref"]),
                procedure_id=state["procedure_id"],
                channel_id=state["channel"],
                unit_id=_as_str(event.payload.get("routed_unit_id")),
                received_at=state["received_at"],
                gaps=gaps,
            )
        )
    return tuple(owed)


def draft_case(
    events: Sequence[Event],
    *,
    config: ConfigBundle,
    journal: JournalStore,
    vault: VaultStore,
    drafts: DraftStore,
    facts: Mapping[str, str] | None = None,
    rechtsfolgenhinweis: bool = False,
    now: datetime | None = None,
) -> DraftOutcome:
    """Write and journal everything one case owes. Idempotent by construction.

    Args:
        facts: extracted ``field id -> value`` of the item, which a prepared
            decision states back to the applicant. Sealed fields arrive as
            placeholders and re-hydrate with everything else. Omit on a replay:
            the tier-1 case is then reported as blocked rather than drafted
            with a blank fact list.
        rechtsfolgenhinweis: opt-in par. 66 Abs. 3 SGB I block, off by default
            (C-6). Part 10's review UI owns the choice, per case.
    """
    owed = owed_drafts(events, config=config)
    if not owed:
        return DraftOutcome(case_id=_case_id(events))
    rehydrator = Rehydrator(vault)
    written: list[Event] = []
    saved: list[DraftRecord] = []
    blocked: list[BlockedDraft] = []
    skipped = 0
    for item in owed:
        record = _render(
            item,
            config=config,
            rehydrator=rehydrator,
            facts=facts,
            rechtsfolgenhinweis=rechtsfolgenhinweis,
            now=now,
        )
        if isinstance(record, BlockedDraft):
            blocked.append(record)
            continue
        if drafts.save(record):
            saved.append(record)
        else:
            # Already in the store but not in the journal: a crash between the
            # two writes. Journal it now rather than dropping it - that is the
            # whole point of the save-first order (ADR-022).
            skipped += 1
        written.append(
            emit(
                journal,
                case_id=item.case_id,
                event_type=EventType.DRAFTED,
                versions=config.version_stamp(),
                occurred_at=now,
                # A draft is the opposite of an informational Realakt: it is
                # prepared to have procedural consequence once a human confirms
                # it. Stated rather than left null, so a reader of the journal
                # never has to infer it (the contract requires the flag only on
                # NOTIFIED, where it must be True).
                informational_only=False,
                template_id=record.template_id,
                payload={
                    "envelope_id": item.envelope_id,
                    "source_event_id": item.source_event_id,
                    "source_event_type": EventType.TIER_DECIDED.value,
                    # Everything below is value-free. The rendered letter lives
                    # in the draft store and nowhere else: the journal records
                    # THAT a draft exists and what it is made of, never what it
                    # says about a person.
                    **record.summary(),
                    "dispatched": False,
                    "dispatch_shape": _dispatch_shape(item, config=config),
                },
            )
        )
    return DraftOutcome(
        case_id=owed[0].case_id,
        drafts=tuple(saved),
        events=tuple(written),
        blocked=tuple(blocked),
        skipped=skipped,
    )


def _render(
    item: OwedDraft,
    *,
    config: ConfigBundle,
    rehydrator: Rehydrator,
    facts: Mapping[str, str] | None,
    rechtsfolgenhinweis: bool,
    now: datetime | None,
) -> DraftRecord | BlockedDraft:
    """One draft, or the reason there is none. Never a partial letter."""
    drafting = config.drafting
    if drafting is None:  # pragma: no cover - owed_drafts returns () without it
        return BlockedDraft(item.case_id, item.kind, item.source_event_id, "no config")
    if item.kind == KIND_PREPARED_DECISION and not facts:
        return BlockedDraft(
            case_id=item.case_id,
            kind=item.kind,
            source_event_id=item.source_event_id,
            reason="a prepared decision states the extracted values back to the "
            "applicant, and the journal deliberately carries none; run it on "
            "the pipeline path",
        )
    missing = [gap.requirement_id for gap in item.gaps if not gap.sentence.strip()]
    if missing:
        return BlockedDraft(
            case_id=item.case_id,
            kind=item.kind,
            source_event_id=item.source_event_id,
            reason=f"no request wording for {missing}; a Nachforderung may not "
            f"silently drop a gap",
        )
    try:
        rendered = build_letter(
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
                facts=dict(facts or {}),
            ),
            config=config,
            record=rehydrator.record(item.vault_ref),
            rechtsfolgenhinweis=rechtsfolgenhinweis,
        )
    except (RehydrationError, DraftingError) as error:
        return BlockedDraft(
            case_id=item.case_id,
            kind=item.kind,
            source_event_id=item.source_event_id,
            reason=str(error),
        )
    return _record_for(item, rendered, drafting_version=drafting.version, now=now)


def _record_for(
    item: OwedDraft,
    rendered: RenderedDraft,
    *,
    drafting_version: str,
    now: datetime | None,
) -> DraftRecord:
    return DraftRecord(
        draft_id=draft_id_for(item.source_event_id, rendered.template_id),
        case_id=item.case_id,
        envelope_id=item.envelope_id,
        kind=rendered.kind,
        template_id=rendered.template_id,
        procedure_id=item.procedure_id,
        tier=item.tier,
        requirement_ids=list(rendered.requirement_ids),
        amtsermittlung_ids=list(rendered.amtsermittlung_ids),
        subject=rendered.subject,
        body=rendered.body,
        resolved_tokens=rendered.resolved_tokens,
        distinct_tokens=rendered.distinct_tokens,
        token_kinds=dict(rendered.token_kinds),
        response_window_days=rendered.response_window_days,
        rechtsfolgenhinweis=rendered.rechtsfolgenhinweis,
        source_event_id=item.source_event_id,
        drafting_version=drafting_version,
        created_at=now or item.source_event_at,
    )


def _dispatch_shape(item: OwedDraft, *, config: ConfigBundle) -> str | None:
    """How this letter would have to LEAVE the house (C-8), if it ever did.

    Recorded on the event because part 08 dispatches nothing: the shape is the
    requirement the pilot inherits, and writing it down per case is what makes
    it checkable later instead of a sentence in a document.
    """
    drafting = config.drafting
    if drafting is None:  # pragma: no cover - unreachable via draft_case
        return None
    channel = drafting.channel(item.channel_id)
    return channel.dispatch if channel is not None else None


def _fold(events: Sequence[Event]) -> dict[str, Any]:
    """The case facts a letter may state, folded out of the event list.

    Its own fold rather than ``derive_case_state`` for the notification
    worker's reason: it reads exactly the keys a draft may use, so a future
    field on the case view cannot become reachable from a letter by accident.
    """
    state: dict[str, Any] = {
        "channel": None,
        "procedure_id": None,
        "vault_ref": None,
        "received_at": None,
        "gaps": [],
    }
    for event in events:
        if event.type is EventType.RECEIVED:
            state["channel"] = _as_str(event.payload.get("channel"))
            state["vault_ref"] = _as_str(event.payload.get("vault_ref"))
            state["received_at"] = event.occurred_at
        elif event.type is EventType.EVIDENCE_ASSEMBLED:
            procedure = event.payload.get("procedure")
            if isinstance(procedure, dict):
                state["procedure_id"] = _as_str(procedure.get("procedure_id"))
            state["gaps"] = _gaps_of(event)
    return state


@dataclass(frozen=True)
class _JournalGap:
    """A gap as the EVIDENCE_ASSEMBLED payload records it.

    Shaped like ``engine.evidence.nachforderung.GapRendering`` so that
    ``gap_sentences`` cannot tell where its input came from: the sentence a
    letter assembles is the sentence the evidence plane rendered, whether it
    arrives on a pipeline result or out of the journal.
    """

    requirement_id: str
    sentence: str


def _gaps_of(event: Event) -> list[_JournalGap]:
    gaps = event.payload.get("gaps")
    if not isinstance(gaps, list):  # pragma: no cover - defensive
        return []
    return [
        _JournalGap(
            requirement_id=str(gap.get("requirement_id", "")),
            sentence=str(gap.get("request_text") or ""),
        )
        for gap in gaps
        if isinstance(gap, dict)
    ]


def _case_id(events: Sequence[Event]) -> str:
    return events[0].case_id if events else ""


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
