# ADR-014: Routing Arbitration Is an Explicit Priority, and an Unresolved Conflict Costs Confidence

**Status:** Accepted, 2026-08-11 (part 03, plan step S3)

## Context
Routing rules fire independently, so several can hit one item and propose
different organizational units. Part 02 shipped every rule hit at confidence 1.0
and let the decision plane take `max(confidence)`, which in Python returns the
*first* maximum. The effective arbitration policy was therefore "the first
matching rule in the file wins", and `config/rules/routing_v1.yaml` said so in a
comment, with the Auslandsbezug rule placed first to make it win.

Two problems with that. The policy lived in line order, so reordering a file for
readability would silently change which Referat receives a case. And it was
silent: nothing recorded that two units had been proposed at all, so an item
routed by a coin flip looked exactly like an item routed by consensus.

## Options
1. **Keep file order, document it harder.** Free, and one careless diff away
   from a wrong Zustaendigkeit.
2. **Priority per rule, winner takes all.** Explicit and stable, but still
   silent about disagreement: a 50/50 tie would produce a confident answer.
3. **Priority per rule, plus a recorded conflict and reduced confidence when the
   priorities do not resolve it.**

## Decision
Option 3.

* Every rule carries an integer `priority` (lower wins). `(priority, rule_id)`
  is a **total order** over rules, so shuffling the file cannot change the
  outcome - there is a Hypothesis property for exactly that.
* `RoutingRule` (a contract) has no `priority` field, and `schemas/` is
  ADR-gated. The loader therefore owns the YAML shape
  (`RoutingRuleSpec`) and exposes the contract subset through `.rule`. A
  contract request for `RoutingRule.priority` is filed; until it is decided,
  arbitration policy is loader-owned rather than smuggled past a strict model.
* Units are ranked by the best order key among the rules proposing them. Every
  proposed unit stays in the evidence; the winner is first.
* Two or more proposed units are recorded as a **conflict** in the
  `evidence_assembled` event, with the rules behind each candidate.
* A conflict the priorities do **not** resolve (the two best units share a
  priority) is *unresolved*: the winner's confidence drops from 1.0 to 0.6, and
  losing units carry 0.5. Both rows of the decision table require confidence at
  or above 0.9, so a contested item cannot reach tier 1 *or* tier 2 and falls to
  the default, tier 3.
* The shipped priority bands: 10 Zustaendigkeits-Vorrang (Auslandsbezug), 20
  content, 40 derived procedure, 50 channel hint. Content outranks the hint,
  because the hint is metadata somebody clicked and the form is the Antrag.

## Consequences
- File order is no longer load-bearing, and the fixtures now assert the
  arbitration *winner*, not only which rules fired.
- An unresolved conflict costs a tier rather than producing a confident wrong
  answer. It still produces a routed unit: a contested Vorgang with no addressee
  would sit in nobody's queue, which is worse than sitting in one Referat's
  queue marked "strittig".
- Confidence is doing double duty - "how sure is this rule" and "how contested
  is this item". That is acceptable while every rule hit is deterministic
  (a rule is either right or absent), and it is exactly the field the decision
  table already reads. It will need revisiting when the classifier of part 05
  produces genuinely calibrated confidences, because then a contested rule hit
  at 0.6 and a classifier hit at 0.6 would mean different things.
- `RoutingSuggestion` still has no field naming the conflict, so the journal is
  the only place the losing candidates survive. That is enough for the audit
  trail and for the review UI (which reads the journal), and it keeps the
  evidence record free of anything a decision could be built on.
- Priorities are agency-editable config, which is the point: which Referat wins
  a disagreement is an organizational decision, not an engineering one.
