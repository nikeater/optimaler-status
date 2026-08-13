# ADR-001: Two-Plane Architecture (Evidence vs. Decision)

**Status:** Accepted, 2026-08-10

## Context
EingangsLotse triages inbound items for public-sector mass procedures. Probabilistic components (LLM extraction, embedding classifier, shadow scorer) are needed for unstructured content, but decisions about tier, routing, and drafting must be auditable (EU AI Act), explainable (Personalrat), reproducible, and testable in CI.

## Options
1. Let model outputs drive decisions directly, with confidence thresholds inline in code.
2. Hard boundary: probabilistic components produce only evidence artifacts; a deterministic, versioned config layer makes every decision.

## Decision
Option 2. Evidence plane emits typed artifacts (ExtractionSet, EvidenceRecord, AnomalyEvidence). The decision plane is a pure functional interpreter over the versioned DecisionTable and AgencyRiskConfig. Same evidence + same config = same decision, forever.

## Consequences
- Decisions are replayable from the journal with the stamped config versions.
- Golden-file tests pin the decision table; the evidence plane can evolve freely behind its contracts.
- Cost: an extra serialization boundary and strict contracts discipline (changes only via ADR).
