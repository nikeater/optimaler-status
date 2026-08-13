"""Shadow-scorer anomaly evidence: the downgrade-only input.

Identity-blind by construction: the scorer sees extracted procedural
features only; everything the vault seals never reaches it. Every score
above threshold must carry feature-level reasons a caseworker can read.
The scorer launches log-only and may not enforce downgrades until its
reviewed flag precision earns it (mode lives in AgencyRiskConfig).

Normative feature-set exclusions (ADR-016, FSV/toeslagenaffaire lesson):
the identity-blind feature set may contain NO per-applicant history and
NO prior-flag features - an earlier flag on the same applicant must never
raise a later score, or flags become self-reinforcing. Part 09 enforces
this with a property test over the feature-set contract.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .common import Stamped, StrictModel


class ScorerMode(str, Enum):
    LOG_ONLY = "log_only"
    ENFORCING = "enforcing"


class AnomalyReason(StrictModel):
    """One feature-level, caseworker-readable reason."""

    feature: str = Field(description="Feature id from the identity-blind set")
    observed: str = Field(description="Observed value/pattern, rendered")
    expected: str = Field(description="Expected range/pattern, rendered")
    contribution: float = Field(description="Signed contribution to the anomaly score")


class AnomalyEvidence(Stamped):
    """Scorer output for one envelope. Referencable ONLY in downgrade
    conditions of the decision table; the config schema has no other field
    for it (see config.py)."""

    envelope_id: str
    case_id: str
    score: float = Field(ge=0.0, le=1.0, description="Calibrated anomaly score")
    threshold_ref: str = Field(
        description="Id of the calibrated threshold in AgencyRiskConfig"
    )
    flagged: bool = Field(description="score above the referenced threshold")
    reasons: list[AnomalyReason] = Field(
        default_factory=list,
        description="MANDATORY when flagged; a flag without readable "
        "reasons never ships",
    )
    mode: ScorerMode = ScorerMode.LOG_ONLY

    def model_post_init(self, __context: object) -> None:
        if self.flagged and not self.reasons:
            raise ValueError("a flagged item must carry feature-level reasons")
