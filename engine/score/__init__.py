"""The shadow scorer: identity-blind anomaly evidence, and nothing else.

This package produces exactly one artifact - :class:`schemas.anomaly.
AnomalyEvidence` - and the decision table may reference it in exactly one
syntactic place (ADR-004). It adds oversight or it adds nothing.

    working copy + evidence
      -> features   identity-blind vector, guarded  (engine/score/features.py)
      -> model      IsolationForest over the frozen reference population
      -> reasons    German, feature-level, one per contribution
      -> AnomalyEvidence, journaled as ANOMALY_SCORED, read by evaluate_downgrades

Nothing here decides anything, nothing here can lower a tier, and nothing here
may stop the pipeline: every failure produces no evidence and a journaled
degradation, which is the state the decision plane was in for eight parts.
"""

from engine.score.config import ScoringConfig
from engine.score.features import (
    FEATURE_IDS,
    Feature,
    FeatureGuardError,
    FeaturePolicy,
    FeatureVector,
    ScoringInput,
    build_features,
)
from engine.score.model import (
    REFERENCE_ARTIFACT,
    Attribution,
    ForestParams,
    ReferencePopulation,
    ScoringModel,
    ScoringModelError,
    build_model,
    load_reference,
    parse_reference,
    reference_document,
    sklearn_version,
)
from engine.score.reasons import build_reasons, reason_is_readable, render_reason
from engine.score.scorer import Scorer, ScoringOutcome, scorer_from_config

__all__ = [
    "FEATURE_IDS",
    "REFERENCE_ARTIFACT",
    "Attribution",
    "Feature",
    "FeatureGuardError",
    "FeaturePolicy",
    "FeatureVector",
    "ForestParams",
    "ReferencePopulation",
    "Scorer",
    "ScoringConfig",
    "ScoringInput",
    "ScoringModel",
    "ScoringModelError",
    "ScoringOutcome",
    "build_features",
    "build_model",
    "build_reasons",
    "load_reference",
    "parse_reference",
    "reason_is_readable",
    "reference_document",
    "render_reason",
    "scorer_from_config",
    "sklearn_version",
]
