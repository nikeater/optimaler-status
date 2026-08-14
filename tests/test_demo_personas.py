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
3. **Each persona still produces its arc.** The showcase promises a tier-1
   Bewilligungsentwurf, a tier-2 Nachforderung, a Clearingstelle case and a
   flagged one. Those are properties of the REAL pipeline over these values, so
   a config edit that quietly broke an arc has to fail here rather than on a
   screen in front of an audience.
"""

from __future__ import annotations

import json
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
)
from engine.journal import InMemoryJournalStore
from engine.pipeline import PipelineResult, run_pipeline
from engine.redact import InMemoryVaultStore
from engine.redact.recognizers import vsnr_checksum_ok

NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)

#: What each persona must produce through the FORM tab. The arcs of the
#: showcase, pinned: (procedure, tier, unit, flagged).
FORM_ARCS = {
    "mustermann_regelaltersrente": ("altersrente", 1, "Referat_312_Renten", False),
    "beispielmann_ohne_rentenbeginn": ("altersrente", 2, "Referat_312_Renten", False),
    "musterfrau_statusfeststellung": (
        "statusfeststellung",
        3,
        "Referat_340_Clearingstelle",
        False,
    ),
    "musterkind_rentenbeginn_2048": ("altersrente", 1, "Referat_312_Renten", True),
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
    assert personas.version == "personas_v2"
    assert {persona.persona_id for persona in personas.personas} == set(FORM_ARCS)
    assert personas.note
    assert len(personas.hints) >= 3
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
    chosen = _persona("mustermann_regelaltersrente")
    assert chosen.field("rentenart") is not None
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


def test_every_persona_name_is_mustermann_class() -> None:
    """Unmistakably fictional to a German reader - the rule, not a preference."""
    marker = ("muster", "beispiel", "demo")
    for persona in demo_personas().personas:
        lowered = persona.display_name.lower()
        assert any(part in lowered for part in marker), (
            f"{persona.display_name!r} is not obviously fictional; a demo "
            "persona indistinguishable from a real person ends up on a "
            "screenshot as one"
        )


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
        Path("config/demo/personas_v2.yaml").read_text(encoding="utf-8")
    )
    assert document["version"] == "personas_v2"


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


def test_the_tier_one_persona_is_complete_and_clear_cut(config: ConfigBundle) -> None:
    """Arc 1: nothing missing, the clear-cut criteria hold, a draft can follow."""
    result, _ = run(config, _form("mustermann_regelaltersrente"))
    assert result.evidence.completeness.verdict.value == "complete"
    assert result.clear_cut is True
    assert result.evidence.completeness.gaps == []


def test_the_gap_persona_reports_exactly_the_field_it_left_blank(
    config: ConfigBundle,
) -> None:
    """Arc 2: an empty field is MISSING, never invalid - different wording."""
    result, _ = run(config, _form("beispielmann_ohne_rentenbeginn"))
    gaps = result.evidence.completeness.gaps
    assert [gap.requirement_id for gap in gaps] == ["rentenbeginn"]
    assert gaps[0].status.value == "missing"
    assert any(
        "Rente beziehen moechten" in rendering.sentence
        for rendering in result.gap_renderings
    )


def test_the_anomaly_persona_is_flagged_with_readable_reasons(
    config: ConfigBundle,
) -> None:
    """Arc 4: tier 1 by the rules AND flagged by the scorer, which moves nothing.

    That combination is the whole demonstration of ADR-024: the shadow scorer
    runs in log_only mode, so a flag is a sentence a caseworker reads and never
    a tier the machine changed.
    """
    result, _ = run(config, _form("musterkind_rentenbeginn_2048"))
    assert result.anomaly is not None
    assert result.anomaly.flagged is True
    assert result.anomaly.reasons
    assert int(result.decision.tier) == 1
    assert int(result.decision.pre_downgrade_tier) == 1


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
    persona = _persona("mustermann_regelaltersrente")
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
    persona = _persona("mustermann_regelaltersrente")
    payload = build_form_submission(
        persona,
        {**persona.form_values(), "ort": "Musterbucht", "auslandsbezug": "ja"},
        submission_id="demo-edit",
        submitted_at=NOW.isoformat(),
    )
    data = payload["data"]
    assert isinstance(data, dict)
    assert data["antragsteller"]["anschrift"]["ort"] == "Musterbucht"
    assert data["antrag"]["auslandsbezug"] == "ja"


def test_a_value_for_an_unknown_field_id_is_ignored() -> None:
    """The form posts what the page rendered; anything else is not a field."""
    persona = _persona("mustermann_regelaltersrente")
    payload = build_form_submission(
        persona,
        {**persona.form_values(), "smuggled": "x"},
        submission_id="demo-smuggle",
        submitted_at=NOW.isoformat(),
    )
    assert "smuggled" not in json.dumps(payload)


def _persona(persona_id: str) -> Persona:
    persona = demo_personas().get(persona_id)
    assert persona is not None
    return persona


def _form(persona_id: str) -> dict[str, object]:
    persona = _persona(persona_id)
    return build_form_submission(
        persona,
        persona.form_values(),
        submission_id=f"demo-{persona_id}",
        submitted_at=NOW.isoformat(),
    )
