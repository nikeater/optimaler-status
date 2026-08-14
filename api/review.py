"""The caseworker surface, server-rendered: queues, case view, confirm flow.

Same principle as ``api/metrics.py`` and ``api/inbox.py`` and for a stronger
reason: this module reads the journal and the stores that already exist and
computes no fact of its own. A review UI that held state would be a second
answer to "what happened to this case", and the whole architecture rests on
there being one.

**The unit picker is a demo, and the page says so.** There is no identity
provider here: the unit is a query parameter, validated against the taxonomy so
a typo cannot act as a role, and that is a stand-in for the Berechtigungskonzept
C-5 names as a pre-pilot deliverable. Every page that shows it says out loud
that it is not authentication. The one thing the picker DOES gate is the draft
section and ``GET /drafts/{case_id}`` - the only surface in this system that
returns re-hydrated identity data.

**Nothing here can send anything to an applicant.** ``/inbox`` stays read-only
and gains no control from this module (ADR-005, the part-07 line): a
notification is a projection of the journal, and a caseworker button that
"approved" one would silently turn a Realakt into something else. The only
outbound thing a caseworker can act on is a prepared LETTER, which is exactly
the artifact ADR-003 built the human step for.

**Every action is a POST that appends an event.** The forms work with
JavaScript switched off - htmx swaps a fragment when it is there and a plain
form submission redirects when it is not - because a public-administration UI
whose only interaction model is a script is a UI that excludes people.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from api.i18n import GERMAN, PageContext, phrase
from api.metrics import render_template
from engine.config_loader import ConfigBundle
from engine.decide import is_audit_sample_reason
from engine.draft.store import DraftRecord, DraftStore
from engine.journal.store import JournalStore
from engine.review import (
    CLEARING_QUEUE,
    Queue,
    ReviewIndex,
    ReviewMetrics,
    ReviewState,
    build_index,
    build_queue,
    review_metrics,
    review_state,
)
from engine.score import render_reason
from schemas.anomaly import AnomalyReason
from schemas.events import Event

#: What the page says wherever the unit picker appears. One sentence, on every
#: page, because "this is not authentication" is the kind of caveat that stops
#: being read when it lives in a document.
#:
#: Read out of the translation table rather than written here since part 16.
#: The sentence appears on the caseworker screens (German, always) AND on the
#: landing page and the tour (translated), and two copies of one sentence is
#: how the two copies start disagreeing.
PICKER_NOTE = phrase("picker.note")

#: The one sentence a sampled case gets. Never the anomaly styling, never the
#: word "auffaellig" (ADR-025).
SAMPLED_NOTE = (
    "Dieser Vorgang wurde zufällig zur Qualitätssicherung ausgewählt "
    "(P-1, par. 88 Abs. 5 Nr. 1 AO analog). Das ist KEIN "
    "Auffälligkeitsbefund: die Ziehung hängt allein an der Vorgangskennung "
    "und sagt nichts über den Vorgang oder die antragstellende Person aus."
)

#: Human-readable tier names. Numbers alone are jargon on a screen a caseworker
#: reads forty times a day. German, for the caseworker templates; the citizen
#: pages ask :func:`tier_label` for the reader's language.
TIER_LABELS = {tier: phrase(f"tier.{tier}") for tier in (1, 2, 3)}

#: What a sealed kind stood for, in words. The working copy shows placeholders
#: and this is how a caseworker knows what one replaced.
KIND_LABELS = {
    kind: phrase(f"kind.{kind}")
    for kind in (
        "VSNR",
        "GEBDAT",
        "ADDR",
        "NAME",
        "ORG",
        "BNR",
        "IBAN",
        "STID",
        "AKTZ",
        "EMAIL",
        "TEL",
        "TEXT",
    )
}

#: The clearing queue's label. Passed EXPLICITLY wherever a queue is built, so
#: the overview and the queue page cannot show two spellings of one queue.
CLEARING_LABEL_KEY = "queue.clearing"


def clearing_label(page: PageContext | None = None) -> str:
    """The clearing queue's name, in one language."""
    return (page or GERMAN).t(CLEARING_LABEL_KEY)


def tier_label(tier: int | None, page: PageContext | None = None) -> str:
    """One tier's name, in one language. An unknown tier keeps its number."""
    if tier in (1, 2, 3):
        return (page or GERMAN).t(f"tier.{tier}")
    return f"Tier {tier}"


def channel_label(channel: str | None, page: PageContext | None = None) -> str:
    """One channel's name, in one language. Anything else keeps its own id."""
    if channel in ("fit_connect", "email"):
        return (page or GERMAN).t(f"channel.{channel}")
    return channel or (page or GERMAN).t("pipeline.d.unknown")


@dataclass(frozen=True)
class Unit:
    """One selectable unit in the demo picker."""

    unit_id: str
    name: str


def acting_unit_name(units: tuple[Unit, ...], unit_id: str | None) -> str:
    """The chosen unit's name, or the empty string when none is chosen.

    Deliberately empty rather than "keine Einheit gewaehlt" (which is what
    :func:`unit_name` returns): the templates branch on it, and a page that
    says "acting as: no unit selected" states a non-fact as a fact.

    Exists because the picker looked broken. Submitting it re-rendered a page
    whose only visible difference was which option the `<select>` had marked -
    and since any unit may read any queue by design (ADR-026), no table moved
    either. The choice DID take effect; nothing on the page said so. This is
    what the page-head says it with.
    """
    if unit_id is None:
        return ""
    for unit in units:
        if unit.unit_id == unit_id:
            return unit.name
    return unit_id


@dataclass(frozen=True)
class QueueSummary:
    """One line of the queue overview."""

    queue_id: str
    label: str
    count: int
    oldest_label: str
    over_budget: int
    clearing: bool


@dataclass(frozen=True)
class QueueOverview:
    """The landing page: every queue with open work, plus the metrics."""

    queues: tuple[QueueSummary, ...]
    units: tuple[Unit, ...]
    unit_id: str | None
    metrics: ReviewMetrics
    now: datetime
    open_items: int
    picker_note: str = PICKER_NOTE

    @property
    def acting_unit(self) -> str:
        """The chosen unit's name, or "" - what the page-head states in words."""
        return acting_unit_name(self.units, self.unit_id)


@dataclass(frozen=True)
class QueueView:
    """One queue page."""

    queue: Queue
    unit_name: str
    units: tuple[Unit, ...]
    unit_id: str | None
    now: datetime
    #: DISPLAY ONLY, and the whole of part 13's footprint on this UI. The demo
    #: tour hands a visitor from the pipeline view to the queue their own case
    #: landed in, and a queue of a hundred rows needs to say which one that
    #: was. It marks a row and changes NOTHING else: the rows arrive from
    #: ``build_queue`` in the order that function produced them, oldest first,
    #: and no branch here sorts, filters or hides. Empty on every non-demo
    #: request, which is every request outside demo mode.
    highlight: str = ""
    tier_labels: dict[int, str] = field(default_factory=lambda: dict(TIER_LABELS))
    picker_note: str = PICKER_NOTE

    @property
    def acting_unit(self) -> str:
        """The chosen unit's name, or "" - what the page-head states in words."""
        return acting_unit_name(self.units, self.unit_id)

    @property
    def highlighted(self) -> bool:
        """Whether the highlighted case is actually in this queue."""
        return bool(self.highlight) and any(
            row.case_id == self.highlight for row in self.queue.rows
        )


@dataclass(frozen=True)
class ReasonLine:
    """One anomaly reason, rendered by the ONE function that renders them."""

    text: str
    contribution: float


@dataclass(frozen=True)
class CaseView:
    """Everything the case view shows, already normalized for the template."""

    state: ReviewState
    events: tuple[Event, ...]
    units: tuple[Unit, ...]
    unit_id: str | None
    unit_name: str
    now: datetime
    sealed_kinds: tuple[tuple[str, str, int], ...] = ()
    anomaly_reasons: tuple[ReasonLine, ...] = ()
    classifier_ranking: tuple[dict[str, Any], ...] = ()
    drafts: tuple[DraftRecord, ...] = ()
    drafts_gated: bool = True
    message: str = ""
    error: str = ""
    #: Printed next to the confirm button. A caseworker may not stamp an
    #: absolute deadline without seeing which calendar computed it.
    dispatch_land: str = ""
    dispatch_holidays: int = 0
    tier_labels: dict[int, str] = field(default_factory=lambda: dict(TIER_LABELS))
    picker_note: str = PICKER_NOTE
    sampled_note: str = SAMPLED_NOTE

    @property
    def acting_unit(self) -> str:
        """The chosen unit's name, or "" - what the page-head states in words."""
        return acting_unit_name(self.units, self.unit_id)

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(self.state.tier or 0, f"Tier {self.state.tier}")

    @property
    def machine_tier_label(self) -> str:
        return TIER_LABELS.get(
            self.state.machine_tier or 0, f"Tier {self.state.machine_tier}"
        )

    @property
    def has_nachforderung(self) -> bool:
        return any(record.kind == "nachforderung" for record in self.drafts)


def known_units(config: ConfigBundle) -> tuple[Unit, ...]:
    """Every unit a caseworker may act as, from the taxonomy and nowhere else."""
    return tuple(
        Unit(unit_id=node.unit_id, name=node.name) for node in config.taxonomy.nodes
    )


def resolve_unit(config: ConfigBundle, unit_id: str | None) -> str | None:
    """The picked unit, or None. A unit the taxonomy does not know is None.

    Deliberately silent rather than an error: an unknown unit in a bookmarked
    URL should show the picker again, not a stack trace - and it must never
    behave as a role, which is what "return None" guarantees.
    """
    if not unit_id:
        return None
    known = {node.unit_id for node in config.taxonomy.nodes}
    return unit_id if unit_id in known else None


def unit_name(config: ConfigBundle, unit_id: str | None) -> str:
    if unit_id is None:
        return "keine Einheit gewaehlt"
    for node in config.taxonomy.nodes:
        if node.unit_id == unit_id:
            return node.name
    return unit_id


def build_overview(
    journal: JournalStore,
    *,
    config: ConfigBundle,
    unit_id: str | None,
    now: datetime | None = None,
) -> QueueOverview:
    """The landing page's view, from one fold over the whole journal."""
    moment = now or datetime.now(UTC)
    index = build_index(journal)
    summaries = [
        _summary(build_queue(index, unit_id=known, now=moment, config=config.queues))
        for known in _units_with_work(index)
    ]
    summaries.append(
        _summary(
            build_queue(
                index,
                unit_id=None,
                now=moment,
                config=config.queues,
                label=clearing_label(),
            )
        )
    )
    return QueueOverview(
        queues=tuple(summaries),
        units=known_units(config),
        unit_id=unit_id,
        metrics=review_metrics(index, now=moment, config=config.queues),
        now=moment,
        open_items=len(index.open_states()),
    )


def build_queue_view(
    journal: JournalStore,
    *,
    config: ConfigBundle,
    queue_id: str,
    unit_id: str | None,
    now: datetime | None = None,
    highlight: str = "",
) -> QueueView:
    """One queue page. ``queue_id`` is a unit id or the clearing marker.

    ``highlight`` marks one row for the demo tour and is display only; it never
    reaches ``build_queue``, so it cannot change what is in the queue or in
    which order (see :class:`QueueView`).
    """
    moment = now or datetime.now(UTC)
    index = build_index(journal)
    target = None if queue_id == CLEARING_QUEUE else queue_id
    queue = build_queue(
        index,
        unit_id=target,
        now=moment,
        config=config.queues,
        label=(clearing_label() if target is None else unit_name(config, target)),
    )
    return QueueView(
        queue=queue,
        unit_name=unit_name(config, unit_id),
        units=known_units(config),
        unit_id=unit_id,
        now=moment,
        highlight=highlight,
    )


def build_case_view(
    journal: JournalStore,
    *,
    config: ConfigBundle,
    case_id: str,
    unit_id: str | None,
    drafts: DraftStore | None = None,
    now: datetime | None = None,
    message: str = "",
    error: str = "",
) -> CaseView | None:
    """The case view, or None when the journal knows no such case."""
    events = journal.read(case_id)
    if not events:
        return None
    moment = now or datetime.now(UTC)
    state = review_state(case_id, events)
    gated = unit_id is None or drafts is None
    return CaseView(
        state=state,
        events=tuple(sorted(events, key=lambda event: event.sequence)),
        units=known_units(config),
        unit_id=unit_id,
        unit_name=unit_name(config, unit_id),
        now=moment,
        sealed_kinds=sealed_kinds(state),
        anomaly_reasons=anomaly_reason_lines(state),
        classifier_ranking=_ranking(state),
        drafts=() if drafts is None or gated else tuple(drafts.records(case_id)),
        drafts_gated=gated,
        message=message,
        error=error,
        dispatch_land=config.dispatch.land if config.dispatch else "nicht konfiguriert",
        dispatch_holidays=len(config.dispatch.holidays) if config.dispatch else 0,
    )


def render_overview(view: QueueOverview, page: PageContext | None = None) -> str:
    return render_template("review_overview.html", view, page)


def render_queue(view: QueueView, page: PageContext | None = None) -> str:
    return render_template("review_queue.html", view, page)


def render_case(view: CaseView, page: PageContext | None = None) -> str:
    return render_template("review_case.html", view, page)


def _units_with_work(index: ReviewIndex) -> list[str]:
    return sorted(
        {state.unit_id for state in index.open_states() if state.unit_id is not None}
    )


def _summary(queue: Queue) -> QueueSummary:
    oldest = queue.oldest_hours
    return QueueSummary(
        queue_id=queue.queue_id,
        label=queue.label,
        count=queue.count,
        oldest_label=("-" if oldest is None else f"{oldest:.1f} Std."),
        over_budget=queue.over_budget_count,
        clearing=queue.clearing,
    )


def sealed_kinds(state: ReviewState) -> tuple[tuple[str, str, int], ...]:
    """What the boundary sealed out of this case's prose, by kind and count.

    Public because part 13's citizen-facing pipeline view shows the same three
    columns to the applicant, and two functions producing "an Aktenzeichen
    stood here" would be two vocabularies for one fact.
    """
    return tuple(
        (kind, KIND_LABELS.get(kind, kind), count)
        for kind, count in sorted(state.case.text_sealed_counts.items())
        if count
    )


def anomaly_reason_lines(state: ReviewState) -> tuple[ReasonLine, ...]:
    """Anomaly reasons, rendered by ``engine.score.render_reason`` and nothing else.

    Not re-worded here, and this is the whole point: ``render_reason`` is the
    one function that turns a reason into a German sentence, the eval gate
    checks the string it produces, and a screen that phrased it differently
    would be a second definition of what the caseworker was told. Part 13's
    pipeline view calls THIS function for the same reason, one layer out: the
    citizen and the caseworker have to be reading the same sentence.
    """
    payload = state.case.anomaly or {}
    lines: list[ReasonLine] = []
    for entry in payload.get("reasons", []) or []:
        if not isinstance(entry, dict):
            continue
        try:
            reason = AnomalyReason(
                feature=str(entry.get("feature", "")),
                observed=str(entry.get("observed", "")),
                expected=str(entry.get("expected", "")),
                contribution=float(entry.get("contribution", 0.0)),
            )
        except (TypeError, ValueError):  # pragma: no cover - defensive
            continue
        lines.append(
            ReasonLine(text=render_reason(reason), contribution=reason.contribution)
        )
    return tuple(lines)


def _ranking(state: ReviewState) -> tuple[dict[str, Any], ...]:
    """The classifier's ranking, for the clearly-labelled log-only panel.

    Read from the classifier payload rather than from ``routing``: a suggestion
    on the routing list looks exactly like a rule hit in a table, and the
    part-06 finding is that a reader who sees them side by side starts weighing
    them. The admitted answer is the ROUTED event and it has its own section.
    """
    ranking = state.case.classifier.get("ranking")
    if not isinstance(ranking, list):
        return ()
    return tuple(entry for entry in ranking if isinstance(entry, dict))


def audit_sample_reasons(state: ReviewState) -> tuple[dict[str, Any], ...]:
    """The decision reasons that are audit draws, in either ADR-025 shape."""
    return tuple(
        reason
        for reason in state.case.reasons
        if is_audit_sample_reason(reason.get("kind"), reason.get("rule_id"))
    )
