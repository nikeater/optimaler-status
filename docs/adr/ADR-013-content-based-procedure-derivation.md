# ADR-013: The Procedure Is Derived in the Evidence Plane, Declaratively, and Never Guessed

**Status:** Accepted, 2026-08-11 (part 03, plan step S3)

## Context
Everything downstream of ingest depends on one answer: which Fachverfahren is
this? The procedure selects the field map (so what gets extracted), the
requirement list (so what completeness means), the clear-cut criteria and the
tier-1 legal gate. Parts 01 and 02 took that answer from the channel's
`procedure_hint`, verbatim.

That produced a cycle part 02 measured and documented as `xx-0004`: the mapper
only extracts fields a *known* procedure declares, so an item without a usable
hint got no extractions, and every routing rule reading `extraction.*` was dead.
An item nobody could classify was also an item nobody could route, even when the
form plainly said "Regelaltersrente, Beginn 01.11.2026". A human reading it
would have known in two seconds.

Two further shapes were mislabelled by the same simplification: `ar-0033`
(channel says Altersrente, form is a full Erwerbsminderungsrente) and `em-0031`
(channel says Erwerbsminderungsrente, form ticks Regelaltersrente). Both were
checked against the *hint's* requirement list, which produced a confident
"incomplete because the Rentenart is not allowed" for an item whose real problem
is that nobody knows what it is.

## Options
1. **Keep taking the hint.** Simple and wrong in the cases that matter; the
   corpus already carried three declared divergences for it.
2. **A classifier (embeddings, or an LLM).** Would work, and would put a model
   in front of the field map, the requirement list and the tier-1 gate - the
   deepest possible place for a probabilistic component to sit. It would also
   need calibration data this project does not have yet.
3. **Declarative content signals per procedure, evaluated deterministically.**
   Each procedure states, in its own config file, which payload content
   identifies it, using the predicate AST of ADR-007.

## Decision
Option 3, in the evidence plane (`engine/evidence/derive.py`), with an explicit
refusal path.

* Signals live in each procedure's `derivation:` block and read the
  **pre-extraction** context: `payload.<dotted.path>`, `procedure_hint`,
  `channel`. They cannot read `extraction.*`, which is what breaks the cycle.
  A procedure without a `derivation:` block is never derived from content -
  silence is not a signal.
* Precedence: **ambiguous content** (two or more procedures match) yields None
  first, before anything else; then a **valid hint** the content does not
  contradict; then **unambiguous content**; then None.
* Two refusals produce None and record why: ambiguity, and a valid hint that
  unambiguous content contradicts. Neither is rescued by the other signal. A
  hint is metadata somebody clicked; the form is the Antrag. When they disagree,
  the honest answer is "we cannot tell", which means NOT_EVALUABLE, which means
  tier 3 and a human.
* Placement in the evidence plane, not in ingest, follows ADR-001: this is a
  claim about content, it is evidence, and the decision plane reads it through
  `completeness.verdict` exactly as before. `engine/decide` is unchanged.
* The outcome (source, candidates, ambiguity, contradiction, a German reason
  string) is written to the `evidence_assembled` event. `EvidenceRecord` has no
  field for it; a contract request is filed rather than smuggled in.

## Consequences
- `xx-0004` routes and is checked; the unknown-procedure subset's routing
  accuracy moved from 0.600 to 0.875 on gold v2, and `ar-0033` and `em-0031`
  reach their fachlich correct tier without a declared divergence.
- A content-derived procedure **may reach tier 1**. The derivation is
  deterministic, config-declared and gold-measured, so this is defensible - but
  it is a real widening, and the decision table cannot currently express "tier 1
  only from a hint" because `QUALIFYING_FIELDS` has no derivation field.
  `ar-0050` is the gold item that measures it. If the agency wants the narrower
  rule, that is a contract request for a new qualifying field, not a code
  change.
- Signals are broad on purpose (a Rentenbeginn alone identifies an Altersrente
  application). Broad signals collide more often, and a collision is a refusal
  rather than a mistake, which is the trade this project wants.
- The signals are only as good as the payload schema. Free-text submissions
  carry none of these keys, so derivation will return None for them until the
  text layer lands in part 05. That is visible in the metric rather than hidden.
- No model anywhere in this path. The classifier option stays available for part
  05 as an *additional* candidate source, but it would then have to compete with
  a deterministic baseline that already scores 1.000 on the corpus.
