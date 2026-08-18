"""The counterparty of a Statusfeststellung, and the form the visitor answers as.

Part 19, the two-party loop. Par. 7a Abs. 2 S. 1 SGB IV orders a
Gesamtwuerdigung and par. 7a Abs. 4 SGB IV hears BOTH sides before it, so a
Statusfeststellung has a second party by law: Sabine Musterfrau is the
Auftragnehmerin, and her Auftraggeber is asked what the working relationship
actually looks like. This module is that second party, as a form.

**The Auftraggeber is a persona, and it is the same dataclass on purpose.**
:func:`statement_form` returns a :class:`~engine.demo.personas.Persona`, so the
statement rides the machinery the intake already has: one grouping of fields
into rows, one echo builder, one submission builder, one set of controls. A
second form implementation would be a second answer to "what does this project
do with a filled-in form", and the first thing a second implementation does is
drift. What is different about this persona is only that its values are not all
written in a file: four of them are carried across from the case being answered
(:class:`~engine.demo.store.StatementLink`), which is what makes the answer an
answer rather than an unrelated second application.

**What the statement carries, and the one thing it deliberately does not.** It
is a Statusantrag filed by the Auftraggeber - ``antragsteller_rolle:
auftraggeber``, which par. 7a Abs. 1 S. 1 SGB IV explicitly allows and which
``corpus/generator/scenarios/statusfeststellung.yaml`` already has a gold item
for (``sf-0004``). It carries the Auftraggeber's OWN identity - contact person,
company, Betriebsnummer, address - and every one of those is sealed at the
boundary exactly like the applicant's, which is the demonstration: the seal is a
property of the system, not a courtesy extended to one party.

It does NOT carry the applicant's Versicherungsnummer or Geburtsdatum. A
Stellungnahme does not need them, so the counterparty surface never shows them
and the second submission never contains them. The consequence is real and is
the honest half of the demonstration: the completeness check reports two gaps,
the case lands on tier 2, and the Clearingstelle asks for what is missing -
which is what it does with every other incomplete item, without knowing or
caring that this one came from the second party.

**Two vocabularies, two sources, and neither of them invented here.**

* ``antragsteller_rolle`` is constrained by ``config/procedures/
  statusfeststellung_v1.yaml`` (a ``one_of`` on a mapped requirement), so the
  select is fed at render time by ``api/demo.py::vocabulary`` and cannot offer a
  value the completeness checker would reject.
* The Indizien of the Gesamtwuerdigung - Weisungsgebundenheit, Eingliederung,
  Arbeitsort, weitere Auftraggeber, Honorarmodell - are deliberately NOT
  requirements (the ``field_map`` comment in that config says why: they are
  Abwaegungsmaterial, not a checklist), so no ``one_of`` exists to read. Their
  options below are READ from the corpus scenario file, verbatim, and
  ``tests/test_demo_gegenpartei.py`` asserts that every option this module
  offers actually occurs there - so the copy is checked rather than promised.

**The prose is submission DATA and keeps the transliterated spelling** (ADR-031,
the rule the persona letters follow): content-derivation rules read
``text.normalized``, and it is the same string on a German page and on an
English one. The request LETTER is the opposite - it is displayed and nothing
reads it - so it carries its umlauts and stays German in both languages, like
every other Behoerdenschreiben on this site.

**Every fixture value here is invented**, Mustermann-class, and collides with
nothing in ``corpus/pii_golden/`` or ``corpus/gold/``. That is the persona
file's own collision rule, and it is asserted over this module's values by the
same kind of test, for the same reason: a demo string that also occurs in a
frozen set makes the canary sweep unable to tell a leak from a fixture.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from engine.demo.personas import (
    DEMO_SUBMISSION_PREFIX,
    Persona,
    PersonaField,
    build_form_submission,
)
from engine.demo.store import StatementAnswer, StatementLink

#: The persona id the statement form submits under. Never in the persona file:
#: the intake picker offers APPLICANTS, and this one is not one of them.
STATEMENT_PERSONA_ID = "auftraggeber_stellungnahme"

#: Prefix of every statement submission id, so a statement is recognisable as
#: one in a queue that also holds intake submissions and the seeded corpus.
STATEMENT_SUBMISSION_PREFIX = f"{DEMO_SUBMISSION_PREFIX}-stellungnahme"

#: The procedure the statement declares. A Stellungnahme in this loop IS a
#: Statusantrag by the other party (par. 7a Abs. 1 S. 1 SGB IV), and naming the
#: procedure is what the paper form does, so the hint is carried rather than
#: left for the content rules to re-derive.
STATEMENT_PROCEDURE_HINT = "statusfeststellung"

#: The counterparty's own fixture identity, invented for this file.
#:
#: The SHAPES are load-bearing rather than decorative, exactly as they are in
#: `config/demo/personas_v4.yaml`: a name behind "Herr"/"Frau", the address as
#: "<Strasse> <Nr>, <PLZ> <Ort>", a street name whose suffix the deterministic
#: recognizer knows ("-allee" is on the list in `engine/redact/recognizers.py`;
#: "-koppel" is not, which part 22 found the hard way). The hosted demo installs
#: no optional model, so an identity value that needed spaCy to be sealed would
#: be refused there and accepted on a developer machine.
CONTACT_GIVEN_NAME = "Ole"
CONTACT_SURNAME = "Musterhold"
COMPANY_STREET = "Werftallee"
COMPANY_NUMBER = "9"
COMPANY_POSTCODE = "18147"
COMPANY_TOWN = "Musterhafen"

#: Eight digits, the shape the Bundesagentur fuer Arbeit issues. Invented, and
#: the one identity kind (BNR) that no other demo surface has ever shown.
COMPANY_BETRIEBSNUMMER = "70415329"

#: The Indizien vocabularies. READ from `corpus/generator/scenarios/
#: statusfeststellung.yaml` and kept in its spelling, because these are values a
#: rule reads rather than words a visitor reads. See the module docstring for
#: why the procedure config has no `one_of` to feed them from.
JA_NEIN: tuple[str, ...] = ("ja", "nein")
ARBEITSORT_OPTIONS: tuple[str, ...] = (
    "beim_auftraggeber",
    "eigene_betriebsstaette",
    "wechselnd",
)
HONORAR_OPTIONS: tuple[str, ...] = ("nach_stunden", "nach_ergebnis")

#: Which fields the counterparty ANSWERS, in the order the form asks. The
#: summary on the applicant's own pipeline page is built from exactly this
#: list, so "what the Auftraggeber said" and "what the form asked" cannot drift.
ANSWER_FIELD_IDS: tuple[str, ...] = (
    "antragsteller_rolle",
    "taetigkeit_beginn",
    "weisungsgebunden",
    "eingliederung_arbeitsorganisation",
    "arbeitsort",
    "weitere_auftraggeber",
    "honorar_modell",
)

#: Which fields are CARRIED from the case being answered and are therefore not
#: questions. They ride as hidden inputs and their values are stated in words
#: above the form, in the letter - a control a visitor cannot meaningfully
#: change is worse than a sentence, and an invisible value nobody is told about
#: is worse than both.
CARRIED_FIELD_IDS: tuple[str, ...] = ("antragsart", "taetigkeit_bezeichnung")

#: Who is answering. One row for the name, one for the address, exactly as the
#: intake form groups them - and for the same reason, which is that a form reads
#: as the handful of ANSWERS it is rather than as eight separate questions.
PARTY_FIELDS: tuple[PersonaField, ...] = (
    PersonaField(
        field_id="ansprechpartner_nachname",
        label="Nachname",
        label_en="Surname",
        path="antragsteller.name",
        join_order=2,
        value=CONTACT_SURNAME,
        kind="NAME",
        group="ansprechpartner",
    ),
    PersonaField(
        field_id="ansprechpartner_vorname",
        label="Vorname",
        label_en="Given name",
        path="antragsteller.name",
        join_order=1,
        value=CONTACT_GIVEN_NAME,
        kind="NAME",
        group="ansprechpartner",
    ),
    PersonaField(
        field_id="firmenname",
        label="Firmenname",
        label_en="Company name",
        path="auftraggeber.firmenname",
        value="",
        kind="ORG",
    ),
    PersonaField(
        field_id="betriebsnummer",
        label="Betriebsnummer",
        label_en="Employer reference number",
        path="auftraggeber.betriebsnummer",
        value=COMPANY_BETRIEBSNUMMER,
        kind="BNR",
        help=(
            "Achtstellige Betriebsnummer der Bundesagentur für Arbeit. "
            "Erfunden wie alles auf dieser Seite."
        ),
        help_en=(
            "The eight-digit employer reference number issued by the Federal "
            "Employment Agency. Invented, like everything on this page."
        ),
    ),
    PersonaField(
        field_id="strasse",
        label="Straße",
        label_en="Street",
        path="auftraggeber.anschrift.strasse",
        value=COMPANY_STREET,
        kind="ADDR",
        group="anschrift",
    ),
    PersonaField(
        field_id="hausnummer",
        label="Hausnummer",
        label_en="Number",
        path="auftraggeber.anschrift.hausnummer",
        value=COMPANY_NUMBER,
        kind="ADDR",
        group="anschrift",
    ),
    PersonaField(
        field_id="plz",
        label="Postleitzahl",
        label_en="Postcode",
        path="auftraggeber.anschrift.plz",
        value=COMPANY_POSTCODE,
        kind="ADDR",
        group="anschrift",
    ),
    PersonaField(
        field_id="ort",
        label="Ort",
        label_en="Town",
        path="auftraggeber.anschrift.ort",
        value=COMPANY_TOWN,
        kind="ADDR",
        group="anschrift",
    ),
)

#: The questions. Every default below states the OPPOSITE of what Sabine's own
#: C0031 says, and that is the whole demonstration: her annex says the working
#: time is hers to divide, the workplace is hers and there is no Weisungsbindung
#: im Einzelnen; this form arrives claiming the reverse. Two sealed,
#: span-verified statements that contradict each other IS the Gesamtwuerdigung
#: par. 7a Abs. 2 S. 1 SGB IV reserves for a human, and it is precisely why this
#: procedure ships `tier1_enabled: false`. A visitor who wants agreement instead
#: changes five selects.
QUESTION_FIELDS: tuple[PersonaField, ...] = (
    PersonaField(
        field_id="antragsteller_rolle",
        label="Sie antworten als",
        label_en="You are answering as",
        path="antrag.antragsteller_rolle",
        control="select",
        value="auftraggeber",
        help=(
            "Die Auswahl kommt aus der Verfahrenskonfiguration, nicht aus "
            "dieser Seite (V0027, Ziffer 9.1)."
        ),
        help_en=(
            "The options come from the procedure configuration rather than "
            "from this page (form V0027, item 9.1)."
        ),
    ),
    PersonaField(
        field_id="taetigkeit_beginn",
        label="Beginn der Tätigkeit nach Ihren Unterlagen",
        label_en="Start of the activity according to your records",
        path="antrag.taetigkeit_beginn",
        control="date",
        value="",
        help=(
            "Vorbelegt mit dem Datum aus dem Antrag. Ein anderes Datum ist "
            "ein echter Widerspruch - beide Angaben bleiben lesbar."
        ),
        help_en=(
            "Prefilled with the date from the application. A different date "
            "is a genuine contradiction, and both answers stay readable."
        ),
    ),
    PersonaField(
        field_id="weisungsgebunden",
        label="Weisungsgebunden",
        label_en="Bound by instructions",
        path="antrag.weisungsgebunden",
        control="select",
        options=JA_NEIN,
        value="ja",
    ),
    PersonaField(
        field_id="eingliederung_arbeitsorganisation",
        label="In Ihre Arbeitsorganisation eingegliedert",
        label_en="Integrated into your work organisation",
        path="antrag.eingliederung_arbeitsorganisation",
        control="select",
        options=JA_NEIN,
        value="ja",
    ),
    PersonaField(
        field_id="arbeitsort",
        label="Arbeitsort",
        label_en="Place of work",
        path="antrag.arbeitsort",
        control="select",
        options=ARBEITSORT_OPTIONS,
        value="beim_auftraggeber",
    ),
    PersonaField(
        field_id="weitere_auftraggeber",
        label="Weitere Auftraggeber bekannt",
        label_en="Other clients known to you",
        path="antrag.weitere_auftraggeber",
        control="select",
        options=JA_NEIN,
        value="nein",
    ),
    PersonaField(
        field_id="honorar_modell",
        label="Honorarmodell",
        label_en="Fee model",
        path="antrag.honorar_modell",
        control="select",
        options=HONORAR_OPTIONS,
        value="nach_stunden",
    ),
)

#: The two carried values, as fields, so ONE builder sees every path.
CARRIED_FIELDS: tuple[PersonaField, ...] = (
    PersonaField(
        field_id="antragsart",
        label="Antragsart",
        label_en="Kind of application",
        path="antrag.antragsart",
        control="hidden",
        value="",
    ),
    PersonaField(
        field_id="taetigkeit_bezeichnung",
        label="Bezeichnung der Tätigkeit",
        label_en="Description of the activity",
        path="antrag.taetigkeit_bezeichnung",
        control="hidden",
        value="",
    ),
)

#: The default Stellungnahme, as a template over the case being answered.
#:
#: Submission DATA: transliterated, German, and read by the content-derivation
#: rules of `config/procedures/statusfeststellung_v1.yaml`. It therefore obeys
#: the persona file's rule 1 - it names this procedure ("Erwerbsstatus",
#: "par. 7a") and never the other one, so two procedures' signals can never fire
#: at once and resolve to "no procedure".
STATEMENT_PROSE = """\
Stellungnahme des Auftraggebers zum Antrag auf Feststellung des Erwerbsstatus
(par. 7a Abs. 4 SGB IV)

Auftraggeber: {auftraggeber}
Ansprechpartner: Herr {vorname} {nachname}, Personalwesen
Anschrift: {strasse} {hausnummer}, {plz} {ort}

Zur Gesamtwuerdigung: die Arbeitszeiten geben wir vor, die Taetigkeit wird in
unseren Raeumen und mit unseren Arbeitsmitteln ausgeuebt, und die beauftragte
Person ist in unsere Arbeitsorganisation eingegliedert. Eine Vertretung durch
Dritte ist nicht vereinbart.

Vorbereiteter Beispieltext dieser Demonstration.
"""

#: The simulated request letter, paragraph by paragraph.
#:
#: DISPLAY text, so it keeps its umlauts - and it stays German on the English
#: page, like the notification bodies in `/inbox` and for the same reason: a
#: Behoerdenschreiben is a German document, and a translated one would be a
#: different document. The page says that in English rather than leaving a
#: reader to wonder why one block did not switch.
#:
#: The flowchart this part implements drew this box as a popup. It is a letter
#: instead, and that is a house-style decision rather than a shortcut: nothing
#: on this site needs JavaScript, and a letter is the artifact a real
#: Anhoerung produces.
REQUEST_LETTER: tuple[str, ...] = (
    "Deutsche Rentenversicherung Bund - Clearingstelle",
    "Anhörung nach par. 7a Abs. 4 SGB IV in Verbindung mit par. 24 SGB X",
    "An: {auftraggeber}",
    "Zeichen: {token}\nVorgang: {case_id}\nDatum: {datum}",
    "Sehr geehrte Damen und Herren,",
    "{applicant} hat die Feststellung des Erwerbsstatus für die Tätigkeit "
    '"{taetigkeit}" beantragt. Als Beginn der Tätigkeit ist der {beginn} '
    "angegeben, die Antragsart lautet {antragsart}.",
    "Bevor über den Erwerbsstatus entschieden wird, geben wir Ihnen als "
    "Auftraggeber Gelegenheit zur Stellungnahme. Bitte äußern Sie sich zu den "
    "Umständen der Tätigkeit - Ihre Angaben gehen in die Gesamtwürdigung "
    "nach par. 7a Abs. 2 Satz 1 SGB IV ein.",
    "Eine Antwort ist freiwillig. Geht keine Stellungnahme ein, wird nach "
    "Aktenlage entschieden; der Vorgang wartet nicht auf Sie.",
    "Mit freundlichen Grüßen\nClearingstelle",
)

#: What the letter prints where the case named nothing. The applicant may have
#: emptied their given name (one of the intake hints does exactly that), and a
#: letter with a hole in it would read as a defect rather than as an answer.
UNNAMED_APPLICANT = "Die auftragnehmende Person"

#: What it prints for an Antragsart the case never carried.
UNSTATED = "ohne Angabe"


#: The payload paths the request letter needs, read off the submitted intake
#: form by PATH rather than by field id.
#:
#: The difference matters the day a persona renames a box. A field id is a name
#: on ONE form; a path is what the submission carries and what the procedure's
#: `field_map` binds a requirement to, so reading by path means this function
#: keeps working for a persona nobody has written yet and stops working loudly
#: rather than quietly for one that drops the field altogether.
PATH_AUFTRAGGEBER = "auftraggeber.firmenname"
PATH_APPLICANT = "antragsteller.name"
PATH_TAETIGKEIT = "antrag.taetigkeit_bezeichnung"
PATH_BEGINN = "antrag.taetigkeit_beginn"
PATH_ANTRAGSART = "antrag.antragsart"


def statement_request(
    persona: Persona,
    values: Mapping[str, str],
    *,
    token: str,
    case_id: str,
    procedure_id: str | None,
    now: datetime,
) -> StatementLink | None:
    """The request this submission earns, or None when it earns none.

    Two conditions, and both are FACTS ABOUT THE SUBMISSION THAT WAS JUST MADE
    rather than opinions formed here. The procedure is the one the pipeline
    derived and journaled - read, never re-derived - because only par. 7a SGB IV
    has a second party to hear; an Altersrente has none, and a page that asked
    a counterparty for a Rentenantrag would be inventing a procedure. And the
    Auftraggeber has to have been NAMED: one of the intake hints tells the
    visitor to empty exactly that field, and a letter addressed to nobody is
    worse than no letter.

    So the answer is None for every seeded corpus item, for every non-status
    procedure and for the emptied-Auftraggeber hint - and the pipeline page then
    renders no two-party section at all, which is the honest shape for a case
    that does not have a second party.
    """
    if procedure_id != STATEMENT_PROCEDURE_HINT:
        return None
    auftraggeber = _joined(persona, values, PATH_AUFTRAGGEBER)
    if not auftraggeber:
        return None
    return StatementLink(
        token=token,
        case_id=case_id,
        created_at=now,
        auftraggeber=auftraggeber,
        applicant=_joined(persona, values, PATH_APPLICANT),
        taetigkeit=_joined(persona, values, PATH_TAETIGKEIT),
        beginn=_joined(persona, values, PATH_BEGINN),
        antragsart=_joined(persona, values, PATH_ANTRAGSART),
    )


def _joined(persona: Persona, values: Mapping[str, str], path: str) -> str:
    """What this submission wrote to one payload path, joined as ingest joins it.

    The same rule ``build_form_submission`` applies, because it has to be the
    same string: the form asks for the Nachname first and the envelope carries
    "Vorname Nachname", so a letter that read the two boxes in screen order
    would address somebody the machine never saw.
    """
    parts = sorted(
        (entry.join_order, str(values.get(entry.field_id, entry.value)).strip())
        for entry in persona.fields
        if entry.path == path
    )
    return " ".join(value for _order, value in parts if value)


def statement_form(link: StatementLink) -> Persona:
    """The counterparty form for one request, prefilled from the case.

    A :class:`Persona` because the statement is a form submission like any
    other here (see the module docstring). Its ``display_name`` is the company
    the visitor typed on the intake page, which is what the pipeline view then
    shows as "ausgefüllt als" - the echo rule of ``engine/demo/store.py``: the
    visitor's own value, shown back to the visitor who typed it.
    """
    carried = {
        "firmenname": link.auftraggeber,
        "taetigkeit_beginn": link.beginn,
        "antragsart": link.antragsart,
        "taetigkeit_bezeichnung": link.taetigkeit,
    }
    fields = tuple(
        replace(entry, value=carried[entry.field_id])
        if entry.field_id in carried
        else entry
        for entry in (*PARTY_FIELDS, *QUESTION_FIELDS, *CARRIED_FIELDS)
    )
    return Persona(
        persona_id=STATEMENT_PERSONA_ID,
        display_name=link.auftraggeber,
        headline="",
        story="",
        expectation="",
        fields=fields,
        letter="",
        procedure_hint=STATEMENT_PROCEDURE_HINT,
    )


def statement_prose(link: StatementLink) -> str:
    """The Stellungnahme the textarea starts with, for one request.

    Rendered from the module's own fixture values rather than from whatever is
    currently in the address boxes, which is the same choice
    ``engine/demo/personas.py`` makes for a prepared document: the text is
    prepared, the visitor may rewrite it, and it does not silently follow edits
    made somewhere else on the page.
    """
    return STATEMENT_PROSE.format(
        auftraggeber=link.auftraggeber or UNSTATED,
        vorname=CONTACT_GIVEN_NAME,
        nachname=CONTACT_SURNAME,
        strasse=COMPANY_STREET,
        hausnummer=COMPANY_NUMBER,
        plz=COMPANY_POSTCODE,
        ort=COMPANY_TOWN,
    )


def request_letter(link: StatementLink) -> tuple[str, ...]:
    """The simulated Anhoerung letter for one request, paragraph by paragraph.

    Split into paragraphs here rather than marked safe in a template: the page
    writes a ``<p>`` around each one and Jinja escapes every character of it, so
    a company name a visitor typed cannot become markup on the way to the
    screen.
    """
    return tuple(
        paragraph.format(
            auftraggeber=link.auftraggeber or UNSTATED,
            applicant=link.applicant or UNNAMED_APPLICANT,
            taetigkeit=link.taetigkeit or UNSTATED,
            beginn=link.beginn or UNSTATED,
            antragsart=link.antragsart or UNSTATED,
            token=link.token,
            case_id=link.case_id,
            datum=link.created_at.date().isoformat(),
        )
        for paragraph in REQUEST_LETTER
    )


def statement_answers(values: Mapping[str, str]) -> tuple[StatementAnswer, ...]:
    """What the counterparty answered, in the order the form asked.

    Driven off :data:`ANSWER_FIELD_IDS` rather than off the posted keys, which
    is the discipline every other reader of a form here follows: a key this form
    does not have is not an answer, and an answer the form asked for that came
    back empty is simply absent rather than recorded as "".
    """
    rows = []
    for field_id in ANSWER_FIELD_IDS:
        value = str(values.get(field_id, "")).strip()
        if value:
            rows.append(StatementAnswer(field_id=field_id, value=value))
    return tuple(rows)


def build_statement_submission(
    persona: Persona,
    values: Mapping[str, str],
    *,
    submission_id: str,
    submitted_at: str,
    body: str,
) -> dict[str, Any]:
    """The statement as a submission, through the intake's own builder.

    ``build_form_submission`` does all of it - the joined name, the dropped
    blanks, the ``procedureHint`` - because this IS a form submission and a
    second builder would be a second shape. The one thing added here is
    ``bodyText``: the Gesamtwuerdigung reads as prose (the procedure config says
    so in as many words), and prose on this channel becomes a free-text part
    that the boundary seals span by span and the verifier checks position by
    position. An emptied textarea simply omits the key, which is what a
    submission with no covering text is.
    """
    payload = build_form_submission(
        persona, values, submission_id=submission_id, submitted_at=submitted_at
    )
    text = body.strip()
    if text:
        payload["bodyText"] = text
    return payload


#: The three fieldsets, as sets of field ids. Membership rather than a lookup
#: per id: the form's own field order already IS the order the page asks in
#: (party, then questions, then the carried values), so filtering keeps it
#: without a second list that could disagree about it.
PARTY_FIELD_IDS: frozenset[str] = frozenset(entry.field_id for entry in PARTY_FIELDS)
QUESTION_FIELD_IDS: frozenset[str] = frozenset(ANSWER_FIELD_IDS)
CARRIED_FIELD_ID_SET: frozenset[str] = frozenset(CARRIED_FIELD_IDS)


def carried_fields(persona: Persona) -> tuple[PersonaField, ...]:
    """This form's fields that were carried from the case being answered."""
    return _select(persona, CARRIED_FIELD_ID_SET)


def party_fields(persona: Persona) -> tuple[PersonaField, ...]:
    """Who is answering: the counterparty's own identity."""
    return _select(persona, PARTY_FIELD_IDS)


def question_fields(persona: Persona) -> tuple[PersonaField, ...]:
    """The questions, in the order the form asks them."""
    return _select(persona, QUESTION_FIELD_IDS)


def _select(persona: Persona, field_ids: frozenset[str]) -> tuple[PersonaField, ...]:
    return tuple(entry for entry in persona.fields if entry.field_id in field_ids)
