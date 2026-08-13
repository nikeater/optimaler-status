# ADR-025: A Sampled Case Carries Its Own Reason Kind

**Status:** Accepted, 2026-08-12 (contracts change; requested by part 09)

## Context

Part 09 shipped the P-1 audit-sampling engine: a deterministic salted-hash draw
that moves a share of tier-1/2 items to full review (par. 88(5) Nr. 1 AO
analog). It had to express the reason with `ReasonKind.DOWNGRADED` plus a
dedicated `rule_id` of `audit_sample`, because the contract enum had no better
value. That works mechanically and misleads semantically: DOWNGRADED is the
kind the one-way valve uses when anomaly evidence fired, and every consumer
that renders "why is this item in front of me" by KIND would show a randomly
audited case with the same face as a suspicious one. The part-10 review UI must
do the opposite - a sampled case is a quality-assurance draw, and a caseworker
who reads it as a machine suspicion starts the review with a bias the draw was
designed not to carry (the toeslagenaffaire lesson applied at the smallest
scale).

## Options

1. Keep DOWNGRADED + rule_id. No contract change, but the semantic split lives
   in a string convention every consumer must know about.
2. Add `ReasonKind.SAMPLED`. Additive enum value; kind-level distinction;
   consumers that switch on kind get the split for free.

## Decision

Option 2. `ReasonKind.SAMPLED = "sampled"` joins the contract with the comment
that a deterministic audit draw is not a suspicion. The decision-plane wiring
(emitting SAMPLED for audit draws instead of DOWNGRADED + rule_id convention)
is part 10's migration, mirroring how ADR-016's fields were landed by the next
part; the `audit_sample` rule_id stays as the stable identifier of the draw
rule itself.

## Consequences

- The review UI can render "zufaellig zur Pruefung ausgewaehlt" from the kind
  alone, and P-6's rubber-stamp metrics can exclude sampled cases from
  flag-precision statistics without string matching.
- Additive and backward compatible: no existing artifact changes meaning;
  JSON Schema artifacts re-exported; SCHEMA_VERSION unchanged at 0.1.0 per the
  standing week-1-freeze note.
- Journal entries written before the part-10 migration carry the old shape
  (DOWNGRADED + `audit_sample`); readers that care must accept both, and the
  migration note in part 10's task file says so.
