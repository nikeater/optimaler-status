"""The demo personas: what they are, what they must not be, what they produce.

Three groups of assertions, and the middle one is the one that would be a
comment in a worse repository.

1. **The file loads and says what it must.** A persona with no fields renders an
   empty form; a field with no path drops a typed value on the floor. Both are
   refused at load time rather than shown to a visitor.
2. **No persona value collides with a frozen set.** `corpus/pii_golden/` is the
   redaction-recall canary set and `corpus/gold/` is the measured corpus. A
   persona sharing a string with either would make the canary sweep over the
   demo pages unable to tell a leak from a persona, and would let a recall
   number be reached by memorisation.
3. **Each persona still produces its arc.** Since part 22 all four file a
   Statusfeststellung nach par. 7a SGB IV, and the four arcs the showcase
   promises are a complete application that still lands with a human, a
   tier-2 Nachforderung naming the one missing answer, a case whose procedure
   is read out of its content because it carries no channel hint, and a
   complete case the shadow scorer flags. Those are properties of the REAL
   pipeline over these values, so a config edit that quietly broke an arc has
   to fail here rather than on a screen in front of an audience.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from engine.config_loader import ConfigBundle
from engine.demo.personas import (
    CHANNEL_EMAIL,
    CHANNEL_FORM,
    Persona,
    PersonaError,
    build_form_submission,
    build_letter_submission,
    demo_personas,
    load_personas,
    selected_attachments,
)
from engine.journal import InMemoryJournalStore
from engine.pipeline import PipelineResult, run_pipeline
from engine.redact import InMemoryVaultStore
from engine.redact.recognizers import vsnr_checksum_ok

NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)

#: What each persona must produce through the FORM tab. The arcs of the
#: showcase, pinned: (procedure, tier, unit, flagged).
#:
#: PART 22: ONE PROCEDURE AND ONE UNIT FOR ALL FOUR, which is the refocus stated
#: as a table. What still differs between them is everything that matters -
#: complete against incomplete, hinted against derived, flagged against quiet -
#: and the two tiers read the way `statusfeststellung_v1.yaml` explains: a
#: COMPLETE par. 7a application matches no row of the decision table and lands
#: on default_tier 3 (a human decides the whole thing), an INCOMPLETE one
#: matches the tier-2 row (routable, ask for what is missing). Tier 1 is not
#: reachable here at all, because the procedure disables it.
FORM_ARCS = {
    "schliebermann_statusfeststellung": (
        "statusfeststellung",
        3,
        "Referat_340_Clearingstelle",
        False,
    ),
    "beispielmann_ohne_taetigkeitsbeginn": (
        "statusfeststellung",
        2,
        "Referat_340_Clearingstelle",
        False,
    ),
    "musterfrau_statusfeststellung": (
        "statusfeststellung",
        3,
        "Referat_340_Clearingstelle",
        False,
    ),
    "musterkind_taetigkeitsbeginn_voraus": (
        "statusfeststellung",
        3,
        "Referat_340_Clearingstelle",
        True,
    ),
}


def run(
    config: ConfigBundle, payload: dict[str, object]
) -> tuple[PipelineResult, InMemoryJournalStore]:
    journal = InMemoryJournalStore()
    result = run_pipeline(
        payload,
        config=config,
        journal=journal,
        vault=InMemoryVaultStore(),
        now=NOW,
    )
    return result, journal


# ------------------------------------------------------------ the loading ---


def test_the_shipped_persona_file_loads_and_covers_the_four_arcs() -> None:
    personas = demo_personas()
    assert personas.version == "personas_v4"
    assert {persona.persona_id for persona in personas.personas} == set(FORM_ARCS)
    assert personas.note
    assert len(personas.hints) == 5
    for persona in personas.personas:
        assert persona.display_name
        assert persona.headline
        assert persona.story
        assert persona.expectation
        assert persona.fields
        assert persona.letter.endswith("\n")


def test_an_unknown_persona_id_is_none_and_never_an_error() -> None:
    """Same discipline as the unit picker: a stale bookmark shows the picker."""
    personas = demo_personas()
    assert personas.get("does-not-exist") is None
    assert personas.get("") is None
    assert personas.get(None) is None
    assert personas.get(personas.first.persona_id) is personas.first


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("[]", "not a YAML mapping"),
        ("version: x\npersonas: []\n", "defines no personas"),
        ("version: x\npersonas: [{}]\n", "no persona_id"),
        ("version: x\npersonas: [{persona_id: p}]\n", "has no fields"),
        (
            "version: x\npersonas: [{persona_id: p, "
            "fields: [{field_id: a, path: b.c}]}]\n",
            "has no letter",
        ),
        (
            "version: x\npersonas: [{persona_id: p, letter: hi, "
            "fields: [{field_id: a}]}]\n",
            "needs a field_id and a path",
        ),
        (
            "version: x\npersonas: [{persona_id: p, letter: hi, fields: [nope]}]\n",
            "a field is not a mapping",
        ),
        ("version: x\npersonas: [nope]\n", "a persona is not a mapping"),
        # Part 20: an attachment that could not be rendered, in every way it
        # could fail. All of them are LOAD errors rather than render-time ones,
        # because the alternative is a visitor being shown a document with
        # `{versicherungsnummer}` printed in it.
        (
            "version: x\npersonas: [{persona_id: p, letter: hi, "
            "fields: [{field_id: a, path: b.c}], attachments: nope}]\n",
            "attachments is not a list",
        ),
        (
            "version: x\npersonas: [{persona_id: p, letter: hi, "
            "fields: [{field_id: a, path: b.c}], attachments: [nope]}]\n",
            "an attachment is not a mapping",
        ),
        (
            "version: x\npersonas: [{persona_id: p, letter: hi, "
            "fields: [{field_id: a, path: b.c}], "
            "attachments: [{attachment_id: d, text: x}]}]\n",
            "needs an attachment_id, a filename and a label",
        ),
        (
            "version: x\npersonas: [{persona_id: p, letter: hi, "
            "fields: [{field_id: a, path: b.c}], "
            "attachments: [{attachment_id: d, filename: d.pdf, label: D}]}]\n",
            "has no text",
        ),
        (
            "version: x\npersonas: [{persona_id: p, letter: hi, "
            "fields: [{field_id: a, path: b.c, value: v}], "
            "attachments: [{attachment_id: d, filename: d.pdf, label: D, "
            "text: 'Nummer: {gibtesnicht}'}]}]\n",
            "which is not one of this persona's fields",
        ),
    ],
)
def test_a_persona_file_that_would_render_wrong_is_refused(
    tmp_path: Path, document: str, message: str
) -> None:
    path = tmp_path / "personas.yaml"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(PersonaError) as raised:
        load_personas(path)
    assert message in str(raised.value)


def test_an_unreadable_persona_file_names_itself(tmp_path: Path) -> None:
    with pytest.raises(PersonaError) as raised:
        load_personas(tmp_path / "absent.yaml")
    assert "cannot read persona file" in str(raised.value)


def test_a_field_id_the_persona_does_not_have_is_none() -> None:
    """Looked up by name in tests and in the collision checks; never an error."""
    chosen = _persona("schliebermann_statusfeststellung")
    assert chosen.field("taetigkeit_bezeichnung") is not None
    assert chosen.field("lieblingsfarbe") is None


def test_a_half_written_hint_is_dropped_rather_than_half_rendered(
    tmp_path: Path,
) -> None:
    """A hint is a label AND a sentence; either alone is a bullet that says nothing.

    Deliberately not an error, unlike a malformed persona: a hint is decoration
    on a panel, and a deployment that trimmed one should lose the bullet rather
    than the page.
    """
    path = tmp_path / "personas.yaml"
    path.write_text(
        "version: t\n"
        "hints:\n"
        "  - not-a-mapping\n"
        "  - {label: nur ein Label}\n"
        "  - {detail: nur ein Satz}\n"
        "  - {label: gut, detail: vollstaendig}\n"
        "personas:\n"
        "  - persona_id: p\n"
        "    letter: hallo\n"
        "    fields: [{field_id: a, path: b.c, value: x}]\n",
        encoding="utf-8",
    )
    personas = load_personas(path)
    assert personas.hints == (("gut", "vollstaendig"),)
    assert personas.get("p") is not None
    # A hints block that is not a list at all is simply no hints.
    path.write_text(
        "version: t\nhints: nope\npersonas:\n"
        "  - persona_id: p\n    letter: hallo\n"
        "    fields: [{field_id: a, path: b.c, value: x}]\n",
        encoding="utf-8",
    )
    assert load_personas(path).hints == ()


# ------------------------------------------------------- the collision rule ---


def identity_values() -> list[tuple[str, str]]:
    """Every persona string that is supposed to be identity-shaped.

    Short values (a house number, "ja") are excluded: they are not identifying
    and a substring rule over them would fire on any five-digit number in any
    corpus letter.
    """
    values: list[tuple[str, str]] = []
    for persona in demo_personas().personas:
        values.append((persona.persona_id, persona.display_name))
        for field in persona.fields:
            if field.identity and len(field.value) >= 4:
                values.append((f"{persona.persona_id}.{field.field_id}", field.value))
    return values


def frozen_text(gold_v4_dir: Path) -> str:
    """Everything the frozen sets say, as one string to search."""
    chunks = [
        path.read_text(encoding="utf-8")
        for path in sorted(Path("corpus/pii_golden").glob("*.yaml"))
    ]
    for directory in sorted(Path("corpus/gold").iterdir()):
        if directory.is_dir():
            chunks.extend(
                path.read_text(encoding="utf-8")
                for path in sorted(directory.glob("*.json"))
            )
    chunks.append(str(gold_v4_dir))
    return "\n".join(chunks)


def test_no_persona_value_collides_with_a_frozen_set(gold_v4_dir: Path) -> None:
    """The rule the persona file's header states, asserted over every value."""
    haystack = frozen_text(gold_v4_dir)
    for where, value in identity_values():
        assert value not in haystack, (
            f"persona value {value!r} ({where}) also occurs in a frozen set; "
            "a persona that shares a string with the canaries or the gold "
            "corpus makes the canary sweep unable to tell a leak from a persona"
        )
    # And each half separately, so a future move of a corpus cannot silently
    # empty the haystack and leave this test passing on nothing.
    assert "11040650L949" in haystack, "the pii_golden canaries must be in scope"
    assert "17170459B012" in haystack, "the gold corpus must be in scope"


#: The one persona name that carries no Muster/Beispiel marker, named here so
#: the exception is countable rather than implied (part 22, the user's own
#: naming choice for the new persona). What keeps the rule's PURPOSE intact for
#: her: the surname is invented and collides with nothing in either frozen set
#: (the test above), and the page says in four separate places that every
#: applicant on it is made up. A second unmarked name does not join her by
#: being added to the file - it fails the test below until somebody adds it
#: here on purpose.
UNMARKED_NAMES = frozenset({"Beate Schliebermann"})


def test_every_persona_name_is_mustermann_class() -> None:
    """Unmistakably fictional to a German reader - the rule, not a preference."""
    marker = ("muster", "beispiel", "demo")
    for persona in demo_personas().personas:
        if persona.display_name in UNMARKED_NAMES:
            continue
        lowered = persona.display_name.lower()
        assert any(part in lowered for part in marker), (
            f"{persona.display_name!r} is not obviously fictional; a demo "
            "persona indistinguishable from a real person ends up on a "
            "screenshot as one. If that is deliberate, add it to "
            "UNMARKED_NAMES with a reason rather than widening the marker list"
        )


def test_the_declared_naming_exception_is_a_persona_that_exists() -> None:
    """An exemption for a name nobody carries would silently outlive its reason."""
    names = {persona.display_name for persona in demo_personas().personas}
    assert names >= UNMARKED_NAMES, UNMARKED_NAMES - names


def test_every_persona_versicherungsnummer_is_checksum_valid_for_its_birthdate() -> (
    None
):
    """Both halves are load-bearing, and for two different reasons.

    The checksum: the deterministic recognizer is checksum-GATED in prose, so a
    number with a broken check digit would not be sealed out of a letter and
    the boundary would refuse the submission. The birth date: the completeness
    checker cross-checks positions 3 to 8 against the Geburtsdatum through the
    transient witness, so a mismatched pair would make every persona look
    invalid.
    """
    for persona in demo_personas().personas:
        vsnr = persona.field("versicherungsnummer")
        birthdate = persona.field("geburtsdatum")
        assert vsnr is not None and birthdate is not None
        assert vsnr_checksum_ok(vsnr.value), persona.persona_id
        day, month, year = (
            birthdate.value[8:10],
            birthdate.value[5:7],
            birthdate.value[2:4],
        )
        assert vsnr.value[2:8] == f"{day}{month}{year}", persona.persona_id


def test_the_persona_file_is_not_read_by_the_config_loader(
    config: ConfigBundle,
) -> None:
    """`config/demo/` is invisible to the bundle, so it cannot move a number.

    The standing lesson of parts 06 to 12 in one assertion: the version stamp
    that gold v4's manifest freezes has no slot for a persona file, and the
    loader never opens the directory.
    """
    stamp = config.version_stamp().model_dump(mode="json")
    assert "personas" not in json.dumps(stamp)
    document = yaml.safe_load(
        Path("config/demo/personas_v4.yaml").read_text(encoding="utf-8")
    )
    assert document["version"] == "personas_v4"
    # Only the current version stays on disk - the house rule for a superseded
    # versioned file, and the reason a reader never has to ask which one is live.
    assert sorted(p.name for p in Path("config/demo").glob("personas_*.yaml")) == [
        "personas_v4.yaml"
    ]


# --------------------------------------------------- the prepared documents ---


def attachment_texts() -> list[tuple[str, str]]:
    """Every rendered document text, with the persona it belongs to."""
    return [
        (f"{persona.persona_id}.{entry.attachment_id}", entry.text)
        for persona in demo_personas().personas
        for entry in persona.attachments
    ]


def frozen_identity_values() -> set[str]:
    """Every identity string the two frozen sets hold, long enough to search.

    The mirror image of :func:`frozen_text`. That function asks "does a persona
    value occur in a frozen set"; this one is for the new direction part 20
    opens: an attachment text is PROSE somebody wrote, and prose is where a
    canary string could be copied in by accident.
    """
    values: set[str] = set()
    document = yaml.safe_load(
        Path("corpus/pii_golden/items.yaml").read_text(encoding="utf-8")
    )
    for item in document.get("items") or []:
        text = str(item.get("text", ""))
        for label in item.get("labels") or []:
            piece = text[int(label["start"]) : int(label["end"])].strip()
            if len(piece) >= 4:
                values.add(piece)
    for directory in sorted(Path("corpus/gold").iterdir()):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            applicant = (payload.get("data") or {}).get("antragsteller") or {}
            address = applicant.get("anschrift") or {}
            for value in (
                applicant.get("name"),
                applicant.get("versicherungsnummer"),
                address.get("strasse"),
                address.get("ort"),
            ):
                piece = str(value or "").strip()
                if len(piece) >= 4:
                    values.add(piece)
    return values


def test_every_persona_brings_two_to_four_recognisable_documents() -> None:
    """The shape of the offer, and the two halves of "honestly labelled".

    A document has a NAME a German caseworker recognises and a FILE NAME that
    says PDF, because the fieldset presents them as the enclosures they stand
    for. Both are asserted here rather than eyeballed, along with the English
    siblings every visitor-facing string in this file has to have.
    """
    for persona in demo_personas().personas:
        assert 2 <= len(persona.attachments) <= 4, persona.persona_id
        ids = [entry.attachment_id for entry in persona.attachments]
        assert len(set(ids)) == len(ids), persona.persona_id
        for entry in persona.attachments:
            where = f"{persona.persona_id}.{entry.attachment_id}"
            assert entry.filename.endswith(".pdf"), where
            assert entry.label and entry.label_en, where
            assert entry.label_en != entry.label, where
            assert entry.note and entry.note_en, where
            assert entry.note_en != entry.note, where
            assert entry.label_for("en") == entry.label_en
            assert entry.label_for("de") == entry.label
            assert entry.note_for("en") == entry.note_en
            assert entry.note_for("de") == entry.note
            assert entry.field_name == f"anlage-{entry.attachment_id}"
            assert persona.attachment(entry.attachment_id) is entry
        assert persona.attachment("keine-solche-anlage") is None


def test_a_document_is_rendered_from_its_own_personas_values() -> None:
    """Deterministic by construction, which is what makes it recordable.

    The text is a template over the persona's own field ids, substituted once
    at load time. Two things follow and both are checked: nothing unresolved
    survives into a document a visitor is shown, and the values in it are that
    persona's own rather than a copy that could drift from the form beside it.
    """
    for persona in demo_personas().personas:
        for entry in persona.attachments:
            where = f"{persona.persona_id}.{entry.attachment_id}"
            assert "{" not in entry.text and "}" not in entry.text, where
            for field_id in ("nachname", "vorname"):
                field = persona.field(field_id)
                assert field is not None
                assert field.value in entry.text, f"{where}: {field_id}"


def test_no_document_makes_a_second_procedure_fire() -> None:
    """The derivation rule of the config header, asserted over the text.

    ``text.normalized`` is where the content signals of the procedure configs
    are read, and two procedures matching at once is an AMBIGUITY that resolves
    to "no procedure" and therefore to tier 3 (ADR-013). A document that named
    a Rentenbeginn on a Statusfeststellung, or an Auftraggeber and a Taetigkeit
    on an Altersrente, would take its persona's arc away - so the words that
    would do it are listed here rather than left to a reviewer's memory.

    ``contains`` and ``matches`` are case-insensitive, so the check is too.
    """
    forbidden = {
        "altersrente": ("statusfeststellung", "erwerbsstatus", "par. 7a"),
        "statusfeststellung": ("altersrente", "rentenbeginn"),
    }
    everywhere = ("erwerbsminderungsrente", "eintritt der erwerbsminderung")
    for persona in demo_personas().personas:
        # Which procedure this persona IS, by the same two facts the pipeline
        # uses: the channel hint, or the payload signals of the form.
        procedure = persona.procedure_hint or "statusfeststellung"
        for entry in persona.attachments:
            lowered = entry.text.lower()
            where = f"{persona.persona_id}.{entry.attachment_id}"
            for word in (*forbidden.get(procedure, ()), *everywhere):
                assert word not in lowered, f"{where} says {word!r}"
            if procedure != "statusfeststellung":
                assert not ("auftraggeber" in lowered and "taetigkeit" in lowered), (
                    f"{where} names an Auftraggeber and a Taetigkeit together, "
                    "which is the statusfeststellung content signal"
                )


def test_no_document_text_collides_with_a_frozen_set() -> None:
    """The collision rule, in the direction an attachment opens.

    A document is prose, and prose is where a canary string gets copied in by
    accident. The invented institutions in these texts (a Beispielkasse, a
    Beispielbetrieb) are Mustermann-class for the same reason the people are.
    """
    frozen = frozen_identity_values()
    assert "11040650L949" in frozen, "the pii_golden canaries must be in scope"
    assert "17170459B012" in frozen, "the gold corpus must be in scope"
    for where, text in attachment_texts():
        for value in frozen:
            assert value not in text, f"{where} carries the frozen value {value!r}"


def test_every_document_seals_clean_without_the_optional_model(
    config: ConfigBundle,
) -> None:
    """The letter rule of the config header, extended to the documents.

    Same reason, one step sharper: the hosted image ships without the
    ``[redact]`` extra, so a document whose sealing needed spaCy would be
    refused by the boundary there and accepted here. Every persona's whole set
    is ticked at once, which is also the worst case for the sweep.
    """
    for persona in demo_personas().personas:
        result, _ = run(config, _form(persona.persona_id, attachments=True))
        assert result.envelope.redaction_verified is True, persona.persona_id
        text = "".join(part.redacted_text or "" for part in result.envelope.parts)
        assert text, f"{persona.persona_id}: no free-text part was produced"
        for field in persona.fields:
            if field.identity and len(field.value) >= 4:
                assert field.value not in text, (
                    f"{persona.persona_id}: {field.field_id} survived into a "
                    "document's working copy"
                )


def test_a_ticked_document_becomes_a_real_part_of_the_envelope(
    config: ConfigBundle,
) -> None:
    """Not a label on a page: a part the whole pipeline runs over.

    The submission carries the attachment in the shape ``engine/ingest`` reads
    (``text`` plus a ``sourceType``), the envelope grows one free-text
    ContentPart per document plus one RawRef naming the file, and the sealing
    counts are reported per part. Born-digital rather than OCR, because that
    decides whether a span is verified exactly or fuzzily.
    """
    persona = _persona("schliebermann_statusfeststellung")
    ticked = persona.attachments[0]
    payload = build_form_submission(
        persona,
        {**persona.form_values(), ticked.field_name: "1"},
        submission_id="demo-anlage",
        submitted_at=NOW.isoformat(),
    )
    assert payload["attachments"] == [
        {
            "id": ticked.attachment_id,
            "filename": ticked.filename,
            "mediaType": "application/pdf",
            "sourceType": "born_digital",
            "text": ticked.text,
        }
    ]
    # An unticked box posts nothing, and nothing ticked is the pre-part-20
    # submission byte for byte.
    assert (
        build_form_submission(
            persona,
            persona.form_values(),
            submission_id="demo-anlage",
            submitted_at=NOW.isoformat(),
        )["attachments"]
        == []
    )

    result, _ = run(config, payload)
    parts = [part for part in result.envelope.parts if part.redacted_text is not None]
    assert [part.part_id for part in parts] == ["part-text-0"]
    assert parts[0].source_type.value == "born_digital"
    assert parts[0].media_type == "text/plain"
    assert [ref.filename for ref in result.envelope.raw_refs if ref.filename] == [
        ticked.filename
    ]
    assert result.redaction is not None
    assert result.redaction.text_sealed_counts["part-text-0"] > 0
    # The text layer really was built over it, which is what makes a span in it
    # verifiable at all.
    assert result.text_layer is not None
    assert [part.part_id for part in result.text_layer.parts] == ["part-text-0"]


def test_a_checkbox_for_a_document_a_persona_does_not_have_is_ignored() -> None:
    """The same "never half-select something" rule the pickers follow."""
    persona = _persona("schliebermann_statusfeststellung")
    values = {
        **persona.form_values(),
        "anlage-gibt-es-nicht": "1",
        # An unticked box posts nothing; a blank one is not a tick either.
        persona.attachments[0].field_name: "",
    }
    assert selected_attachments(persona, values) == ()
    payload = build_form_submission(
        persona, values, submission_id="demo-x", submitted_at=NOW.isoformat()
    )
    assert payload["attachments"] == []
    assert "gibt-es-nicht" not in json.dumps(payload)


def test_the_documents_are_offered_in_the_order_the_file_lists_them() -> None:
    """Ticking a subset keeps that order, because a reader reads a list."""
    persona = _persona("musterfrau_statusfeststellung")
    first, _second, third = persona.attachments
    chosen = selected_attachments(
        persona,
        {third.field_name: "1", first.field_name: "1"},
    )
    assert [entry.attachment_id for entry in chosen] == [
        first.attachment_id,
        third.attachment_id,
    ]


def test_an_attachment_fixture_would_only_ever_be_a_duplicate(
    config: ConfigBundle,
) -> None:
    """WHY THE DOCUMENTS CARRY NO ``extractionFixture``, measured rather than said.

    The corpus ships a fixture next to every generated letter so the replay
    extractor can hand the verifier proposals. It cannot do that on this
    channel, and the reason is structural: a form submission's payload already
    fills every field the procedure's ``field_map`` declares, and ADR-020's
    precedence rule says the schema mapper wins - so a text proposal over any
    of them is discarded as ``duplicate_field`` BEFORE the double lock runs.

    The cost is not theoretical either, and part 22 changed only where it is
    VISIBLE. ``extraction.discarded_count == 0`` is still a qualifying condition
    of the tier-1 row - read out of the shipped decision table below rather than
    remembered - so one such entry still costs a tier-1 row its qualification on
    any procedure that has one. It cannot be shown on these four personas any
    more, because ``statusfeststellung_v1.yaml`` disables tier 1 outright and
    all four are already at the tier a discard would have pushed them to. So
    what this test measures now is the discard itself, plus the condition that
    makes it expensive, plus the ONE arc a fixture would still take away here:
    Bernd Beispielmann's, whose payload does NOT carry the field he leaves
    empty, so a proposal over it would not be a duplicate at all. That is the
    reason his documents say "noch offen" instead of a date.
    """
    persona = _persona("schliebermann_statusfeststellung")
    ticked = persona.attachments[0]
    payload = build_form_submission(
        persona,
        {**persona.form_values(), ticked.field_name: "1"},
        submission_id="demo-fixture-probe",
        submitted_at=NOW.isoformat(),
    )
    payload["extractionFixture"] = [
        {
            "field": "versicherungsnummer",
            "part_id": "part-text-0",
            "anchor": "Versicherungsnummer:",
            "mode": "sealed",
        }
    ]
    result, _ = run(config, payload)
    assert result.extraction is not None
    assert result.extraction.failure_counts() == {"duplicate_field": 1}
    assert result.extractions.discarded_count == 1
    # Without the fixture nothing is discarded, so the entry is what produced
    # the discard and nothing else did.
    del payload["extractionFixture"]
    unfixed, _ = run(config, payload)
    assert unfixed.extractions.discarded_count == 0

    # And the condition that makes a discard cost a tier, read from the table
    # that would apply it rather than from memory.
    tier1 = next(row for row in config.decision_table.rows if int(row.tier) == 1)
    assert any(
        condition.field == "extraction.discarded_count" and condition.value == 0
        for condition in tier1.when_all
    ), "the tier-1 row no longer requires a clean extraction"

    # The one arc a fixture could still take away here: the gap persona's
    # payload does not carry the field he leaves empty.
    gap_persona = _persona("beispielmann_ohne_taetigkeitsbeginn")
    gap_payload = build_form_submission(
        gap_persona,
        gap_persona.form_values(),
        submission_id="demo-fixture-gap",
        submitted_at=NOW.isoformat(),
    )
    data = gap_payload["data"]
    assert isinstance(data, dict)
    assert "taetigkeit_beginn" not in data["antrag"]
    for entry in gap_persona.attachments:
        assert "Beginn der Taetigkeit: noch offen" in entry.text, entry.attachment_id


# ------------------------------------------------------------- the arcs ---


@pytest.mark.parametrize("persona_id", sorted(FORM_ARCS))
def test_each_persona_produces_its_arc_through_the_form(
    config: ConfigBundle, persona_id: str
) -> None:
    persona = demo_personas().get(persona_id)
    assert persona is not None
    payload = build_form_submission(
        persona,
        persona.form_values(),
        submission_id=f"demo-{persona_id}",
        submitted_at=NOW.isoformat(),
    )
    assert payload["channel"] == CHANNEL_FORM
    result, _journal = run(config, payload)
    procedure, tier, unit, flagged = FORM_ARCS[persona_id]
    assert result.procedure_id == procedure
    assert int(result.decision.tier) == tier
    assert result.decision.routed_unit_id == unit
    assert (result.anomaly is not None and result.anomaly.flagged) is flagged
    assert result.envelope.redaction_verified is True
    assert result.redaction is not None
    assert result.redaction.sealed_count >= 4


@pytest.mark.parametrize("persona_id", sorted(FORM_ARCS))
def test_enclosing_every_document_leaves_the_arc_where_it_was(
    config: ConfigBundle, persona_id: str
) -> None:
    """Part 20's arc decision, and it IS a decision rather than a coincidence.

    Attachments add evidence, and evidence is allowed to move a case. What was
    decided for these four is that it must not: each persona exists to
    demonstrate ONE thing, and a demo where enclosing the documents an agency
    asks for makes the case worse would teach the opposite of what it means.
    The documents were designed against that - none of them names a field its
    persona's form does not already carry, and none of them carries a fixture
    (see the test above for the measurement) - so procedure, tier, unit and
    flag are the same with every box ticked as with none.

    What DOES move is the part the demonstration is about and it is asserted
    here in the same breath: more free-text parts, more sealed spans, per-part
    counts where there were none. The one number that moves without changing an
    arc is Bernd Beispielmann's anomaly score (0.109 -> 0.505, threshold 0.86):
    an item with prose in it is a rarer shape than a bare form, the scorer says
    so, and the flag stays off. That is the scorer being honest, not a defect.
    """
    persona = demo_personas().get(persona_id)
    assert persona is not None
    bare, _ = run(config, _form(persona_id))
    laden, _ = run(config, _form(persona_id, attachments=True))

    procedure, tier, unit, flagged = FORM_ARCS[persona_id]
    assert laden.procedure_id == procedure
    assert int(laden.decision.tier) == tier
    assert laden.decision.routed_unit_id == unit
    assert (laden.anomaly is not None and laden.anomaly.flagged) is flagged
    assert laden.derivation.source == bare.derivation.source
    assert laden.clear_cut is bare.clear_cut
    assert laden.evidence.completeness.verdict == bare.evidence.completeness.verdict
    assert [gap.requirement_id for gap in laden.evidence.completeness.gaps] == [
        gap.requirement_id for gap in bare.evidence.completeness.gaps
    ]
    assert laden.extractions.discarded_count == bare.extractions.discarded_count

    assert laden.redaction is not None and bare.redaction is not None
    assert laden.redaction.sealed_count > bare.redaction.sealed_count
    assert len(laden.redaction.text_sealed_counts) == len(persona.attachments)
    assert bare.redaction.text_sealed_counts == {}
    assert all(count > 0 for count in laden.redaction.text_sealed_counts.values())


def test_the_complete_persona_still_lands_with_a_human(config: ConfigBundle) -> None:
    """Arc 1: nothing missing, and that is exactly why it is tier 3.

    The par. 7a shape, asserted rather than described: a COMPLETE application
    fails the tier-1 row on ``procedure.tier1_enabled`` and the tier-2 row on
    ``completeness.verdict``, so no row matches and the table's own default
    tier 3 applies. Both reasons are checked, because "no row matched" is only
    reassuring when the rows are the ones that were meant to fail.
    """
    result, _ = run(config, _form("schliebermann_statusfeststellung"))
    assert result.evidence.completeness.verdict.value == "complete"
    assert result.evidence.completeness.gaps == []
    assert result.clear_cut is False
    assert int(result.decision.tier) == 3
    reasons = {reason.rule_id: reason for reason in result.decision.reasons}
    assert reasons["default"].kind.value == "defaulted"
    assert "tier1_enabled" in reasons["tier1_clear_and_complete"].detail
    assert "complete" in reasons["tier2_routable_incomplete"].detail


def test_the_gap_persona_reports_exactly_the_field_it_left_blank(
    config: ConfigBundle,
) -> None:
    """Arc 2: an empty field is MISSING, never invalid - different wording."""
    result, _ = run(config, _form("beispielmann_ohne_taetigkeitsbeginn"))
    gaps = result.evidence.completeness.gaps
    assert [gap.requirement_id for gap in gaps] == ["taetigkeit_beginn"]
    assert gaps[0].status.value == "missing"
    assert any(
        "wann die zu beurteilende Taetigkeit begonnen hat" in rendering.sentence
        for rendering in result.gap_renderings
    )
    assert int(result.decision.tier) == 2


def test_the_anomaly_persona_is_flagged_with_readable_reasons(
    config: ConfigBundle,
) -> None:
    """Arc 4: complete and permissible by the rules AND flagged by the scorer.

    The demonstration of ADR-024: the shadow scorer runs in log_only mode, so a
    flag is a sentence a caseworker reads and never a tier the machine changed.
    Since part 22 the case is a Statusfeststellung, so the tier it does not move
    is 3 rather than 1 - the flag moves nothing in the strongest possible sense,
    and the reason it names is the distance of the procedure's own leading date.
    """
    result, _ = run(config, _form("musterkind_taetigkeitsbeginn_voraus"))
    assert result.anomaly is not None
    assert result.anomaly.flagged is True
    assert result.anomaly.reasons
    assert result.evidence.completeness.verdict.value == "complete"
    assert int(result.decision.tier) == 3
    assert int(result.decision.pre_downgrade_tier) == 3
    # The reason is about the leading date this procedure declares, in words.
    assert any(
        reason.feature == "leitdatum_abstand_jahre" for reason in result.anomaly.reasons
    )
    assert any(
        "taetigkeit_beginn" in reason.observed for reason in result.anomaly.reasons
    )


def test_the_letter_tab_derives_its_procedure_from_the_sealed_text(
    config: ConfigBundle,
) -> None:
    """Arc 3: a prose letter with no hint and no structured data.

    Everything the derivation reads has already been through the boundary: the
    company name in the letter is a placeholder by the time a rule sees it, so
    what carries the derivation is the WORD "Auftraggeber", not the
    Auftraggeber (ADR-020).
    """
    persona = demo_personas().get("musterfrau_statusfeststellung")
    assert persona is not None
    payload = build_letter_submission(
        persona,
        persona.letter,
        submission_id="demo-sf-letter",
        submitted_at=NOW.isoformat(),
    )
    assert payload["channel"] == CHANNEL_EMAIL
    assert "procedureHint" not in payload
    assert payload["data"] == {}
    result, _ = run(config, payload)
    assert result.procedure_id == "statusfeststellung"
    assert result.derivation.source.value == "content"
    assert result.decision.routed_unit_id == "Referat_340_Clearingstelle"
    assert result.redaction is not None
    assert result.redaction.text_sealed_count >= 6
    assert result.envelope.redaction_verified is True


def test_every_persona_letter_seals_clean_without_the_optional_model(
    config: ConfigBundle,
) -> None:
    """The letter rule of the config header, asserted.

    The hosted demo image ships without the ``[redact]`` extra. A letter whose
    sealing depended on spaCy would be refused by the boundary there and
    accepted here, which is the one asymmetry ADR-019 ruling 2 exists to
    prevent.
    """
    for persona in demo_personas().personas:
        result, _ = run(
            config,
            build_letter_submission(
                persona,
                persona.letter,
                submission_id=f"demo-letter-{persona.persona_id}",
                submitted_at=NOW.isoformat(),
            ),
        )
        assert result.envelope.redaction_verified is True, persona.persona_id
        text = "".join(part.redacted_text or "" for part in result.envelope.parts)
        for field in persona.fields:
            if field.identity and len(field.value) >= 4:
                assert field.value not in text, (
                    f"{persona.persona_id}: {field.field_id} survived into the "
                    "working copy"
                )


# ------------------------------------------------------- the submissions ---


def test_a_blank_field_is_omitted_rather_than_sent_empty() -> None:
    """ "Missing" and "invalid" are different verdicts with different wording."""
    persona = _persona("schliebermann_statusfeststellung")
    payload = build_form_submission(
        persona,
        {**persona.form_values(), "versicherungsnummer": "   "},
        submission_id="demo-blank",
        submitted_at=NOW.isoformat(),
    )
    data = payload["data"]
    assert isinstance(data, dict)
    assert "versicherungsnummer" not in data["antragsteller"]
    assert (
        data["antragsteller"]["geburtsdatum"] == persona.form_values()["geburtsdatum"]
    )


def test_an_edited_value_reaches_the_submission_at_its_declared_path() -> None:
    persona = _persona("schliebermann_statusfeststellung")
    payload = build_form_submission(
        persona,
        {
            **persona.form_values(),
            "ort": "Musterbucht",
            "antragsart": "prognose_vor_aufnahme",
        },
        submission_id="demo-edit",
        submitted_at=NOW.isoformat(),
    )
    data = payload["data"]
    assert isinstance(data, dict)
    assert data["antragsteller"]["anschrift"]["ort"] == "Musterbucht"
    assert data["antrag"]["antragsart"] == "prognose_vor_aufnahme"


def test_a_value_for_an_unknown_field_id_is_ignored() -> None:
    """The form posts what the page rendered; anything else is not a field."""
    persona = _persona("schliebermann_statusfeststellung")
    payload = build_form_submission(
        persona,
        {**persona.form_values(), "smuggled": "x"},
        submission_id="demo-smuggle",
        submitted_at=NOW.isoformat(),
    )
    assert "smuggled" not in json.dumps(payload)


def test_the_split_name_produces_the_envelope_the_single_field_produced() -> None:
    """The part-16 identity claim, written out rather than argued.

    The form asks for the surname and the given name in two boxes, in that
    order, because that is how a German administrative form asks. What reaches
    the submission is the one string ``antragsteller.name`` has always carried,
    at the same path, with the same neighbours in the same order - which is
    what makes every downstream arc unchanged rather than merely equivalent.
    The dictionary below is the whole payload, typed out here so that a future
    edit to the persona file has to disagree with a literal instead of with
    another derivation of itself. Part 22 retyped it for a Statusfeststellung
    applicant; the shape of the claim did not move, only which fields ride in
    ``antrag`` and the ``auftraggeber`` namespace no Altersrente form has.
    """
    persona = _persona("schliebermann_statusfeststellung")
    payload = build_form_submission(
        persona,
        persona.form_values(),
        submission_id="demo-split",
        submitted_at=NOW.isoformat(),
    )
    assert payload["data"] == {
        "antragsteller": {
            "name": "Beate Schliebermann",
            "geburtsdatum": "1979-05-14",
            "versicherungsnummer": "24140579S013",
            "anschrift": {
                "strasse": "Prickenweg",
                "hausnummer": "4",
                "plz": "24939",
                "ort": "Musterwarft",
            },
        },
        "auftraggeber": {"firmenname": "Nordlicht Beispieltechnik GmbH"},
        "antrag": {
            "antragsart": "feststellung_nach_aufnahme",
            "antragsteller_rolle": "auftragnehmer",
            "taetigkeit_bezeichnung": "Technische Redaktion und Dokumentation",
            "taetigkeit_beginn": "2026-03-02",
        },
    }
    # The key ORDER too, because a byte-identical envelope is the claim.
    assert list(payload["data"]["antragsteller"]) == [
        "name",
        "geburtsdatum",
        "versicherungsnummer",
        "anschrift",
    ]


def test_a_half_of_the_name_left_blank_is_dropped_rather_than_joined() -> None:
    """Emptying one box submits the other alone, with no stray space."""
    persona = _persona("beispielmann_ohne_taetigkeitsbeginn")
    values = {**persona.form_values(), "vorname": "  "}
    data = build_form_submission(
        persona, values, submission_id="demo-half", submitted_at=NOW.isoformat()
    )["data"]
    assert isinstance(data, dict)
    assert data["antragsteller"]["name"] == "Beispielmann"
    # And emptying both drops the field, exactly as a blank single box did.
    both = build_form_submission(
        persona,
        {**values, "nachname": ""},
        submission_id="demo-none",
        submitted_at=NOW.isoformat(),
    )["data"]
    assert isinstance(both, dict)
    assert "name" not in both["antragsteller"]


def test_the_form_order_and_the_value_order_are_different_on_purpose() -> None:
    """Nachname first on the screen, Vorname first in the value."""
    for persona in demo_personas().personas:
        fields = [entry.field_id for entry in persona.fields]
        assert fields[0] == "nachname"
        assert fields[1] == "vorname"
        surname = persona.field("nachname")
        given = persona.field("vorname")
        assert surname is not None and given is not None
        assert surname.path == given.path == "antragsteller.name"
        assert given.join_order < surname.join_order
        assert f"{given.value} {surname.value}" == persona.display_name


def test_every_control_a_persona_declares_is_one_the_form_can_render() -> None:
    """A typo in `control` would render a text box where a date was meant."""
    for persona in demo_personas().personas:
        for entry in persona.fields:
            assert entry.control in ("text", "date", "select"), entry.field_id
            if entry.control == "select":
                # Either the persona file carries the vocabulary or a procedure
                # requirement does; a select with neither is a dead control.
                assert entry.options or entry.path in (
                    "antrag.antragsart",
                    "antrag.antragsteller_rolle",
                ), entry.field_id
            if entry.control == "date":
                assert (
                    re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry.value) or not entry.value
                )


def test_every_visitor_facing_persona_string_has_an_english_sibling() -> None:
    """Part 16: the intake page is translated, personas included.

    Values, paths and letters have no English: they are data, not interface
    text. Everything a visitor READS does, and this is where a persona added
    later fails rather than shipping half-translated.
    """
    personas = demo_personas()
    assert personas.note_en and personas.note_en != personas.note
    assert len(personas.hints_en) == len(personas.hints)
    assert personas.hints_for("en") == personas.hints_en
    assert personas.hints_for("de") == personas.hints
    for persona in personas.personas:
        for attribute in ("headline", "story", "expectation"):
            german = getattr(persona, attribute)
            english = getattr(persona, f"{attribute}_en")
            assert english, f"{persona.persona_id}.{attribute}"
            assert english != german, f"{persona.persona_id}.{attribute}"
        for entry in persona.fields:
            assert entry.label_en, f"{persona.persona_id}.{entry.field_id}"
            assert entry.label_for("en") == entry.label_en
            assert entry.label_for("de") == entry.label
            if entry.help:
                assert entry.help_en, f"{persona.persona_id}.{entry.field_id}"


def _persona(persona_id: str) -> Persona:
    persona = demo_personas().get(persona_id)
    assert persona is not None
    return persona


def _form(persona_id: str, *, attachments: bool = False) -> dict[str, object]:
    persona = _persona(persona_id)
    values = dict(persona.form_values())
    if attachments:
        values.update({entry.field_name: "1" for entry in persona.attachments})
    return build_form_submission(
        persona,
        values,
        submission_id=f"demo-{persona_id}",
        submitted_at=NOW.isoformat(),
    )
