# ADR-010: Gold Sets Are Frozen, Versioned and Never Trained On

**Status:** Accepted, 2026-08-11 (part 02, plan step S2)

## Context
Every number this project will ever quote - routing accuracy, tier accuracy, the
false-clear rate that gates releases, later the anomaly scorer's precision -
comes from one artifact: the gold set. An evaluation corpus has two failure
modes, and both are silent:

1. **Leakage.** If gold items are used to train, tune or prompt-engineer
   anything, the metric stops measuring generalisation and starts measuring
   memorisation. Nobody notices, because the numbers get better.
2. **Drift by convenience.** If a label can be edited when the system disagrees
   with it, the corpus slowly converges on "whatever the system does", and the
   false-clear gate becomes a tautology. Again nobody notices, because the
   numbers get better.

Part 02 builds the first real corpus (41 items, two procedures), so the policy
has to exist now, before the first inconvenient red number tempts someone.

## Options
1. A living corpus that is edited as understanding improves. Cheap, and destroys
   the comparability of every metric across time.
2. A frozen corpus with an exception process for label fixes. The exception
   becomes the rule the first time a release is blocked at 17:00 on a Friday.
3. A frozen, versioned corpus: items are immutable; corrections happen by
   building a new versioned set from corrected scenario specs.

## Decision
Option 3, enforced by tooling rather than by discipline.

* `corpus/gold/v1/` is **frozen**. Items are never edited and never trained on.
  The `MANIFEST.yaml` states this in the artifact itself, next to a SHA-256 per
  item.
* A wrong label is fixed by **superseding the whole set**: correct the scenario
  spec, build `corpus/gold/v2/`, and leave v1 in git so numbers quoted against
  it keep their meaning. The eval report records which gold dir produced it.
* The **generator is the only writer** of a gold directory. Hand edits are
  caught by `python -m corpus.generator.build --check` and by a test that
  re-verifies the committed corpus against the committed specs.
* Generation is **reproducible**: output is a pure function of (scenario specs,
  seed, generator version, paraphrase strategy), so "which corpus was that?" has
  an answer that is not a date.
* Labels are **by construction**: the renderer emits the payload and the label
  sidecar from the same facts object. A rendering bug can therefore produce a
  missing item, never a mislabelled one, and the build re-runs the real pipeline
  over every item as a second, independent check.

Training-time enforcement is not yet possible (no training exists). When part 05
or part 06 introduces fitting of any kind, the gold directories must be excluded
by construction in the data-loading path, and that exclusion belongs in this
ADR's consequences, not in a comment.

## Consequences
- Metrics are comparable across the whole project timeline, and a regression
  cannot be resolved by editing the expectation.
- Fixing a genuinely wrong label costs a new version of the set. That is the
  intended price: it makes label quality a design-time concern.
- The corpus is only as good as its specs, so the specs are reviewable YAML with
  a description per item, not code.
- Gold items are synthetic by construction, which is a separate limitation:
  they test the mechanics honestly but cannot stand in for real inbound
  distribution. Real-data evaluation needs the design partner and a DPIA, and
  is out of scope until then.
