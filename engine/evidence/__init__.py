"""Evidence plane: produces evidence, never decisions.

Procedure derivation, routing suggestions with explicit arbitration,
completeness verdicts with gaps and their Nachforderung wording, and the
per-procedure clear-cut result. All of it is input to the decision plane;
nothing here decides a tier.
"""

from engine.evidence.assemble import assemble_evidence
from engine.evidence.classify import (
    Calibration,
    CalibrationBin,
    ClassifierSuggestion,
    Embedder,
    HashingEmbedder,
    UnitClassifier,
    UnitText,
    classifier_from_config,
    render_item_text,
    unit_texts,
)
from engine.evidence.clearcut import evaluate_clear_cut
from engine.evidence.completeness import (
    UNKNOWN_REQUIREMENTS_VERSION,
    ValidationFailure,
    Visibility,
    evaluate_completeness,
    validation_problem,
)
from engine.evidence.context import build_context, build_payload_context
from engine.evidence.derive import (
    DerivationSource,
    HintStatus,
    ProcedureDerivation,
    content_candidates,
    derive_procedure,
)
from engine.evidence.nachforderung import GapRendering, render_gap, render_gaps
from engine.evidence.routing import (
    ALTERNATIVE_CONFIDENCE,
    CONTESTED_CONFIDENCE,
    RULE_CONFIDENCE,
    RoutingEngine,
    RoutingOutcome,
    UnitCandidate,
)

__all__ = [
    "ALTERNATIVE_CONFIDENCE",
    "CONTESTED_CONFIDENCE",
    "RULE_CONFIDENCE",
    "UNKNOWN_REQUIREMENTS_VERSION",
    "Calibration",
    "CalibrationBin",
    "ClassifierSuggestion",
    "DerivationSource",
    "Embedder",
    "GapRendering",
    "HashingEmbedder",
    "HintStatus",
    "ProcedureDerivation",
    "RoutingEngine",
    "RoutingOutcome",
    "UnitCandidate",
    "UnitClassifier",
    "UnitText",
    "ValidationFailure",
    "Visibility",
    "assemble_evidence",
    "build_context",
    "build_payload_context",
    "classifier_from_config",
    "content_candidates",
    "derive_procedure",
    "evaluate_clear_cut",
    "evaluate_completeness",
    "render_gap",
    "render_gaps",
    "render_item_text",
    "unit_texts",
    "validation_problem",
]
