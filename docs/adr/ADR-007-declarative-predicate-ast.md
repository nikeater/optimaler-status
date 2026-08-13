# ADR-007: One Declarative Predicate AST, and Clear-Cut as Evidence

**Status:** Accepted, 2026-08-10 (part 01, plan step S1)

## Context
Three places need "evaluate a condition over an item": routing rules
(`config/rules/`), the per-procedure clear-cut criteria that gate tier 1
(`config/procedures/`), and the decision table (`config/decision/`). The
decision table already has its condition format fixed by the contracts
(`QualifyingCondition` / `DowngradeCondition`, ADR-004). The other two were
open.

Two questions had to be answered together:
1. Do rules and clear-cut criteria get their own expression language each, a
   shared one, or Python?
2. Where is "is this a clear-cut case?" evaluated - in the evidence plane or
   inside the decision table interpreter?

Question 2 matters more than it looks. `procedure.clear_cut` is a qualifying
field of the decision table, so if the interpreter evaluated the criteria
itself, the decision plane would start reading raw extraction values and the
two-plane split (ADR-001) would erode from the inside.

## Options
1. Clear-cut criteria as Python per procedure. Fastest to write, but the
   agency cannot read or change its own tier-1 gate, which contradicts "config
   is the product".
2. A second small expression language for clear-cut criteria, separate from
   routing rules. Two grammars, two interpreters, two sets of bugs.
3. One predicate AST shared by routing rules and clear-cut criteria, evaluated
   in the evidence plane; the decision plane receives only the boolean result
   and keeps `compare` as its single shared primitive.

## Decision
Option 3.

* The AST has exactly three node shapes: `{all: [...]}`, `{any: [...]}` and
  `{field, op, value}` over the operator set already defined by
  `schemas.config.Op`. It lives in `engine/predicate.py`.
* Evaluation is total and defensive: an unknown field, or a field whose value
  is `None`, makes the comparison `False`; ordering operators require numbers
  on both sides; booleans never compare equal to numbers. Missing evidence can
  therefore only ever cost an item a qualification, never grant one.
* Malformed AST *structure* is a different class of problem and raises
  `PredicateError` while config is loaded, not while an item is decided.
* Clear-cut criteria are evaluated in `engine/evidence/clearcut.py` and enter
  the decision table as the ordinary qualifying field `procedure.clear_cut`.
  A procedure without criteria evaluates to `False`: absent criteria must never
  qualify an item for tier 1.
* `engine/decide` imports `compare` and nothing else from the predicate module.
  The AST evaluator is evidence-plane machinery.

## Consequences
- One grammar to document, test and fuzz; routing rules and clear-cut criteria
  cannot drift apart in semantics.
- The agency can read and edit its own tier-1 gate without touching code.
- The evidence/decision split stays clean: the decision plane never reads a raw
  extraction value.
- Cost: the AST is deliberately weak (no negation, no arithmetic). Anything more
  expressive needs a new ADR, which is the intended friction - a routing rule
  nobody can read is a routing rule nobody can audit.
