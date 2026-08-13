"""Deriving the procedure from prose, and refusing to when the prose says two.

Until part 05 a free-text Anschreiben had no derivable procedure at all: every
``payload.*`` signal is silent when there is no structured payload, so the answer
was "no procedure, tier 3" by construction rather than by judgement. The
``text.*`` namespace closes that, and it closes it with the SAME signature each
procedure already declared for its payload - a named Rentenart or a Rentenbeginn,
a named Erwerbsminderung or its Eintritt, the par. 7a wording or Auftraggeber and
Taetigkeit together.

The house rule does not move: two procedures' signals firing at once is an
ambiguity, and an ambiguity is "we do not know" (ADR-013, extended in ADR-020).
These tests mirror part 03's ambiguity tests one for one, in prose instead of
in a form.
"""

from __future__ import annotations

import pytest

from engine.config_loader import ConfigBundle
from engine.evidence.derive import DerivationSource, derive_procedure
from engine.ingest import build_ingest
from engine.journal.store import InMemoryJournalStore
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore, SeededTokenSource, text_seal_detector
from engine.textlayer import build_text_layer, merged_text
from schemas.textlayer import TextLayer

DETECTOR = text_seal_detector(with_ner=False)

ALTERSRENTE_LETTER = (
    "Sehr geehrte Damen und Herren,\n"
    "hiermit beantrage ich meine Regelaltersrente.\n"
    "Rentenbeginn: 2026-11-01\n"
    "Mit freundlichen Gruessen"
)
EM_LETTER = (
    "Sehr geehrte Damen und Herren,\n"
    "ich beantrage eine Erwerbsminderungsrente.\n"
    "Eintritt der Erwerbsminderung: 2025-03-04\n"
    "Mit freundlichen Gruessen"
)
STATUS_LETTER = (
    "Sehr geehrte Damen und Herren,\n"
    "ich bitte um Feststellung meines Erwerbsstatus nach par. 7a SGB IV.\n"
    "Taetigkeit: IT-Beratung\n"
    "Mit freundlichen Gruessen"
)
STATUS_LETTER_WITHOUT_THE_WORD = (
    "Sehr geehrte Damen und Herren,\n"
    "ich arbeite seit Januar fuer einen einzigen Auftraggeber.\n"
    "Taetigkeit: Pflegehilfe\n"
    "Mit freundlichen Gruessen"
)
AMBIGUOUS_LETTER = (
    "Sehr geehrte Damen und Herren,\n"
    "ich beantrage eine Altersrente, hilfsweise eine Erwerbsminderungsrente.\n"
    "Mit freundlichen Gruessen"
)
UNRELATED_LETTER = (
    "Sehr geehrte Damen und Herren,\n"
    "ich bitte um einen Termin in Ihrer Auskunftsstelle.\n"
    "Mit freundlichen Gruessen"
)


def submission(
    body: str, *, hint: str | None = None, channel: str = "email"
) -> dict[str, object]:
    payload: dict[str, object] = {
        "submissionId": "derive-text-0001",
        "destinationId": "drv-bund-eingang-test",
        "channel": channel,
        "submittedAt": "2026-08-11T09:00:00+00:00",
        "data": {},
        "attachments": [],
        "bodyText": body,
    }
    if hint is not None:
        payload["procedureHint"] = hint
    return payload


def derive(
    config: ConfigBundle, body: str, *, hint: str | None = None
) -> tuple[object, TextLayer | None]:
    """Ingest a letter and derive its procedure, exactly as the pipeline does."""
    result = build_ingest(
        submission(body, hint=hint),
        versions=config.version_stamp(),
        vault=InMemoryVaultStore(),
        policy=config.redaction,
        token_source=SeededTokenSource(13),
        text_detector=DETECTOR,
    )
    layer = build_text_layer(result.envelope, versions=config.version_stamp())
    return derive_procedure(result.envelope, config.procedures, layer=layer), layer


# ------------------------------------------------------- one procedure ---


@pytest.mark.parametrize(
    ("letter", "expected"),
    [
        (ALTERSRENTE_LETTER, "altersrente"),
        (EM_LETTER, "erwerbsminderungsrente"),
        (STATUS_LETTER, "statusfeststellung"),
        (STATUS_LETTER_WITHOUT_THE_WORD, "statusfeststellung"),
    ],
)
def test_a_letter_that_names_its_procedure_is_derivable_from_content(
    config: ConfigBundle, letter: str, expected: str
) -> None:
    derivation, _ = derive(config, letter)
    assert derivation.procedure_id == expected  # type: ignore[attr-defined]
    assert derivation.source is DerivationSource.CONTENT  # type: ignore[attr-defined]


def test_the_signal_reads_the_redacted_text_not_the_letter(
    config: ConfigBundle,
) -> None:
    """What a rule can see is prose with placeholders where identity was. That
    is not an inconvenience to work around - it is what lets the config lint
    promise that no rule quotes a person's data."""
    letter = STATUS_LETTER.replace(
        "Taetigkeit: IT-Beratung",
        "Auftraggeber: Musterbau GmbH\nTaetigkeit: IT-Beratung",
    )
    derivation, layer = derive(config, letter)
    assert derivation.procedure_id == "statusfeststellung"  # type: ignore[attr-defined]
    text = merged_text(layer)
    assert "Musterbau GmbH" not in text
    assert "Auftraggeber: [[PII|ORG|" in text


# ---------------------------------------------------------- two of them ---


def test_a_letter_that_names_two_procedures_is_not_guessed_at(
    config: ConfigBundle,
) -> None:
    """The standing house rule, now in prose: signals from two procedures mean
    no procedure, which means no completeness check and tier 3."""
    derivation, _ = derive(config, AMBIGUOUS_LETTER)
    assert derivation.procedure_id is None  # type: ignore[attr-defined]
    assert derivation.source is DerivationSource.NONE  # type: ignore[attr-defined]
    assert derivation.ambiguous is True  # type: ignore[attr-defined]
    assert derivation.candidates == (  # type: ignore[attr-defined]
        "altersrente",
        "erwerbsminderungsrente",
    )


def test_an_ambiguous_letter_ends_at_tier_three(config: ConfigBundle) -> None:
    journal = InMemoryJournalStore()
    result = run_pipeline(
        submission(AMBIGUOUS_LETTER),
        config=config,
        journal=journal,
        vault=InMemoryVaultStore(),
        text_detector=DETECTOR,
    )
    assert result.procedure_id is None
    assert int(result.decision.tier) == 3
    assert result.evidence.completeness.verdict.value == "not_evaluable"


def test_a_channel_hint_that_the_letter_contradicts_is_not_believed(
    config: ConfigBundle,
) -> None:
    derivation, _ = derive(config, EM_LETTER, hint="altersrente")
    assert derivation.procedure_id is None  # type: ignore[attr-defined]
    assert derivation.hint_contradicted is True  # type: ignore[attr-defined]


def test_a_channel_hint_the_letter_agrees_with_wins_the_source(
    config: ConfigBundle,
) -> None:
    """'The channel told us' and 'the letter told us' are different claims, and
    the metric reports them separately."""
    derivation, _ = derive(config, EM_LETTER, hint="erwerbsminderungsrente")
    assert derivation.procedure_id == "erwerbsminderungsrente"  # type: ignore[attr-defined]
    assert derivation.source is DerivationSource.HINT  # type: ignore[attr-defined]


def test_a_letter_about_something_else_derives_nothing(config: ConfigBundle) -> None:
    derivation, _ = derive(config, UNRELATED_LETTER)
    assert derivation.procedure_id is None  # type: ignore[attr-defined]
    assert derivation.candidates == ()  # type: ignore[attr-defined]
    assert derivation.ambiguous is False  # type: ignore[attr-defined]


# ------------------------------------------------ the structured path ---


def test_without_a_layer_the_text_signals_cannot_fire_at_all(
    config: ConfigBundle,
) -> None:
    """The regression identity the 77 frozen items rest on: an item with no
    prose evaluates exactly the predicates it evaluated before part 05."""
    result = build_ingest(
        {
            "submissionId": "structured-only",
            "destinationId": "drv-bund-eingang-test",
            "channel": "fit_connect",
            "submittedAt": "2026-08-11T09:00:00+00:00",
            "data": {"antrag": {"rentenart": "regelaltersrente"}},
        },
        versions=config.version_stamp(),
        vault=InMemoryVaultStore(),
        policy=config.redaction,
    )
    with_layer = derive_procedure(result.envelope, config.procedures, layer=None)
    assert with_layer.procedure_id == "altersrente"
    assert with_layer.source is DerivationSource.CONTENT
    assert build_text_layer(result.envelope, versions=config.version_stamp()) is None
