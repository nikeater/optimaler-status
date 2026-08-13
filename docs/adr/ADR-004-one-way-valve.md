# ADR-004: One-Way Valve Semantics for the Shadow Scorer

**Status:** Accepted, 2026-08-10

## Context
An unsupervised anomaly scorer watches items the deterministic rules would clear. ML must be able to add human oversight but never remove it: no score may promote an item past a failed deterministic check or into tier 1 (EU AI Act high-risk posture; false-clear is the fatal error class).

## Options
1. Policy rule: reviewers promise not to use anomaly scores as qualifying input.
2. Structural rule: the config format has exactly one syntactic home for anomaly evidence, downgrade conditions, and no other field can reference it.

## Decision
Option 2, enforced three times over:
1. Schema: `QualifyingCondition` rejects `anomaly.*` fields; `DowngradeCondition` accepts only `anomaly.*` fields with monotone-increasing operators (gt/ge on score, eq-true on flagged) and a fixed target of tier 3; the engine applies max(tier, to_tier).
2. Record: `DecisionRecord` refuses to persist a tier better than its pre-downgrade tier.
3. Test: a Hypothesis property test proves end to end that adding or raising anomaly evidence can never raise a tier, against the real decision table, on every commit.

The scorer launches log-only with a downgrade-rate efficiency budget in `AgencyRiskConfig`; enforcement is enabled only when reviewed flag precision justifies it. Phase 1 is unsupervised and identity-blind; supervised scoring is a pilot-phase upgrade under the same valve. Every flag carries feature-level reasons; a flag without readable reasons never ships.

## Consequences
- "ML only adds oversight" is a property of the config format, provable to auditors and the Personalrat.
- Cost: legitimate future uses of anomaly signals as qualifying input are impossible without a new ADR and schema change, which is exactly the point.
