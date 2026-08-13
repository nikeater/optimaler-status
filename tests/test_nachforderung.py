"""Gap rendering: from a requirement id to a sentence somebody can send."""

from __future__ import annotations

from engine.config_loader import ConfigBundle
from engine.evidence import (
    GapRendering,
    evaluate_completeness,
    render_gap,
    render_gaps,
)
from schemas.config import Requirement
from schemas.evidence import GapItem, RequirementStatus
from tests.factories import make_extractions

VALUES = {
    "geburtsdatum": "1959-04-17",
    "versicherungsnummer": "17170459B012",
    "rentenart": "regelaltersrente",
    "rentenbeginn": "2026-11-01",
}


def _renderings(config: ConfigBundle, values: dict[str, str]) -> list[GapRendering]:
    procedure = config.procedure("altersrente")
    assert procedure is not None
    completeness = evaluate_completeness(
        make_extractions(values),
        procedure.requirements,
        field_paths=procedure.field_paths,
    )
    return list(render_gaps(completeness, procedure))


def test_a_missing_field_renders_the_configured_request(
    config: ConfigBundle,
) -> None:
    values = {k: v for k, v in VALUES.items() if k != "versicherungsnummer"}
    rendering = _renderings(config, values)[0]
    assert rendering.requirement_id == "versicherungsnummer"
    assert rendering.status == "missing"
    assert "Sozialversicherungsausweis" in rendering.sentence
    assert "{" not in rendering.sentence, "no placeholder may survive"


def test_an_invalid_field_renders_the_invalid_variant_with_the_problem(
    config: ConfigBundle,
) -> None:
    rendering = _renderings(config, {**VALUES, "rentenart": "witwenrente"})[0]
    assert rendering.status == "invalid"
    assert "gehoert nicht zu den Altersrenten" in rendering.sentence
    assert "witwenrente" in rendering.sentence, "the rejected value must appear"


def test_template_data_carries_the_facts_without_prose(
    config: ConfigBundle,
) -> None:
    """Part 08 gets structured data, not a sentence it has to re-parse."""
    values = {k: v for k, v in VALUES.items() if k != "rentenbeginn"}
    data = _renderings(config, values)[0].template_data
    assert data["requirement_id"] == "rentenbeginn"
    assert data["status"] == "missing"
    assert data["payload_path"] == "antrag.rentenbeginn"
    assert data["procedure_id"] == "altersrente"
    assert data["description"].startswith("Gewuenschter Rentenbeginn")


def test_every_gap_of_a_multi_defect_item_renders(config: ConfigBundle) -> None:
    values = {
        "geburtsdatum": "1974-02-08",
        "rentenart": "regelaltersrente",
        "rentenbeginn": "2027-06-01",
    }
    renderings = _renderings(config, values)
    assert [rendering.requirement_id for rendering in renderings] == [
        "versicherungsnummer",
        "rentenbeginn",
    ]
    assert all(rendering.sentence.strip() for rendering in renderings)


def test_a_requirement_without_configured_wording_still_renders() -> None:
    """A new requirement produces a usable sentence on day one."""
    rendering = render_gap(
        GapItem(
            requirement_id="befundbericht",
            status=RequirementStatus.MISSING,
            span=None,
            detail="Pflichtangabe fehlt",
        ),
        Requirement(
            requirement_id="befundbericht",
            description="Aerztlicher Befundbericht",
            kind="document",
        ),
    )
    assert rendering.sentence == (
        "Bitte reichen Sie folgende Angabe nach: Aerztlicher Befundbericht"
    )


def test_a_gap_without_a_requirement_falls_back_to_its_id() -> None:
    rendering = render_gap(
        GapItem(
            requirement_id="unbekannt",
            status=RequirementStatus.INVALID,
            span=None,
            detail="Wert passt nicht",
        ),
        None,
    )
    assert "unbekannt" in rendering.sentence
    assert "Wert passt nicht" in rendering.sentence


def test_an_unknown_placeholder_stays_literal_instead_of_raising() -> None:
    """Agency-editable wording must not be able to crash the evidence plane."""
    rendering = render_gap(
        GapItem(
            requirement_id="x",
            status=RequirementStatus.MISSING,
            span=None,
            detail=None,
        ),
        None,
        template="Bitte {gibtsnicht} nachreichen ({requirement_id}).",
    )
    assert rendering.sentence == "Bitte {gibtsnicht} nachreichen (x)."


def test_rendering_without_a_procedure_produces_nothing_to_send(
    config: ConfigBundle,
) -> None:
    """An unknown procedure has no gaps, so there is nothing to ask for."""
    completeness = evaluate_completeness(
        make_extractions(VALUES), None, procedure_id="bauantrag"
    )
    assert render_gaps(completeness, None) == []


def test_a_requirement_the_procedure_has_no_wording_for_uses_the_fallback(
    config: ConfigBundle,
) -> None:
    """Adding a requirement must not silently produce an empty request."""
    procedure = config.procedure("altersrente")
    assert procedure is not None
    stripped = procedure.model_copy(update={"nachforderung": []})
    completeness = evaluate_completeness(
        make_extractions({k: v for k, v in VALUES.items() if k != "rentenart"}),
        stripped.requirements,
        field_paths=stripped.field_paths,
    )
    rendering = render_gaps(completeness, stripped)[0]
    assert rendering.sentence.startswith("Bitte reichen Sie folgende Angabe nach:")
