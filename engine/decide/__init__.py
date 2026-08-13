"""Decision plane: deterministic tier decisions from versioned config.

Same evidence plus same config equals the same decision, reproducibly. Nothing
in this package is probabilistic, nothing calls a model, and anomaly evidence
can only ever raise a tier (ADR-004).
"""

from engine.decide.interpreter import (
    ADMITTED_ROUTING_SOURCES,
    AUDIT_SAMPLE_RULE_ID,
    SAMPLEABLE_TIERS,
    AuditSample,
    DowngradeOutcome,
    admitted_routing,
    audit_sample_draw,
    decide,
    evaluate_audit_sample,
    evaluate_downgrades,
    is_audit_sample_reason,
    resolve_anomaly_fields,
    resolve_qualifying_fields,
)

__all__ = [
    "ADMITTED_ROUTING_SOURCES",
    "AUDIT_SAMPLE_RULE_ID",
    "SAMPLEABLE_TIERS",
    "AuditSample",
    "DowngradeOutcome",
    "admitted_routing",
    "audit_sample_draw",
    "decide",
    "evaluate_audit_sample",
    "evaluate_downgrades",
    "is_audit_sample_reason",
    "resolve_anomaly_fields",
    "resolve_qualifying_fields",
]
