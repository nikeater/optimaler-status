"""Queues are journal projections, and every clock on them is display-only.

A queue is "the open cases routed to one unit", derived, newest-visible-first
with an age. It is not a work-distribution engine: nothing here assigns, locks,
prioritises or hides an item, because a queue that re-ordered work when a timer
ran out would be making a legal decision with a stopwatch. What the clocks do
is make lateness VISIBLE, which is the Robodebt-shaped failure they exist to
catch (P-10: an exception queue quietly getting older is the leading indicator).

Four flags, three of them with a legal basis and one of them operational:

* **Frist laeuft** (C-9) on Widerspruch items: the Eingangszeitpunkt and the
  channel it arrived on. Nothing about Zulaessigkeit, Fristwahrung or
  Begruendetheit - those belong to the Widerspruchsstelle and the
  Widerspruchsausschuss (par. 84, par. 85 Abs. 2 Nr. 2 SGG), and routing is a
  Realakt. The Aktenzeichen is shown as PRESENT, never as a value: an
  Aktenzeichen is sealed identity data (kind ``AKTZ``) and a queue page is not
  on the re-hydration surface.
* **par. 14 SGB IX** (C-10) on Reha items: the statutory two-week
  Weiterleitungsfrist from Antragseingang, with the norm named. The flag stops
  at "the period ends on this date"; par. 14 Abs. 2 SGB IX turns missing it
  into own responsibility for the case, and that is a legal finding a queue may
  not make.
* **Clearing SLA** (C-10, par. 16 Abs. 2 S. 1 SGB I) on items that reached no
  unit: an operational self-commitment, labelled as one, because
  "unverzueglich" is a legal standard without a number.
* **Latenzbudget** (P-10) per tier: the agency's own target, reported next to
  the age of the oldest open item.

The clock is injected everywhere. A queue page that read the wall clock would
be a page whose tests pass on Monday.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from engine.config_loader import QueuesConfig
from engine.review.state import ReviewIndex, ReviewState

#: The queue id of the clearing queue - the items that reached no unit at all.
#: A string rather than ``None`` so a URL, a heading and a metric row can all
#: name it (par. 16 Abs. 2 SGB I: an item nobody owns still has an owner).
CLEARING_QUEUE = "__clearing__"

#: Sealed-value kind of an Aktenzeichen, as the redaction boundary records it.
AKTENZEICHEN_KIND = "AKTZ"


@dataclass(frozen=True)
class QueueFlag:
    """One thing a caseworker must see about a row, in words and never colour.

    ``tone`` exists for the stylesheet; the same fact is always in ``label``
    and ``detail`` as text, because BITV 2.0 / WCAG 1.4.1 does not accept
    colour as the only carrier of meaning and neither does a screen reader.
    """

    flag_id: str
    label: str
    detail: str
    tone: str = "neutral"


@dataclass(frozen=True)
class QueueRow:
    """One item in a queue, with everything the row shows already computed."""

    state: ReviewState
    age_hours: float | None
    over_budget: bool
    budget_hours: int | None
    flags: tuple[QueueFlag, ...]

    @property
    def case_id(self) -> str:
        return self.state.case_id

    @property
    def tier(self) -> int | None:
        return self.state.tier

    @property
    def age_label(self) -> str:
        """The age as a human reads it, never as a bare float."""
        if self.age_hours is None:
            return "unbekannt"
        if self.age_hours < 1:
            return f"{int(self.age_hours * 60)} Min."
        if self.age_hours < 48:
            return f"{self.age_hours:.1f} Std."
        return f"{self.age_hours / 24:.1f} Tage"


@dataclass(frozen=True)
class Queue:
    """One unit's open work, oldest first."""

    queue_id: str
    label: str
    rows: tuple[QueueRow, ...]
    clearing: bool = False
    note: str = ""

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def oldest_hours(self) -> float | None:
        ages = [row.age_hours for row in self.rows if row.age_hours is not None]
        return max(ages) if ages else None

    @property
    def over_budget_count(self) -> int:
        return sum(1 for row in self.rows if row.over_budget)


def build_queue(
    index: ReviewIndex,
    *,
    unit_id: str | None,
    now: datetime,
    config: QueuesConfig | None,
    label: str | None = None,
) -> Queue:
    """The open items of one unit, or of the clearing queue when unit is None.

    Sorted oldest first: a queue's job is to surface the item that has waited
    longest, and "newest first" is how a backlog becomes invisible. The task
    file's "newest-visible-first" is honoured by the age column and the flags,
    which is the half of it that carries information.
    """
    rows = [
        _row(state, now=now, config=config)
        for state in index.open_states()
        if state.unit_id == unit_id
    ]
    rows.sort(key=lambda row: (-(row.age_hours or 0.0), row.case_id))
    clearing = unit_id is None
    return Queue(
        queue_id=unit_id or CLEARING_QUEUE,
        label=label or (unit_id if unit_id is not None else "Zentrale Klaerung"),
        rows=tuple(rows),
        clearing=clearing,
        note=(config.clearing.note if clearing and config is not None else ""),
    )


def queue_ids(index: ReviewIndex, *, config: QueuesConfig | None) -> list[str]:
    """Every queue that has open work, plus the clearing queue, always.

    The clearing queue is listed even when it is empty. An empty clearing queue
    is a fact worth showing - it means nothing fell through - and a queue that
    only appears once it has work is a queue nobody thinks to open.
    """
    units = sorted(
        {state.unit_id for state in index.open_states() if state.unit_id is not None}
    )
    del config
    return [*units, CLEARING_QUEUE]


def _row(state: ReviewState, *, now: datetime, config: QueuesConfig | None) -> QueueRow:
    age_hours = state.age_hours(now)
    budget = config.budget_hours(state.tier) if config is not None else None
    return QueueRow(
        state=state,
        age_hours=age_hours,
        over_budget=(
            budget is not None and age_hours is not None and age_hours > budget
        ),
        budget_hours=budget,
        flags=_flags(state, now=now, config=config, age_hours=age_hours),
    )


def _flags(
    state: ReviewState,
    *,
    now: datetime,
    config: QueuesConfig | None,
    age_hours: float | None,
) -> tuple[QueueFlag, ...]:
    flags: list[QueueFlag] = []
    if state.sampled:
        # ADR-025, and the most important single line in this module: a drawn
        # case is not a suspicious one, and it may never wear the alarm tone.
        flags.append(
            QueueFlag(
                flag_id="sampled",
                label="Stichprobe",
                detail=(
                    "Zufaellig zur Qualitaetssicherung ausgewaehlt (P-1, par. 88 "
                    "Abs. 5 Nr. 1 AO analog). Kein Auffaelligkeitsbefund - die "
                    "Ziehung haengt allein an der Vorgangskennung."
                ),
                tone="neutral",
            )
        )
    if state.flagged:
        flags.append(
            QueueFlag(
                flag_id="anomaly",
                label="Auffaelligkeit (nur Protokoll)",
                detail=(
                    "Der Schattenscorer hat Merkmale markiert. Die Markierung "
                    "hat kein Tier bewegt (log_only) und ist eine Beobachtung "
                    "am Vorgang, kein Befund ueber eine Person."
                ),
                tone="attention",
            )
        )
    if state.escalated:
        flags.append(
            QueueFlag(
                flag_id="escalated",
                label="Eskaliert",
                detail="Manuell zur vollstaendigen Pruefung angehoben (P-4).",
                tone="attention",
            )
        )
    if config is not None:
        flags.extend(_config_flags(state, now=now, config=config))
    if age_hours is not None and config is not None:
        budget = config.budget_hours(state.tier)
        if budget is not None and age_hours > budget:
            flags.append(
                QueueFlag(
                    flag_id="over_budget",
                    label="Ueber Latenzbudget",
                    detail=(
                        f"Wartet {age_hours:.1f} Std.; betrieblicher Zielwert "
                        f"fuer Tier {state.tier} ist {budget} Std. Zielwert, "
                        f"keine Rechtsfrist."
                    ),
                    tone="attention",
                )
            )
    return tuple(flags)


def _config_flags(
    state: ReviewState, *, now: datetime, config: QueuesConfig
) -> list[QueueFlag]:
    flags: list[QueueFlag] = []
    if state.unit_id in set(config.widerspruch.unit_ids):
        flags.append(_widerspruch_flag(state, config=config))
    if state.unit_id in set(config.reha.unit_ids):
        flags.append(_reha_flag(state, now=now, config=config))
    if state.unit_id is None:
        flags.append(_clearing_flag(state, now=now, config=config))
    return flags


def _widerspruch_flag(state: ReviewState, *, config: QueuesConfig) -> QueueFlag:
    """C-9's queue flag. Visibility only; no admissibility statement anywhere."""
    received = state.received_at
    channel = state.case.channel or "unbekannt"
    aktenzeichen = (
        "Aktenzeichen versiegelt vorhanden"
        if _has_aktz(state)
        else ("kein Aktenzeichen erkannt")
    )
    return QueueFlag(
        flag_id="widerspruch",
        label=config.widerspruch.flag_label,
        detail=(
            f"Eingang {received.isoformat() if received else 'unbekannt'} "
            f"ueber {channel}; {aktenzeichen}. Diese Anzeige trifft KEINE "
            f"Aussage zu Zulaessigkeit, Fristwahrung oder Begruendetheit "
            f"(par. 84, par. 85 SGG) - die Zuordnung ist ein Realakt."
        ),
        tone="attention",
    )


def _reha_flag(state: ReviewState, *, now: datetime, config: QueuesConfig) -> QueueFlag:
    """C-10's par. 14 Abs. 1 SGB IX clock, from the Eingangszeitpunkt."""
    received = state.received_at
    if received is None:
        return QueueFlag(
            flag_id="reha_frist",
            label="par. 14 SGB IX",
            detail=(
                "Eingangszeitpunkt nicht im Journal; die Zwei-Wochen-Frist "
                "kann nicht berechnet werden."
            ),
            tone="attention",
        )
    due = received + timedelta(days=config.reha.weiterleitung_days)
    remaining = (due - now).total_seconds() / 86400.0
    state_word = (
        f"noch {remaining:.1f} Tage"
        if remaining >= 0
        else f"seit {-remaining:.1f} Tagen abgelaufen"
    )
    return QueueFlag(
        flag_id="reha_frist",
        label="par. 14 SGB IX",
        detail=(
            f"Weiterleitungsfrist laeuft am {due.date().isoformat()} ab "
            f"({state_word}); Grundlage {config.reha.basis}. Zur Rechtsfolge "
            f"nach par. 14 Abs. 2 SGB IX trifft diese Anzeige keine Aussage."
        ),
        tone="attention" if remaining < 0 else "neutral",
    )


def _clearing_flag(
    state: ReviewState, *, now: datetime, config: QueuesConfig
) -> QueueFlag:
    """The clearing queue's operational SLA, labelled as operational."""
    age = state.age_hours(now)
    breached = age is not None and age > config.clearing.sla_hours
    return QueueFlag(
        flag_id="clearing_sla",
        label="Klaerung offen",
        detail=(
            f"Keiner Einheit zugeordnet; betriebliche Zielzeit "
            f"{config.clearing.sla_hours} Std. "
            f"({'ueberschritten' if breached else 'eingehalten'}). Grundlage "
            f"{config.clearing.basis} - Rechtsbegriff ohne Stundenzahl, der "
            f"Wert ist eine Selbstverpflichtung."
        ),
        tone="attention" if breached else "neutral",
    )


def _has_aktz(state: ReviewState) -> bool:
    """Whether an Aktenzeichen was recognised and SEALED for this case.

    Presence, never the value. The counts come off the REDACTED event, which
    records what the boundary did BY KIND and never what it found - so C-9's
    "Aktenzeichen extraction" lands on a queue page as "there is one, sealed",
    and the value stays behind the re-hydration surface where it belongs.
    """
    return state.sealed_kind_count(AKTENZEICHEN_KIND) > 0
