"""Append-only case journal event envelope.

Every stage writes immutable, version-stamped events; current state is
derived. Audit log, AI-Act logging, Art. 22 human-involvement proof,
applicant notifications, and correction-training data are all projections
of this one mechanism.

Notification rule encoded here: the journal's 'received' event triggers the
instant receipt, 'routed' triggers the status update. Notifications are
informational Realakte, never Verwaltungsakte, and never pass the review UI.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, model_validator

from .common import StrictModel, VersionStamp


class EventType(str, Enum):
    RECEIVED = "received"
    REDACTED = "redacted"
    EXTRACTED = "extracted"
    EVIDENCE_ASSEMBLED = "evidence_assembled"
    ANOMALY_SCORED = "anomaly_scored"
    TIER_DECIDED = "tier_decided"
    ROUTED = "routed"
    DRAFTED = "drafted"
    CONFIRMED = "confirmed"
    OVERRIDDEN = "overridden"
    NOTIFIED = "notified"


class ActorKind(str, Enum):
    SYSTEM = "system"
    CASEWORKER = "caseworker"  # role/unit scoped; never per-person telemetry


class Actor(StrictModel):
    kind: ActorKind
    unit_id: str | None = Field(
        default=None,
        description="Organizational unit for caseworker actions. Session-"
        "scoped confirm identity stays in the agency IdP, not the journal.",
    )


class Event(StrictModel):
    """One immutable journal entry."""

    event_id: str
    case_id: str
    sequence: int = Field(ge=0, description="Monotonic per case")
    type: EventType
    occurred_at: datetime
    actor: Actor
    versions: VersionStamp
    payload: dict[str, object] = Field(default_factory=dict)
    informational_only: bool | None = Field(
        default=None,
        description="REQUIRED (True) for NOTIFIED events: marks the outbound "
        "message as a Realakt, no Verwaltungsakt",
    )
    template_id: str | None = Field(
        default=None, description="Notification/draft template used, if any"
    )

    @model_validator(mode="after")
    def _notification_invariants(self) -> "Event":
        if self.type is EventType.NOTIFIED:
            if self.informational_only is not True:
                raise ValueError("NOTIFIED events must carry informational_only=True")
            if not self.template_id:
                raise ValueError("NOTIFIED events must carry a template_id")
        return self
