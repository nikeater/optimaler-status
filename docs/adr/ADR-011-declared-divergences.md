# ADR-011: Gold Labels State the Fachlich Correct Outcome; Divergences Are Declared

**Status:** Accepted, 2026-08-11 (part 02, plan step S2)

## Context
While writing the first real corpus, several items had an obvious answer for a
caseworker and a different answer from today's rules. Examples from
`corpus/gold/v1/`:

* A submission whose channel says *Altersrente* while the form is an application
  for *volle Erwerbsminderungsrente*. A human sees a contradictory case that
  needs full review; the rules see an invalid Rentenart, call the item
  incomplete and route it by the channel hint.
* An item with no procedure hint but a filled-in Rentenart. A human routes it to
  the Altersrenten in a second; the engine extracts nothing at all, because the
  schema mapper only reads fields a procedure's `field_map` declares and the
  procedure is unknown. No extraction, no rule hit, no unit.

Labelling those items with what the system currently does would have produced a
corpus at 1.000 accuracy that measures nothing. Dropping them would have hidden
exactly the weaknesses parts 03 and 06 exist to fix.

## Options
1. Label what the system does. Comfortable, self-congratulatory, useless.
2. Leave such cases out of the corpus. The metric stays clean and the known
   weaknesses become invisible; the classifier work in part 03 then has no
   evidence to justify it.
3. Label the fachlich correct outcome and let the metric take the hit, with no
   further ceremony. Honest, but a mismatch in the report is then
   indistinguishable from a fresh regression.
4. Label the fachlich correct outcome AND declare the divergence in the spec,
   with a reason; the build enforces that the divergence actually happens.

## Decision
Option 4.

* A scenario spec may declare `known_divergence: [unit|tier]` together with a
  mandatory `divergence_reason` naming the mechanism and the part that will fix
  it. Four of the 41 items in v1 do.
* The build **requires the declared divergence to occur**. If a declared
  mismatch stops happening (because the classifier landed, or a rule changed),
  the build fails and the spec has to be updated. A stale "known problem" cannot
  rot into the corpus unnoticed.
* Declared divergences are **not excluded from any metric**. They lower routing
  and tier accuracy exactly as an undeclared error would; the eval report only
  marks them `DECL` so a reader can tell a documented gap from a new one.
* **Tier divergences may never point at tier 1.** A gold item that expects
  oversight and is cleared by the pipeline is a false clear, and no amount of
  documentation makes that acceptable: the spec model rejects the declaration
  and the build's self-check rejects the outcome (ADR-004, one-way valve).

## Consequences
- v1 reports routing accuracy 0.927 and tier accuracy 0.951 rather than 1.000,
  and the missing tenth is documented, attributable and assigned to a part.
- The corpus carries headroom: when part 03's classifier and part 06's scorer
  land, the numbers can move for real reasons.
- Cost: writing a divergent item costs a paragraph of justification. That is the
  intended friction - it prevents "the system is wrong here" from being asserted
  casually.
- The two divergences found while writing v1 are themselves findings worth
  carrying forward: routing arbitration between competing equal-confidence rules
  is currently "first rule in the file wins", and rule predicates over
  `extraction.*` are dead for unknown procedures because extraction is gated on
  a known procedure.
