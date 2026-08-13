"""Routing and completeness evidence (probabilistic plane output).

Evidence records carry suggestions, verdicts, and gaps with provenance.
They never carry decisions; the decision plane interprets them against
versioned config.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .common import Span, Stamped, StrictModel


class RoutingSource(str, Enum):
    RULE = "rule"
    CLASSIFIER = "classifier"


class RoutingSuggestion(StrictModel):
    """One candidate organizational unit with its evidence."""

    unit_id: str = Field(description="Taxonomy node id, e.g. 'Referat_312_Renten'")
    source: RoutingSource
    rule_ids: list[str] = Field(
        default_factory=list, description="Rules that fired (source=RULE)"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Calibrated confidence (1.0 for rule hits)"
    )
    evidence_span: Span | None = Field(
        default=None, description="Passage supporting the suggestion"
    )


class RequirementStatus(str, Enum):
    PRESENT = "present"
    MISSING = "missing"
    INVALID = "invalid"


class GapItem(StrictModel):
    """One requirement that is not satisfied."""

    requirement_id: str
    status: RequirementStatus
    span: Span | None = Field(
        default=None, description="Where the invalid value was found, if any"
    )
    detail: str | None = None


class CompletenessVerdict(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    NOT_EVALUABLE = "not_evaluable"  # e.g. procedure unknown; pushes to tier 3


class CompletenessEvidence(StrictModel):
    procedure_id: str | None
    verdict: CompletenessVerdict
    gaps: list[GapItem] = Field(default_factory=list)
    requirements_version: str


class DerivationSource(str, Enum):
    HINT = "hint"
    CONTENT = "content"
    NONE = "none"


class DerivationOutcome(StrictModel):
    """How the procedure was identified (ADR-013/ADR-016)."""

    source: DerivationSource
    candidates: list[str] = Field(
        default_factory=list,
        description="Procedure ids whose signals fired; 2+ means ambiguity",
    )
    detail: str | None = Field(
        default=None, description="Refusal reason (ambiguity, hint contradicted)"
    )


class RoutingConflict(StrictModel):
    """Two or more units proposed for one item (ADR-014/ADR-016)."""

    unit_ids: list[str] = Field(min_length=2)
    resolved_by: str = Field(
        description="'priority' when the total order resolved it; "
        "'unresolved' drops the winner's confidence"
    )
    detail: str | None = None


class EvidenceRecord(Stamped):
    """Assembled evidence for one envelope, input to the decision table.

    Anomaly evidence is deliberately NOT part of this record; it travels as
    a separate AnomalyEvidence artifact because the decision-table schema
    may reference it only in downgrade conditions.
    """

    envelope_id: str
    case_id: str
    routing: list[RoutingSuggestion] = Field(default_factory=list)
    derivation: DerivationOutcome | None = Field(
        default=None, description="Procedure derivation outcome, if evaluated"
    )
    conflicts: list[RoutingConflict] = Field(
        default_factory=list,
        description="Routing conflicts; losing candidates survive here, "
        "not only in the journal",
    )
    completeness: CompletenessEvidence
    extraction_min_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Lowest confidence among used extractions",
    )
    extraction_discarded_count: int = Field(ge=0, default=0)
