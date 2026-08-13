"""Rubber-stamp and exception-path metrics, aggregate at UNIT level or coarser.

The design goal of a review UI is meaningful review, not friction-free
approval, and the difference between the two is measurable. P-6 measures it two
ways and neither of them is about a person:

* **confirm-without-edit rate** per unit - the share of confirmations where the
  caseworker did not change the prepared letter. A high rate is not proof of
  rubber-stamping (most drafts are meant to be right), but a rate that climbs
  toward 1.00 while the override rate falls toward 0.00 is the shape the
  Robodebt and toeslagen post-mortems describe.
* **median time-to-confirm** per unit - decision to confirmation, from the two
  timestamps the journal already holds. It measures QUEUE DWELL, not attention.
  Measuring attention would need per-session telemetry about a named person,
  which is exactly what BPersVG par. 80 Abs. 1 Nr. 21 makes co-determined and
  what the unit-scoped ``Actor`` structurally prevents. The metric is honest
  about being the weaker of the two signals.

P-10's remaining half is here too: the age of the oldest open item per tier and
per queue, against the per-tier latency budget in ``config/queues/``.

**C-4 is a property, not a promise.** Nothing this module produces carries a
natural-person identifier of a caseworker, because nothing it reads does: the
journal's ``Actor`` has ``kind`` and ``unit_id`` and no third field. A test in
``tests/test_review_no_person.py`` asserts it over every metric, page, export
and payload this part writes anyway - a structural guarantee that nobody checks
is a structural guarantee until somebody adds a field.

Two exclusions worth stating:

* **SAMPLED cases are excluded from flag-precision statistics** (ADR-025). An
  audit draw is not a scorer finding, and counting one as a flag would make the
  scorer look worse the more quality assurance an agency does.
* **Units with fewer than ``MIN_UNIT_ITEMS`` confirmations report no rate.** A
  confirm-without-edit rate over two cases is a number about two cases, and in
  a small unit it is also close enough to being about one person that the
  BPersVG question reopens. Reported as "zu wenige Vorgaenge", never as 1.000.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from engine.config_loader import QueuesConfig
from engine.review.state import ReviewIndex, ReviewState

#: Below this many confirmations a unit reports no rate at all. Small enough
#: that a demo shows numbers, large enough that a rate is not a statement about
#: one afternoon of one person's work.
MIN_UNIT_ITEMS = 5

#: What a suppressed rate says instead of a number.
TOO_FEW = "zu wenige Vorgaenge"


@dataclass(frozen=True)
class UnitReview:
    """P-6 for one unit. Every field is a count or a rate over counts."""

    unit_id: str
    confirmed: int = 0
    confirmed_without_edit: int = 0
    overridden: int = 0
    escalated: int = 0
    rerouted: int = 0
    sampled_confirmed: int = 0
    latencies_seconds: tuple[float, ...] = ()

    @property
    def reportable(self) -> bool:
        return self.confirmed >= MIN_UNIT_ITEMS

    @property
    def confirm_without_edit_rate(self) -> float | None:
        if not self.reportable:
            return None
        return self.confirmed_without_edit / self.confirmed

    @property
    def override_rate(self) -> float | None:
        """C-5's measured Art. 22 override rate, per unit.

        Denominator is confirmations plus corrections, i.e. every item this
        unit has taken a decision about: an override that has not yet been
        followed by a confirmation is still a human disagreeing with the
        machine, and dropping it would flatter the number.
        """
        total = self.confirmed + self.overridden
        if total < MIN_UNIT_ITEMS:
            return None
        return self.overridden / total

    @property
    def median_seconds_to_confirm(self) -> float | None:
        if not self.latencies_seconds or not self.reportable:
            return None
        return statistics.median(self.latencies_seconds)

    def as_payload(self) -> dict[str, Any]:
        """One row of the metrics panel. Units and counts, never a person."""
        return {
            "unit_id": self.unit_id,
            "confirmed": self.confirmed,
            "confirmed_without_edit": self.confirmed_without_edit,
            "confirm_without_edit_rate": self.confirm_without_edit_rate,
            "overridden": self.overridden,
            "override_rate": self.override_rate,
            "escalated": self.escalated,
            "rerouted": self.rerouted,
            "sampled_confirmed": self.sampled_confirmed,
            "median_seconds_to_confirm": self.median_seconds_to_confirm,
            "reportable": self.reportable,
            "suppressed_reason": None if self.reportable else TOO_FEW,
        }


@dataclass(frozen=True)
class TierBacklog:
    """P-10 for one tier: how long the oldest waiting item has waited."""

    tier: int
    open_items: int
    oldest_hours: float | None
    budget_hours: int | None

    @property
    def over_budget(self) -> bool:
        return (
            self.budget_hours is not None
            and self.oldest_hours is not None
            and self.oldest_hours > self.budget_hours
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "open_items": self.open_items,
            "oldest_hours": self.oldest_hours,
            "budget_hours": self.budget_hours,
            "over_budget": self.over_budget,
        }


@dataclass(frozen=True)
class ReviewMetrics:
    """Everything the panel and the eval section show about the human half."""

    units: tuple[UnitReview, ...] = ()
    backlog: tuple[TierBacklog, ...] = ()
    open_items: int = 0
    confirmed_items: int = 0
    sampled_open: int = 0
    notes: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "open_items": self.open_items,
            "confirmed_items": self.confirmed_items,
            "sampled_open": self.sampled_open,
            "min_unit_items": MIN_UNIT_ITEMS,
            "units": [unit.as_payload() for unit in self.units],
            "backlog": [tier.as_payload() for tier in self.backlog],
            "notes": list(self.notes),
        }


@dataclass
class _Accumulator:
    confirmed: int = 0
    without_edit: int = 0
    overridden: int = 0
    escalated: int = 0
    rerouted: int = 0
    sampled: int = 0
    latencies: list[float] = field(default_factory=list)


def review_metrics(
    index: ReviewIndex, *, now: datetime, config: QueuesConfig | None = None
) -> ReviewMetrics:
    """Fold the review index into the aggregate numbers. No person anywhere."""
    per_unit: dict[str, _Accumulator] = {}
    for state in index.states:
        _accumulate(per_unit, state)
    return ReviewMetrics(
        units=tuple(
            _unit_review(unit_id, accumulator)
            for unit_id, accumulator in sorted(per_unit.items())
        ),
        backlog=tuple(_backlog(index, now=now, config=config)),
        open_items=sum(1 for state in index.states if state.open),
        confirmed_items=sum(1 for state in index.states if state.confirmed),
        sampled_open=sum(1 for state in index.states if state.open and state.sampled),
        notes=(
            "Bestaetigungsquote und Dauer sind Einheitswerte; personenbezogene "
            "Auswertung findet nicht statt (BPersVG par. 80 Abs. 1 Nr. 21, C-4).",
            "Dauer misst Liegezeit von der Entscheidung bis zur Bestaetigung, "
            "nicht Lesezeit - Lesezeit waere personenbezogene Telemetrie.",
            "Stichprobenvorgaenge (P-1) zaehlen nicht in die Trefferstatistik "
            "des Scorers (ADR-025).",
            f"Einheiten unter {MIN_UNIT_ITEMS} Vorgaengen melden keine Quote.",
        ),
    )


def queue_census(
    index: ReviewIndex, *, config: QueuesConfig | None = None
) -> dict[str, Any]:
    """What the queues would look like, as COUNTS and nothing else.

    The eval harness's half of P-10, and the reason it is counts rather than
    ages: a gold run has no human in it, so every item is open and every age is
    the distance to whenever the harness happened to run. A report that carried
    that number would be reporting the machine it ran on.

    What IS meaningful on a gold run, and is what this returns: how the 101
    items distribute over the units, how many reach no unit at all (the
    clearing queue, C-10), how many carry each statutory clock, and how many
    the audit sample drew. Deterministic, so the report can be diffed.

    P-6's rates are deliberately absent and named as absent: they need
    confirmations, a gold run has none, and inventing them would put fictional
    human behaviour into the file that gates the build.
    """
    open_states = index.open_states()
    per_unit: dict[str, int] = {}
    per_tier: dict[str, int] = {}
    for state in open_states:
        key = state.unit_id or CLEARING_KEY
        per_unit[key] = per_unit.get(key, 0) + 1
        per_tier[str(state.tier)] = per_tier.get(str(state.tier), 0) + 1
    widerspruch = set(config.widerspruch.unit_ids) if config else set()
    reha = set(config.reha.unit_ids) if config else set()
    return {
        "open_items": len(open_states),
        "confirmed_items": sum(1 for state in index.states if state.confirmed),
        "by_unit": dict(sorted(per_unit.items())),
        "by_tier": dict(sorted(per_tier.items())),
        "clearing_queue": per_unit.get(CLEARING_KEY, 0),
        "widerspruch_frist_laeuft": sum(
            1 for state in open_states if state.unit_id in widerspruch
        ),
        "reha_par14_clock": sum(1 for state in open_states if state.unit_id in reha),
        "sampled_open": sum(1 for state in open_states if state.sampled),
        "note": (
            "Nur Anzahlen. Alter und Liegezeit fehlen absichtlich: im Goldlauf "
            "hat kein Mensch bestaetigt, jedes Alter waere der Abstand zum "
            "Laufzeitpunkt. Die P-6-Quoten (Bestaetigung ohne Aenderung, "
            "Liegezeit) brauchen Bestaetigungen und stehen deshalb nur in der "
            "Bearbeitungsoberflaeche ueber dem echten Journal."
        ),
    }


#: Key the census uses for items that reached no unit. Not the queue id, which
#: is a URL segment: a report is read by people and by diffs.
CLEARING_KEY = "(ohne Einheit)"


def _accumulate(per_unit: dict[str, _Accumulator], state: ReviewState) -> None:
    """Attribute one case to the unit that acted on it, or to nobody."""
    unit_id = state.unit_id
    if unit_id is None:
        return
    accumulator = per_unit.setdefault(unit_id, _Accumulator())
    if state.confirmed:
        accumulator.confirmed += 1
        confirmation = state.case.confirmation or {}
        if not confirmation.get("draft_edited"):
            accumulator.without_edit += 1
        latency = confirmation.get("seconds_since_decision")
        if isinstance(latency, int | float) and not isinstance(latency, bool):
            accumulator.latencies.append(float(latency))
        if state.sampled:
            accumulator.sampled += 1
    if state.overrides:
        accumulator.overridden += 1
    if state.escalated:
        accumulator.escalated += 1
    if state.rerouted:
        accumulator.rerouted += 1


def _unit_review(unit_id: str, accumulator: _Accumulator) -> UnitReview:
    return UnitReview(
        unit_id=unit_id,
        confirmed=accumulator.confirmed,
        confirmed_without_edit=accumulator.without_edit,
        overridden=accumulator.overridden,
        escalated=accumulator.escalated,
        rerouted=accumulator.rerouted,
        sampled_confirmed=accumulator.sampled,
        latencies_seconds=tuple(accumulator.latencies),
    )


def _backlog(
    index: ReviewIndex, *, now: datetime, config: QueuesConfig | None
) -> list[TierBacklog]:
    """P-10: the age of the oldest open item, per tier, against its budget."""
    tiers: dict[int, list[float]] = {}
    for state in index.open_states():
        if state.tier is None:
            continue
        age = state.age_hours(now)
        tiers.setdefault(state.tier, []).append(age if age is not None else 0.0)
    return [
        TierBacklog(
            tier=tier,
            open_items=len(ages),
            oldest_hours=max(ages) if ages else None,
            budget_hours=config.budget_hours(tier) if config is not None else None,
        )
        for tier, ages in sorted(tiers.items())
    ]
