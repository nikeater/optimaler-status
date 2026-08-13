"""Procedure derivation: precedence, refusals, and the promise never to guess."""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from engine.config_loader import ConfigBundle, ProcedureConfig, load_config
from engine.evidence import (
    DerivationSource,
    HintStatus,
    ProcedureDerivation,
    build_payload_context,
    content_candidates,
    derive_procedure,
)
from tests.factories import make_envelope

#: Hypothesis cannot take a function-scoped fixture, and the config is the
#: config: loading it once here keeps the property tests honest about running
#: against what ships.
CONFIG: ConfigBundle = load_config()

ALTERSRENTE_FORM: dict[str, Any] = {"antrag": {"rentenart": "regelaltersrente"}}
EM_FORM: dict[str, Any] = {"antrag": {"rentenart": "erwerbsminderungsrente_voll"}}
BOTH_FORMS: dict[str, Any] = {
    "antrag": {
        "rentenart": "regelaltersrente",
        "rentenbeginn": "2027-03-01",
        "eintritt_erwerbsminderung": "2025-04-01",
    }
}
NOTHING: dict[str, Any] = {"antrag": {"anliegen": "Rueckfrage zur Renteninformation"}}
SF_ANTRAGSART: dict[str, Any] = {"antrag": {"antragsart": "prognose_vor_aufnahme"}}
SF_SIGNATURE: dict[str, Any] = {
    "antrag": {"taetigkeit_bezeichnung": "IT-Beratung und Softwareentwicklung"},
    "auftraggeber": {"firmenname": "Nordlicht Systemhaus GmbH"},
}
SF_AND_ALTERSRENTE: dict[str, Any] = {
    "antrag": {
        "antragsart": "feststellung_nach_aufnahme",
        "rentenart": "regelaltersrente",
        "rentenbeginn": "2029-08-01",
    }
}


def _derive(
    config: ConfigBundle, payload: dict[str, Any], hint: str | None
) -> ProcedureDerivation:
    return derive_procedure(
        make_envelope(payload, procedure_hint=hint), config.procedures
    )


# ------------------------------------------------------------- precedence ---


def test_a_valid_hint_wins_when_the_content_agrees(config: ConfigBundle) -> None:
    derivation = _derive(config, ALTERSRENTE_FORM, "altersrente")
    assert derivation.procedure_id == "altersrente"
    assert derivation.source is DerivationSource.HINT
    assert derivation.hint_status is HintStatus.KNOWN


def test_a_valid_hint_wins_when_the_content_says_nothing(
    config: ConfigBundle,
) -> None:
    derivation = _derive(config, NOTHING, "erwerbsminderungsrente")
    assert derivation.procedure_id == "erwerbsminderungsrente"
    assert derivation.source is DerivationSource.HINT
    assert derivation.candidates == ()


def test_unambiguous_content_carries_an_item_without_a_hint(
    config: ConfigBundle,
) -> None:
    """The xx-0004 case: the form says it, so the engine may say it."""
    derivation = _derive(config, ALTERSRENTE_FORM, None)
    assert derivation.procedure_id == "altersrente"
    assert derivation.source is DerivationSource.CONTENT
    assert derivation.hint_status is HintStatus.ABSENT


def test_unambiguous_content_also_beats_a_hint_nobody_configured(
    config: ConfigBundle,
) -> None:
    derivation = _derive(config, EM_FORM, "grundsicherung")
    assert derivation.procedure_id == "erwerbsminderungsrente"
    assert derivation.source is DerivationSource.CONTENT
    assert derivation.hint_status is HintStatus.UNKNOWN


def test_nothing_at_all_derives_nothing(config: ConfigBundle) -> None:
    derivation = _derive(config, NOTHING, None)
    assert derivation.procedure_id is None
    assert derivation.source is DerivationSource.NONE
    assert derivation.ambiguous is False
    assert derivation.hint_contradicted is False


# --------------------------------------------------------------- refusals ---


def test_ambiguous_content_refuses_to_pick(config: ConfigBundle) -> None:
    derivation = _derive(config, BOTH_FORMS, None)
    assert derivation.procedure_id is None
    assert derivation.source is DerivationSource.NONE
    assert derivation.ambiguous is True
    assert derivation.candidates == (
        "altersrente",
        "erwerbsminderungsrente",
    )


def test_ambiguous_content_is_not_rescued_by_a_valid_hint(
    config: ConfigBundle,
) -> None:
    """A hint is metadata about the channel, not evidence about the content."""
    derivation = _derive(config, BOTH_FORMS, "altersrente")
    assert derivation.procedure_id is None
    assert derivation.ambiguous is True


def test_a_hint_contradicted_by_the_content_derives_nothing(
    config: ConfigBundle,
) -> None:
    """ar-0033: channel says Altersrente, the form is an EM application."""
    derivation = _derive(config, EM_FORM, "altersrente")
    assert derivation.procedure_id is None
    assert derivation.source is DerivationSource.NONE
    assert derivation.hint_contradicted is True
    assert derivation.candidates == ("erwerbsminderungsrente",)
    assert "widerspricht" in derivation.detail


def test_every_outcome_carries_a_readable_reason(config: ConfigBundle) -> None:
    for payload, hint in (
        (ALTERSRENTE_FORM, "altersrente"),
        (ALTERSRENTE_FORM, None),
        (EM_FORM, "altersrente"),
        (BOTH_FORMS, None),
        (NOTHING, None),
    ):
        derivation = _derive(config, payload, hint)
        assert derivation.detail
        assert derivation.as_payload()["source"] in {"hint", "content", "none"}


# ----------------------------------------------------------------- config ---


def test_a_procedure_without_signals_is_never_derived_from_content(
    config: ConfigBundle,
) -> None:
    """Silence is not a signal."""
    procedure = config.procedure("altersrente")
    assert procedure is not None
    silent = ProcedureConfig.model_validate(
        procedure.model_dump(exclude={"derivation"})
    )
    assert silent.derivation is None
    context = build_payload_context(make_envelope(ALTERSRENTE_FORM))
    assert content_candidates({"altersrente": silent}, context) == ()


def test_derivation_reads_the_payload_not_the_extractions(
    config: ConfigBundle,
) -> None:
    """The whole point: it runs before anything has been extracted."""
    context = build_payload_context(make_envelope(ALTERSRENTE_FORM))
    assert not any(key.startswith("extraction.") for key in context)
    assert context["payload.antrag.rentenart"] == "regelaltersrente"


# ------------------------------------------------------------- properties ---

_RENTENART = st.sampled_from(
    [
        "regelaltersrente",
        "altersrente_langjaehrig",
        "erwerbsminderungsrente_voll",
        "erwerbsminderungsrente_teilweise",
        "witwenrente",
        "teilhabe_arbeitsleben",
        "",
    ]
)
_DATE = st.sampled_from(["2026-11-01", "01.11.2026", "1994-05-02", ""])
_HINT = st.sampled_from(
    ["altersrente", "erwerbsminderungsrente", "reha", "grundsicherung", None]
)


@st.composite
def _payloads(draw: st.DrawFn) -> dict[str, Any]:
    antrag: dict[str, str] = {}
    for key, strategy in (
        ("rentenart", _RENTENART),
        ("rentenbeginn", _DATE),
        ("eintritt_erwerbsminderung", _DATE),
    ):
        if draw(st.booleans()):
            antrag[key] = draw(strategy)
    return {"antrag": antrag}


@given(payload=_payloads(), hint=_HINT)
def test_derivation_never_invents_a_procedure(
    payload: dict[str, Any], hint: str | None
) -> None:
    """It may only ever name a procedure this config actually has."""
    bundle = CONFIG
    derivation = derive_procedure(
        make_envelope(payload, procedure_hint=hint), bundle.procedures
    )
    assert (
        derivation.procedure_id is None or derivation.procedure_id in bundle.procedures
    )
    assert set(derivation.candidates) <= set(bundle.procedures)


@given(payload=_payloads(), hint=_HINT)
def test_derivation_is_deterministic(payload: dict[str, Any], hint: str | None) -> None:
    envelope = make_envelope(payload, procedure_hint=hint)
    first = derive_procedure(envelope, CONFIG.procedures)
    second = derive_procedure(envelope, CONFIG.procedures)
    assert first == second


@given(payload=_payloads(), hint=_HINT)
def test_ambiguity_and_contradiction_always_end_in_none(
    payload: dict[str, Any], hint: str | None
) -> None:
    """The refusals are the safety property: they may never yield a procedure."""
    derivation = derive_procedure(
        make_envelope(payload, procedure_hint=hint), CONFIG.procedures
    )
    if derivation.ambiguous or derivation.hint_contradicted:
        assert derivation.procedure_id is None
        assert derivation.source is DerivationSource.NONE


# ------------------------------ statusfeststellung signals (par. 7a SGB IV) ---


def test_the_antragsart_alone_identifies_a_statusfeststellung(
    config: ConfigBundle,
) -> None:
    derivation = _derive(config, SF_ANTRAGSART, None)
    assert derivation.procedure_id == "statusfeststellung"
    assert derivation.source is DerivationSource.CONTENT


def test_auftraggeber_plus_taetigkeit_is_the_second_signature(
    config: ConfigBundle,
) -> None:
    """No other configured procedure has an `auftraggeber.*` namespace."""
    derivation = _derive(config, SF_SIGNATURE, None)
    assert derivation.procedure_id == "statusfeststellung"
    assert derivation.source is DerivationSource.CONTENT


def test_an_auftraggeber_without_a_taetigkeit_is_not_a_signal(
    config: ConfigBundle,
) -> None:
    """Half a signature is not a signature; silence beats a guess."""
    derivation = _derive(config, {"auftraggeber": {"firmenname": "Muster GmbH"}}, None)
    assert derivation.procedure_id is None
    assert derivation.candidates == ()


def test_a_statusantrag_carrying_rentenangaben_derives_nothing(
    config: ConfigBundle,
) -> None:
    """sf-0031: two procedures claim the same form, so neither is derived."""
    derivation = _derive(config, SF_AND_ALTERSRENTE, "statusfeststellung")
    assert derivation.procedure_id is None
    assert derivation.ambiguous is True
    assert derivation.candidates == ("altersrente", "statusfeststellung")


def test_the_contract_outcome_mirrors_the_dataclass(config: ConfigBundle) -> None:
    """ADR-016: EvidenceRecord.derivation is rendered from the same object."""
    derivation = _derive(config, SF_AND_ALTERSRENTE, "statusfeststellung")
    outcome = derivation.as_outcome()
    assert outcome.source.value == derivation.source.value
    assert outcome.candidates == list(derivation.candidates)
    assert outcome.detail == derivation.detail
