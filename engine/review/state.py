"""What a caseworker sees: the machine's answer and the human's, side by side.

A second fold over the same journal ``derive_case_state`` folds, and the split
between the two is the point. ``CaseState`` says what the decision plane
decided; this module says where the item stands NOW, which is a different
question as soon as a human has re-routed, escalated or confirmed it.

Two rules the whole module is built on:

* **The routing answer is ``engine.decide.admitted_routing``**, arriving here as
  the ROUTED event the pipeline wrote from it. Nothing in this package
  re-derives a unit from ``EvidenceRecord.routing`` - that list carries
  suggestions from sources the agency has NOT admitted (the part-06 finding),
  and a queue built from it would put items in front of people the decision
  plane never sent them to. The classifier ranking is rendered separately and
  labelled log-only.
* **An override is an appended fact, not an edit.** ``machine_tier`` and
  ``machine_unit_id`` never change. ``tier`` and ``unit_id`` are the effective
  values after the human's corrections, computed by replaying the OVERRIDDEN
  events in sequence order, and the difference between the two pairs is exactly
  what the correction pool exports (C-5's measured Art. 22 override rate).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from engine.decide import is_audit_sample_reason
from engine.journal.projection import CaseState, derive_case_state
from schemas.events import Event

#: ``field`` values an OVERRIDDEN payload may carry. A correction is one of
#: three things, and naming them here rather than accepting free strings is what
#: makes the training pool a labelled set rather than a log.
OVERRIDE_UNIT = "unit"
OVERRIDE_TIER = "tier"
OVERRIDE_ESCALATION = "escalation"
OVERRIDE_FIELDS = frozenset({OVERRIDE_UNIT, OVERRIDE_TIER, OVERRIDE_ESCALATION})

#: The tier a one-click escalation moves an item to (P-4, par. 88 Abs. 5 Nr. 3
#: AO analog). Full human review and nothing else: an escalation that could
#: land anywhere else would be a re-decision rather than a request for
#: oversight.
ESCALATION_TIER = 3


@dataclass(frozen=True)
class ReviewState:
    """One case as a queue row and as a case view. Derived, never stored."""

    case_id: str
    case: CaseState
    machine_tier: int | None
    machine_unit_id: str | None
    tier: int | None
    unit_id: str | None
    confirmed: bool
    confirmed_at: datetime | None
    escalated: bool
    sampled: bool
    flagged: bool
    overrides: tuple[dict[str, Any], ...] = ()
    dispatch: dict[str, Any] | None = None

    @property
    def open(self) -> bool:
        """Whether this item still waits for a human.

        Confirmation closes an item; an override does not. Re-routing a case
        hands it to a different queue and it is still unconfirmed there, which
        is precisely the state the receiving unit needs to see.
        """
        return not self.confirmed

    @property
    def rerouted(self) -> bool:
        return self.unit_id != self.machine_unit_id

    @property
    def tier_changed(self) -> bool:
        return self.tier != self.machine_tier

    @property
    def received_at(self) -> datetime | None:
        return self.case.received_at

    @property
    def decided_at(self) -> datetime | None:
        """When the decision plane finished with it - the queue clock's start.

        Falls back to the received timestamp: an item whose journal was
        truncated must still show an age, and an age that is too LONG is the
        safe direction to be wrong in for a queue.
        """
        return (
            self.case.last_event_at if self.case.tier is not None else self.received_at
        )

    def age(self, now: datetime) -> timedelta | None:
        """How long this item has been waiting, from receipt. Display only."""
        if self.received_at is None:
            return None
        return now - self.received_at

    def age_hours(self, now: datetime) -> float | None:
        age = self.age(now)
        return None if age is None else age.total_seconds() / 3600.0

    def sealed_kind_count(self, kind: str) -> int:
        """How many values of one sealed KIND this case's prose held.

        Presence, never the value: "an Aktenzeichen stood here" is what a
        caseworker needs in order to read a working copy, and it is also the
        most any page outside the re-hydration surface may say.
        """
        return self.case.text_sealed_counts.get(kind, 0)


def review_state(case_id: str, events: list[Event]) -> ReviewState:
    """Fold one case's events into the state the review UI renders."""
    case = derive_case_state(case_id, events)
    tier = case.tier
    unit_id = case.routed_unit_id
    escalated = False
    for override in sorted(case.overrides, key=lambda item: item.get("sequence", 0)):
        which = override.get("field")
        if which == OVERRIDE_UNIT:
            unit_id = _as_str_or_none(override.get("to"))
        elif which in (OVERRIDE_TIER, OVERRIDE_ESCALATION):
            moved = _as_int_or_none(override.get("to"))
            if moved is not None:
                tier = moved
            escalated = escalated or which == OVERRIDE_ESCALATION
    confirmation = case.confirmation or {}
    return ReviewState(
        case_id=case_id,
        case=case,
        machine_tier=case.tier,
        machine_unit_id=case.routed_unit_id,
        tier=tier,
        unit_id=unit_id,
        confirmed=case.confirmed,
        confirmed_at=case.confirmed_at,
        escalated=escalated,
        sampled=is_sampled(case),
        flagged=bool((case.anomaly or {}).get("flagged")),
        overrides=tuple(case.overrides),
        dispatch=_as_dict_or_none(confirmation.get("dispatch")),
    )


def is_sampled(case: CaseState) -> bool:
    """Whether the P-1 audit sample drew this item (ADR-025, either shape).

    Read from the decision reasons rather than from the ``audit_sample`` block,
    because the block is only written when sampling is switched on at all while
    a reason is written when a draw actually happened - and this predicate must
    answer "was this item drawn", not "does this agency sample".
    """
    return any(
        is_audit_sample_reason(reason.get("kind"), reason.get("rule_id"))
        for reason in case.reasons
    )


@dataclass
class ReviewIndex:
    """Every case in a store, folded once.

    Built in one pass because every consumer - the queues, the metrics, the
    corrections export - needs the same fold, and three independent folds over
    the same journal would eventually disagree about what "open" means.
    """

    states: list[ReviewState] = field(default_factory=list)

    def open_states(self) -> list[ReviewState]:
        return [state for state in self.states if state.open]

    def by_unit(self, unit_id: str | None) -> list[ReviewState]:
        return [state for state in self.states if state.unit_id == unit_id]

    def get(self, case_id: str) -> ReviewState | None:
        for state in self.states:
            if state.case_id == case_id:
                return state
        return None


def build_index(store: Any) -> ReviewIndex:
    """Fold every case a journal store knows about.

    Takes the ``JournalStore`` protocol structurally rather than by import, so
    a projection cannot become a reason to import a backend.
    """
    return ReviewIndex(
        states=[
            review_state(case_id, store.read(case_id)) for case_id in store.case_ids()
        ]
    )


def _as_str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_dict_or_none(value: object) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None
