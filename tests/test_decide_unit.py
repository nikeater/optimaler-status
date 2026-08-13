"""Unit tests for the decision interpreter's edges and its defensive paths."""

from __future__ import annotations

import pytest

from engine.config_loader import ConfigBundle
from engine.decide import (
    admitted_routing,
    decide,
    evaluate_downgrades,
    resolve_anomaly_fields,
    resolve_qualifying_fields,
)
from engine.decide import interpreter as interpreter_module
from schemas.common import Tier
from schemas.config import (
    AgencyRiskConfig,
    AnomalyThreshold,
    DecisionRow,
    DecisionTable,
    DowngradeCondition,
    DowngradeRule,
    Op,
    ProcedureFlags,
    QualifyingCondition,
)
from schemas.decision import ReasonKind
from schemas.evidence import CompletenessVerdict, RoutingSource
from tests.factories import (
    FIXED_NOW,
    make_anomaly,
    make_completeness,
    make_evidence,
    make_suggestion,
)

TIER1_FLAGS = ProcedureFlags(
    procedure_id="altersrente", tier1_enabled=True, fully_automated=False
)


def _enforcing(config: ConfigBundle) -> AgencyRiskConfig:
    return config.risk.model_copy(update={"scorer_mode": "enforcing"})


def test_resolve_qualifying_fields_covers_exactly_the_allowed_names() -> None:
    from schemas.config import QUALIFYING_FIELDS

    fields = resolve_qualifying_fields(make_evidence(), TIER1_FLAGS, True)
    assert set(fields) == set(QUALIFYING_FIELDS)


def test_resolve_qualifying_fields_without_routing_or_flags() -> None:
    fields = resolve_qualifying_fields(
        make_evidence(routing=[], min_confidence=None), None, False
    )
    assert fields["routing.confidence"] == 0.0
    assert fields["routing.rule_hit"] is False
    assert fields["procedure.tier1_enabled"] is False
    assert fields["procedure.clear_cut"] is False
    assert fields["extraction.min_confidence"] is None


def test_an_unadmitted_classifier_suggestion_is_invisible_to_the_table() -> None:
    """Log-only, as a property of the decision plane rather than a promise.

    The suggestion is on the record - a caseworker sees it - and the table sees
    a routing confidence of 0.0 and no rule hit, exactly as it did before the
    classifier existed.
    """
    evidence = make_evidence(
        routing=[make_suggestion(source=RoutingSource.CLASSIFIER, confidence=0.97)]
    )
    fields = resolve_qualifying_fields(evidence, TIER1_FLAGS, True)
    assert fields["routing.rule_hit"] is False
    assert fields["routing.confidence"] == 0.0
    assert admitted_routing(evidence) == []
    assert evidence.routing != []


def test_an_admitted_classifier_suggestion_is_not_a_rule_hit_either() -> None:
    """Admitting the source lets its confidence in; it is still not a rule."""
    fields = resolve_qualifying_fields(
        make_evidence(
            routing=[make_suggestion(source=RoutingSource.CLASSIFIER, confidence=0.97)]
        ),
        TIER1_FLAGS,
        True,
        {RoutingSource.RULE, RoutingSource.CLASSIFIER},
    )
    assert fields["routing.rule_hit"] is False
    assert fields["routing.confidence"] == pytest.approx(0.97)


def test_an_unadmitted_suggestion_never_becomes_the_routed_unit(
    config: ConfigBundle,
) -> None:
    """The addressee half of the same rule: nobody's queue beats a guess."""
    evidence = make_evidence(
        routing=[
            make_suggestion(
                "Referat_320_Reha", source=RoutingSource.CLASSIFIER, confidence=0.97
            )
        ]
    )
    record = decide(evidence, None, config.decision_table, config.risk, TIER1_FLAGS)
    assert record.routed_unit_id is None
    assert int(record.tier) == 3

    admitted = decide(
        evidence,
        None,
        config.decision_table,
        config.risk,
        TIER1_FLAGS,
        routing_sources={RoutingSource.RULE, RoutingSource.CLASSIFIER},
    )
    assert admitted.routed_unit_id == "Referat_320_Reha"
    # Admitting the source moves the ADDRESSEE, not the tier: both table rows
    # still require routing.rule_hit, and no rule fired.
    assert int(admitted.tier) == 3


def test_resolve_anomaly_fields_exposes_only_score_and_flagged() -> None:
    fields = resolve_anomaly_fields(make_anomaly(score=0.42, flagged=False))
    assert fields == {"anomaly.score": 0.42, "anomaly.flagged": False}


def test_evaluate_downgrades_without_anomaly_is_empty(config: ConfigBundle) -> None:
    assert evaluate_downgrades(None, config.decision_table, enforcing=True) == []


def test_evaluate_downgrades_reports_would_fire_in_log_only(
    config: ConfigBundle,
) -> None:
    """Log-only still evaluates, so the journal can record what would happen."""
    outcomes = evaluate_downgrades(
        make_anomaly(score=0.99, flagged=True),
        config.decision_table,
        enforcing=False,
    )
    assert [outcome.fired for outcome in outcomes] == [True, True]
    assert [outcome.applied for outcome in outcomes] == [False, False]
    assert all("log_only" in outcome.detail for outcome in outcomes)


def test_unflagged_high_score_downgrade_renders_without_reasons(
    config: ConfigBundle,
) -> None:
    """A score-triggered downgrade on an unflagged item still reads sensibly."""
    record = decide(
        make_evidence(),
        make_anomaly(score=0.9, flagged=False, with_reasons=False),
        config.decision_table,
        _enforcing(config),
        TIER1_FLAGS,
        clear_cut=True,
        now=FIXED_NOW,
    )
    assert record.tier is Tier.FULL_HUMAN_REVIEW
    assert record.pre_downgrade_tier is Tier.CLEAR_AND_COMPLETE
    downgrades = [r for r in record.reasons if r.kind is ReasonKind.DOWNGRADED]
    assert len(downgrades) == 1
    assert "keine Merkmalsbegruendungen" in downgrades[0].detail


def test_unknown_placeholder_in_reason_template_stays_literal() -> None:
    """A typo in agency-editable config must not raise inside the valve."""
    table = DecisionTable(
        version="table_test",
        rows=[
            DecisionRow(
                row_id="always_tier1",
                tier=Tier.CLEAR_AND_COMPLETE,
                when_all=[
                    QualifyingCondition(
                        field="procedure.clear_cut", op=Op.EQ, value=True
                    )
                ],
            )
        ],
        downgrades=[
            DowngradeRule(
                row_id="dg_typo",
                when_all=[
                    DowngradeCondition(field="anomaly.flagged", op=Op.EQ, value=True)
                ],
                to_tier=3,
                reason_template="score {score}, tippfehler {vermerk}",
            )
        ],
        default_tier=3,
    )
    risk = AgencyRiskConfig(
        version="risk_test",
        scorer_mode="enforcing",
        thresholds=[
            AnomalyThreshold(threshold_id="t", value=0.85, calibrated_on="test")
        ],
        downgrade_rate_budget=0.15,
        procedures=[],
    )
    record = decide(
        make_evidence(),
        make_anomaly(score=0.91, flagged=True),
        table,
        risk,
        TIER1_FLAGS,
        clear_cut=True,
        now=FIXED_NOW,
    )
    detail = record.reasons[-1].detail
    assert "score 0.910" in detail
    assert "{vermerk}" in detail


def test_min_confidence_none_fails_a_condition_on_it() -> None:
    """Unresolvable evidence fails its condition instead of raising."""
    table = DecisionTable(
        version="table_test",
        rows=[
            DecisionRow(
                row_id="needs_min_confidence",
                tier=Tier.CLEAR_AND_COMPLETE,
                when_all=[
                    QualifyingCondition(
                        field="extraction.min_confidence", op=Op.GE, value=0.5
                    )
                ],
            )
        ],
        downgrades=[],
        default_tier=3,
    )
    risk = AgencyRiskConfig(
        version="risk_test",
        thresholds=[
            AnomalyThreshold(threshold_id="t", value=0.85, calibrated_on="test")
        ],
        downgrade_rate_budget=0.15,
    )
    record = decide(
        make_evidence(min_confidence=None),
        None,
        table,
        risk,
        TIER1_FLAGS,
        now=FIXED_NOW,
    )
    assert record.tier is Tier.FULL_HUMAN_REVIEW
    assert record.reasons[-1].kind is ReasonKind.DEFAULTED
    assert "nicht erfuellt" in record.reasons[0].detail


def test_routed_unit_is_the_highest_confidence_suggestion(
    config: ConfigBundle,
) -> None:
    record = decide(
        make_evidence(
            routing=[
                make_suggestion("Referat_390_Sonstiges", confidence=0.4),
                make_suggestion("Referat_312_Renten", confidence=0.8),
            ],
            completeness=make_completeness(
                CompletenessVerdict.INCOMPLETE, gap_ids=["x"]
            ),
        ),
        None,
        config.decision_table,
        config.risk,
        TIER1_FLAGS,
        now=FIXED_NOW,
    )
    assert record.routed_unit_id == "Referat_312_Renten"


def test_version_stamp_defaults_to_the_schema_version(config: ConfigBundle) -> None:
    record = decide(
        make_evidence(), None, config.decision_table, config.risk, TIER1_FLAGS
    )
    assert record.versions.decision_table_version == config.decision_table.version
    assert record.versions.thresholds_version == config.risk.version
    assert record.versions.taxonomy_version is None


def test_evaluation_errors_produce_tier_three_and_never_raise(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defensive path: broken evaluation means more oversight, not a 500."""

    def boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(interpreter_module, "resolve_qualifying_fields", boom)
    record = decide(
        make_evidence(),
        make_anomaly(),
        config.decision_table,
        config.risk,
        TIER1_FLAGS,
        clear_cut=True,
        now=FIXED_NOW,
    )
    assert record.tier is Tier.FULL_HUMAN_REVIEW
    assert record.pre_downgrade_tier is Tier.FULL_HUMAN_REVIEW
    assert record.routed_unit_id is None
    assert [reason.kind for reason in record.reasons] == [ReasonKind.ERROR]
    assert "RuntimeError" in record.reasons[0].detail
