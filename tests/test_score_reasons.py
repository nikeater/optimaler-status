"""Reasons, determinism and the calibration artifact: the scorer's own suite.

Three groups, and each one is a promise made in an earlier part:

* **Reasons.** ADR-004, part 01: "every flag carries feature-level reasons; a
  flag without readable reasons never ships." Here that is the contract
  (``AnomalyEvidence`` refuses a flag with no reasons), the renderer (a German
  sentence with all four parts), the fallback (an item unusual only as a
  COMBINATION still gets a reason) and the corpus (every flag on gold v4).
* **Determinism.** ADR-024: two runs of the same item produce the identical
  score, flag and reasons on this machine and this library version. The claim
  is machine-local and the version is recorded in the artifact.
* **The reference population.** It is a pure function of (corpus, feature set,
  seed, engine), which ``python -m eval.score_fit --check`` turns into a gate,
  and it refuses to load when it was fitted on a different feature set.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from engine.config_loader import ConfigBundle, ConfigError, load_config
from engine.journal.store import InMemoryJournalStore
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore
from engine.score import (
    FEATURE_IDS,
    ScoringInput,
    ScoringModel,
    ScoringModelError,
    build_features,
    parse_reference,
    reason_is_readable,
    render_reason,
    scorer_from_config,
)
from engine.score.config import ScoringConfig
from eval.harness import load_corpus
from eval.score_fit import build_rows, fit_document, render
from schemas.anomaly import AnomalyEvidence, AnomalyReason


def _payload(gold_dir: Path, stem: str) -> dict[str, object]:
    return json.loads((gold_dir / f"{stem}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------- reasons ---


def test_the_contract_refuses_a_flag_without_reasons() -> None:
    """The structural half, which has been true since part 01."""
    from tests.factories import FIXED_NOW, TEST_VERSIONS

    with pytest.raises(ValueError, match="feature-level reasons"):
        AnomalyEvidence(
            envelope_id="env",
            case_id="case",
            score=0.99,
            threshold_ref="anomaly_gold_v4_v1",
            flagged=True,
            reasons=[],
            created_at=FIXED_NOW,
            versions=TEST_VERSIONS,
        )


def test_a_rendered_reason_names_feature_observation_expectation_and_share() -> None:
    """The four parts the gate checks, in one German sentence."""
    reason = AnomalyReason(
        feature="leitdatum_abstand_jahre",
        observed="Abstand des Leitdatums zum Eingang: rentenbeginn 2039-01-01",
        expected="Referenzbestand: Median 0 Jahre, mittlere Haelfte -0.54 bis 0.25",
        contribution=0.99,
    )
    rendered = render_reason(reason)
    assert rendered.startswith("Merkmal leitdatum_abstand_jahre:")
    assert "beobachtet" in rendered
    assert "erwartet" in rendered
    assert "+0.990" in rendered
    assert rendered.endswith(".")
    assert reason_is_readable(reason)


@pytest.mark.parametrize(
    "reason",
    [
        AnomalyReason(feature=" ", observed="x", expected="y", contribution=0.1),
        AnomalyReason(feature="f", observed=" ", expected="y", contribution=0.1),
        AnomalyReason(feature="f", observed="x", expected=" ", contribution=0.1),
        AnomalyReason(
            feature="f",
            observed="unbeschriebenes Merkmal",
            expected="Referenzbestand: Median 0",
            contribution=0.1,
        ),
        AnomalyReason(
            feature="f",
            observed="Abstand des Leitdatums: 12 Jahre",
            expected="unbeschriebenes Merkmal",
            contribution=0.1,
        ),
        AnomalyReason(feature="f", observed="x", expected="y", contribution=0.1),
    ],
)
def test_an_incomplete_reason_is_not_readable(reason: AnomalyReason) -> None:
    """What the eval gate refuses. The last case is simply too short to be one."""
    assert not reason_is_readable(reason)


def test_every_flag_on_gold_v4_carries_a_readable_reason(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """The gate, over the whole corpus rather than over an example."""
    flagged = 0
    for item in load_corpus(gold_v4_dir):
        outcome = run_pipeline(
            item.payload,
            config=config,
            journal=InMemoryJournalStore(),
            vault=InMemoryVaultStore(),
        )
        anomaly = outcome.anomaly
        assert anomaly is not None, item.item_id
        if not anomaly.flagged:
            assert not anomaly.reasons, item.item_id
            continue
        flagged += 1
        assert anomaly.reasons, item.item_id
        for reason in anomaly.reasons:
            assert reason.feature in FEATURE_IDS, item.item_id
            assert reason_is_readable(reason), (item.item_id, reason)
    assert flagged == 15


def test_a_flag_that_no_single_feature_explains_still_gets_a_reason(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """The fallback, driven rather than described.

    With ``min_contribution`` set above every possible contribution, no feature
    clears the bar - which is exactly the "unusual only as a combination" case -
    and the renderer must still produce one reason, or a flagged item would
    reach the contract with an empty list and raise.
    """
    assert config.scoring is not None
    strict = config.scoring.model_copy(
        update={
            "reasons": config.scoring.reasons.model_copy(
                update={"min_contribution": 1.0}
            )
        }
    )
    scorer = scorer_from_config(strict, config.scoring_dir)
    assert scorer is not None
    payload = _payload(gold_v4_dir, "sf-0041-honorar-unter-vergleichslohn")
    outcome = run_pipeline(
        payload,
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
    )
    procedure = config.procedure(outcome.procedure_id)
    result = scorer.score(
        outcome.envelope,
        outcome.extractions,
        outcome.evidence,
        procedure_id=outcome.procedure_id,
        field_paths=procedure.field_paths if procedure else {},
    )
    assert result.evidence is not None and result.evidence.flagged
    assert len(result.evidence.reasons) == 1
    assert reason_is_readable(result.evidence.reasons[0])


def test_reasons_are_capped_and_ordered_by_contribution(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """A caseworker reading five sentences reads none of them."""
    assert config.scoring is not None
    for stem in (
        "sf-0040-scheinselbststaendigkeit-indizienbuendel",
        "ar-0040-rentenbeginn-weit-in-der-zukunft",
    ):
        outcome = run_pipeline(
            _payload(gold_v4_dir, stem),
            config=config,
            journal=InMemoryJournalStore(),
            vault=InMemoryVaultStore(),
        )
        assert outcome.anomaly is not None
        reasons = outcome.anomaly.reasons
        assert 1 <= len(reasons) <= config.scoring.max_reasons
        contributions = [reason.contribution for reason in reasons]
        assert contributions == sorted(contributions, reverse=True)


def test_the_reason_for_the_date_anomaly_names_the_date_and_the_distance(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """The one sentence this whole part exists to be able to print."""
    outcome = run_pipeline(
        _payload(gold_v4_dir, "ar-0040-rentenbeginn-weit-in-der-zukunft"),
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
    )
    assert outcome.anomaly is not None and outcome.anomaly.flagged
    top = render_reason(outcome.anomaly.reasons[0])
    assert "leitdatum_abstand_jahre" in top
    assert "rentenbeginn 2039-01-01" in top
    assert "+4539 Tage" in top
    assert "Referenzbestand" in top


# --------------------------------------------------------- determinism ---


def test_two_runs_of_the_whole_corpus_produce_identical_anomaly_outcomes(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """The two-run gate, in the suite rather than only in the release checklist."""
    items = load_corpus(gold_v4_dir)

    def outcomes() -> list[tuple[str, float, bool, tuple[str, ...]]]:
        rows = []
        for item in items:
            result = run_pipeline(
                item.payload,
                config=config,
                journal=InMemoryJournalStore(),
                vault=InMemoryVaultStore(),
            )
            anomaly = result.anomaly
            assert anomaly is not None
            rows.append(
                (
                    item.item_id,
                    anomaly.score,
                    anomaly.flagged,
                    tuple(render_reason(reason) for reason in anomaly.reasons),
                )
            )
        return rows

    assert outcomes() == outcomes()


def test_the_score_does_not_depend_on_the_random_placeholder_tokens(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """Part 06's bug class, checked for the scorer.

    Every run seals identity behind freshly drawn random tokens. If a single
    feature read one of them, the score would wobble between runs - which is
    exactly how the classifier's 0.0003 ranking margin was found to be noise.
    """
    payload = _payload(gold_v4_dir, "sf-0040-scheinselbststaendigkeit-indizienbuendel")
    tokens = set()
    scores = set()
    for _ in range(4):
        outcome = run_pipeline(
            payload,
            config=config,
            journal=InMemoryJournalStore(),
            vault=InMemoryVaultStore(),
        )
        assert outcome.anomaly is not None
        scores.add(outcome.anomaly.score)
        tokens.add(outcome.envelope.vault_ref)
    assert len(tokens) == 4, "the run is not drawing fresh tokens; the test is vacuous"
    assert len(scores) == 1


# ------------------------------------------------- reference population ---


def test_the_reference_population_reproduces_from_the_frozen_corpus(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """Calibration reproducibility: the committed artifact IS the recomputed one."""
    rows = build_rows(load_corpus(gold_v4_dir), config=config)
    document, _ = fit_document(rows, config=config, corpus=gold_v4_dir)
    assert config.scoring is not None
    committed = (config.scoring_dir / config.scoring.reference_population).read_text(
        encoding="utf-8"
    )
    assert render(document) == committed


def test_the_artifact_records_what_produced_it(config: ConfigBundle) -> None:
    """Provenance is not optional: a matrix of numbers is not a calibration."""
    assert config.scoring is not None
    document = json.loads(
        (config.scoring_dir / config.scoring.reference_population).read_text(
            encoding="utf-8"
        )
    )
    assert document["artifact"] == "anomaly_reference_population"
    assert document["feature_set_version"] == config.scoring.feature_set_version
    assert document["reference_id"] == config.scoring.reference_id
    assert document["seed"] == config.scoring.forest.seed
    assert document["feature_names"] == list(FEATURE_IDS)
    assert document["item_count"] == 101
    assert document["sklearn_version"]
    assert document["row_ids"] == sorted(document["row_ids"])


def test_a_population_fitted_on_another_feature_set_is_refused(
    config: ConfigBundle,
) -> None:
    """The failure this artifact exists to make loud rather than silent."""
    assert config.scoring is not None
    document = json.loads(
        (config.scoring_dir / config.scoring.reference_population).read_text(
            encoding="utf-8"
        )
    )
    document["feature_names"] = ["etwas_anderes"]
    with pytest.raises(ScoringModelError, match="re-fit"):
        parse_reference(json.dumps(document), label="test")


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda doc: doc.update({"artifact": "irgendwas"}), "artifact"),
        (lambda doc: doc.update({"rows": []}), "no reference rows"),
        (lambda doc: doc["rows"].append([1.0]), "rows of width"),
    ],
)
def test_a_malformed_population_is_refused_with_its_reason(
    config: ConfigBundle, mutate: object, match: str
) -> None:
    assert config.scoring is not None
    document = json.loads(
        (config.scoring_dir / config.scoring.reference_population).read_text(
            encoding="utf-8"
        )
    )
    mutate(document)  # type: ignore[operator]
    with pytest.raises(ScoringModelError, match=match):
        parse_reference(json.dumps(document), label="test")


def test_unreadable_json_is_refused_rather_than_ignored() -> None:
    with pytest.raises(ScoringModelError, match="readable JSON"):
        parse_reference("{nope", label="test")


def test_a_missing_population_degrades_the_item_and_never_the_pipeline(
    config: ConfigBundle, gold_v4_dir: Path, tmp_path: Path
) -> None:
    """A scorer that cannot load costs the extra oversight and nothing else."""
    assert config.scoring is not None
    broken = config.scoring.model_copy(
        update={"reference_population": "gibt-es-nicht.json"}
    )
    bundle = config.__class__(**{**config.__dict__, "scoring": broken})
    journal = InMemoryJournalStore()
    outcome = run_pipeline(
        _payload(gold_v4_dir, "ar-0001-regelaltersrente-vollstaendig"),
        config=bundle,
        journal=journal,
        vault=InMemoryVaultStore(),
    )
    assert outcome.anomaly is None
    assert outcome.scoring is not None and outcome.scoring.degraded
    assert "scorer_unavailable" in (outcome.scoring.degradation or "")
    assert int(outcome.decision.tier) == 1  # unchanged, and never worse than that
    events = [
        event
        for event in journal.read(outcome.envelope.case_id)
        if event.type.value == "anomaly_scored"
    ]
    assert events and events[0].payload["degraded"] is True


def test_an_agency_without_a_scoring_directory_runs_no_scorer(
    tmp_path: Path, gold_v4_dir: Path
) -> None:
    """Absent is a defined state, exactly as it is for the classifier and drafts."""
    import shutil

    source = Path("config")
    target = tmp_path / "config"
    shutil.copytree(source, target)
    shutil.rmtree(target / "scoring")
    bundle = load_config(target)
    assert bundle.scoring is None
    journal = InMemoryJournalStore()
    outcome = run_pipeline(
        _payload(gold_v4_dir, "ar-0001-regelaltersrente-vollstaendig"),
        config=bundle,
        journal=journal,
        vault=InMemoryVaultStore(),
    )
    assert outcome.anomaly is None
    assert outcome.scoring is None
    assert not [
        event
        for event in journal.read(outcome.envelope.case_id)
        if event.type.value == "anomaly_scored"
    ]


# ------------------------------------------------------------- config ---


def _document(config: ConfigBundle) -> dict[str, Any]:
    import yaml

    assert config.scoring is not None
    path = config.scoring_dir / f"{config.scoring.version}.yaml"
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return document


def test_a_feature_the_engine_does_not_compute_is_refused(
    config: ConfigBundle,
) -> None:
    document = _document(config)
    features = dict(document["features"])
    features["erfundenes_merkmal"] = {"label": "x"}
    document["features"] = features
    with pytest.raises(ValueError, match="unknown feature"):
        ScoringConfig.model_validate(document)


def test_a_feature_without_wording_is_refused(config: ConfigBundle) -> None:
    document = _document(config)
    features = dict(document["features"])
    features.pop(FEATURE_IDS[0])
    document["features"] = features
    with pytest.raises(ValueError, match="no wording"):
        ScoringConfig.model_validate(document)


def test_a_short_salt_is_refused(config: ConfigBundle) -> None:
    document = _document(config)
    document["audit_sampling"] = {"salt": "kurz"}
    with pytest.raises(ValueError):
        ScoringConfig.model_validate(document)


def test_a_leading_date_field_no_procedure_maps_is_refused(
    config: ConfigBundle, tmp_path: Path
) -> None:
    """The loader check, driven through a real config directory."""
    import shutil

    import yaml

    target = tmp_path / "config"
    shutil.copytree(Path("config"), target)
    path = target / "scoring" / "scoring_v1.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["leading_date_fields"]["altersrente"] = "gibt_es_nicht"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError, match="leading dates"):
        load_config(target)


def test_a_threshold_id_that_collides_with_the_risk_config_is_refused(
    config: ConfigBundle, tmp_path: Path
) -> None:
    """``threshold_ref`` must name exactly one number."""
    import shutil

    import yaml

    target = tmp_path / "config"
    shutil.copytree(Path("config"), target)
    path = target / "scoring" / "scoring_v1.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["threshold"]["threshold_id"] = "anomaly_default_v0"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError, match="exactly one number"):
        load_config(target)


def test_the_model_reads_its_reference_spans_from_the_population(
    config: ConfigBundle,
) -> None:
    """The 'expected' half of a reason is a measurement, not a guess."""
    assert config.scoring is not None
    scorer = scorer_from_config(config.scoring, config.scoring_dir)
    assert scorer is not None
    low, median, high = scorer.model.reference_span("leitdatum_abstand_jahre")
    assert low <= median <= high
    assert isinstance(scorer.model, ScoringModel)
    assert scorer.model.drift() == 0.0


def test_the_two_readings_are_reported_separately(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """A combination finding and a single-value finding are different findings."""
    assert config.scoring is not None
    scorer = scorer_from_config(config.scoring, config.scoring_dir)
    assert scorer is not None
    outcome = run_pipeline(
        _payload(gold_v4_dir, "ar-0040-rentenbeginn-weit-in-der-zukunft"),
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
    )
    procedure = config.procedure(outcome.procedure_id)
    vector = build_features(
        ScoringInput(
            envelope=outcome.envelope,
            extractions=outcome.extractions,
            evidence=outcome.evidence,
            procedure_id=outcome.procedure_id,
            field_paths=procedure.field_paths if procedure else {},
        ),
        config.scoring.policy,
    )
    combination, single = scorer.model.readings(vector.values)
    assert 0.0 <= combination <= 1.0
    assert 0.0 <= single <= 1.0
    # This item is the extreme of one column, so the single-value reading is
    # what marks it - which is the whole reason the second reading exists.
    assert single > combination
