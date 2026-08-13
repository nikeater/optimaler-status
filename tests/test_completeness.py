"""Completeness: present, missing, invalid, not evaluable - and why."""

from __future__ import annotations

import pytest

from engine.config_loader import ConfigBundle
from engine.evidence import (
    UNKNOWN_REQUIREMENTS_VERSION,
    Visibility,
    evaluate_completeness,
    validation_problem,
)
from engine.redact import Witness
from schemas.config import Requirement, RequirementList
from schemas.evidence import (
    CompletenessEvidence,
    CompletenessVerdict,
    RequirementStatus,
)
from tests.factories import make_extractions

COMPLETE_VALUES = {
    "geburtsdatum": "1959-04-17",
    "versicherungsnummer": "17170459B012",
    "rentenart": "regelaltersrente",
    "rentenbeginn": "2026-11-01",
}


def _requirements(config: ConfigBundle) -> RequirementList:
    procedure = config.procedure("altersrente")
    assert procedure is not None
    return procedure.requirements


def _paths(config: ConfigBundle) -> dict[str, str]:
    procedure = config.procedure("altersrente")
    assert procedure is not None
    return procedure.field_paths


def _evaluate(config: ConfigBundle, values: dict[str, str]) -> CompletenessEvidence:
    return evaluate_completeness(
        make_extractions(values), _requirements(config), field_paths=_paths(config)
    )


def test_all_requirements_satisfied_is_complete(config: ConfigBundle) -> None:
    evidence = evaluate_completeness(
        make_extractions(COMPLETE_VALUES), _requirements(config)
    )
    assert evidence.verdict is CompletenessVerdict.COMPLETE
    assert evidence.gaps == []
    assert evidence.requirements_version == "altersrente_requirements_v1"


def test_missing_field_produces_a_missing_gap(config: ConfigBundle) -> None:
    values = {k: v for k, v in COMPLETE_VALUES.items() if k != "versicherungsnummer"}
    evidence = evaluate_completeness(make_extractions(values), _requirements(config))
    assert evidence.verdict is CompletenessVerdict.INCOMPLETE
    assert [(gap.requirement_id, gap.status) for gap in evidence.gaps] == [
        ("versicherungsnummer", RequirementStatus.MISSING)
    ]
    assert evidence.gaps[0].detail is not None


def test_blank_value_counts_as_missing(config: ConfigBundle) -> None:
    values = {**COMPLETE_VALUES, "rentenbeginn": " "}
    evidence = evaluate_completeness(make_extractions(values), _requirements(config))
    assert evidence.gaps[0].requirement_id == "rentenbeginn"
    assert evidence.gaps[0].status is RequirementStatus.MISSING


def test_pattern_violation_produces_an_invalid_gap(config: ConfigBundle) -> None:
    values = {**COMPLETE_VALUES, "geburtsdatum": "17.04.1959"}
    evidence = evaluate_completeness(make_extractions(values), _requirements(config))
    assert [(gap.requirement_id, gap.status) for gap in evidence.gaps] == [
        ("geburtsdatum", RequirementStatus.INVALID)
    ]


def test_one_of_violation_produces_an_invalid_gap(config: ConfigBundle) -> None:
    values = {**COMPLETE_VALUES, "rentenart": "erwerbsminderungsrente"}
    evidence = evaluate_completeness(make_extractions(values), _requirements(config))
    assert evidence.gaps[0].status is RequirementStatus.INVALID
    assert "nicht zulaessig" in (evidence.gaps[0].detail or "")


def test_unknown_procedure_is_not_evaluable() -> None:
    """Not evaluable must never be mistaken for complete."""
    evidence = evaluate_completeness(
        make_extractions(COMPLETE_VALUES), None, procedure_id="bauantrag"
    )
    assert evidence.verdict is CompletenessVerdict.NOT_EVALUABLE
    assert evidence.procedure_id == "bauantrag"
    assert evidence.requirements_version == UNKNOWN_REQUIREMENTS_VERSION
    assert evidence.gaps == []


def test_length_constraints_are_checked() -> None:
    requirements = RequirementList(
        procedure_id="test",
        version="test_v0",
        requirements=[
            Requirement(
                requirement_id="kurz",
                description="mindestens 5 Zeichen",
                kind="field",
                validation={"min_length": 5},
            ),
            Requirement(
                requirement_id="lang",
                description="hoechstens 3 Zeichen",
                kind="field",
                validation={"max_length": 3},
            ),
        ],
    )
    evidence = evaluate_completeness(
        make_extractions({"kurz": "ab", "lang": "abcdef"}), requirements
    )
    assert [gap.status for gap in evidence.gaps] == [
        RequirementStatus.INVALID,
        RequirementStatus.INVALID,
    ]
    assert "kuerzer" in (evidence.gaps[0].detail or "")
    assert "laenger" in (evidence.gaps[1].detail or "")


def test_requirements_without_validation_only_check_presence() -> None:
    requirements = RequirementList(
        procedure_id="test",
        version="test_v0",
        requirements=[
            Requirement(requirement_id="freitext", description="beliebig", kind="field")
        ],
    )
    evidence = evaluate_completeness(make_extractions({"freitext": "x"}), requirements)
    assert evidence.verdict is CompletenessVerdict.COMPLETE


def test_document_requirements_are_reported_as_missing() -> None:
    """No document evidence yet; saying so beats silently passing."""
    requirements = RequirementList(
        procedure_id="test",
        version="test_v0",
        requirements=[
            Requirement(
                requirement_id="rentenbescheid",
                description="Kopie des Bescheids",
                kind="document",
            )
        ],
    )
    evidence = evaluate_completeness(make_extractions({}), requirements)
    assert evidence.verdict is CompletenessVerdict.INCOMPLETE
    assert evidence.gaps[0].status is RequirementStatus.MISSING
    assert "Dokumentenpruefung" in (evidence.gaps[0].detail or "")


# ------------------------------------------------------------- provenance ---


def test_gaps_carry_the_payload_path_and_the_failed_constraint(
    config: ConfigBundle,
) -> None:
    """A caseworker needs to know which key of the submission is at fault."""
    evidence = _evaluate(config, {**COMPLETE_VALUES, "rentenart": "witwenrente"})
    detail = evidence.gaps[0].detail or ""
    assert "Feld: rentenart" in detail
    assert "Pfad: antrag.rentenart" in detail
    assert "Pruefregel: one_of" in detail


def test_missing_gaps_carry_the_payload_path_too(config: ConfigBundle) -> None:
    values = {k: v for k, v in COMPLETE_VALUES.items() if k != "rentenbeginn"}
    evidence = _evaluate(config, values)
    detail = evidence.gaps[0].detail or ""
    assert "Pfad: antrag.rentenbeginn" in detail


# ------------------------------------------------------- date plausibility ---


@pytest.mark.parametrize(
    ("value", "expected_constraint"),
    [
        ("2029-02-30", "date"),
        ("1889-01-01", "date.min"),
        ("2011-01-01", "date.max"),
    ],
)
def test_date_bounds_and_calendar_validity(
    config: ConfigBundle, value: str, expected_constraint: str
) -> None:
    """The format pattern accepts all three; only a real date check rejects them."""
    evidence = _evaluate(config, {**COMPLETE_VALUES, "geburtsdatum": value})
    gap = next(gap for gap in evidence.gaps if gap.requirement_id == "geburtsdatum")
    assert gap.status is RequirementStatus.INVALID
    assert f"Pruefregel: {expected_constraint}" in (gap.detail or "")


def test_a_date_inside_the_bounds_passes(config: ConfigBundle) -> None:
    evidence = _evaluate(
        config,
        {
            **COMPLETE_VALUES,
            "geburtsdatum": "1900-01-01",
            "versicherungsnummer": "17010100B012",
        },
    )
    assert [gap.requirement_id for gap in evidence.gaps] == []


def test_a_requirement_without_a_date_block_is_not_date_checked() -> None:
    requirements = RequirementList(
        procedure_id="test",
        version="test_v0",
        requirements=[
            Requirement(requirement_id="irgendwas", description="frei", kind="field")
        ],
    )
    evidence = evaluate_completeness(
        make_extractions({"irgendwas": "2029-02-30"}), requirements
    )
    assert evidence.verdict is CompletenessVerdict.COMPLETE


# --------------------------------------------------------- cross-field ---


def test_pension_start_before_the_sixtieth_birthday_is_invalid(
    config: ConfigBundle,
) -> None:
    evidence = _evaluate(
        config,
        {**COMPLETE_VALUES, "geburtsdatum": "1975-05-20", "rentenbeginn": "2027-01-01"},
    )
    gap = next(gap for gap in evidence.gaps if gap.requirement_id == "rentenbeginn")
    assert gap.status is RequirementStatus.INVALID
    assert "Pruefregel: cross_field.min_years_after" in (gap.detail or "")
    assert "60 Jahre" in (gap.detail or "")


def test_exactly_sixty_years_is_still_allowed(config: ConfigBundle) -> None:
    """The boundary belongs to the permitted side; par. 237a says 'ab 60'."""
    evidence = _evaluate(
        config,
        {
            **COMPLETE_VALUES,
            "geburtsdatum": "1966-11-01",
            "versicherungsnummer": "17011166B012",
            "rentenbeginn": "2026-11-01",
        },
    )
    assert evidence.gaps == []


def test_the_versicherungsnummer_must_carry_the_stated_birthdate(
    config: ConfigBundle,
) -> None:
    evidence = _evaluate(
        config, {**COMPLETE_VALUES, "versicherungsnummer": "12010157A001"}
    )
    gap = next(
        gap for gap in evidence.gaps if gap.requirement_id == "versicherungsnummer"
    )
    assert gap.status is RequirementStatus.INVALID
    assert "Pruefregel: cross_field.birthdate_in_vsnr" in (gap.detail or "")


def test_a_versicherungsnummer_without_a_real_date_is_invalid_on_its_own(
    config: ConfigBundle,
) -> None:
    """The structural half runs even when the birth date is missing."""
    values = {k: v for k, v in COMPLETE_VALUES.items() if k != "geburtsdatum"}
    evidence = _evaluate(config, {**values, "versicherungsnummer": "12310259D064"})
    gap = next(
        gap for gap in evidence.gaps if gap.requirement_id == "versicherungsnummer"
    )
    assert gap.status is RequirementStatus.INVALID
    assert "kein gueltiges Geburtsdatum" in (gap.detail or "")


def test_an_unusable_partner_value_does_not_produce_a_second_gap(
    config: ConfigBundle,
) -> None:
    """The cause is reported once; the consequence is not reported at all."""
    evidence = _evaluate(config, {**COMPLETE_VALUES, "geburtsdatum": "17.04.1959"})
    assert [gap.requirement_id for gap in evidence.gaps] == ["geburtsdatum"]


def test_cross_field_kinds_over_the_raw_helper() -> None:
    """not_before / not_after / max_years_after, without a procedure around them."""
    not_before = {"cross_field": [{"kind": "not_before", "field": "start"}]}
    assert validation_problem(not_before, "2020-01-01", {"start": "2021-01-01"})
    assert validation_problem(not_before, "2022-01-01", {"start": "2021-01-01"}) is None

    not_after = {"cross_field": [{"kind": "not_after", "field": "start"}]}
    assert validation_problem(not_after, "2022-01-01", {"start": "2021-01-01"})
    assert validation_problem(not_after, "2020-01-01", {"start": "2021-01-01"}) is None

    at_most = {
        "cross_field": [{"kind": "max_years_after", "field": "start", "years": 5}]
    }
    assert validation_problem(at_most, "2030-01-01", {"start": "2021-01-01"})
    assert validation_problem(at_most, "2025-01-01", {"start": "2021-01-01"}) is None


def test_a_cross_field_check_with_an_absent_partner_is_skipped() -> None:
    check = {"cross_field": [{"kind": "not_before", "field": "start"}]}
    assert validation_problem(check, "2020-01-01", {}) is None
    assert validation_problem(check, "2020-01-01", {"start": "kein datum"}) is None


def test_the_configured_detail_text_replaces_the_generic_message() -> None:
    check = {
        "cross_field": [
            {"kind": "not_before", "field": "start", "detail": "Eigener Hinweis"}
        ]
    }
    failure = validation_problem(check, "2020-01-01", {"start": "2021-01-01"})
    assert failure is not None
    assert failure.message == "Eigener Hinweis"


def test_a_malformed_cross_field_entry_is_ignored_rather_than_raising() -> None:
    """The loader rejects these; the evaluator still must not crash on one."""
    assert validation_problem({"cross_field": ["nonsense"]}, "x", {}) is None
    assert validation_problem({"cross_field": "nonsense"}, "x", {}) is None
    assert (
        validation_problem(
            {"cross_field": [{"kind": "gibtsnicht", "field": "start"}]},
            "2020-01-01",
            {"start": "2021-01-01"},
        )
        is None
    )


def test_a_versicherungsnummer_with_letters_in_the_date_block_is_invalid(
    config: ConfigBundle,
) -> None:
    evidence = _evaluate(
        config, {**COMPLETE_VALUES, "versicherungsnummer": "17ABCDEFB012"}
    )
    gap = next(
        gap for gap in evidence.gaps if gap.requirement_id == "versicherungsnummer"
    )
    assert gap.status is RequirementStatus.INVALID


# ------------------------------- statusfeststellung (par. 7a SGB IV) ---

SF_COMPLETE_VALUES = {
    "versicherungsnummer": "12140385K023",
    "geburtsdatum": "1985-03-14",
    "antragsart": "feststellung_nach_aufnahme",
    "antragsteller_rolle": "auftragnehmer",
    "taetigkeit_bezeichnung": "IT-Beratung und Softwareentwicklung",
    "taetigkeit_beginn": "2026-01-15",
    "auftraggeber_name": "Nordlicht Systemhaus GmbH",
}


def _sf(config: ConfigBundle, values: dict[str, str]) -> CompletenessEvidence:
    procedure = config.procedure("statusfeststellung")
    assert procedure is not None
    return evaluate_completeness(
        make_extractions(values),
        procedure.requirements,
        field_paths=procedure.field_paths,
    )


def test_a_complete_statusantrag_is_complete(config: ConfigBundle) -> None:
    evidence = _sf(config, SF_COMPLETE_VALUES)
    assert evidence.verdict is CompletenessVerdict.COMPLETE
    assert evidence.gaps == []
    assert evidence.requirements_version == "statusfeststellung_requirements_v1"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Prognoseantrag nach par. 7a Abs. 4a SGB IV: a Beginn years in the
        # future is the REGULAR case here and must never be a defect. Nothing in
        # completeness reads the clock, so this holds on any day it is run.
        ("2035-12-31", None),
        ("2030-06-01", None),
        ("1999-04-01", None),
        # ... but the absolute bounds still apply.
        ("2036-01-01", RequirementStatus.INVALID),
        ("1989-12-31", RequirementStatus.INVALID),
        ("01.03.2026", RequirementStatus.INVALID),
        ("2026-02-30", RequirementStatus.INVALID),
    ],
)
def test_taetigkeit_beginn_allows_the_future_but_not_the_impossible(
    config: ConfigBundle, value: str, expected: RequirementStatus | None
) -> None:
    evidence = _sf(config, {**SF_COMPLETE_VALUES, "taetigkeit_beginn": value})
    statuses = {gap.requirement_id: gap.status for gap in evidence.gaps}
    assert statuses.get("taetigkeit_beginn") == expected


def test_a_taetigkeit_before_the_fourteenth_birthday_is_invalid(
    config: ConfigBundle,
) -> None:
    evidence = _sf(
        config,
        {
            **SF_COMPLETE_VALUES,
            "geburtsdatum": "2008-06-20",
            "versicherungsnummer": "15200608H037",
            "taetigkeit_beginn": "2020-09-01",
        },
    )
    gap = next(
        gap for gap in evidence.gaps if gap.requirement_id == "taetigkeit_beginn"
    )
    assert gap.status is RequirementStatus.INVALID
    assert gap.detail is not None
    assert "14. Geburtstag" in gap.detail


def test_an_unknown_antragsart_is_invalid_not_a_rejection(
    config: ConfigBundle,
) -> None:
    """C0050 matters mislabelled into a V0027 are a question for a human."""
    evidence = _sf(config, {**SF_COMPLETE_VALUES, "antragsart": "gruppenfeststellung"})
    assert [(gap.requirement_id, gap.status) for gap in evidence.gaps] == [
        ("antragsart", RequirementStatus.INVALID)
    ]
    assert evidence.verdict is CompletenessVerdict.INCOMPLETE


def test_a_statusantrag_without_an_auftraggeber_is_incomplete(
    config: ConfigBundle,
) -> None:
    values = {k: v for k, v in SF_COMPLETE_VALUES.items() if k != "auftraggeber_name"}
    evidence = _sf(config, values)
    gap = next(
        gap for gap in evidence.gaps if gap.requirement_id == "auftraggeber_name"
    )
    assert gap.status is RequirementStatus.MISSING
    assert gap.detail is not None
    assert "auftraggeber.firmenname" in gap.detail


def test_the_indizien_are_not_requirements(config: ConfigBundle) -> None:
    """Turning an Abwaegung into a checklist is the failure mode to avoid."""
    procedure = config.procedure("statusfeststellung")
    assert procedure is not None
    ids = {item.requirement_id for item in procedure.requirements.requirements}
    assert not ids & {
        "weisungsgebunden",
        "eingliederung_arbeitsorganisation",
        "arbeitsort",
        "weitere_auftraggeber",
        "umsatzanteil_hauptauftraggeber",
        "honorar_modell",
        "rahmenvertrag",
        "dreiecksverhaeltnis",
    }
    assert not set(procedure.field_paths) & {"weisungsgebunden", "honorar_modell"}


# --------------------------- sealed values: witness resolution and visibility ---
#
# Part 04. Two things change once identity-classed fields are sealed at ingest:
# the checker validates through the transient witness instead of against the
# placeholder, and the problem strings for those fields stop quoting what they
# saw. The tests above deliberately keep their `Wert '...'` assertions: they call
# the checker WITHOUT a redaction policy, which is the honest shape for a caller
# that has none, and non-identity fields keep the more useful wording either way.


SEALED_VSNR = "[[PII|VSNR|BCDFGHJKMNPQ]]"
SEALED_GEBDAT = "[[PII|GEBDAT|BCDFGHJKMNPR]]"


def _sealed(
    config: ConfigBundle,
    values: dict[str, str],
    witness: dict[str, str],
    sealed_fields: tuple[str, ...] = ("versicherungsnummer", "geburtsdatum"),
) -> CompletenessEvidence:
    return evaluate_completeness(
        make_extractions(values),
        _requirements(config),
        field_paths=_paths(config),
        witness=Witness(witness),
        sealed_fields=sealed_fields,
    )


def test_a_sealed_value_is_validated_through_the_witness(config: ConfigBundle) -> None:
    """The real Versicherungsnummer is checked; the placeholder never is."""
    evidence = _sealed(
        config,
        {**COMPLETE_VALUES, "versicherungsnummer": SEALED_VSNR},
        {SEALED_VSNR: "17170459B012"},
    )
    assert evidence.verdict is CompletenessVerdict.COMPLETE
    assert evidence.gaps == []


def test_a_sealed_value_that_is_invalid_is_still_caught(config: ConfigBundle) -> None:
    evidence = _sealed(
        config,
        {**COMPLETE_VALUES, "versicherungsnummer": SEALED_VSNR},
        {SEALED_VSNR: "17140259K01"},
    )
    gap = next(
        gap for gap in evidence.gaps if gap.requirement_id == "versicherungsnummer"
    )
    assert gap.status is RequirementStatus.INVALID
    assert "Pruefregel: pattern" in (gap.detail or "")
    # ... and the invalid value does NOT travel with the gap. This is the leak
    # part 01 shipped: an invalid Versicherungsnummer used to land in the journal.
    assert "17140259K01" not in (gap.detail or "")
    assert (gap.detail or "").startswith("Der Wert entspricht nicht dem Format")


def test_an_unresolvable_placeholder_is_invalid_not_valid(
    config: ConfigBundle,
) -> None:
    """Defensive toward tier 3: could-not-check is never checked-and-fine."""
    evidence = _sealed(
        config, {**COMPLETE_VALUES, "versicherungsnummer": SEALED_VSNR}, {}
    )
    gap = next(
        gap for gap in evidence.gaps if gap.requirement_id == "versicherungsnummer"
    )
    assert gap.status is RequirementStatus.INVALID
    assert "Pruefregel: sealed.unresolved" in (gap.detail or "")
    assert "versiegelt" in (gap.detail or "")


def test_without_a_witness_at_all_a_sealed_field_is_unresolvable(
    config: ConfigBundle,
) -> None:
    evidence = evaluate_completeness(
        make_extractions({**COMPLETE_VALUES, "versicherungsnummer": SEALED_VSNR}),
        _requirements(config),
        field_paths=_paths(config),
        sealed_fields=("versicherungsnummer",),
    )
    assert evidence.gaps[0].requirement_id == "versicherungsnummer"
    assert evidence.gaps[0].status is RequirementStatus.INVALID


def test_a_sealed_date_problem_names_the_bound_but_not_the_date(
    config: ConfigBundle,
) -> None:
    """The bound describes the RULE; the value describes the person."""
    evidence = _sealed(
        config,
        {**COMPLETE_VALUES, "geburtsdatum": SEALED_GEBDAT},
        {SEALED_GEBDAT: "1889-01-01"},
    )
    gap = next(gap for gap in evidence.gaps if gap.requirement_id == "geburtsdatum")
    assert "Untergrenze 1900-01-01" in (gap.detail or "")
    assert "1889-01-01" not in (gap.detail or "")
    assert "Das Datum liegt vor" in (gap.detail or "")


def test_a_sealed_date_that_is_not_a_date_is_reported_value_free(
    config: ConfigBundle,
) -> None:
    evidence = _sealed(
        config,
        {**COMPLETE_VALUES, "geburtsdatum": SEALED_GEBDAT},
        {SEALED_GEBDAT: "2029-02-30"},
    )
    gap = next(gap for gap in evidence.gaps if gap.requirement_id == "geburtsdatum")
    assert "Pruefregel: date" in (gap.detail or "")
    assert "2029-02-30" not in (gap.detail or "")


def test_a_cross_field_message_hides_the_sealed_operand_only() -> None:
    """min_years_after may name the open Rentenbeginn, never the sealed birth date."""
    check = {
        "cross_field": [
            {"kind": "min_years_after", "field": "geburtsdatum", "years": 60}
        ]
    }
    failure = validation_problem(
        check,
        "2027-01-01",
        {"geburtsdatum": "1975-05-20"},
        visibility=Visibility(frozenset({"geburtsdatum"})),
        field_id="rentenbeginn",
    )
    assert failure is not None
    assert "1975-05-20" not in failure.message
    assert "geburtsdatum" in failure.message
    assert "2027-01-01" in failure.message


@pytest.mark.parametrize("kind", ["not_before", "not_after"])
def test_the_simple_cross_field_kinds_hide_a_sealed_operand(kind: str) -> None:
    later, earlier = ("2020-01-01", "2021-01-01")
    value, other = (later, earlier) if kind == "not_before" else (earlier, later)
    failure = validation_problem(
        {"cross_field": [{"kind": kind, "field": "geburtsdatum"}]},
        value,
        {"geburtsdatum": other},
        visibility=Visibility(frozenset({"geburtsdatum", "rentenbeginn"})),
        field_id="rentenbeginn",
    )
    assert failure is not None
    assert other not in failure.message
    assert value not in failure.message


def test_max_years_after_hides_a_sealed_operand() -> None:
    failure = validation_problem(
        {
            "cross_field": [
                {"kind": "max_years_after", "field": "geburtsdatum", "years": 5}
            ]
        },
        "2030-01-01",
        {"geburtsdatum": "2021-01-01"},
        visibility=Visibility(frozenset({"geburtsdatum"})),
        field_id="rentenbeginn",
    )
    assert failure is not None
    assert "2021-01-01" not in failure.message
    assert "mehr als 5 Jahre" in failure.message


def test_the_versicherungsnummer_structural_message_is_value_free(
    config: ConfigBundle,
) -> None:
    values = {k: v for k, v in COMPLETE_VALUES.items() if k != "geburtsdatum"}
    evidence = _sealed(
        config,
        {**values, "versicherungsnummer": SEALED_VSNR},
        {SEALED_VSNR: "12310259D064"},
    )
    gap = next(
        gap for gap in evidence.gaps if gap.requirement_id == "versicherungsnummer"
    )
    assert "kein gueltiges Geburtsdatum" in (gap.detail or "")
    assert "12310259D064" not in (gap.detail or "")


def test_the_birthdate_mismatch_fallback_names_neither_date() -> None:
    """Naming one date would reconstruct the other from the fact they disagree."""
    failure = validation_problem(
        {"cross_field": [{"kind": "birthdate_in_vsnr", "field": "geburtsdatum"}]},
        "12010157A001",
        {"geburtsdatum": "1959-04-17"},
        visibility=Visibility(frozenset({"versicherungsnummer", "geburtsdatum"})),
        field_id="versicherungsnummer",
    )
    assert failure is not None
    assert "12010157A001" not in failure.message
    assert "1959-04-17" not in failure.message
    assert "kodiert ein anderes Geburtsdatum" in failure.message


def test_an_open_field_keeps_the_more_useful_wording(config: ConfigBundle) -> None:
    """Non-identity fields are not made worse by the redaction boundary."""
    evidence = _sealed(config, {**COMPLETE_VALUES, "rentenart": "witwenrente"}, {})
    gap = next(gap for gap in evidence.gaps if gap.requirement_id == "rentenart")
    assert "Wert 'witwenrente'" in (gap.detail or "")


def test_the_bundle_derives_sealed_field_ids_per_procedure(
    config: ConfigBundle,
) -> None:
    """One source of truth: the policy plus the procedure's own field_map."""
    assert config.sealed_field_ids("altersrente") == {
        "geburtsdatum",
        "versicherungsnummer",
    }
    assert config.sealed_field_ids("statusfeststellung") == {
        "geburtsdatum",
        "versicherungsnummer",
        "auftraggeber_name",
    }
    assert config.sealed_field_ids(None) == frozenset()
    assert config.sealed_field_ids("bauantrag") == frozenset()
