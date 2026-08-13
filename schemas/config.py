"""Agency-editable, versioned config formats. Config is the product.

The one-way valve is a property of this format, not a policy promise:

  * QualifyingCondition rejects any reference to anomaly evidence, so no
    anomaly signal can qualify an item for a better tier or rescue a
    failed deterministic check.
  * DowngradeCondition is the ONLY place anomaly fields may appear, its
    target tier is fixed at tier 3, and anomaly fields may only be tested
    with monotone-increasing operators (gt/ge on score, eq-true on
    flagged), so RAISING anomaly evidence can never deactivate a
    downgrade and thereby raise a tier.

A Hypothesis property test additionally enforces end-to-end monotonicity
against the real decision-table interpreter on every commit.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import StrictModel, Tier

ANOMALY_FIELD_PREFIX = "anomaly."

#: Evidence fields qualifying conditions may reference (anomaly excluded
#: by construction; extend via ADR only).
QUALIFYING_FIELDS = frozenset(
    {
        "routing.confidence",
        "routing.rule_hit",
        "completeness.verdict",
        "completeness.gap_count",
        "extraction.min_confidence",
        "extraction.discarded_count",
        "procedure.tier1_enabled",
        "procedure.clear_cut",
    }
)

#: Anomaly fields downgrade conditions may reference, with their only
#: permitted operators (monotone in the anomaly direction).
DOWNGRADE_ANOMALY_FIELDS: dict[str, frozenset[str]] = {
    "anomaly.score": frozenset({"gt", "ge"}),
    "anomaly.flagged": frozenset({"eq"}),
}


class Op(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"
    IN = "in"


class QualifyingCondition(StrictModel):
    """Predicate over non-anomaly evidence. Used to qualify tiers 1 and 2."""

    field: str
    op: Op
    value: object

    @field_validator("field")
    @classmethod
    def _no_anomaly(cls, v: str) -> str:
        if v.startswith(ANOMALY_FIELD_PREFIX):
            raise ValueError(
                "anomaly evidence may not appear in qualifying conditions "
                "(one-way valve)"
            )
        if v not in QUALIFYING_FIELDS:
            raise ValueError(f"unknown qualifying field: {v}")
        return v


class DowngradeCondition(StrictModel):
    """Predicate over anomaly evidence. The ONLY place it may be referenced.

    Firing can only move an item to tier 3 (the engine applies
    max(current_tier, to_tier), so a downgrade never improves a tier).
    """

    field: str
    op: Op
    value: object

    @model_validator(mode="after")
    def _monotone_anomaly_only(self) -> "DowngradeCondition":
        allowed = DOWNGRADE_ANOMALY_FIELDS.get(self.field)
        if allowed is None:
            raise ValueError(
                f"downgrade conditions test anomaly fields only, got: {self.field}"
            )
        if self.op.value not in allowed:
            raise ValueError(
                f"op '{self.op.value}' on {self.field} is not monotone in "
                f"the anomaly direction; allowed: {sorted(allowed)}"
            )
        if self.field == "anomaly.flagged" and self.value is not True:
            raise ValueError("anomaly.flagged may only be tested against True")
        return self


class DecisionRow(StrictModel):
    """One row of the tier decision table; rows evaluate top-down, first
    match wins, default is tier 3 (in doubt, tier 3)."""

    row_id: str
    tier: Tier
    when_all: list[QualifyingCondition] = Field(min_length=1)


class DowngradeRule(StrictModel):
    row_id: str
    when_all: list[DowngradeCondition] = Field(min_length=1)
    to_tier: Literal[3] = 3
    reason_template: str = Field(
        description="Rendered with the anomaly feature-level reasons"
    )


class DecisionTable(StrictModel):
    """The versioned tier decision table (config/decision/)."""

    version: str
    rows: list[DecisionRow] = Field(min_length=1)
    downgrades: list[DowngradeRule] = Field(default_factory=list)
    default_tier: Literal[3] = 3


class ProcedureFlags(StrictModel):
    """Per-procedure legal/config gates (config/procedures/)."""

    procedure_id: str
    tier1_enabled: bool = Field(
        description="May tier 1 be produced at all; requires an identified "
        "legal basis (par. 35a VwVfG / par. 31a SGB X mapping)"
    )
    fully_automated: bool = Field(
        default=False,
        description="Reserved: flipping prepared-plus-confirm to automated "
        "issuance is a config change once the legal basis exists. Always "
        "False in phase 1.",
    )


class AnomalyThreshold(StrictModel):
    threshold_id: str
    value: float = Field(ge=0.0, le=1.0)
    calibrated_on: str = Field(description="Gold-set version used to calibrate")


class AgencyRiskConfig(StrictModel):
    """Versioned, agency-editable risk configuration (config/thresholds.yaml
    + config/decision/). Referenced by version from every DecisionRecord."""

    version: str
    scorer_mode: Literal["log_only", "enforcing"] = "log_only"
    thresholds: list[AnomalyThreshold] = Field(min_length=1)
    downgrade_rate_budget: float = Field(
        gt=0.0,
        le=1.0,
        description="Efficiency budget: max share of tier-1-eligible items "
        "the scorer may downgrade before it must return to log-only",
    )
    audit_sample_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Par. 88(5) Nr. 1 AO analog: share of tier-1/2 items "
        "deterministically sampled (salted case-id hash, reproducible) into "
        "full human review, journal-tagged audit_sample. Sampling only adds "
        "review, never removes it (valve-compatible). Engine support lands "
        "with part 09 (ADR-016).",
    )
    review_due: str | None = Field(
        default=None,
        description="ISO date by which thresholds and downgrade conditions "
        "must be re-reviewed (par. 88(5) Nr. 4 AO analog); the eval harness "
        "warns when overdue (ADR-016)",
    )
    procedures: list[ProcedureFlags] = Field(default_factory=list)


class RoutingRule(StrictModel):
    """Declarative routing rule (config/rules/); ships with fixtures."""

    rule_id: str
    unit_id: str
    priority: int = Field(
        default=100,
        ge=0,
        description="Arbitration precedence, lower wins; total order is "
        "(priority, rule_id). ADR-014/ADR-016.",
    )
    predicate: dict[str, object] = Field(
        description="Small YAML predicate AST interpreted by engine/evidence"
    )
    fixtures: list[str] = Field(
        min_length=1, description="Fixture ids CI runs against this rule"
    )


class Requirement(StrictModel):
    requirement_id: str
    description: str
    kind: Literal["field", "document"]
    validation: dict[str, object] | None = Field(
        default=None, description="Optional value constraints (format, range)"
    )


class RequirementList(StrictModel):
    """Per-procedure completeness requirements (config/procedures/)."""

    procedure_id: str
    version: str
    requirements: list[Requirement] = Field(min_length=1)


class TaxonomyNode(StrictModel):
    """One organizational unit from the published GVP (config/taxonomy/).
    Units and roles only; never named individuals (BPersVG)."""

    unit_id: str
    name: str
    parent_id: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    source: str = Field(description="Citation into the published organizational plan")
