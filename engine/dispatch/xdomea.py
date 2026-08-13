"""The xdomea-SHAPED handover stub: a placeholder that admits to being one.

Confirming a prepared letter is the first moment in this system where something
would actually leave the building. Nothing leaves it today - there is no print
path and no qualified-electronic path (C-8's remainder) - so what confirm
produces is a file in an out-directory for a pilot adapter to collect, and this
module writes it.

Three decisions worth reading before using this anywhere near a real Postausgang:

**It is not conformant, and it says so in its own first line.** xdomea 3.x is a
XOEV standard with a namespace, a schema and a message-type taxonomy. This file
borrows its SHAPE (Nachrichtenkopf, Vorgang, Dokument) so a pilot adapter has
something recognisable to map, and carries a leading comment plus a
``konform="false"`` attribute so it can never be mistaken for the real thing.
The real adapter replaces this module, not the call site.

**It carries no letter text and no addressee.** A dispatched Nachforderung is a
letter to a named person, so the obvious design would put the re-hydrated body
in here - and that would make an operator-visible out-directory the THIRD place
in this system holding personal data, after the vault and the draft store. The
canary exception list is two members long and part 10 is not the part that makes
it three. The stub therefore carries identifiers, dates and shapes; the body
stays in the draft store, and the adapter that has the right to read it joins
the two. Every field written here is swept by the canary suite.

**It carries no caseworker.** ``Organisationseinheit`` is a unit id, because
that is the only actor the journal knows (BPersVG, C-4), and a property test
asserts that no natural-person identifier reaches this file.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from xml.etree import ElementTree

from engine.draft.bekanntgabe import ResponseDeadline

DISPATCH_DIR_ENV = "EINGANGSLOTSE_DISPATCH_DIR"

#: Namespace of the stub. Deliberately NOT a XOEV urn: a file claiming
#: ``urn:xoev-de:xdomea:...`` while failing schema validation would be worse
#: than a file that says what it is.
STUB_NAMESPACE = "urn:eingangslotse:dispatch:stub:v0"

STUB_NOT_CONFORMANT = (
    " Kein konformer xdomea-Nachrichtensatz. Angelehnt an xdomea 3.x "
    "(Nachrichtenkopf, Vorgang, Dokument) als Platzhalter fuer den "
    "Pilot-Adapter: kein XOEV-Namensraum, keine Schemavalidierung, keine "
    "Signatur. Der Brieftext steht bewusst NICHT in dieser Datei - er liegt "
    "im Entwurfsspeicher, und nur ein Adapter mit Leseberechtigung fuehrt "
    "beides zusammen. "
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,160}$")


@dataclass(frozen=True)
class DispatchFacts:
    """Everything one confirm-and-dispatch produced, value-free throughout.

    One object rather than a dozen parameters because the same facts go three
    places and must be identical in all three: the CONFIRMED journal payload,
    the handover stub, and the confirmation the caseworker reads back. A
    deadline recomputed per consumer would eventually disagree with itself.
    """

    case_id: str
    envelope_id: str
    draft_id: str
    draft_kind: str
    template_id: str
    unit_id: str | None
    procedure_id: str | None
    dispatch_shape: str
    dispatch_channel: str | None
    dispatched_at: datetime
    dispatch_date: date
    #: Absolute response deadline, for a Nachforderung. None for a prepared
    #: decision, which asks for nothing and therefore has no window.
    deadline: ResponseDeadline | None = None
    #: The Land whose holiday set produced the deadline, and how many dates
    #: were in it. Journaled so a date can always be re-derived, and printed on
    #: the confirm screen so nobody stamps a deadline without seeing which
    #: calendar computed it.
    land: str = ""
    holiday_count: int = 0

    def as_payload(self) -> dict[str, object]:
        """The dispatch block of the CONFIRMED payload."""
        payload: dict[str, object] = {
            "dispatched_at": _isoformat(self.dispatched_at),
            "dispatch_date": self.dispatch_date.isoformat(),
            "dispatch_shape": self.dispatch_shape,
            "dispatch_channel": self.dispatch_channel,
            "draft_id": self.draft_id,
            "draft_kind": self.draft_kind,
            "template_id": self.template_id,
            "land": self.land,
            "holiday_count": self.holiday_count,
        }
        if self.deadline is not None:
            payload["deadline"] = self.deadline.as_payload()
        return payload


@dataclass(frozen=True)
class DispatchStub:
    """One handover file: where it went and what it says it is.

    ``sha256`` is over the bytes as written, so the CONFIRMED journal payload
    can name the artifact by digest rather than by a path that may be rotated,
    archived or mounted somewhere else tomorrow.
    """

    path: Path
    format_id: str
    sha256: str
    byte_count: int

    def as_payload(self) -> dict[str, object]:
        """Journal-shaped description: a name and a digest, never a body."""
        return {
            "format_id": self.format_id,
            "filename": self.path.name,
            "sha256": self.sha256,
            "byte_count": self.byte_count,
        }


def dispatch_dir() -> Path | None:
    """The configured out-directory, or None when nothing is configured.

    None is a normal state, not a degraded one: a demo run journals the
    dispatch facts and writes no file, exactly as an in-memory journal is a
    normal state for the journal itself.
    """
    directory = os.environ.get(DISPATCH_DIR_ENV)
    return Path(directory) if directory else None


def stub_filename(case_id: str, draft_id: str) -> str:
    """Stable name for one dispatch: same case and draft, same file.

    A function of the two identifiers and nothing else - no counter, no clock -
    for the reason ``notification_id_for`` and ``draft_id_for`` are: a re-run
    that dispatched the same draft twice would otherwise leave two files that
    look like two letters.
    """
    digest = hashlib.sha256(f"{case_id}/{draft_id}".encode()).hexdigest()[:16]
    return f"{case_id}-{digest}.xml"


def build_stub_xml(facts: DispatchFacts, *, format_id: str) -> str:
    """The stub document as a string. Pure: nothing here touches the disk."""
    root = ElementTree.Element(
        f"{{{STUB_NAMESPACE}}}VersandStub",
        {"format": format_id, "konform": "false"},
    )
    kopf = ElementTree.SubElement(root, "Nachrichtenkopf")
    message_id = stub_filename(facts.case_id, facts.draft_id).removesuffix(".xml")
    _text(kopf, "NachrichtID", message_id)
    _text(kopf, "Erstellungszeitpunkt", _isoformat(facts.dispatched_at))
    absender = ElementTree.SubElement(kopf, "Absender")
    # A unit, never a person: the journal's Actor is unit-scoped by contract and
    # an export may not know more than the journal does (C-4).
    _text(absender, "Organisationseinheit", facts.unit_id or "")
    vorgang = ElementTree.SubElement(root, "Vorgang")
    _text(vorgang, "Kennzeichen", facts.case_id)
    _text(vorgang, "Eingangskennzeichen", facts.envelope_id)
    _text(vorgang, "Verfahren", facts.procedure_id or "")
    _document_element(root, facts)
    body = ElementTree.tostring(root, encoding="unicode")
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f"<!--{STUB_NOT_CONFORMANT}-->\n"
        f"{body}\n"
    )


def write_stub(
    directory: Path | str, *, facts: DispatchFacts, xml: str, format_id: str
) -> DispatchStub:
    """Write one stub and describe it. Rewrites its own idempotent name."""
    if not _SAFE_ID.match(facts.case_id) or not _SAFE_ID.match(facts.draft_id):
        raise ValueError(
            f"case_id {facts.case_id!r} / draft_id {facts.draft_id!r} is not "
            f"filesystem-safe; allowed: letters, digits, dot, underscore, hyphen"
        )
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    path = out / stub_filename(facts.case_id, facts.draft_id)
    data = xml.encode("utf-8")
    path.write_bytes(data)
    return DispatchStub(
        path=path,
        format_id=format_id,
        sha256=hashlib.sha256(data).hexdigest(),
        byte_count=len(data),
    )


def _document_element(root: ElementTree.Element, facts: DispatchFacts) -> None:
    dokument = ElementTree.SubElement(root, "Dokument")
    _text(dokument, "Kennzeichen", facts.draft_id)
    _text(dokument, "Dokumenttyp", facts.draft_kind)
    _text(dokument, "Vorlage", facts.template_id)
    _text(dokument, "Versandweg", facts.dispatch_shape)
    _text(dokument, "Eingangsweg", facts.dispatch_channel or "")
    _text(dokument, "Versanddatum", facts.dispatch_date.isoformat())
    if facts.deadline is not None:
        _text(dokument, "Bekanntgabe", facts.deadline.bekanntgabe_date.isoformat())
        _text(dokument, "Fristablauf", facts.deadline.deadline.isoformat())
        _text(dokument, "Fristgrundlage", "par. 37 Abs. 2 SGB X, par. 26 Abs. 3 SGB X")
    _text(
        dokument,
        "Inhaltsverweis",
        "Entwurfsspeicher; der Brieftext ist bewusst nicht Teil dieser Datei",
    )


def _text(parent: ElementTree.Element, tag: str, value: str) -> None:
    ElementTree.SubElement(parent, tag).text = value


def _isoformat(moment: datetime) -> str:
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).isoformat()
