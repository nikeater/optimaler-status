"""Letter assembly: config wording plus case facts, then re-hydration.

Two kinds of letter and one pipeline for both:

    template (config) + context (case)  ->  Jinja2  ->  text WITH placeholders
      ->  engine.draft.rehydrate        ->  text with every token resolved

The order is the whole design. The template is rendered against a context that
still carries the reserved ``[[PII|KIND|TOKEN]]`` syntax where identity belongs,
and the re-hydrator resolves it afterwards against the fetched vault record.
That is why "every placeholder in the output resolves" is a property of the
rendering rather than a promise about the context builder: a template that
invents an identity slot produces an unknown token and the draft is blocked.

**This is the other side of part 07's seam.** ``engine.notify.render.render_text``
refuses ANY output holding a placeholder; this module produces them on purpose
and resolves them in the next step. Two modules, two artifact classes, no flag
in between - a notification is a Realakt a machine sends, a draft is a letter a
human confirms, and the code paths never meet (ADR-023).

**Nothing is composed here.** A Nachforderung ASSEMBLES the gap sentences the
procedure configs already author (``engine/evidence/nachforderung.py`` renders
them); this module never rewrites one, and there is no model anywhere on the
path (C-13). What it adds is the frame: the par. 60 SGB I anchor, the relative
response window, the reply channel, the Amtsermittlung softening (C-7) and -
only when a caseworker asks for it - the par. 66 Abs. 3 SGB I block.
"""

from __future__ import annotations

import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import jinja2

from engine.config_loader import (
    DRAFT_CONTEXT_KEYS,
    DRAFT_FILTERS,
    ConfigBundle,
    DraftingConfig,
    DraftTemplate,
    ProcedureConfig,
)
from engine.draft.rehydrate import RehydrationError, placeholders_by_path, rehydrate
from engine.redact.vault import VaultRecord
from schemas.config import Requirement

#: The two kinds, spelled once. The strings match ``config_loader.DRAFT_KINDS``
#: and the ``kind`` of a template in ``config/drafting/``.
KIND_NACHFORDERUNG = "nachforderung"
KIND_PREPARED_DECISION = "prepared_decision"

#: How a journal timestamp is written in a letter. Date only: a Nachforderung
#: says which day the item arrived, and the minute it arrived is operational
#: detail that belongs in the case view, not in a letter to a person.
DATE_FORMAT = "%d.%m.%Y"

#: Where a letter line wraps. Typography of the renderer rather than agency
#: policy, which is why it is here and not in the config: a config file full of
#: hand-wrapped prose would make every wording change a re-flow, and a folded
#: YAML scalar arrives as one long line by definition.
LINE_WIDTH = 76


class DraftingError(RuntimeError):
    """Raised when a draft could not be assembled. No draft is produced."""


class GapLike(Protocol):
    """What a letter needs to know about a gap: its id and its sentence.

    A protocol rather than a concrete type because the sentence reaches this
    module from two places - ``engine.evidence.nachforderung.GapRendering`` on
    the pipeline path and the EVIDENCE_ASSEMBLED payload on a replay - and the
    assembly must not be able to tell them apart. If it could, the two paths
    could word a letter differently.
    """

    @property
    def requirement_id(self) -> str: ...

    @property
    def sentence(self) -> str: ...


@dataclass(frozen=True)
class GapSentence:
    """One gap as the letter states it, plus whether C-7 softened it."""

    requirement_id: str
    sentence: str
    amtsermittlung: bool = False


@dataclass(frozen=True)
class DraftRequest:
    """Everything one letter needs, assembled from the case.

    ``facts`` are extracted VALUES, which for a sealed field is a placeholder -
    that is the point: the letter head and the fact list both re-hydrate, from
    the same record, in the same pass.
    """

    case_id: str
    envelope_id: str
    kind: str
    tier: int
    vault_ref: str
    procedure_id: str | None = None
    channel_id: str | None = None
    unit_id: str | None = None
    received_at: datetime | None = None
    gaps: tuple[GapSentence, ...] = ()
    facts: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderedDraft:
    """One assembled letter, re-hydrated, ready for the draft store."""

    template_id: str
    kind: str
    subject: str
    body: str
    requirement_ids: tuple[str, ...]
    amtsermittlung_ids: tuple[str, ...]
    resolved_tokens: int
    distinct_tokens: int
    token_kinds: dict[str, int]
    response_window_days: int | None
    rechtsfolgenhinweis: bool
    dispatch_shape: str | None


def environment() -> jinja2.Environment:
    """The Jinja environment for letter text.

    Autoescape OFF (these are plain-text letters) and ``StrictUndefined`` ON,
    so a typo in a template is a loud failure rather than a blank line in a
    letter a caseworker then confirms. Deliberately a second environment rather
    than the notification one: the two renderers must not become one function
    with a flag.
    """
    jinja = jinja2.Environment(
        autoescape=False,
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    # One filter, and it does nothing but re-flow. Config wording arrives as a
    # folded YAML scalar - one long line - and a letter that mixed 76-column
    # paragraphs with 300-column ones would look machine-made in the way that
    # makes a reader stop reading. The loader validates templates against stubs
    # of exactly these names, so a template can never use a filter that only
    # exists here or only exists there.
    jinja.filters.update(DRAFT_FILTER_IMPLS)
    return jinja


def wrap(text: str, indent: str = "") -> str:
    """Re-flow one paragraph to :data:`LINE_WIDTH`, with a hanging indent.

    Deterministic and content-preserving: ``textwrap`` only moves whitespace,
    so a re-hydrated value cannot be altered by it. Used on the config-supplied
    sentences and on the gap list, never on an address.
    """
    if not text:
        return ""
    return textwrap.fill(
        " ".join(text.split()),
        width=LINE_WIDTH,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


#: The filter table, keyed the way the loader declares it. Asserted rather than
#: assumed: a filter the config permits and nobody implements would fail at
#: render time, which is the moment this whole part is designed to protect.
DRAFT_FILTER_IMPLS: dict[str, Any] = {"wrap": wrap}
assert set(DRAFT_FILTER_IMPLS) == set(DRAFT_FILTERS), (
    "engine.draft.letters must implement exactly the filters config_loader "
    "permits in a draft template"
)


def build_letter(
    request: DraftRequest,
    *,
    config: ConfigBundle,
    record: VaultRecord,
    rechtsfolgenhinweis: bool = False,
) -> RenderedDraft:
    """Assemble and re-hydrate one letter.

    Args:
        request: the case facts, gap sentences included.
        config: the loaded bundle; ``config.drafting`` supplies every word.
        record: the fetched vault record. Fetched by the caller
            (``engine.draft.projection``) exactly once per draft.
        rechtsfolgenhinweis: opt-in par. 66 Abs. 3 SGB I block. **Defaults to
            off**, and the config carries no switch that could change that: the
            choice belongs to the caseworker who signs the letter, and part
            10's review UI is where it is made.

    Raises:
        DraftingError: no drafting config, or no template for this kind.
        RehydrationError: a token did not resolve; nothing is returned.
    """
    drafting = config.drafting
    if drafting is None:
        raise DraftingError(
            "no config/drafting/ in this config directory: this agency prepares "
            "no drafts"
        )
    template = drafting.template_for(request.kind)
    if template is None:  # pragma: no cover - the loader requires both kinds
        raise DraftingError(f"no draft template for kind {request.kind!r}")
    procedure = config.procedure(request.procedure_id)
    scope = _rechtsfolgen_scope(request, rechtsfolgenhinweis)
    context = _context(
        request,
        config=config,
        drafting=drafting,
        procedure=procedure,
        scope=scope,
        record=record,
    )
    subject = _render(template, "subject", template.subject, context)
    body = _render(template, "body", template.body, context)
    resolved_subject = rehydrate(subject, record=record)
    resolved_body = rehydrate(body, record=record)
    kinds = dict(resolved_subject.kinds)
    for kind, count in resolved_body.kinds.items():
        kinds[kind] = kinds.get(kind, 0) + count
    channel = drafting.channel(request.channel_id)
    return RenderedDraft(
        template_id=template.template_id,
        kind=request.kind,
        subject=_tidy(resolved_subject.text),
        body=_tidy(resolved_body.text),
        requirement_ids=tuple(gap.requirement_id for gap in request.gaps),
        amtsermittlung_ids=tuple(
            gap.requirement_id for gap in request.gaps if gap.amtsermittlung
        ),
        resolved_tokens=resolved_subject.resolved_tokens
        + resolved_body.resolved_tokens,
        distinct_tokens=resolved_subject.distinct_tokens
        + resolved_body.distinct_tokens,
        token_kinds=dict(sorted(kinds.items())),
        response_window_days=(
            drafting.response_window_days
            if request.kind == KIND_NACHFORDERUNG
            else None
        ),
        rechtsfolgenhinweis=bool(scope),
        dispatch_shape=channel.dispatch if channel is not None else None,
    )


def gap_sentences(
    renderings: Sequence[GapLike],
    *,
    drafting: DraftingConfig | None,
    procedure_id: str | None,
) -> tuple[GapSentence, ...]:
    """Turn evidence-plane gap renderings into letter sentences.

    The sentences are taken AS RENDERED. This function's only judgement is the
    C-7 one: which of them concern a requirement the agency can determine
    itself, and therefore soften and leave out of any par. 66 scope.
    """
    softened = (
        drafting.amtsermittlung.requirement_ids(procedure_id)
        if drafting is not None
        else frozenset()
    )
    return tuple(
        GapSentence(
            requirement_id=rendering.requirement_id,
            sentence=rendering.sentence,
            amtsermittlung=rendering.requirement_id in softened,
        )
        for rendering in renderings
    )


def requirement_label(requirement: Requirement | None, requirement_id: str) -> str:
    """How a requirement is named in a letter.

    The description from the procedure config, cut at the first parenthesis or
    full stop: those descriptions are written for a caseworker and carry format
    notes ("12 Stellen: Bereichsnummer, Geburtsdatum TTMMJJ, ...") that read as
    noise in a letter. No second wording is introduced - a label is always a
    prefix of the description an agency already wrote.
    """
    if requirement is None:
        # A mapped field that is nobody's requirement (``auslandsbezug`` is the
        # one in the shipped configs, and the Altersrente clear-cut criteria
        # read it). Its id capitalized is a readable label and is not a second
        # wording: nothing was invented for it.
        return requirement_id[:1].upper() + requirement_id[1:]
    text = " ".join(requirement.description.split())
    for cut in ("(", "."):
        index = text.find(cut)
        if index > 0:
            text = text[:index]
    return text.strip().rstrip(",;:") or requirement_id


def _context(
    request: DraftRequest,
    *,
    config: ConfigBundle,
    drafting: DraftingConfig,
    procedure: ProcedureConfig | None,
    scope: tuple[str, ...],
    record: VaultRecord,
) -> dict[str, Any]:
    """Everything a template may reference, and nothing else.

    The identity slots come from the VAULT RECORD's own paths rather than from
    the working copy, so the same function serves a live pipeline run and a
    journal replay: both have the vault_ref, neither needs the payload.
    """
    identity = addressee_slots(record, drafting=drafting)
    unit = config.unit(request.unit_id) if request.unit_id is not None else None
    channel = drafting.channel(request.channel_id)
    requirements = _requirements(procedure)
    context: dict[str, Any] = {
        "case_id": request.case_id,
        "received_at": _format_date(request.received_at),
        "procedure_label": drafting.procedure_label(request.procedure_id),
        "unit_name": unit.name if unit is not None else drafting.fallback_unit_name,
        "empfaenger_anschrift": identity.get("anschrift", ""),
        "versicherungsnummer": identity.get("versicherungsnummer", ""),
        "geburtsdatum": identity.get("geburtsdatum", ""),
        "gaps": [
            {
                "requirement_id": gap.requirement_id,
                "sentence": gap.sentence,
                "amtsermittlung": gap.amtsermittlung,
                "amtsermittlung_note": drafting.amtsermittlung.note_for(
                    request.procedure_id
                ),
                # The sentence with the C-7 softening already appended, because
                # a ``{% if %}`` inside a list item swallows its own line break
                # under trim_blocks. The template keeps the choice (it can use
                # the flag and the note separately); this is the ready-made one.
                "sentence_full": _softened(gap, drafting, request.procedure_id),
            }
            for gap in request.gaps
        ],
        "facts": [
            {
                "label": requirement_label(requirements.get(field_id), field_id),
                "value": value,
            }
            for field_id, value in _ordered_facts(request.facts, procedure)
        ],
        "window_days": drafting.response_window_days,
        "reply_channel": channel.reply if channel is not None else "",
        "rechtsfolgenhinweis": bool(scope),
        "rechtsfolgenhinweis_heading": drafting.rechtsfolgenhinweis.heading,
        "rechtsfolgenhinweis_body": _rechtsfolgen_body(
            drafting, requirements=requirements, scope=scope
        ),
        "entwurf_banner": drafting.framing.banner,
        "no_model_notice": drafting.framing.no_model_notice,
        "dispatch_note": drafting.framing.dispatch_note,
    }
    # The loader refuses a template naming anything outside DRAFT_CONTEXT_KEYS;
    # this is the same invariant from the other side, so the two cannot drift.
    if set(context) != set(DRAFT_CONTEXT_KEYS):
        raise DraftingError(  # pragma: no cover - a programming error, not config
            f"the draft context does not match the declared keys: "
            f"{sorted(set(context) ^ set(DRAFT_CONTEXT_KEYS))}"
        )
    return context


def addressee_slots(record: VaultRecord, *, drafting: DraftingConfig) -> dict[str, str]:
    """Slot name -> placeholder, for the addressee paths this agency declares.

    Empty slots are not produced: a path this submission did not carry is
    simply absent, and the template drops the line. Gold v4 carries no
    ``antragsteller.name``, so its drafts open with "Sehr geehrte Damen und
    Herren" rather than with a blank where a name should be.
    """
    by_path = placeholders_by_path(record)
    return {
        slot: by_path[path]
        for slot, path in drafting.addressee.paths().items()
        if path in by_path
    }


def _softened(
    gap: GapSentence, drafting: DraftingConfig, procedure_id: str | None
) -> str:
    """One gap sentence, with the Amtsermittlung note appended when C-7 applies."""
    if not gap.amtsermittlung:
        return gap.sentence
    return f"{gap.sentence} {drafting.amtsermittlung.note_for(procedure_id)}"


def _ordered_facts(
    facts: Mapping[str, str], procedure: ProcedureConfig | None
) -> list[tuple[str, str]]:
    """Facts in requirement order; anything not a requirement follows, sorted.

    Requirement order is the order the procedure config declares, which is the
    order a caseworker reads the form in. A stable order matters here because
    the letter is a golden file.
    """
    if procedure is None:
        return sorted(facts.items())
    declared = [
        requirement.requirement_id
        for requirement in procedure.requirements.requirements
    ]
    ordered = [(name, facts[name]) for name in declared if name in facts]
    ordered.extend(sorted((k, v) for k, v in facts.items() if k not in declared))
    return ordered


def _requirements(procedure: ProcedureConfig | None) -> dict[str, Requirement]:
    if procedure is None:
        return {}
    return {
        requirement.requirement_id: requirement
        for requirement in procedure.requirements.requirements
    }


def _rechtsfolgen_scope(request: DraftRequest, requested: bool) -> tuple[str, ...]:
    """Which requirements a par. 66 Abs. 3 block may cover, or nothing at all.

    Three refusals, all deliberate: the block is off unless the caller asked
    for it, it exists only on a Nachforderung, and it never covers a
    requirement the agency can determine itself (C-7). When the scope comes out
    empty the block is not rendered AT ALL - an empty Rechtsfolgenhinweis would
    be boilerplate, and boilerplate is what par. 66 Abs. 3 SGB I does not
    permit.
    """
    if not requested or request.kind != KIND_NACHFORDERUNG:
        return ()
    return tuple(gap.requirement_id for gap in request.gaps if not gap.amtsermittlung)


def _rechtsfolgen_body(
    drafting: DraftingConfig,
    *,
    requirements: Mapping[str, Requirement],
    scope: tuple[str, ...],
) -> str:
    """The par. 66 block with its ``{{ requirements }}`` slot filled in."""
    if not scope:
        return ""
    labels = ", ".join(
        requirement_label(requirements.get(requirement_id), requirement_id)
        for requirement_id in scope
    )
    return (
        environment()
        .from_string(drafting.rechtsfolgenhinweis.body)
        .render(requirements=labels)
    )


def _render(
    template: DraftTemplate, label: str, text: str, context: Mapping[str, Any]
) -> str:
    """Render one template string; an unknown name is a hard error."""
    try:
        return environment().from_string(text).render(**context)
    except jinja2.UndefinedError as error:
        raise DraftingError(
            f"{template.template_id}.{label}: template references an unknown "
            f"name ({error.message})"
        ) from error


def _format_date(moment: datetime | None) -> str:
    return moment.strftime(DATE_FORMAT) if moment is not None else ""


def _tidy(text: str) -> str:
    """Collapse the blank-line debris a dropped ``{% if %}`` leaves behind.

    Deliberately the same cosmetic rule the notification renderer applies, and
    deliberately a second copy of it: sharing the function would put the two
    renderers in one module, and the seam between them is the point.
    """
    lines = [line.rstrip() for line in text.strip().splitlines()]
    tidied: list[str] = []
    for line in lines:
        if not line and tidied and not tidied[-1]:
            continue
        tidied.append(line)
    return "\n".join(tidied)


__all__ = [
    "DATE_FORMAT",
    "KIND_NACHFORDERUNG",
    "KIND_PREPARED_DECISION",
    "DraftRequest",
    "DraftingError",
    "GapSentence",
    "RehydrationError",
    "RenderedDraft",
    "addressee_slots",
    "build_letter",
    "environment",
    "gap_sentences",
    "requirement_label",
]
