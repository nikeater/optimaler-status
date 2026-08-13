"""Unit coverage for the scorer's small parts and its refusal branches.

The suites next door drive the scorer through the real pipeline, which is the
right way to test what it DOES. This file covers the parts that only appear
when something is wrong or absent - a malformed number, a feature nobody asked
about, a model that raises, an agency with no scoring config - because a
degradation path nobody exercises is a degradation path nobody knows still
degrades.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from engine.config_loader import ConfigBundle
from engine.journal.store import InMemoryJournalStore
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore
from engine.score import (
    Feature,
    FeatureVector,
    ScoringOutcome,
    load_reference,
    scorer_from_config,
)
from engine.score.config import IndizSpec, ScoringConfig
from engine.score.features import _at, _decimal, _working_copy
from engine.score.model import Attribution, ScoringModelError
from engine.score.scorer import Scorer


def _payload(gold_dir: Path, stem: str) -> dict[str, Any]:
    import json

    return json.loads((gold_dir / f"{stem}.json").read_text(encoding="utf-8"))


def _outcome(config: ConfigBundle, gold_dir: Path, stem: str) -> Any:
    return run_pipeline(
        _payload(gold_dir, stem),
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
    )


# ------------------------------------------------------- small helpers ---


def test_a_display_for_a_feature_nobody_computed_is_a_dash() -> None:
    """Never a KeyError in front of a caseworker, and never a blank either."""
    vector = FeatureVector(
        feature_set_version="fsv_v1",
        features=(Feature("leitdatum_vorhanden", 1.0, "Leitdatum vorhanden", True),),
    )
    assert vector.names == ("leitdatum_vorhanden",)
    assert vector.values == [1.0]
    assert vector.display("leitdatum_vorhanden") == "Leitdatum vorhanden"
    assert vector.display("gibt_es_nicht") == "-"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("100", 100.0), (" 70 ", 70.0), ("12,5", 12.5), ("keine Zahl", None), ("", None)],
)
def test_a_number_that_is_not_one_is_absent_rather_than_zero(
    text: str, expected: float | None
) -> None:
    """A form field holding "keine Angabe" must not become a 0 percent share."""
    assert _decimal(text) == expected


def test_a_path_that_does_not_resolve_is_absent(config: ConfigBundle) -> None:
    payload: dict[str, Any] = {"antrag": {"honorar_modell": "fest_monatlich"}}
    assert _at(payload, "antrag.honorar_modell") == "fest_monatlich"
    assert _at(payload, "antrag.gibt_es_nicht") is None
    assert _at(payload, "gibt.es.nicht") is None
    # A non-scalar leaf is absent rather than str()-ed: an address subtree
    # rendered into a feature would be a sealed object in a report.
    assert _at({"antragsteller": {"anschrift": {"ort": "x"}}}, "antragsteller") is None


def test_a_letter_has_no_structured_working_copy(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """The empty mapping is a real state: a letter answers no payload path."""
    outcome = _outcome(config, gold_v4_dir, "ar-0067-email-anschreiben-ohne-angaben")
    assert _working_copy(outcome.envelope) == {}


def test_an_envelope_with_no_structured_part_at_all_still_scores(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """A prose-only envelope: every payload feature is absent, nothing raises.

    Reached by dropping the structured part rather than by finding an item
    without one, because the ingest path always builds one today and the branch
    still has to hold for an adapter that does not.
    """
    outcome = _outcome(
        config, gold_v4_dir, "ar-0060-email-regelaltersrente-vollstaendig"
    )
    prose_only = outcome.envelope.model_copy(
        update={
            "parts": [
                part
                for part in outcome.envelope.parts
                if part.structured_payload is None
            ]
        }
    )
    assert prose_only.parts, "the fixture must carry a text part"
    assert _working_copy(prose_only) == {}


# ------------------------------------------------- outcome conveniences ---


def test_an_outcome_without_evidence_reports_nothing_rather_than_zero() -> None:
    outcome = ScoringOutcome(
        envelope_id="env", case_id="case", evidence=None, degraded=True
    )
    assert outcome.score is None
    assert outcome.flagged is False
    assert outcome.contribution("leitdatum_abstand_jahre") == 0.0
    assert outcome.mean_abs_contribution == 0.0


def test_contributions_are_addressed_by_feature_and_averaged_by_magnitude() -> None:
    """Sign-blind on purpose: P-2 asks how MUCH the model explains, not which way."""
    outcome = ScoringOutcome(
        envelope_id="env",
        case_id="case",
        evidence=None,
        attributions=(
            Attribution("leitdatum_abstand_jahre", 0.4, 1.0, 0.0),
            Attribution("ocr_vorgang", -0.2, 0.0, 0.0),
        ),
    )
    assert outcome.contribution("leitdatum_abstand_jahre") == 0.4
    assert outcome.contribution("gibt_es_nicht") == 0.0
    assert outcome.mean_abs_contribution == pytest.approx(0.3)


# --------------------------------------------------- refusals and gaps ---


def test_no_scoring_config_means_no_scorer(config: ConfigBundle) -> None:
    assert scorer_from_config(None, config.scoring_dir) is None


def test_a_missing_reference_file_raises_where_it_can_be_reported(
    config: ConfigBundle, tmp_path: Path
) -> None:
    with pytest.raises(ScoringModelError, match="missing reference population"):
        load_reference(tmp_path / "gibt-es-nicht.json")


def test_a_model_that_raises_degrades_the_item_rather_than_the_run(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """The catch-all branch: anything at all, and the item scores nothing.

    Driven with a model whose scoring raises, because the realistic version of
    this is a library upgrade changing a signature - and the system's answer to
    that has to be "no anomaly evidence", not a traceback out of the pipeline.
    """
    assert config.scoring is not None
    scorer = scorer_from_config(config.scoring, config.scoring_dir)
    assert scorer is not None
    outcome = _outcome(config, gold_v4_dir, "ar-0001-regelaltersrente-vollstaendig")

    names = scorer.model.feature_names

    class Exploding:
        feature_names = names

        def explain(self, values: object) -> None:
            raise RuntimeError("das Modell ist umgefallen")

    broken = Scorer(config.scoring, Exploding())  # type: ignore[arg-type]
    result = broken.score(
        outcome.envelope,
        outcome.extractions,
        outcome.evidence,
        procedure_id=outcome.procedure_id,
        field_paths={},
    )
    assert result.evidence is None
    assert result.degraded
    assert "scoring_failed: RuntimeError" in (result.degradation or "")
    assert "umgefallen" in (result.degradation or "")


def test_a_duplicate_indiz_path_is_refused(config: ConfigBundle) -> None:
    """Two rows for one field would weigh one Indiz twice in the bundle."""
    assert config.scoring is not None
    document = config.scoring.model_dump()
    document["indizien"] = [
        IndizSpec(
            path="antrag.weisungsgebunden",
            label="Weisungsgebundenheit",
            beschaeftigung_values=["ja"],
        ).model_dump(),
        IndizSpec(
            path="antrag.weisungsgebunden",
            label="Noch einmal dasselbe",
            beschaeftigung_values=["ja"],
        ).model_dump(),
    ]
    with pytest.raises(ValueError, match="duplicate Indiz path"):
        ScoringConfig.model_validate(document)


def test_the_scorer_exposes_the_feature_set_version_it_stamps(
    config: ConfigBundle,
) -> None:
    assert config.scoring is not None
    scorer = scorer_from_config(config.scoring, config.scoring_dir)
    assert scorer is not None
    assert scorer.feature_set_version == config.scoring.feature_set_version


def test_the_reference_population_knows_how_many_items_it_holds(
    config: ConfigBundle,
) -> None:
    assert config.scoring is not None
    population = load_reference(
        config.scoring_dir / config.scoring.reference_population
    )
    assert population.item_count == 101
    assert population.reference_id == config.scoring.reference_id


def test_drift_reports_a_full_disagreement_when_the_row_count_changed(
    config: ConfigBundle,
) -> None:
    """A population whose recorded scores do not line up is fully drifted.

    The number is reported rather than raised: a drift is a fact about the
    machine and the library version, and the report says so rather than
    refusing to run.
    """
    assert config.scoring is not None
    scorer = scorer_from_config(config.scoring, config.scoring_dir)
    assert scorer is not None
    trimmed = scorer.model.population
    shortened = trimmed.__class__(
        **{**trimmed.__dict__, "expected_scores": trimmed.expected_scores[:-1]}
    )
    model = scorer.model.__class__(shortened, scorer.model.params)
    assert model.drift() == 1.0
