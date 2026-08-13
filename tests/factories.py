"""Builders for contract objects, so tests read like the case they describe."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from schemas import SCHEMA_VERSION
from schemas.anomaly import AnomalyEvidence, AnomalyReason, ScorerMode
from schemas.common import VersionStamp
from schemas.envelope import ContentPart, Envelope, RawRef
from schemas.evidence import (
    CompletenessEvidence,
    CompletenessVerdict,
    EvidenceRecord,
    GapItem,
    RequirementStatus,
    RoutingSource,
    RoutingSuggestion,
)
from schemas.extraction import ExtractionRecord, ExtractionSet, MatchMode
from schemas.textlayer import OffsetSegment, TextLayer, TextLayerPart

FIXED_NOW = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
TEST_VERSIONS = VersionStamp(
    schema_version=SCHEMA_VERSION,
    taxonomy_version="taxonomy_drv_bund_v0",
    rules_version="routing_v0",
    decision_table_version="table_v0",
    thresholds_version="risk_v0",
)


def make_suggestion(
    unit_id: str = "Referat_312_Renten",
    *,
    source: RoutingSource = RoutingSource.RULE,
    confidence: float = 1.0,
    rule_ids: Sequence[str] = ("rule_altersrente_hint",),
) -> RoutingSuggestion:
    """A routing suggestion, rule-sourced by default."""
    return RoutingSuggestion(
        unit_id=unit_id,
        source=source,
        rule_ids=list(rule_ids),
        confidence=confidence,
        evidence_span=None,
    )


def make_completeness(
    verdict: CompletenessVerdict = CompletenessVerdict.COMPLETE,
    *,
    procedure_id: str | None = "altersrente",
    gap_ids: Sequence[str] = (),
    requirements_version: str = "altersrente_requirements_v0",
) -> CompletenessEvidence:
    """Completeness evidence with simple MISSING gaps."""
    return CompletenessEvidence(
        procedure_id=procedure_id,
        verdict=verdict,
        gaps=[
            GapItem(
                requirement_id=gap_id,
                status=RequirementStatus.MISSING,
                span=None,
                detail=None,
            )
            for gap_id in gap_ids
        ],
        requirements_version=requirements_version,
    )


def make_evidence(
    *,
    routing: Sequence[RoutingSuggestion] | None = None,
    completeness: CompletenessEvidence | None = None,
    min_confidence: float | None = 1.0,
    discarded_count: int = 0,
    case_id: str = "case-test",
    envelope_id: str = "env-test",
) -> EvidenceRecord:
    """An evidence record that qualifies for tier 1 unless told otherwise."""
    return EvidenceRecord(
        envelope_id=envelope_id,
        case_id=case_id,
        routing=list(routing) if routing is not None else [make_suggestion()],
        completeness=completeness or make_completeness(),
        extraction_min_confidence=min_confidence,
        extraction_discarded_count=discarded_count,
        created_at=FIXED_NOW,
        versions=TEST_VERSIONS,
    )


def make_anomaly(
    score: float = 0.9,
    *,
    flagged: bool = True,
    threshold_ref: str = "anomaly_default_v0",
    mode: ScorerMode = ScorerMode.LOG_ONLY,
    with_reasons: bool = True,
    case_id: str = "case-test",
    envelope_id: str = "env-test",
) -> AnomalyEvidence:
    """Anomaly evidence; flagged items always carry readable reasons."""
    reasons = (
        [
            AnomalyReason(
                feature="rentenbeginn_abstand_tage",
                observed="4",
                expected="30..365",
                contribution=0.42,
            )
        ]
        if with_reasons or flagged
        else []
    )
    return AnomalyEvidence(
        envelope_id=envelope_id,
        case_id=case_id,
        score=score,
        threshold_ref=threshold_ref,
        flagged=flagged,
        reasons=reasons,
        mode=mode,
        created_at=FIXED_NOW,
        versions=TEST_VERSIONS,
    )


def make_envelope(
    payload: dict[str, Any] | None = None,
    *,
    procedure_hint: str | None = "altersrente",
    case_id: str = "case-test",
    envelope_id: str = "env-test",
) -> Envelope:
    """A minimal structured envelope."""
    return Envelope(
        envelope_id=envelope_id,
        case_id=case_id,
        channel="fit_connect",
        procedure_hint=procedure_hint,
        raw_refs=[RawRef(ref_id="raw-1", media_type="application/json")],
        vault_ref="vault:test",
        parts=[
            ContentPart(
                part_id="part-structured-0",
                source_type="born_digital",
                media_type="application/json",
                redacted_text=None,
                structured_payload=payload if payload is not None else {},
            )
        ],
        redaction_verified=True,
        created_at=FIXED_NOW,
        versions=TEST_VERSIONS,
    )


def make_text_layer(
    *parts: tuple[str, str, str],
    case_id: str = "case-test",
    envelope_id: str = "env-test",
) -> TextLayer:
    """A layer over ALREADY normalized text: ``(part_id, source_type, text)``.

    The offset map is the identity, which is what a normalizer produces for text
    that needed no normalizing. Span verification never reads the map - it reads
    ``normalized_text`` - so a test that wants to control offsets to the
    character can state the text and get exactly those coordinates.
    """
    return TextLayer(
        envelope_id=envelope_id,
        case_id=case_id,
        parts=[
            TextLayerPart(
                part_id=part_id,
                source_type=source_type,
                normalized_text=text,
                offset_map=[
                    OffsetSegment(
                        norm_start=0,
                        norm_end=len(text),
                        orig_start=0,
                        orig_end=len(text),
                    )
                ]
                if text
                else [],
            )
            for part_id, source_type, text in parts
        ],
        created_at=FIXED_NOW,
        versions=TEST_VERSIONS,
    )


def make_extractions(
    values: dict[str, str] | None = None,
    *,
    discarded_count: int = 0,
    procedure_id: str | None = "altersrente",
    case_id: str = "case-test",
    envelope_id: str = "env-test",
) -> ExtractionSet:
    """An extraction set of structured records."""
    return ExtractionSet(
        envelope_id=envelope_id,
        case_id=case_id,
        procedure_id=procedure_id,
        records=[
            ExtractionRecord(
                field=field,
                value=value,
                span=None,
                match_mode=MatchMode.STRUCTURED,
                match_score=None,
                confidence=1.0,
                extractor_id="mapper:v0",
            )
            for field, value in (values or {}).items()
        ],
        discarded_count=discarded_count,
        created_at=FIXED_NOW,
        versions=TEST_VERSIONS,
    )
