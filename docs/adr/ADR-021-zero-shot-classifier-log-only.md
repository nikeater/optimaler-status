# ADR-021: The Fallback Unit Classifier Is Zero-Shot, Rules-Last, and Log-Only Until an Agency Admits It

**Status:** Accepted, 2026-08-12 (part 06, plan step S6)

## Context

Five of gold v4's 101 items reach no routing rule at all: a Grundsicherungs-
Anfrage that belongs to a different Traeger, a submission with no
Verfahrenskennung, a Terminanfrage, and two letters naming two procedures at
once. They land at tier 3 with `routed_unit_id: null` - correct, and in nobody's
queue. Somebody in the Zentraler Eingang has to open each one to find out who it
belongs to, which is exactly the work this system exists to reduce.

A classifier is the obvious answer and the obvious risk. The whole architecture
rests on a line between evidence that is auditable (a rule an agency wrote) and
evidence that is statistical, and on the promise that nothing statistical can
qualify an item for less oversight. A classifier that quietly started deciding
Zustaendigkeiten would erase that line in the one place a caseworker cannot see
it, because a wrongly routed Vorgang does not look wrong - it looks routed.

ADR-014 already flagged this: "confidence is doing double duty ... it will need
revisiting when the classifier produces genuinely calibrated confidences,
because then a contested rule hit at 0.6 and a classifier hit at 0.6 would mean
different things."

## Options

1. **No classifier.** Free, honest, and leaves the five items where they are.
2. **Train a classifier on the gold set.** Best accuracy, and it makes the gold
   set training data - which ADR-010 forbids for exactly this reason, and which
   would make the corpus stop being a measurement.
3. **Zero-shot similarity against the taxonomy the agency already edits.**
   Weaker, and it has the property the other two do not: re-cutting a Referat
   re-aims the classifier by editing YAML, with no model to retrain and no
   label set to maintain.

## Decision

Option 3, with six properties that are the actual decision.

### 1. Zero-shot, rules first, fallback only

Per-unit texts are `TaxonomyNode.name` plus `responsibilities`. `source` is
deliberately excluded: it is provenance about the CONFIG ("abgeleiteter
Platzhalter bis zur Bestaetigung durch den Design-Partner") and every node's
source ends in nearly the same sentence, which would pull every unit toward
every other one.

Item text is the normalized, already-redacted prose for a letter and a
deterministic `path: value` rendering of the `payload.*` namespace for a form,
sorted by path so key order in the submission JSON cannot change it. The path is
half the signal: `antrag.rentenart: regelaltersrente` says more to an embedding
than `regelaltersrente` alone.

The classifier is consulted only when routing arbitration produced no candidate.
That is policy, not optimization: a similarity may not be asked about an item an
agency's own sentence already answered, because a reader comparing the two would
eventually start weighing them.

The v2 taxonomy's responsibilities turned out to be rich enough - no `drv_bund_v3`
supersession was needed.

### 2. Log-only by default, and log-only is a property of the decision plane

The suggestion rides `EvidenceRecord.routing` with `source=CLASSIFIER` (the
contract enum has carried the value since part 01) and its full ranking goes
into the `EVIDENCE_ASSEMBLED` payload. It is real evidence a caseworker and an
auditor can both read and disbelieve.

What keeps it from deciding anything is a new rail in `engine/decide`: **the
admitted routing sources**, defaulting to `{RULE}`. `routing.confidence`,
`routing.rule_hit` and `routed_unit_id` are all computed over admitted
suggestions only, so a caller that knows nothing about the classifier decides
exactly as it did before the classifier existed. Hiding the suggestion from the
record would have been the other way to get log-only, and it would have cost the
caseworker the one piece of help the classifier can give.

A classifier suggestion is never a routing CONFLICT. Two units proposed by rules
is a disagreement between sentences an agency wrote; calling a fallback guess the
same thing would mark an item "strittig" that nothing contested.

This also settles ADR-014's worry, and in a way that is better than expected:
because the classifier is fallback-only, no item ever carries both a rule
suggestion and a classifier suggestion, so `max(confidence)` never compares the
two scales at all.

### 3. A raw cosine is not a confidence

Without a fitted calibration a suggestion carries `confidence = 0.0` and its
honest `raw_score`. The configured `min_confidence` is a statement about
calibrated confidence and is therefore not applied to a raw score - the two are
different scales, and comparing them would be a category error that happens to
produce a tier.

Calibration is data with provenance: gold set, model id, date, and a monotone
per-bin map fitted by `python -m eval.calibrate` and pasted into config by a
human. The loader **refuses** `enabled: true` without it, and refuses a
calibration fitted on a different model.

### 4. The settings live in their own versioned file

`config/classifier/classifier_v1.yaml`, not a block inside
`config/rules/routing_v3.yaml`. The reason is not tidiness: the routing config's
version string is frozen into every gold-set MANIFEST, so a classifier tweak
inside that file would either ship under a version that no longer describes the
file or invalidate a frozen corpus. An independently versioned subsystem gets an
independently versioned file. The same reasoning put the P-5 review date in
`config/review/threshold_review_v1.yaml` instead of superseding
`config/thresholds.yaml` to `risk_v1`.

### 5. The model is optional and never auto-loaded

`intfloat/multilingual-e5-small` rides an optional `[classify]` extra behind the
lazy-loader pattern of `engine/redact/ner.py`. `run_pipeline` takes an
`embedder` argument that defaults to None, and no gate ever passes one: a gated
number may not depend on which wheels a machine happens to have. Every code path
is covered by a deterministic hashed-n-gram stub, which uses `hashlib` rather
than Python's salted `hash()` so two runs rank alike.

### 6. Any failure is "no suggestion"

Missing extra, model error, empty taxonomy, an item with no readable text: all
of them return None, which is what the system did before this module existed.
Nothing here can raise into the pipeline or touch a tier.

## Consequences

- **Enabling it moves the addressee, not the tier.** Both table rows require
  `routing.rule_hit`, and a classifier suggestion is not a rule hit, so a
  fallback-routed item still goes to a human - it just goes to a named Referat's
  queue instead of nobody's. Getting more than that would take a new table row,
  which is a separate, visible agency decision.
- **Measured on gold v4 with the real model** (log-only, nothing gated; the
  whole gated report scored exactly what it scores without the model - routing
  1.000, tier 1.000, false clear 0.000, derivation 1.000, 88/88 spans,
  structured subset HELD): suggestions for 5/5 rule-less items; agreement 0.708
  (68/96) on the items the corpus does label, where the classifier is never
  actually consulted; raw expected calibration error 0.2212, which is what "a
  cosine is not a probability" looks like as a number. The fitted map turns a
  raw range of 0.827-0.897 into confidences from 0.21 to 0.95.
- **The five proposals, so a reader can judge them as proposals.** The two
  Anfragen with no Verfahrenskennung both went to Referat 320 (Reha) at margins
  of 0.0000 and 0.0003, which is a coin flip wearing a decimal point; the
  Terminanfrage went to the Widerspruchsstelle; the two letters naming two
  procedures went to Referat 312. Three of the five are defensible and two are
  the classifier failing to abstain, which is the argument for keeping the
  minimum confidence high rather than for switching it on.
- **The classifier cannot say "I do not know" except by silence.** Referat 390
  (Zentraler Eingang) is excluded, along with the two Geschaeftsbereiche: its
  responsibility text describes the ABSENCE of a match, which an embedding
  cannot represent - it would rank highest for letters that happen to use the
  word "unklar". A confidence below the minimum is the honest way to abstain.
- **Sealed values may never reach the embedder.** The first two real-model runs
  of the same corpus disagreed by one item, because a letter's prose still
  carried the random placeholder tokens the seal had drawn and two Referate
  scored within 0.0003 of each other. Prose is now masked through
  `engine.redact.mask_placeholders`. The general rule this instance belongs to:
  anything computed over a sealed value is a feature over noise unless it comes
  through the witness.
- **The gold set stays a measurement.** Nothing is trained on it. The
  calibration fitted from it is a map from score to observed accuracy, not a
  model, and the eval reports its in-sample nature in the same breath.
- **The fit set and the serving set are different populations.** The calibration
  is learned from items a rule already routed, because those are the only ones
  with ground truth, and it is applied to items no rule caught. That is a real
  generalization gap and it is the main reason the shipped config carries no
  calibration block: the measurement exists, the adoption does not.
