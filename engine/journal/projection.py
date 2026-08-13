"""Derived case state: a projection, never a second source of truth.

The journal is the truth; this fold over its events is what the S1 "UI"
(``GET /cases/{case_id}``) and later the review UI render. Reading is
deliberately defensive - an unknown or malformed payload key degrades the
projection, it never raises - because a rendering bug must not be able to take
down the audit trail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from schemas.events import Event, EventType


class CaseState(BaseModel):
    """Everything the case view needs, folded out of the event list."""

    case_id: str
    event_count: int = 0
    received_at: datetime | None = None
    last_event_at: datetime | None = None
    last_event_type: EventType | None = None
    envelope_id: str | None = None
    channel: str | None = None
    procedure_hint: str | None = None
    procedure_id: str | None = None
    derivation: dict[str, Any] = Field(default_factory=dict)
    classifier: dict[str, Any] = Field(default_factory=dict)
    routing_arbitration: dict[str, Any] = Field(default_factory=dict)
    # What the privacy boundary did, by KIND and never by value. The review
    # UI's working-copy section renders exactly this: "an Aktenzeichen stood
    # here" is what a caseworker needs to read a sealed document, and it is
    # also the most a page outside the re-hydration surface may say.
    sealed_count: int | None = None
    text_sealed_counts: dict[str, int] = Field(default_factory=dict)
    redaction_verified: bool | None = None
    text_layer: dict[str, Any] = Field(default_factory=dict)
    extracted_fields: list[str] = Field(default_factory=list)
    extraction_verification: dict[str, Any] = Field(default_factory=dict)
    spans: list[dict[str, Any]] = Field(default_factory=list)
    discarded_count: int | None = None
    routing: list[dict[str, Any]] = Field(default_factory=list)
    completeness_verdict: str | None = None
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    clear_cut: bool | None = None
    anomaly: dict[str, Any] | None = None
    tier: int | None = None
    pre_downgrade_tier: int | None = None
    routed_unit_id: str | None = None
    reasons: list[dict[str, Any]] = Field(default_factory=list)
    shadow_downgrades: list[dict[str, Any]] = Field(default_factory=list)
    # ------------------------------------------------ the human's half (10) --
    # Everything above is what the machine did. Everything below is what a
    # caseworker did about it, and it is recorded the same way: as events that
    # were appended, never as fields that were updated. ``tier`` and
    # ``routed_unit_id`` therefore keep meaning "what the decision plane
    # decided" for the life of the case, and an override is a separate fact
    # next to it rather than an edit of it (ADR-008).
    drafts: list[dict[str, Any]] = Field(default_factory=list)
    confirmed_at: datetime | None = None
    confirmed_unit_id: str | None = None
    confirmation: dict[str, Any] | None = None
    overrides: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        """Whether a human has taken responsibility for this item."""
        return self.confirmed_at is not None


def derive_case_state(case_id: str, events: list[Event]) -> CaseState:
    """Fold a case's events into the current derived state."""
    state = CaseState(case_id=case_id, event_count=len(events))
    for event in sorted(events, key=lambda item: item.sequence):
        payload = event.payload
        state.last_event_at = event.occurred_at
        state.last_event_type = event.type
        if event.type is EventType.RECEIVED:
            state.received_at = event.occurred_at
            state.envelope_id = _as_str(payload.get("envelope_id"))
            state.channel = _as_str(payload.get("channel"))
            state.procedure_hint = _as_str(payload.get("procedure_hint"))
        elif event.type is EventType.REDACTED:
            state.sealed_count = _as_int(payload.get("sealed_count"))
            state.text_sealed_counts = _as_count_map(payload.get("text_sealed_counts"))
            state.redaction_verified = _as_bool(payload.get("redaction_verified"))
            state.text_layer = _as_dict(payload.get("text_layer"))
        elif event.type is EventType.EXTRACTED:
            state.extracted_fields = _as_str_list(payload.get("fields"))
            state.discarded_count = _as_int(payload.get("discarded_count"))
            state.extraction_verification = _as_dict(payload.get("verification"))
            state.spans = _as_dict_list(payload.get("spans"))
        elif event.type is EventType.EVIDENCE_ASSEMBLED:
            state.routing = _as_dict_list(payload.get("routing"))
            state.derivation = _as_dict(payload.get("procedure"))
            state.procedure_id = _as_str(state.derivation.get("procedure_id"))
            state.classifier = _as_dict(payload.get("classifier"))
            state.routing_arbitration = _as_dict(payload.get("routing_arbitration"))
            state.completeness_verdict = _as_str(payload.get("completeness_verdict"))
            state.gaps = _as_dict_list(payload.get("gaps"))
            state.clear_cut = _as_bool(payload.get("clear_cut"))
        elif event.type is EventType.ANOMALY_SCORED:
            state.anomaly = dict(payload)
        elif event.type is EventType.TIER_DECIDED:
            state.tier = _as_int(payload.get("tier"))
            state.pre_downgrade_tier = _as_int(payload.get("pre_downgrade_tier"))
            state.reasons = _as_dict_list(payload.get("reasons"))
            state.shadow_downgrades = _as_dict_list(payload.get("downgrades"))
        elif event.type is EventType.ROUTED:
            state.routed_unit_id = _as_str(payload.get("unit_id"))
        elif event.type is EventType.DRAFTED:
            state.drafts.append(dict(payload))
        elif event.type is EventType.CONFIRMED:
            state.confirmed_at = event.occurred_at
            state.confirmed_unit_id = event.actor.unit_id
            state.confirmation = dict(payload)
        elif event.type is EventType.OVERRIDDEN:
            state.overrides.append(
                {
                    **payload,
                    "occurred_at": event.occurred_at.isoformat(),
                    "unit_id": event.actor.unit_id,
                    "sequence": event.sequence,
                }
            )
    return state


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_count_map(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): count
        for key, count in value.items()
        if isinstance(count, int) and not isinstance(count, bool)
    }


def _as_dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
