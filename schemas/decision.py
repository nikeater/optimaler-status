"""Deterministic tier decision record (decision plane output).

Same evidence + same config = same decision, reproducibly. The record
stamps every config version used so the journal can prove it.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .common import Stamped, StrictModel, Tier


class ReasonKind(str, Enum):
    QUALIFIED = "qualified"  # a qualifying condition satisfied
    FAILED = "failed"  # a qualifying condition not satisfied
    DOWNGRADED = "downgraded"  # a downgrade condition fired (anomaly)
    DEFAULTED = "defaulted"  # in doubt, tier 3
    ERROR = "errored"  # defensive path; errors push toward tier 3
    # ADR-025: a deterministic audit draw (P-1) is not a suspicion. The kind
    # exists so no consumer can mistake a sampled case for a flagged one.
    SAMPLED = "sampled"


class DecisionReason(StrictModel):
    """Machine-readable reason, renderable to caseworkers."""

    kind: ReasonKind
    rule_id: str = Field(description="Decision-table row or downgrade id")
    detail: str = Field(description="Human-readable rendering")


class DecisionRecord(Stamped):
    """The tier decision for one envelope."""

    envelope_id: str
    case_id: str
    tier: Tier
    pre_downgrade_tier: Tier = Field(
        description="Tier from qualifying conditions alone; tier >= this "
        "value always (one-way valve invariant, property-tested)"
    )
    routed_unit_id: str | None = Field(
        default=None, description="Chosen organizational unit (never a person)"
    )
    reasons: list[DecisionReason] = Field(min_length=1)
    decision_table_version: str
    risk_config_version: str

    def model_post_init(self, __context: object) -> None:
        # Structural half of the one-way valve: a persisted decision can
        # never show a downgrade that improved the tier.
        if self.tier.value < self.pre_downgrade_tier.value:
            raise ValueError(
                "tier may never be better than pre_downgrade_tier (one-way valve)"
            )
