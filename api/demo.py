"""The guided showcase: a citizen submits, watches the machine, becomes the clerk.

Three pages, all demo-mode-only, all server-rendered like everything else here.

``/demo/rundgang``
    The tour. The whole system told from beginning to end in six steps for a
    visitor who has never seen it, each step linking to the page where that
    step actually happens. Since part 16 the page is written in ONE language
    at a time - the header's toggle switches it - rather than carrying an
    English aside under every German paragraph. **It derives nothing.** Every
    sentence is either static prose or a
    fact read off the same projections the other pages read: whether this
    deployment accepts submissions at all, and - for the seeded case the tour
    points at - the unit and tier the journal already recorded. When the state
    was not seeded from the frozen corpus, the tour says so and links the
    caseworker surface instead of a case that is not there.

``/demo/antrag``
    The intake surface. A persona picker over ``config/demo/personas_v2.yaml``,
    an EDITABLE prefilled form (Formular tab) or an editable prose letter
    (E-Mail tab), and a panel that suggests what to break. The submission goes
    through the app's own ``run_ingest`` - the same sealing, the same
    validation, the same journal as ``POST /ingest`` - with the deployment's
    ingest token presented server-side. The raw endpoint keeps its 403 posture
    for direct callers; what changes is who is holding the token, not what the
    gate does.

``/demo/case/{case_id}/pipeline``
    The glass pipeline. Seven stages, one plain sentence each in the
    reader's language, and the REAL data underneath. **It re-derives
    nothing.** The routing answer is the
    ROUTED event through ``review_state``; anomaly reasons come from
    ``api.review.anomaly_reason_lines``, which calls ``engine.score
    .render_reason`` and no other wording; a sampled case renders as
    Qualitaetssicherung and never with anomaly styling (ADR-025). The only
    thing this module holds that the journal does not is the redacted working
    copy and the visitor's own echo, and both live in the demo-only TTL store
    with the reasoning in ``engine/demo/store.py``.

**Nothing here can send anything to an applicant.** ``/inbox`` is linked and
never touched (ADR-005, the part-07 line): the tour shows the receipt that was
produced automatically, and there is no control on any of these pages that
produces, edits or re-sends one.

**Phase 3 is the existing review UI.** The tour hands over with a link and a
``highlight`` query parameter, which marks one row and changes nothing else -
``engine/review`` gains no demo branch and the queue stays oldest-first.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from api.i18n import GERMAN, PageContext
from api.metrics import render_template
from api.review import (
    ReasonLine,
    anomaly_reason_lines,
    channel_label,
    clearing_label,
    sealed_kinds,
    tier_label,
    unit_name,
)
from engine.config_loader import ConfigBundle
from engine.demo.mode import DemoPosture
from engine.demo.personas import (
    CHANNEL_EMAIL,
    CHANNEL_FORM,
    CHANNELS,
    Persona,
    PersonaField,
    PersonaSet,
)
from engine.demo.store import DemoStore, DemoSubmission, TypedValue
from engine.ingest.envelope import case_id_for
from engine.journal.store import JournalStore
from engine.notify.outbox import Outbox, OutboxEntry
from engine.redact.placeholders import PLACEHOLDER_RE
from engine.review import CLEARING_QUEUE, review_state
from engine.review.state import ReviewState

#: The three phases, in order. Rendered on demo pages only - the caseworker UI
#: does not learn about the tour (ruling 5). Keys rather than labels since part
#: 16: the strip is translated, and a German label frozen into a view object
#: would be a German label on an English page.
PHASES = ("antrag", "maschine", "sachbearbeitung")

#: What the intake page says when this instance accepts no submission at all.
#: Not an error and not a bug: an unset ingest token is the SAFE state and it
#: means closed for everybody, the demo app included (ADR-027, ruling 4).
CLOSED_NOTE = "intake.ingest.closed"

#: And what it says when the deployment did configure one.
OPEN_NOTE = "intake.ingest.open"

#: What the tour says about phase 1 in each of the two postures.
TOUR_CLOSED_NOTE = "tour.ingest.closed"
TOUR_OPEN_NOTE = "tour.ingest.open"

#: The step (c) caveat, said out loud rather than left to be noticed. A seeded
#: case has no working copy in the demo store - that compartment holds what a
#: VISITOR typed, for half an hour, and nobody typed this one.
TOUR_SEEDED_NOTE = "tour.seeded"

#: What step (c) says when nothing was seeded at all (a developer who started
#: the app on an empty state directory). Honest, and not a dead link.
TOUR_UNSEEDED_NOTE = "tour.unseeded"

#: The refusal wordings the intake page renders, as keys.
REFUSED_REDACTION_KEY = "intake.refused.redaction"
REFUSED_ENVELOPE_KEY = "intake.refused.envelope"

#: The sentence stage (b) exists for.
SEAL_SENTENCE = "pipeline.b.seal_sentence"

#: Stage (c), when the reader had nothing to replay. Said out loud rather than
#: shown as an empty table: the honest reason is a design decision (ADR-028).
NO_EXTRACTION_NOTE = "pipeline.c.no_extraction"

#: Stage (e), when the shadow scorer flagged an item that stayed where it was.
LOG_ONLY_NOTE = "pipeline.e.log_only"

#: What the pipeline view says when the demo store no longer holds a submission.
EXPIRED_NOTE = "pipeline.expired"


def phase_index(phase: str) -> int:
    """Which of the three phases a view is on, 1-based; 0 for the tour.

    Read by the step indicator to decide which circles are behind the reader
    (checkmark) and which are ahead (number). One function rather than three
    view properties saying the same thing.
    """
    return PHASES.index(phase) + 1 if phase in PHASES else 0


# ------------------------------------------------------------------ the tour ---

#: The gold item the tour points at for step (c). Deliberately a SEEDED case
#: rather than a fresh submission: the seven stages have to be walkable before a
#: visitor has submitted anything, and on an instance with no ingest token they
#: are the only way to walk them at all.
#:
#: Why this one, out of a hundred and one. It is a Regelaltersrente form that
#: arrived without its Rentenbeginn, so every stage of the pipeline view has
#: something in it: sealed identity fields with their kinds, extracted values
#: with verified character offsets, ONE gap carrying the procedure's own
#: Nachforderung wording, a routing rule that fired, a tier the decision table
#: can justify line by line, and two delivered notifications. A complete case
#: would show an empty gap table; a Statusfeststellung would end at tier 3
#: without demonstrating that the tiers differ.
TOUR_ITEM_ID = "ar-0011-ohne-rentenbeginn"


@dataclass(frozen=True)
class TourView:
    """Everything the tour renders, and not one derived fact of its own.

    Three things here are read rather than written: whether this deployment
    accepts submissions (the posture), which gold set it was seeded from, and -
    when the seeded case is present - the unit and tier the journal recorded
    for it. Everything else on the page is prose in the translation table.
    """

    ingest_open: bool
    ingest_note_key: str
    gold_dir: str
    repo_url: str
    case_id: str
    case_present: bool
    unit_id: str
    unit_label: str
    queue_id: str
    tier_label: str
    #: No phase is current here: the tour is the map, not a position on it, so
    #: the three-phase indicator stays off this one page (``demo_base.html``).
    phase: str = ""
    phases: tuple[str, ...] = PHASES

    @property
    def phase_index(self) -> int:
        return phase_index(self.phase)

    @property
    def pipeline_href(self) -> str:
        return f"/demo/case/{self.case_id}/pipeline"

    @property
    def queue_href(self) -> str:
        return f"/review/queue/{self.queue_id}?unit={self.unit_id}"

    @property
    def case_href(self) -> str:
        return f"/review/case/{self.case_id}?unit={self.unit_id}"


def build_tour_view(
    journal: JournalStore,
    *,
    config: ConfigBundle,
    posture: DemoPosture,
    gold_dir: str,
    page: PageContext | None = None,
) -> TourView:
    """The tour for this deployment, in whichever of its two states it is in.

    ``journal.read`` on the seeded case is the only lookup: a page that linked
    a case id it had not checked would hand a visitor a 404 on the one screen
    that exists to make a first impression. Where the case IS present, its unit
    and tier come from ``review_state`` - the same projection the caseworker UI
    and the pipeline view fold - so the tour cannot state a routing answer that
    differs from the one the system gave.
    """
    context = page or GERMAN
    case_id = case_id_for(TOUR_ITEM_ID)
    events = journal.read(case_id)
    state = review_state(case_id, events) if events else None
    unit_id = state.unit_id if state is not None else None
    return TourView(
        ingest_open=posture.ingest_open,
        ingest_note_key=(TOUR_OPEN_NOTE if posture.ingest_open else TOUR_CLOSED_NOTE),
        gold_dir=gold_dir,
        repo_url=posture.repo_url,
        case_id=case_id,
        case_present=state is not None,
        unit_id=unit_id or "",
        unit_label=(unit_name(config, unit_id) if unit_id else clearing_label(context)),
        queue_id=unit_id or CLEARING_QUEUE,
        tier_label=(tier_label(state.tier, context) if state is not None else ""),
    )


def render_tour(view: TourView, page: PageContext | None = None) -> str:
    return render_template("demo_tour.html", view, page)


# --------------------------------------------------------------- the intake ---


@dataclass(frozen=True)
class FieldView:
    """One editable input on the intake form.

    ``control`` is what the browser renders - a text box, a native date picker
    or a select - and it changes NOTHING about what is submitted: a date input
    posts the ISO string a text box was typed into, and a select posts the same
    value typing produced. ``choices`` is the vocabulary the select offers, read
    from the procedure configuration or from the persona file and never
    invented here.

    ``required`` is part 20 and is a RULE rather than a list of field names:
    a field the persona arrived with a value for must still carry one when it
    is sent. See :func:`required_for`.
    """

    field_id: str
    label: str
    value: str
    help: str
    kind: str
    control: str = "text"
    choices: tuple[str, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class IntakeView:
    """Everything the intake template renders."""

    posture: DemoPosture
    personas: tuple[Persona, ...]
    persona: Persona
    channel: str
    rows: tuple[tuple[FieldView, ...], ...]
    body: str
    note: str
    hints: tuple[tuple[str, str], ...]
    ingest_open: bool
    ingest_note_key: str
    error_key: str = ""
    error_details: tuple[str, ...] = ()
    phase: str = "antrag"
    phases: tuple[str, ...] = PHASES
    channels: tuple[str, ...] = CHANNELS

    @property
    def is_email(self) -> bool:
        return self.channel == CHANNEL_EMAIL

    @property
    def phase_index(self) -> int:
        return phase_index(self.phase)

    @property
    def fields(self) -> tuple[FieldView, ...]:
        """Every field, flattened out of its row. For tests and for callers."""
        return tuple(field for row in self.rows for field in row)


#: Which of the two channel ids this page still OFFERS. One, since part 20.
#:
#: The user's decision of 2026-08-18 reverses the part-13 fork: the "Weg
#: waehlen" chooser is commented out of ``ui/templates/demo_intake.html`` and
#: the intake page is the form, full stop. Two things follow, and they are the
#: whole of the change:
#:
#: * ``?kanal=email`` and a POST carrying ``kanal=email`` both resolve to the
#:   form. A bookmarked link from before this part therefore shows a page
#:   rather than a 404 or an unlinked one, which is the same "never
#:   half-select something" rule the unit picker and the language switch follow.
#: * Nothing underneath is deleted. ``CHANNELS`` still names both ids because
#:   both are still legal values of a submission; ``build_letter_submission``
#:   still builds an e-mail envelope and still has its unit coverage in
#:   ``tests/test_demo_personas.py``; and ``IntakeView.channels`` still returns
#:   both, so uncommenting the template block restores the tab exactly.
#:
#: Restoring the choice is this tuple plus that comment block, and nothing else.
OFFERED_CHANNELS: tuple[str, ...] = (CHANNEL_FORM,)


def resolve_channel(raw: str | None) -> str:
    """The channel this page submits on; anything else is the form.

    Same discipline as the unit picker: an unknown value in a bookmarked URL is
    not an error and must never half-select something. There is no scan channel
    and, since part 20, no e-mail one either - a file upload on a public page
    would be an ingest path around the redaction boundary, and the e-mail tab
    is the user's own removal (see :data:`OFFERED_CHANNELS`).
    """
    return raw if raw in OFFERED_CHANNELS else CHANNEL_FORM


#: Which persona a visitor lands on with no ``?persona=`` in the URL, and which
#: one is offered first in the picker.
#:
#: A VIEW DECISION, not a configuration one. `config/demo/personas_v2.yaml` is
#: frozen and its order is the order the personas were written in; which of
#: them a first-time visitor should meet is a question about this page, and the
#: answer changes with what the demonstration is trying to show. Keeping it
#: here means the config stays a description of four applicants rather than
#: also being a running order.
#:
#: Statusfeststellung is the choice because it is the richest of the four on
#: first load: it is the persona whose form carries the three configured
#: selects and whose story the hints panel is written against, so a visitor who
#: touches nothing still sees the interesting screen.
LEAD_PERSONA = "musterfrau_statusfeststellung"


def ordered_personas(personas: PersonaSet) -> tuple[Persona, ...]:
    """Every persona, the lead one first, the rest in their configured order.

    A rotation and not a filter: all four stay present and reachable, and
    nothing about any of them changes. A persona set that does not contain the
    lead id is returned untouched rather than reordered around an absence.
    """
    lead = personas.get(LEAD_PERSONA)
    if lead is None:
        return personas.personas
    return (lead, *(p for p in personas.personas if p.persona_id != LEAD_PERSONA))


def default_persona(personas: PersonaSet) -> Persona:
    """The persona a request that named none is answered with.

    Falls back to the set's own first entry when the lead id is unknown, which
    is the same "never half-select something" rule the channel and the unit
    picker follow: a renamed persona shows the picker's first card again, never
    an error and never an empty page.
    """
    return personas.get(LEAD_PERSONA) or personas.first


def vocabulary(config: ConfigBundle, path: str) -> tuple[str, ...]:
    """The allowed values for a payload path, READ from the procedure configs.

    The intake page's selects are fed from here rather than from a list in the
    template, and the difference is the whole point: ``one_of`` in
    ``config/procedures/*.yaml`` is what the completeness checker validates
    against, so an option this function offers is by construction an option the
    evidence plane accepts. A hand-written list would be a second vocabulary,
    and the first thing a second vocabulary does is drift.

    The lookup goes through the procedure's own ``field_map``, which is the one
    place that says which payload path a requirement id belongs to. Empty when
    no procedure constrains the path - a free-text requirement, or a path no
    procedure knows - and the caller then falls back to a text input.
    """
    for procedure in config.procedures.values():
        for entry in procedure.field_map:
            if entry.path != path:
                continue
            for requirement in procedure.requirements.requirements:
                if requirement.requirement_id != entry.field:
                    continue
                allowed = (requirement.validation or {}).get("one_of")
                if isinstance(allowed, list) and allowed:
                    return tuple(str(value) for value in allowed)
    return ()


def required_for(entry: PersonaField) -> bool:
    """Whether this field renders with the HTML ``required`` attribute.

    **The rule: what the persona ARRIVED with has to be sent.** A field the
    persona file gives a value for is required; a field it deliberately leaves
    empty is not. That is one expression over the persona's own declaration,
    not a list of field names and not per-persona machinery - rename a field,
    add a persona, reorder the file, and the rule still says the same thing.

    Read off ``entry.value`` - the DECLARED value - and never off what is
    currently in the box. The difference shows on a re-render: a page that
    recomputed this from the submitted values would drop the attribute from
    exactly the field somebody had just emptied, which is the one moment it
    exists for.

    What the rule buys is the user's own sentence for part 20: press "Antrag
    absenden" with an empty field and the browser marks it and refuses to send.
    The blocking is the browser's - no JavaScript is added anywhere here - and
    what this function does is decide which fields get to ask for it.

    The one field in the shipped demo that this leaves optional is Bernd
    Beispielmann's Rentenbeginn, which is empty BY DESIGN because his whole
    arc is the incomplete submission: tier 2, and a Nachforderung in the
    procedure's own words. His card says so, because a form that behaves
    differently on one screen has to explain itself on that screen. He is
    deprecation-pending (see ``config/demo/``), and when he goes the rule does
    not change - it simply has nothing left to except.
    """
    return bool(entry.value.strip())


def _field_view(
    entry: PersonaField,
    *,
    value: str,
    config: ConfigBundle | None,
    page: PageContext,
) -> FieldView:
    """One persona field as the form renders it, in one language."""
    choices: tuple[str, ...] = ()
    control = entry.control
    if control == "select":
        choices = entry.options or (
            vocabulary(config, entry.path) if config is not None else ()
        )
        if not choices:
            # A select with nothing in it would be a control a visitor cannot
            # use. Degrade to the text input the field had before part 16.
            control = "text"
        elif value and value not in choices:
            # A tampered or superseded value is KEPT and offered, because the
            # page must not silently change what a visitor is submitting.
            choices = (value, *choices)
    help_text = entry.help_for(page.lang)
    if control == "date":
        help_text = " ".join(filter(None, (help_text, page.t("intake.date.hint"))))
    elif control == "select":
        help_text = " ".join(filter(None, (help_text, page.t("intake.select.hint"))))
    return FieldView(
        field_id=entry.field_id,
        label=entry.label_for(page.lang),
        value=value,
        help=help_text,
        kind=entry.kind,
        control=control,
        choices=choices,
        required=required_for(entry),
    )


def field_rows(
    persona: Persona,
    values: Mapping[str, str],
    *,
    config: ConfigBundle | None,
    page: PageContext,
) -> tuple[tuple[FieldView, ...], ...]:
    """The persona's fields, grouped the way the persona file groups them.

    Consecutive fields sharing a ``group`` become one row - the two halves of
    the name, the four parts of the address - so the form reads as the handful
    of ANSWERS it is rather than as eleven separate questions. Purely visual:
    the grouping changes no field id, no path and nothing that is submitted.
    """
    rows: list[list[FieldView]] = []
    current = ""
    for entry in persona.fields:
        view = _field_view(
            entry,
            value=values.get(entry.field_id, entry.value),
            config=config,
            page=page,
        )
        if entry.group and entry.group == current and rows:
            rows[-1].append(view)
        else:
            rows.append([view])
        current = entry.group
    return tuple(tuple(row) for row in rows)


def build_intake_view(
    posture: DemoPosture,
    personas: PersonaSet,
    *,
    persona_id: str | None = None,
    channel: str | None = None,
    values: Mapping[str, str] | None = None,
    body: str | None = None,
    error_key: str = "",
    error_details: Sequence[str] = (),
    config: ConfigBundle | None = None,
    page: PageContext | None = None,
) -> IntakeView:
    """The intake page for one persona, one channel and whatever was typed."""
    context = page or GERMAN
    persona = personas.get(persona_id) or default_persona(personas)
    return IntakeView(
        posture=posture,
        personas=ordered_personas(personas),
        persona=persona,
        channel=resolve_channel(channel),
        rows=field_rows(persona, dict(values or {}), config=config, page=context),
        body=persona.letter if body is None else body,
        note=personas.note_for(context.lang),
        hints=personas.hints_for(context.lang),
        ingest_open=posture.ingest_open,
        ingest_note_key=OPEN_NOTE if posture.ingest_open else CLOSED_NOTE,
        error_key=error_key,
        error_details=tuple(error_details),
    )


def echo_values(persona: Persona, values: Mapping[str, str]) -> tuple[TypedValue, ...]:
    """The identity values the visitor typed, grouped the way sealing groups them.

    The four address inputs become ONE entry, because the redaction policy seals
    ``antragsteller.anschrift`` as a subtree into a single placeholder
    (``config/redaction/identity_fields_v1.yaml``). Showing four typed values
    next to one placeholder would teach a visitor a mapping the system does not
    have.

    Fields without a declared kind are procedural content - a Rentenart, a date
    - and are deliberately absent: this compartment exists for one moment of
    the tour and holds nothing beyond what that moment needs.
    """
    ordered: list[str] = []
    grouped: dict[str, list[tuple[int, str]]] = {}
    labels: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for entry in persona.fields:
        if not entry.identity:
            continue
        typed = values.get(entry.field_id, entry.value).strip()
        if not typed:
            continue
        key = entry.pair_key
        if key not in grouped:
            ordered.append(key)
            grouped[key] = []
            labels[key] = entry.group.capitalize() if entry.group else entry.label
            kinds[key] = entry.kind
        grouped[key].append((entry.join_order, typed))
    # Sorted by ``join_order`` for the same reason the submission builder sorts
    # by it: the name is ASKED for surname-first and READ back given-name-first,
    # and the echo has to show the visitor the string the machine received, not
    # the order the boxes were in.
    return tuple(
        TypedValue(
            label=labels[key],
            value=" ".join(
                typed for _order, typed in sorted(grouped[key], key=lambda p: p[0])
            ),
            kind=kinds[key],
        )
        for key in ordered
    )


# -------------------------------------------------------- the pipeline view ---


@dataclass(frozen=True)
class Segment:
    """A run of working-copy text, marked when it IS a placeholder."""

    text: str
    kind: str

    @property
    def placeholder(self) -> bool:
        return bool(self.kind)


@dataclass(frozen=True)
class PartView:
    """One part of the working copy, with its placeholders marked."""

    part_id: str
    shape: str
    segments: tuple[Segment, ...]


@dataclass(frozen=True)
class Pairing:
    """ "You typed this; the machine got that." One row of the side by side."""

    label: str
    typed: str
    placeholder: str
    kind: str


@dataclass(frozen=True)
class PipelineView:
    """Everything the seven stages need, and not one derived fact of its own."""

    case_id: str
    state: ReviewState
    now: datetime
    channel_label: str
    persona_label: str
    parts: tuple[PartView, ...]
    pairings: tuple[Pairing, ...]
    echo_body: str
    sealed_kinds: tuple[tuple[str, str, int], ...]
    anomaly_reasons: tuple[ReasonLine, ...]
    notifications: tuple[OutboxEntry, ...]
    queue_id: str
    queue_label: str
    unit_label: str
    held: bool
    sampled: bool
    phase: str = "maschine"
    phases: tuple[str, ...] = PHASES

    @property
    def case(self) -> Any:
        """The folded case state, as the review templates name it."""
        return self.state.case

    @property
    def phase_index(self) -> int:
        return phase_index(self.phase)

    @property
    def downgraded(self) -> bool:
        """Whether the one-way valve actually moved this item."""
        before = self.state.case.pre_downgrade_tier
        after = self.state.case.tier
        return before is not None and after is not None and after > before

    @property
    def flagged_but_log_only(self) -> bool:
        """Flagged, and the tier did not move - the ADR-024 demonstration."""
        return self.state.flagged and not self.downgraded

    @property
    def queue_href(self) -> str:
        """Phase 3's hand-off. ``highlight`` is display only (see api/review)."""
        unit = self.state.unit_id or ""
        query = f"?highlight={self.case_id}" + (f"&unit={unit}" if unit else "")
        return f"/review/queue/{self.queue_id}{query}"


def build_pipeline_view(
    journal: JournalStore,
    *,
    config: ConfigBundle,
    case_id: str,
    outbox: Outbox,
    store: DemoStore | None,
    now: datetime | None = None,
    page: PageContext | None = None,
) -> PipelineView | None:
    """The pipeline view, or None when the journal knows no such case.

    Every fact here is read from the journal through the SAME projections the
    caseworker UI reads (``review_state``, and through it ``derive_case_state``)
    or from a store that already exists. Nothing is recomputed: a citizen-facing
    page that re-derived a routing answer would be a second answer to "who is
    responsible", and there is exactly one.
    """
    context = page or GERMAN
    events = journal.read(case_id)
    if not events:
        return None
    moment = now or datetime.now(UTC)
    state = review_state(case_id, events)
    held = store.get(case_id, now=moment) if store is not None else None
    unit_id = state.unit_id
    return PipelineView(
        case_id=case_id,
        state=state,
        now=moment,
        channel_label=channel_label(state.case.channel, context),
        persona_label=held.persona_label if held else "",
        parts=tuple(_part_views(held)),
        pairings=_pairings(held),
        echo_body=held.echo_body if held else "",
        sealed_kinds=sealed_kinds(state),
        anomaly_reasons=anomaly_reason_lines(state),
        notifications=tuple(outbox.entries(case_id)),
        queue_id=unit_id or CLEARING_QUEUE,
        queue_label=(
            unit_name(config, unit_id) if unit_id else clearing_label(context)
        ),
        unit_label=unit_name(config, unit_id),
        held=held is not None,
        # ADR-025 through the SAME projection the caseworker UI reads: a
        # sampled case renders as Qualitaetssicherung and never with anomaly
        # styling, and two definitions of "sampled" would be one too many.
        sampled=state.sampled,
    )


def render_intake(view: IntakeView, page: PageContext | None = None) -> str:
    return render_template("demo_intake.html", view, page)


def render_pipeline(view: PipelineView, page: PageContext | None = None) -> str:
    return render_template("demo_pipeline.html", view, page)


def segments(text: str) -> tuple[Segment, ...]:
    """Split working-copy text into plain runs and placeholder runs.

    Built in Python rather than with a template filter so the page never has to
    mark anything safe: Jinja escapes every segment, and the ``<mark>`` element
    is written by the template around text it was handed rather than injected
    into it.
    """
    parts: list[Segment] = []
    position = 0
    for match in PLACEHOLDER_RE.finditer(text):
        if match.start() > position:
            parts.append(Segment(text=text[position : match.start()], kind=""))
        parts.append(Segment(text=match.group(0), kind=match.group("kind")))
        position = match.end()
    if position < len(text):
        parts.append(Segment(text=text[position:], kind=""))
    return tuple(parts)


def _part_views(held: DemoSubmission | None) -> Sequence[PartView]:
    if held is None:
        return ()
    return [
        PartView(part_id=part.part_id, shape=part.shape, segments=segments(part.text))
        for part in held.working_copy
    ]


def _pairings(held: DemoSubmission | None) -> tuple[Pairing, ...]:
    """Pair each typed identity value with the placeholder that replaced it.

    Pairing is by KIND, in order of appearance, and it is honest about the
    limit: when the boundary produced no placeholder of a kind - because the
    value was not identity-classed on this path, or because the visitor emptied
    the field - the row shows what was typed and says the pairing is open,
    rather than pointing at somebody else's token.
    """
    if held is None or not held.echo:
        return ()
    available: dict[str, list[str]] = {}
    for part in held.working_copy:
        for match in PLACEHOLDER_RE.finditer(part.text):
            available.setdefault(match.group("kind"), []).append(match.group(0))
    used: dict[str, int] = {}
    rows: list[Pairing] = []
    for typed in held.echo:
        index = used.get(typed.kind, 0)
        tokens = available.get(typed.kind, [])
        placeholder = tokens[index] if index < len(tokens) else ""
        used[typed.kind] = index + 1
        rows.append(
            Pairing(
                label=typed.label,
                typed=typed.value,
                placeholder=placeholder,
                kind=typed.kind,
            )
        )
    return tuple(rows)
