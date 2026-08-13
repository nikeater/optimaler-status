"""Assemble the EvidenceRecord the decision plane consumes.

Note what is *not* here: anomaly evidence. It travels as its own artifact
because the decision-table schema may reference it only in downgrade conditions
(ADR-004). Keeping it out of this record means no future refactor can quietly
smuggle a score into a qualifying condition.

A classifier suggestion IS here, and that is a deliberately different answer to
a similar-looking question (ADR-021). Anomaly evidence is kept off the record
because the valve must be structural: no field, no way to reference it. A
classifier suggestion is an addressee proposal, the same KIND of thing a rule
produces, and hiding it would cost the caseworker the one piece of help it can
give. So it rides ``EvidenceRecord.routing`` with ``source=CLASSIFIER``, and
the decision plane refuses to build on it until an agency admits the source
(``engine.decide.ADMITTED_ROUTING_SOURCES``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from engine.evidence.classify import ClassifierSuggestion
from engine.evidence.derive import ProcedureDerivation
from engine.evidence.nachforderung import GapRendering
from engine.evidence.routing import RoutingOutcome
from engine.journal.store import JournalStore, emit
from schemas.common import VersionStamp
from schemas.envelope import Envelope
from schemas.events import EventType
from schemas.evidence import CompletenessEvidence, EvidenceRecord, RoutingSuggestion
from schemas.extraction import ExtractionSet


def assemble_evidence(
    envelope: Envelope,
    extractions: ExtractionSet,
    routing: Sequence[RoutingSuggestion],
    completeness: CompletenessEvidence,
    *,
    journal: JournalStore,
    versions: VersionStamp,
    clear_cut: bool = False,
    derivation: ProcedureDerivation | None = None,
    outcome: RoutingOutcome | None = None,
    renderings: Sequence[GapRendering] = (),
    classifier: ClassifierSuggestion | None = None,
    now: datetime | None = None,
) -> EvidenceRecord:
    """Build the evidence record and record the EVIDENCE_ASSEMBLED event.

    Part 03 wrote "why this procedure" and "which units were proposed and lost"
    into the journal only, because ``EvidenceRecord`` had no field for either.
    ADR-016 added ``derivation`` and ``conflicts``, so both now travel on the
    record as well - rendered from the same two objects that render the journal
    payload, so the audit trail and the record cannot drift apart. The journal
    payload keeps its shape: it is the wider view (hint provenance, per-unit
    rule ids and priorities), and nothing downstream had to be re-taught.

    ``classifier`` is appended last and is never part of ``conflicts``: two
    units a rule proposed for one item is a disagreement between sentences an
    agency wrote, and calling a fallback guess the same thing would put a
    "strittig" marker on an item nothing contested.
    """
    confidences = [record.confidence for record in extractions.records]
    sentences = {
        rendering.requirement_id: rendering.sentence for rendering in renderings
    }
    suggestions = list(routing)
    if classifier is not None:
        suggestions.append(classifier.as_routing_suggestion())
    evidence = EvidenceRecord(
        envelope_id=envelope.envelope_id,
        case_id=envelope.case_id,
        routing=suggestions,
        derivation=derivation.as_outcome() if derivation is not None else None,
        conflicts=outcome.as_conflicts() if outcome is not None else [],
        completeness=completeness,
        extraction_min_confidence=min(confidences) if confidences else None,
        extraction_discarded_count=extractions.discarded_count,
        created_at=now or datetime.now(UTC),
        versions=versions,
    )
    emit(
        journal,
        case_id=envelope.case_id,
        event_type=EventType.EVIDENCE_ASSEMBLED,
        versions=versions,
        occurred_at=now,
        payload={
            "envelope_id": envelope.envelope_id,
            "routing": [
                {
                    "unit_id": suggestion.unit_id,
                    "source": suggestion.source.value,
                    "rule_ids": list(suggestion.rule_ids),
                    "confidence": suggestion.confidence,
                }
                for suggestion in evidence.routing
            ],
            "routing_arbitration": (
                outcome.as_payload() if outcome is not None else None
            ),
            # The full ranking, the raw score and whether it was calibrated -
            # everything a reviewer needs to disbelieve the suggestion. Present
            # as null when no classifier ran, so "nothing was suggested" is a
            # recorded fact rather than a missing key.
            "classifier": classifier.as_payload() if classifier is not None else None,
            "procedure": derivation.as_payload() if derivation is not None else None,
            "completeness_verdict": completeness.verdict.value,
            "requirements_version": completeness.requirements_version,
            "gap_count": len(completeness.gaps),
            "gaps": [
                {
                    "requirement_id": gap.requirement_id,
                    "status": gap.status.value,
                    "detail": gap.detail,
                    "request_text": sentences.get(gap.requirement_id),
                }
                for gap in completeness.gaps
            ],
            "clear_cut": clear_cut,
            "extraction_min_confidence": evidence.extraction_min_confidence,
            "extraction_discarded_count": evidence.extraction_discarded_count,
        },
    )
    return evidence
