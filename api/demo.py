"""The guided showcase: a citizen submits, watches the machine, becomes the clerk.

Three pages, all demo-mode-only, all server-rendered like everything else here.

``/demo/rundgang``
    The tour. The whole system told from beginning to end in six steps for a
    visitor who has never seen it, each step linking to the page where that
    step actually happens. German leads and every step carries a short English
    aside. **It derives nothing.** Every sentence is either static prose or a
    fact read off the same projections the other pages read: whether this
    deployment accepts submissions at all, and - for the seeded case the tour
    points at - the unit and tier the journal already recorded. When the state
    was not seeded from the frozen corpus, the tour says so and links the
    caseworker surface instead of a case that is not there.

``/demo/antrag``
    The intake surface. A persona picker over ``config/demo/personas_v1.yaml``,
    an EDITABLE prefilled form (Formular tab) or an editable prose letter
    (E-Mail tab), and a panel that suggests what to break. The submission goes
    through the app's own ``run_ingest`` - the same sealing, the same
    validation, the same journal as ``POST /ingest`` - with the deployment's
    ingest token presented server-side. The raw endpoint keeps its 403 posture
    for direct callers; what changes is who is holding the token, not what the
    gate does.

``/demo/case/{case_id}/pipeline``
    The glass pipeline. Seven stages, one plain-German sentence each, and the
    REAL data underneath. **It re-derives nothing.** The routing answer is the
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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from api.metrics import environment
from api.review import (
    KIND_LABELS,
    PICKER_NOTE,
    TIER_LABELS,
    ReasonLine,
    anomaly_reason_lines,
    sealed_kinds,
    unit_name,
)
from engine.config_loader import ConfigBundle
from engine.demo.mode import DemoPosture
from engine.demo.personas import (
    CHANNEL_EMAIL,
    CHANNEL_FORM,
    CHANNEL_LABELS,
    CHANNEL_NOTES,
    CHANNELS,
    Persona,
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
#: does not learn about the tour (ruling 5).
PHASES = (
    ("antrag", "Phase 1: Antrag"),
    ("maschine", "Phase 2: Maschine"),
    ("sachbearbeitung", "Phase 3: Sachbearbeitung"),
)

#: What the intake page says when this instance accepts no submission at all.
#: Not an error and not a bug: an unset ingest token is the SAFE state and it
#: means closed for everybody, the demo app included (ADR-027, ruling 4).
CLOSED_NOTE = (
    "Diese Instanz nimmt keine Antraege entgegen: es ist kein Ingest-Token "
    "konfiguriert, und ohne Token ist der Eingang fuer jeden Aufrufer "
    "gesperrt - auch fuer diese Seite. Das ist der sichere Zustand und kein "
    "Fehler. Die Phasen 2 und 3 koennen Sie trotzdem begehen: der Datenbestand "
    "aus dem eingefrorenen Goldsatz steht in der Bearbeitung und im Postfach."
)

#: And what it says when the deployment did configure one.
OPEN_NOTE = (
    "Ihr Antrag geht durch dieselbe Verarbeitung wie jeder andere Eingang: "
    "versiegeln, Arbeitskopie, Auslesen, Belegen, Entscheiden, Benachrichtigen. "
    "Diese Seite legt das Token dieser Bereitstellung serverseitig bei; der "
    "rohe Endpunkt POST /ingest bleibt fuer direkte Aufrufer mit 403 gesperrt."
)

#: The sentence stage (b) exists for.
SEAL_SENTENCE = (
    "Die Maschine hat Ihren Namen nie gesehen. Was Sie oben eingegeben haben, "
    "wurde am Eingang versiegelt - bevor die Arbeitskopie entstand, auf der "
    "alles Weitere rechnet."
)

#: Stage (c), when the reader had nothing to replay. Said out loud rather than
#: shown as an empty table: the honest reason is a design decision (ADR-028).
NO_EXTRACTION_NOTE = (
    "Aus diesem Anschreiben wurde nichts ausgelesen, und das ist kein Fehler "
    "dieser Seite. Der Leser fuer Freitext ist in dieser Bereitstellung ein "
    "REPLAY aufgezeichneter Modellausgaben (ADR-028): zu einem Brief, den Sie "
    "gerade selbst geschrieben haben, gibt es keine Aufzeichnung. Ein Modell "
    "raten zu lassen und das Ergebnis nicht belegen zu koennen, waere die "
    "schlechtere Antwort - der Vorgang geht deshalb unvollstaendig zu einem "
    "Menschen."
)

#: Stage (e), when the shadow scorer flagged an item that stayed where it was.
LOG_ONLY_NOTE = (
    "Der Schattenscorer laeuft im Modus log_only: er hat den Vorgang markiert "
    "und seinen Grund genannt, aber KEIN Tier bewegt. Das Einwegventil "
    "(ADR-004) laesst Unsicherheit ohnehin nur in eine Richtung wirken - zu "
    "einem Menschen hin, nie von ihm weg."
)

#: Stage (e), when a downgrade actually happened.
VALVE_NOTE = (
    "Das Einwegventil hat gegriffen: die Auffaelligkeit hat den Vorgang von "
    "Tier {before} auf Tier {after} geschoben. Umgekehrt geht es nicht - keine "
    "Regel dieses Systems kann ein Tier senken."
)

#: What the pipeline view says when the demo store no longer holds a submission.
EXPIRED_NOTE = (
    "Die Arbeitskopie zu diesem Vorgang wird nicht mehr vorgehalten. Der "
    "Zwischenspeicher dieser Demo haelt sie nur fuer kurze Zeit und ausschliesslich "
    "im Arbeitsspeicher; alles Uebrige auf dieser Seite kommt aus dem Journal "
    "und bleibt lesbar."
)


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

#: What the tour says about phase 1 when this deployment cannot accept anything.
#: Not an apology: an unset ingest token is the safe state (ADR-027), and the
#: tour is walkable end to end without it because the state is seeded.
TOUR_CLOSED_NOTE = (
    "Diese Bereitstellung nimmt zurzeit keine Antraege entgegen: ohne "
    "konfiguriertes Ingest-Token ist der Eingang fuer jeden Aufrufer gesperrt, "
    "auch fuer die Antragsseite selbst. Der Rundgang funktioniert trotzdem "
    "vollstaendig - die Schritte 3 bis 6 laufen ueber den eingefrorenen "
    "Goldsatz, der beim Start eingespielt wurde."
)

#: And when the deployment did configure one.
TOUR_OPEN_NOTE = (
    "Diese Bereitstellung nimmt Antraege entgegen: Sie koennen den Rundgang "
    "mit Ihrem EIGENEN Vorgang laufen, von der Einreichung bis zur "
    "Eingangsbestaetigung im Postfach."
)

#: The step (c) caveat, said out loud rather than left to be noticed. A seeded
#: case has no working copy in the demo store - that compartment holds what a
#: VISITOR typed, for half an hour, and nobody typed this one.
TOUR_SEEDED_NOTE = (
    "Der Vorgang, auf den dieser Schritt zeigt, stammt aus dem eingefrorenen "
    "Goldsatz und nicht aus einer Eingabe von Ihnen. Deshalb fehlt dort die "
    "Gegenueberstellung von eingegebenem Wert und Arbeitskopie: dieser "
    "Zwischenspeicher haelt ausschliesslich, was eine Besucherin oder ein "
    "Besucher selbst getippt hat, und zwar nur fuer kurze Zeit im "
    "Arbeitsspeicher. Alles Uebrige - Versiegelung, Fundstellen, Luecken, "
    "Zuordnung, Entscheidung, Nachrichten - kommt aus dem Journal und steht "
    "vollstaendig da."
)

#: What step (c) says when nothing was seeded at all (a developer who started
#: the app on an empty state directory). Honest, and not a dead link.
TOUR_UNSEEDED_NOTE = (
    "Auf dieser Instanz ist kein Goldsatz eingespielt, deshalb gibt es hier "
    "keinen vorbereiteten Vorgang zum Mitlaufen. Stellen Sie einen Antrag "
    "(Schritt 2) oder spielen Sie den Bestand mit dem Befehl "
    "python -m engine.demo.seed ein; die Bearbeitungsoberflaeche ist in beiden "
    "Faellen erreichbar."
)


@dataclass(frozen=True)
class TourView:
    """Everything the tour renders, and not one derived fact of its own.

    Three things here are read rather than written: whether this deployment
    accepts submissions (the posture), which gold set it was seeded from, and -
    when the seeded case is present - the unit and tier the journal recorded
    for it. Everything else on the page is prose.
    """

    ingest_open: bool
    ingest_note: str
    gold_dir: str
    repo_url: str
    case_id: str
    case_present: bool
    unit_id: str
    unit_label: str
    queue_id: str
    tier_label: str
    seeded_note: str = TOUR_SEEDED_NOTE
    unseeded_note: str = TOUR_UNSEEDED_NOTE
    picker_note: str = PICKER_NOTE
    #: No phase is current here: the tour is the map, not a position on it, so
    #: the three-phase indicator stays off this one page (``demo_base.html``).
    phase: str = ""
    phases: tuple[tuple[str, str], ...] = PHASES

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
) -> TourView:
    """The tour for this deployment, in whichever of its two states it is in.

    ``journal.read`` on the seeded case is the only lookup: a page that linked
    a case id it had not checked would hand a visitor a 404 on the one screen
    that exists to make a first impression. Where the case IS present, its unit
    and tier come from ``review_state`` - the same projection the caseworker UI
    and the pipeline view fold - so the tour cannot state a routing answer that
    differs from the one the system gave.
    """
    case_id = case_id_for(TOUR_ITEM_ID)
    events = journal.read(case_id)
    state = review_state(case_id, events) if events else None
    unit_id = state.unit_id if state is not None else None
    return TourView(
        ingest_open=posture.ingest_open,
        ingest_note=TOUR_OPEN_NOTE if posture.ingest_open else TOUR_CLOSED_NOTE,
        gold_dir=gold_dir,
        repo_url=posture.repo_url,
        case_id=case_id,
        case_present=state is not None,
        unit_id=unit_id or "",
        unit_label=(
            unit_name(config, unit_id)
            if unit_id
            else "Zentrale Klaerung (par. 16 Abs. 2 SGB I)"
        ),
        queue_id=unit_id or CLEARING_QUEUE,
        tier_label=(
            TIER_LABELS.get(state.tier or 0, f"Tier {state.tier}")
            if state is not None
            else ""
        ),
    )


def render_tour(view: TourView) -> str:
    return environment().get_template("demo_tour.html").render(view=view)


# --------------------------------------------------------------- the intake ---


@dataclass(frozen=True)
class FieldView:
    """One editable input on the intake form."""

    field_id: str
    label: str
    value: str
    help: str
    kind: str

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, "")


@dataclass(frozen=True)
class IntakeView:
    """Everything the intake template renders."""

    posture: DemoPosture
    personas: tuple[Persona, ...]
    persona: Persona
    channel: str
    fields: tuple[FieldView, ...]
    body: str
    note: str
    hints: tuple[tuple[str, str], ...]
    ingest_open: bool
    ingest_note: str
    error: str = ""
    error_details: tuple[str, ...] = ()
    phase: str = "antrag"
    phases: tuple[tuple[str, str], ...] = PHASES
    channels: tuple[str, ...] = CHANNELS
    channel_labels: dict[str, str] = field(default_factory=lambda: dict(CHANNEL_LABELS))
    channel_notes: dict[str, str] = field(default_factory=lambda: dict(CHANNEL_NOTES))

    @property
    def is_email(self) -> bool:
        return self.channel == CHANNEL_EMAIL


def resolve_channel(raw: str | None) -> str:
    """The chosen channel; anything unknown is the form.

    Same discipline as the unit picker: an unknown value in a bookmarked URL is
    not an error and must never half-select something. There are exactly two
    channels here and no scan, because a file upload on a public page would be
    an ingest path around the redaction boundary.
    """
    return raw if raw in CHANNELS else CHANNEL_FORM


def build_intake_view(
    posture: DemoPosture,
    personas: PersonaSet,
    *,
    persona_id: str | None = None,
    channel: str | None = None,
    values: Mapping[str, str] | None = None,
    body: str | None = None,
    error: str = "",
    error_details: Sequence[str] = (),
) -> IntakeView:
    """The intake page for one persona, one channel and whatever was typed."""
    persona = personas.get(persona_id) or personas.first
    submitted = dict(values or {})
    return IntakeView(
        posture=posture,
        personas=personas.personas,
        persona=persona,
        channel=resolve_channel(channel),
        fields=tuple(
            FieldView(
                field_id=field.field_id,
                label=field.label,
                value=submitted.get(field.field_id, field.value),
                help=field.help,
                kind=field.kind,
            )
            for field in persona.fields
        ),
        body=persona.letter if body is None else body,
        note=personas.note,
        hints=personas.hints,
        ingest_open=posture.ingest_open,
        ingest_note=OPEN_NOTE if posture.ingest_open else CLOSED_NOTE,
        error=error,
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
    grouped: dict[str, list[str]] = {}
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
        grouped[key].append(typed)
    return tuple(
        TypedValue(label=labels[key], value=" ".join(grouped[key]), kind=kinds[key])
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

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


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

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


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
    seal_sentence: str = SEAL_SENTENCE
    no_extraction_note: str = NO_EXTRACTION_NOTE
    log_only_note: str = LOG_ONLY_NOTE
    expired_note: str = EXPIRED_NOTE
    phase: str = "maschine"
    phases: tuple[tuple[str, str], ...] = PHASES
    tier_labels: dict[int, str] = field(default_factory=lambda: dict(TIER_LABELS))

    @property
    def case(self) -> Any:
        """The folded case state, as the review templates name it."""
        return self.state.case

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(self.state.tier or 0, f"Tier {self.state.tier}")

    @property
    def downgraded(self) -> bool:
        """Whether the one-way valve actually moved this item."""
        before = self.state.case.pre_downgrade_tier
        after = self.state.case.tier
        return before is not None and after is not None and after > before

    @property
    def valve_note(self) -> str:
        return VALVE_NOTE.format(
            before=self.state.case.pre_downgrade_tier, after=self.state.case.tier
        )

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
) -> PipelineView | None:
    """The pipeline view, or None when the journal knows no such case.

    Every fact here is read from the journal through the SAME projections the
    caseworker UI reads (``review_state``, and through it ``derive_case_state``)
    or from a store that already exists. Nothing is recomputed: a citizen-facing
    page that re-derived a routing answer would be a second answer to "who is
    responsible", and there is exactly one.
    """
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
        channel_label=CHANNEL_LABELS.get(
            state.case.channel or "", state.case.channel or "unbekannt"
        ),
        persona_label=held.persona_label if held else "",
        parts=tuple(_part_views(held)),
        pairings=_pairings(held),
        echo_body=held.echo_body if held else "",
        sealed_kinds=sealed_kinds(state),
        anomaly_reasons=anomaly_reason_lines(state),
        notifications=tuple(outbox.entries(case_id)),
        queue_id=unit_id or CLEARING_QUEUE,
        queue_label=(
            unit_name(config, unit_id)
            if unit_id
            else "Zentrale Klaerung (par. 16 Abs. 2 SGB I)"
        ),
        unit_label=unit_name(config, unit_id),
        held=held is not None,
        # ADR-025 through the SAME projection the caseworker UI reads: a
        # sampled case renders as Qualitaetssicherung and never with anomaly
        # styling, and two definitions of "sampled" would be one too many.
        sampled=state.sampled,
    )


def render_intake(view: IntakeView) -> str:
    return environment().get_template("demo_intake.html").render(view=view)


def render_pipeline(view: PipelineView) -> str:
    return environment().get_template("demo_pipeline.html").render(view=view)


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
