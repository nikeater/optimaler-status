"""The demo's fictional applicants, and the submissions they turn into.

Read from ``config/demo/personas_v3.yaml`` - a NEW independently versioned file
that ``engine.config_loader`` deliberately does not know about. Nothing here
reaches the pipeline, the decision table or a version stamp; the module is
imported only by the demo routes, which exist only when the demo flag is on.

**A persona is PII-shaped on purpose.** The whole showcase is that a visitor
watches their own typed Versicherungsnummer vanish behind a placeholder before
anything downstream sees it, and that only works if what they typed looks like
the real thing. What makes it safe is that every value is invented, every name
is Mustermann-class fictional, and the collision rule in the config's header is
asserted by a test rather than promised by a comment.

**One persona, two representations, one pipeline.** :meth:`Persona.form_values`
is what the Formular tab prefills, and :func:`build_form_submission` turns the
(possibly edited) values into a FIT-Connect-shaped submission. :attr:`Persona
.letter` is what the E-Mail tab prefills, and :func:`build_letter_submission`
turns the (possibly rewritten) prose into an e-mail submission. Both go through
``POST /ingest`` - the same sealing, the same validation, the same journal.
There is no third path and no shortcut: a demo of a shortcut demonstrates the
shortcut.

**The e-mail channel is SIMULATED and the UI says so.** No mailbox is polled,
no IMAP adapter exists, and the real one is pilot scope (P-14). What is real is
everything after the adapter, which is the part worth showing. Since part 20
the intake page does not OFFER that channel any more (a user decision;
``api/demo.py::OFFERED_CHANNELS``), and :func:`build_letter_submission` stays
here because the shape it builds is still a legal submission and still tested.

**A persona also brings PREPARED DOCUMENTS (part 20).** :attr:`Persona
.attachments` is 2 to 4 synthetic PDFs the visitor may tick, and a ticked one
becomes a REAL attachment on the submission - ``text`` plus a ``sourceType``,
which is exactly the shape a FIT-Connect PDF takes here once its text layer has
been read. There is no file upload and there will not be one: an upload control
on a public page is an ingest path around the redaction boundary (the part-10
refusal), and the intake page says so in words. The document TEXT is rendered
once at load time from the persona's own field values, so the same persona
always produces the same bytes.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

#: Where the persona file lives. Overridable so a test can point at a fixture
#: without shipping a second loader.
PERSONAS_DIR_ENV = "EINGANGSLOTSE_PERSONAS_FILE"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PERSONAS_FILE = REPO_ROOT / "config" / "demo" / "personas_v3.yaml"

#: The destination every demo submission declares. The same test destination the
#: gold corpus uses, because it is not a fact about the person.
DEMO_DESTINATION_ID = "drv-bund-eingang-test"

#: Prefix of every case id the demo mints, so a demo submission is recognisable
#: as one in a queue that also holds the seeded corpus.
DEMO_SUBMISSION_PREFIX = "demo"

#: The two channels the visitor may choose. No scan channel and no file upload:
#: an upload control on a public page is an ingest path around the redaction
#: boundary, and the OCR limitation of ADR-019 is a documented known error
#: rather than something to demonstrate to a stranger with their own document.
CHANNEL_FORM = "fit_connect"
CHANNEL_EMAIL = "email"
CHANNELS = (CHANNEL_FORM, CHANNEL_EMAIL)

#: What an attachment claims to be, and both halves are load-bearing rather
#: than decorative.
#:
#: ``application/pdf`` because that is what an applicant encloses and what the
#: visitor is told they are ticking. The bytes of the PDF never exist - only
#: the text a real adapter would have read out of it - and that is precisely
#: what the shape means here: ``engine/ingest/envelope.py`` turns "an
#: attachment carrying extracted ``text``" into a free-text ContentPart, so
#: this IS how a PDF travels through this system once its text layer has been
#: read. The page says "simuliert" next to the word PDF, so nothing is claimed
#: twice.
#:
#: ``born_digital`` because a PDF with a text layer is not a scan, and the
#: distinction decides whether span verification matches EXACTLY or with a
#: bounded fuzzy ratio (``engine/extract/verify.py``). Calling a prepared
#: document OCR would license fuzzy matching where exactness is free.
ATTACHMENT_MEDIA_TYPE = "application/pdf"
ATTACHMENT_SOURCE_TYPE = "born_digital"

#: How a ticked document arrives in the posted form. One checkbox per document
#: rather than one repeated name, because ``api/app.py::form_fields`` parses
#: the body into a plain dict and a repeated name would keep only the last.
ATTACHMENT_FIELD_PREFIX = "anlage-"


def attachment_field(attachment_id: str) -> str:
    """The form control name for one document."""
    return f"{ATTACHMENT_FIELD_PREFIX}{attachment_id}"


#: How the two tabs are labelled and what the page says the second one is now
#: lives in the translation table (``api/i18n.py``, keys ``channel.*``), because
#: those two sentences are read by a visitor in one of two languages and this
#: module has no business knowing which. What stays here is the two channel
#: IDS, which are values in a submission and belong to no language at all.


class PersonaError(RuntimeError):
    """The persona file is missing, unreadable or does not say what it must."""


@dataclass(frozen=True)
class PersonaField:
    """One editable field of a persona's form.

    ``kind`` is the placeholder kind this value is expected to become once the
    boundary has seen it. It is used for exactly one thing - pairing "what you
    typed" with "what the machine got" on the pipeline view - and never to
    decide what gets sealed: that decision belongs to
    ``config/redaction/identity_fields_v1.yaml`` and to the detector union, and
    a second opinion here would be a second redaction policy.

    ``control`` and ``join_order`` are part 16 and both are PRESENTATION ONLY.

    ``control`` says which HTML control renders the field - a text box, a
    native date picker, a select. None of the three changes what is submitted:
    a date input posts the same ISO string that used to be typed, and a select
    posts the same value typing produced.

    ``join_order`` is what makes the split name work. Two fields may declare
    the SAME ``path``; the submission builder then joins their values in
    ``join_order`` and writes one string to that path. The form asks for the
    Nachname first because that is how a German administrative form asks; the
    envelope receives "Vorname Nachname" because that is the string it has
    always received. The order on the screen and the order in the value are
    different questions and this is where they are kept apart.
    """

    field_id: str
    label: str
    path: str
    value: str
    kind: str = ""
    group: str = ""
    help: str = ""
    label_en: str = ""
    help_en: str = ""
    control: str = "text"
    options: tuple[str, ...] = ()
    join_order: int = 0

    @property
    def identity(self) -> bool:
        """Whether this field is expected to be sealed."""
        return bool(self.kind)

    @property
    def pair_key(self) -> str:
        """What this field pairs under: its group when it has one, else itself."""
        return self.group or self.field_id

    def label_for(self, lang: str) -> str:
        """This field's label in one language; German when none was written."""
        return self.label_en if lang == "en" and self.label_en else self.label

    def help_for(self, lang: str) -> str:
        """This field's help sentence in one language, possibly empty."""
        return self.help_en if lang == "en" and self.help_en else self.help


@dataclass(frozen=True)
class PersonaAttachment:
    """One prepared, synthetic document a visitor may enclose (part 20).

    ``text`` is ALREADY RENDERED: the persona file writes it as a template over
    that persona's own field ids and :func:`load_personas` substitutes them
    once, so an unresolved key is a load error rather than a half-rendered
    document in front of a visitor, and nothing has to be computed per request.

    ``label`` and ``note`` are read by a visitor and therefore carry English
    siblings. ``text`` and ``filename`` do not: the first is submission DATA
    that content-derivation rules read - it keeps the transliterated spelling
    of the letters for the reason ADR-031 gives - and the second is a file
    name, which no more translates than a Versicherungsnummer does.
    """

    attachment_id: str
    filename: str
    label: str
    text: str
    note: str = ""
    label_en: str = ""
    note_en: str = ""

    @property
    def field_name(self) -> str:
        """The checkbox name this document is ticked with."""
        return attachment_field(self.attachment_id)

    def label_for(self, lang: str) -> str:
        """This document's name in one language."""
        return self.label_en if lang == "en" and self.label_en else self.label

    def note_for(self, lang: str) -> str:
        """Why this document is asked for, in one language."""
        return self.note_en if lang == "en" and self.note_en else self.note

    def as_payload(self) -> dict[str, Any]:
        """This document as one entry of a submission's ``attachments``."""
        return {
            "id": self.attachment_id,
            "filename": self.filename,
            "mediaType": ATTACHMENT_MEDIA_TYPE,
            "sourceType": ATTACHMENT_SOURCE_TYPE,
            "text": self.text,
        }


@dataclass(frozen=True)
class Persona:
    """One fictional applicant, in both of the shapes the intake page offers."""

    persona_id: str
    display_name: str
    headline: str
    story: str
    expectation: str
    fields: tuple[PersonaField, ...]
    letter: str
    attachments: tuple[PersonaAttachment, ...] = ()
    procedure_hint: str | None = None
    headline_en: str = ""
    story_en: str = ""
    expectation_en: str = ""

    def form_values(self) -> dict[str, str]:
        """The prefill for the Formular tab, field id to value."""
        return {field.field_id: field.value for field in self.fields}

    def field(self, field_id: str) -> PersonaField | None:
        """This persona's field with that id, or None. Never an error."""
        for field in self.fields:
            if field.field_id == field_id:
                return field
        return None

    def attachment(self, attachment_id: str) -> PersonaAttachment | None:
        """This persona's document with that id, or None. Never an error."""
        for entry in self.attachments:
            if entry.attachment_id == attachment_id:
                return entry
        return None

    def headline_for(self, lang: str) -> str:
        """The one-line summary in one language."""
        return self.headline_en if lang == "en" and self.headline_en else self.headline

    def story_for(self, lang: str) -> str:
        """Who this person is, in one language."""
        return self.story_en if lang == "en" and self.story_en else self.story

    def expectation_for(self, lang: str) -> str:
        """What the pipeline is expected to do with them, in one language."""
        if lang == "en" and self.expectation_en:
            return self.expectation_en
        return self.expectation


@dataclass(frozen=True)
class PersonaSet:
    """Every persona this deployment offers, plus the panel texts."""

    version: str
    note: str
    hints: tuple[tuple[str, str], ...]
    personas: tuple[Persona, ...]
    note_en: str = ""
    hints_en: tuple[tuple[str, str], ...] = ()

    def note_for(self, lang: str) -> str:
        """The sentence above the picker, in one language."""
        return self.note_en if lang == "en" and self.note_en else self.note

    def hints_for(self, lang: str) -> tuple[tuple[str, str], ...]:
        """The "what you can try" panel, in one language."""
        return self.hints_en if lang == "en" and self.hints_en else self.hints

    def get(self, persona_id: str | None) -> Persona | None:
        """The named persona, or None. An unknown id is None, never an error.

        Same discipline as the unit picker (``api/review.resolve_unit``): a
        bookmarked URL with a persona that no longer exists shows the picker
        again rather than a stack trace, and it must never half-select
        something.
        """
        if not persona_id:
            return None
        for persona in self.personas:
            if persona.persona_id == persona_id:
                return persona
        return None

    @property
    def first(self) -> Persona:
        return self.personas[0]


def personas_file() -> Path:
    """Which persona file this process reads."""
    override = os.environ.get(PERSONAS_DIR_ENV, "").strip()
    return Path(override) if override else DEFAULT_PERSONAS_FILE


def load_personas(path: Path | None = None) -> PersonaSet:
    """Read and validate the persona file.

    Validation is deliberately strict about the two things that would fail
    silently on a rendered page: a persona with no fields (an empty form) and a
    field with no path (a value the submission would drop on the floor).
    """
    source = path or personas_file()
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as error:
        raise PersonaError(f"cannot read persona file {source}: {error}") from error
    if not isinstance(document, dict):
        raise PersonaError(f"persona file {source} is not a YAML mapping")
    entries = document.get("personas")
    if not isinstance(entries, list) or not entries:
        raise PersonaError(f"persona file {source} defines no personas")
    return PersonaSet(
        version=str(document.get("version", "")),
        note=str(document.get("note", "")).strip(),
        hints=tuple(_hints(document.get("hints"))),
        personas=tuple(_persona(entry, source) for entry in entries),
        note_en=str(document.get("note_en", "")).strip(),
        hints_en=tuple(_hints(document.get("hints_en"))),
    )


@lru_cache(maxsize=1)
def demo_personas() -> PersonaSet:
    """The process-wide persona set, read once.

    Cached for the same reason the demo posture is: the set is a property of the
    deployment, and a page that re-read a file per request would let an operator
    change what a visitor is halfway through submitting.
    """
    return load_personas()


def build_form_submission(
    persona: Persona,
    values: Mapping[str, str],
    *,
    submission_id: str,
    submitted_at: str,
) -> dict[str, Any]:
    """A FIT-Connect-shaped submission from the (edited) form values.

    An empty value is OMITTED rather than sent as an empty string, because that
    is what a form with a field left blank means and because the completeness
    checker's "missing" and "invalid" are different verdicts with different
    Nachforderung wording. Sending "" would report every blank as invalid, which
    is a worse sentence to send an applicant.

    **The ticked documents ride along** (part 20). They are read out of the
    SAME mapping the field values come from - a checkbox named
    ``anlage-<attachment_id>`` - because that mapping is what the visitor
    posted and a second source for one form would be a second answer to "what
    was submitted". Nothing ticked means ``attachments: []``, which is byte for
    byte the submission this function built before part 20 existed.

    **Fields that share a path are JOINED** (part 16). The form asks for the
    Nachname and the Vorname separately, in that order, because that is how a
    German administrative form asks; both declare
    ``path: antragsteller.name`` and their values are joined in ``join_order``,
    which produces the exact "Vorname Nachname" string the envelope carried
    before the split existed. Two consequences worth stating: the join happens
    HERE, in the one function that builds a submission, so nothing downstream
    learns that the form has two boxes; and a blank half is dropped rather than
    joined, so emptying the Vorname submits a surname alone instead of a string
    with a stray space in it.
    """
    data: dict[str, Any] = {}
    for path, parts in _by_path(persona, values).items():
        raw = " ".join(part for _order, part in sorted(parts, key=lambda p: p[0]))
        if not raw:
            continue
        _assign(data, path, raw)
    payload: dict[str, Any] = {
        "submissionId": submission_id,
        "destinationId": DEMO_DESTINATION_ID,
        "channel": CHANNEL_FORM,
        "submittedAt": submitted_at,
        "data": data,
        "attachments": [
            entry.as_payload() for entry in selected_attachments(persona, values)
        ],
    }
    if persona.procedure_hint:
        payload["procedureHint"] = persona.procedure_hint
    return payload


def build_letter_submission(
    persona: Persona,
    body: str,
    *,
    submission_id: str,
    submitted_at: str,
) -> dict[str, Any]:
    """An e-mail submission carrying the (edited) prose and nothing else.

    No ``procedureHint`` and no ``data``: a letter that arrives by mail carries
    neither, and the point of this tab is to watch the evidence plane derive the
    procedure from the CONTENT (ADR-013, extended in ADR-020) over text that has
    already been sealed span by span.

    No ``extractionFixture`` either, which is the honest part: the gold letters
    carry one so the replay extractor can quote them, and a letter a visitor
    just wrote has none. The deterministic readers do what they can, the
    verifier discards what it cannot double-check, and discards cost tier -
    which is exactly the behaviour ADR-020 describes and worth showing rather
    than hiding behind a fixture nobody could have.
    """
    return {
        "submissionId": submission_id,
        "destinationId": DEMO_DESTINATION_ID,
        "channel": CHANNEL_EMAIL,
        "submittedAt": submitted_at,
        "data": {},
        "attachments": [],
        "bodyText": body,
    }


def selected_attachments(
    persona: Persona, values: Mapping[str, str]
) -> tuple[PersonaAttachment, ...]:
    """The documents this submission ticked, in the persona's own order.

    Driven off the persona rather than off the posted keys, which is the same
    discipline every other reader of a form here follows: a checkbox naming a
    document this persona does not have is not an error and is not a document,
    it is simply not in the list. An unticked box posts nothing at all, so an
    absent key and an empty one both mean "not enclosed".
    """
    return tuple(
        entry
        for entry in persona.attachments
        if str(values.get(entry.field_name, "")).strip()
    )


def _by_path(
    persona: Persona, values: Mapping[str, str]
) -> dict[str, list[tuple[int, str]]]:
    """The submitted values grouped by the payload path they are written to.

    A dict preserves insertion order, so a persona with no shared path produces
    exactly the sequence of assignments the pre-split builder produced - which
    is what makes the envelope byte-identical rather than merely equivalent.
    """
    grouped: dict[str, list[tuple[int, str]]] = {}
    for field in persona.fields:
        raw = str(values.get(field.field_id, field.value)).strip()
        grouped.setdefault(field.path, [])
        if raw:
            grouped[field.path].append((field.join_order, raw))
    return grouped


def _assign(target: dict[str, Any], path: str, value: str) -> None:
    """Set a dotted path in a nested dict, creating the objects on the way."""
    keys = path.split(".")
    node = target
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[keys[-1]] = value


def _hints(raw: object) -> Sequence[tuple[str, str]]:
    if not isinstance(raw, list):
        return ()
    hints: list[tuple[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label", "")).strip()
        detail = str(entry.get("detail", "")).strip()
        if label and detail:
            hints.append((label, detail))
    return hints


def _persona(entry: object, source: Path) -> Persona:
    if not isinstance(entry, dict):
        raise PersonaError(f"persona file {source}: a persona is not a mapping")
    persona_id = str(entry.get("persona_id", "")).strip()
    if not persona_id:
        raise PersonaError(f"persona file {source}: a persona has no persona_id")
    raw_fields = entry.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise PersonaError(f"persona {persona_id} has no fields")
    letter = str(entry.get("letter", "")).strip()
    if not letter:
        raise PersonaError(f"persona {persona_id} has no letter")
    hint = entry.get("procedure_hint")
    fields = tuple(_field(raw, persona_id) for raw in raw_fields)
    return Persona(
        persona_id=persona_id,
        display_name=str(entry.get("display_name", persona_id)).strip(),
        headline=str(entry.get("headline", "")).strip(),
        story=" ".join(str(entry.get("story", "")).split()),
        expectation=" ".join(str(entry.get("expectation", "")).split()),
        fields=fields,
        letter=letter + "\n",
        attachments=tuple(_attachments(entry.get("attachments"), persona_id, fields)),
        procedure_hint=str(hint).strip() if isinstance(hint, str) and hint else None,
        headline_en=str(entry.get("headline_en", "")).strip(),
        story_en=" ".join(str(entry.get("story_en", "")).split()),
        expectation_en=" ".join(str(entry.get("expectation_en", "")).split()),
    )


def _attachments(
    raw: object, persona_id: str, fields: Sequence[PersonaField]
) -> Sequence[PersonaAttachment]:
    """Every prepared document of one persona, with its text already rendered.

    A persona with no ``attachments`` block simply has none - unlike a missing
    field list, which would render an empty form. The intake page then shows no
    document fieldset for that persona, which is a page saying the truth rather
    than an empty box.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PersonaError(f"persona {persona_id}: attachments is not a list")
    values = {field.field_id: field.value for field in fields}
    return [_attachment(item, persona_id, values) for item in raw]


def _attachment(
    raw: object, persona_id: str, values: Mapping[str, str]
) -> PersonaAttachment:
    if not isinstance(raw, dict):
        raise PersonaError(f"persona {persona_id}: an attachment is not a mapping")
    attachment_id = str(raw.get("attachment_id", "")).strip()
    filename = str(raw.get("filename", "")).strip()
    label = str(raw.get("label", "")).strip()
    if not attachment_id or not filename or not label:
        raise PersonaError(
            f"persona {persona_id}: every attachment needs an attachment_id, a "
            f"filename and a label (got {attachment_id!r} / {filename!r} / "
            f"{label!r})"
        )
    template = str(raw.get("text", ""))
    if not template.strip():
        raise PersonaError(
            f"persona {persona_id}: attachment {attachment_id} has no text; an "
            "attachment with nothing in it produces no envelope part at all"
        )
    try:
        text = template.format(**values)
    except (KeyError, IndexError) as error:
        # At LOAD time rather than per request, and loudly: the alternative is
        # a visitor being shown a document with `{versicherungsnummer}` in it.
        raise PersonaError(
            f"persona {persona_id}: attachment {attachment_id} refers to "
            f"{error} which is not one of this persona's fields"
        ) from error
    return PersonaAttachment(
        attachment_id=attachment_id,
        filename=filename,
        label=label,
        text=text,
        note=" ".join(str(raw.get("note", "")).split()),
        label_en=str(raw.get("label_en", "")).strip(),
        note_en=" ".join(str(raw.get("note_en", "")).split()),
    )


def _field(raw: object, persona_id: str) -> PersonaField:
    if not isinstance(raw, dict):
        raise PersonaError(f"persona {persona_id}: a field is not a mapping")
    field_id = str(raw.get("field_id", "")).strip()
    path = str(raw.get("path", "")).strip()
    if not field_id or not path:
        raise PersonaError(
            f"persona {persona_id}: every field needs a field_id and a path "
            f"(got {field_id!r} / {path!r})"
        )
    options = raw.get("options")
    return PersonaField(
        field_id=field_id,
        label=str(raw.get("label", field_id)).strip(),
        path=path,
        value=str(raw.get("value", "")),
        kind=str(raw.get("kind", "")).strip(),
        group=str(raw.get("group", "")).strip(),
        help=" ".join(str(raw.get("help", "")).split()),
        label_en=str(raw.get("label_en", "")).strip(),
        help_en=" ".join(str(raw.get("help_en", "")).split()),
        control=str(raw.get("control", "text")).strip() or "text",
        options=(
            tuple(str(option) for option in options)
            if isinstance(options, list)
            else ()
        ),
        join_order=int(raw.get("join_order", 0) or 0),
    )
