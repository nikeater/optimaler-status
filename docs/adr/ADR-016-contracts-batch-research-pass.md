# ADR-016: Contracts Batch from the 2026-08-11 Research Pass

**Status:** Accepted, 2026-08-11

## Context
Part 03 filed four contract requests (priority on routing rules, arbitration/derivation surfacing on EvidenceRecord, optional derived-from-hint qualifying field). The research pass (docs/research/) added contract-level obligations: par. 88(5) AO's four RMS guarantees as the German statutory template (random sampling of the cleared stream, periodic review), and the toeslagenaffaire/FSV lesson that prior flags must never feed future scores. Contracts change only via ADR; batching moves them once.

## Decision
Applied to `schemas/` (additive, backward-compatible defaults; pre-freeze, so SCHEMA_VERSION stays 0.1.0 until the week-1 freeze):

1. `RoutingRule.priority: int = 100` (lower wins; total order (priority, rule_id)) - legalizes what part 03's loader shim carried; the shim (`RoutingRuleSpec` priority handling) migrates onto the contract field in part 03b.
2. `EvidenceRecord.derivation: DerivationOutcome | None` and `EvidenceRecord.conflicts: list[RoutingConflict]` - derivation outcomes and losing routing candidates become first-class evidence instead of journal-only payloads.
3. `AgencyRiskConfig.audit_sample_rate: float = 0.0` - par. 88(5) Nr. 1 AO analog. Sampling must be DETERMINISTIC-reproducible (salted case-id hash, never wall-clock or RNG state) so decide stays a pure function; a sampled item is journal-tagged `audit_sample` and gains full human review. Valve-compatible by construction: sampling only ever adds review. Engine implementation lands with part 09.
4. `AgencyRiskConfig.review_due: str | None` - par. 88(5) Nr. 4 AO analog; harness warns when overdue (part 06).
5. `schemas/anomaly.py` docstring gains the normative feature-set exclusion: no per-applicant history, no prior-flag features (FSV lesson); part 09 property-tests it.

Rejected for now: `procedure.derived_from_hint` as a qualifying field (part 03's optional request 4) - no current consumer; the wider behavior is measured by gold item ar-0050; revisit if an agency wants the narrower rule.

## Consequences
- Part 03b adapts the loader to the contract priority field, populates derivation/conflicts, and re-exports the JSON Schema artifacts.
- Parts 06/09 implement review_due warning, audit sampling, proxy-skew reporting, and the feature-set property test (see docs/compliance-backlog.md P-1/P-2/P-3/P-5).
- The one-way valve invariants are untouched; audit sampling and downgrades both move items only toward more human review.
