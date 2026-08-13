"""The demo's fictional applicants, and the submissions they turn into.

Read from ``config/demo/personas_v1.yaml`` - a NEW independently versioned file
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
everything after the adapter, which is the part worth showing.
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
DEFAULT_PERSONAS_FILE = REPO_ROOT / "config" / "demo" / "personas_v1.yaml"

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

#: How the two tabs are labelled, and what the page says the second one is.
CHANNEL_LABELS = {
    CHANNEL_FORM: "Formular (FIT-Connect)",
    CHANNEL_EMAIL: "E-Mail (simulierter Adapter)",
}

CHANNEL_NOTES = {
    CHANNEL_FORM: (
        "Ein strukturierter Eingang, wie ihn eine FIT-Connect-Zustellung "
        "liefert. Die identitaetsbezogenen Felder werden als PFADE versiegelt, "
        "bevor die Arbeitskopie entsteht."
    ),
    CHANNEL_EMAIL: (
        "SIMULIERTER Adapter: es wird kein Postfach abgerufen, keine Mail "
        "empfangen und keine Adresse betrieben. Ihr Text geht direkt in "
        "dieselbe Verarbeitung, die ein echter Adapter beliefern wuerde - der "
        "Adapter selbst ist Pilotumfang (P-14). Im Freitext findet die "
        "Erkennerunion die Identitaetsangaben und versiegelt sie SPANNE FUER "
        "SPANNE."
    ),
}


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
    """

    field_id: str
    label: str
    path: str
    value: str
    kind: str = ""
    group: str = ""
    help: str = ""

    @property
    def identity(self) -> bool:
        """Whether this field is expected to be sealed."""
        return bool(self.kind)

    @property
    def pair_key(self) -> str:
        """What this field pairs under: its group when it has one, else itself."""
        return self.group or self.field_id


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
    procedure_hint: str | None = None

    def form_values(self) -> dict[str, str]:
        """The prefill for the Formular tab, field id to value."""
        return {field.field_id: field.value for field in self.fields}

    def field(self, field_id: str) -> PersonaField | None:
        for field in self.fields:
            if field.field_id == field_id:
                return field
        return None


@dataclass(frozen=True)
class PersonaSet:
    """Every persona this deployment offers, plus the panel texts."""

    version: str
    note: str
    hints: tuple[tuple[str, str], ...]
    personas: tuple[Persona, ...]

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
    """
    data: dict[str, Any] = {}
    for field in persona.fields:
        raw = str(values.get(field.field_id, field.value)).strip()
        if not raw:
            continue
        _assign(data, field.path, raw)
    payload: dict[str, Any] = {
        "submissionId": submission_id,
        "destinationId": DEMO_DESTINATION_ID,
        "channel": CHANNEL_FORM,
        "submittedAt": submitted_at,
        "data": data,
        "attachments": [],
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
    return Persona(
        persona_id=persona_id,
        display_name=str(entry.get("display_name", persona_id)).strip(),
        headline=str(entry.get("headline", "")).strip(),
        story=" ".join(str(entry.get("story", "")).split()),
        expectation=" ".join(str(entry.get("expectation", "")).split()),
        fields=tuple(_field(raw, persona_id) for raw in raw_fields),
        letter=letter + "\n",
        procedure_hint=str(hint).strip() if isinstance(hint, str) and hint else None,
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
    return PersonaField(
        field_id=field_id,
        label=str(raw.get("label", field_id)).strip(),
        path=path,
        value=str(raw.get("value", "")),
        kind=str(raw.get("kind", "")).strip(),
        group=str(raw.get("group", "")).strip(),
        help=" ".join(str(raw.get("help", "")).split()),
    )
