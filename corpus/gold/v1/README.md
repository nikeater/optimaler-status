# Gold set v1 - FROZEN

The evaluation corpus for the EingangsLotse triage pipeline: 41 synthetic
inbound items across two procedures, each with a label sidecar carrying the
ground truth. Generated, not hand-written; see `MANIFEST.yaml` for the exact
generator version, seed, config versions and per-item hashes.

## The freeze policy (ADR-010)

- Gold items are **never trained on**. They measure the system; a model that has
  seen them can no longer measure anything.
- Gold items are **never edited**. Not to fix a typo, not to fix a label. A set
  whose labels move whenever the system disagrees with them measures nothing.
- A label that turns out to be wrong is fixed by **superseding the whole set**
  with a new versioned directory (`corpus/gold/v2/`), built from corrected
  scenario specs. The old set stays in git so old numbers keep their meaning.
- The **generator is the only writer** of this directory. Editing a file here by
  hand is detected by `python -m corpus.generator.build --check` and by
  `tests/test_corpus_generator.py`.

## Contents

| File | What it is |
|---|---|
| `<item>.json` | FIT-Connect-shaped submission payload |
| `<item>.labels.yaml` | Ground truth: unit, tier, gaps, anomaly marker, paraphrase provenance |
| `MANIFEST.yaml` | Generator version, seed, counts, per-item SHA-256, freeze policy |

Item ids encode the procedure: `ar-` Altersrente, `em-` Erwerbsminderungsrente,
`xx-` an item that belongs to no configured procedure.

## Coverage

| Scenario kind | Items | Expected outcome |
|---|---|---|
| `complete_clear` | 8 | Altersrente: tier 1. Erwerbsminderungsrente: tier 3, because `tier1_enabled: false` |
| `missing_field` | 9 | tier 2 with the missing requirement as a gap |
| `invalid_field` | 7 | tier 2 with the requirement flagged `invalid` |
| `ambiguous_conflicting` | 6 | tier 3: complete but not clear-cut, Auslandsbezug, or contradictory content |
| `unknown_procedure` | 5 | tier 3: `not_evaluable` is never `complete` |
| `anomalous_rule_passing` | 6 | the tier today's rules produce, plus `anomaly_expected: true` |

Six items carry `anomaly_expected: true`: internally consistent, format-valid,
statistically absurd (a Rentenbeginn 13 years out, an applicant aged 118, a
Versicherungsnummer whose embedded birth date contradicts the stated one). They
are labelled with the tier the rules produce **today**, because the shadow
scorer that should catch them does not exist yet (part 06). Their
`anomaly_pattern` field names the feature that ought to fire.

## Declared divergences

Four items declare `known_divergence`: the gold label states what a caseworker
would do, and the corpus records that today's rules do something else, with the
reason in `divergence_reason`. They are **not** excluded from any metric - a
documented error is still an error, and the eval report simply marks them `DECL`
so a reader knows the difference between a known gap and a fresh regression.
No divergence may point at tier 1: a gold item that expects oversight and gets
cleared fails the build, always.

## Synthetic by construction

Every value is invented. Versicherungsnummern are format-valid and belong to
nobody, dates and addresses are made up, and no real submission, case file or
person is represented here. No real personal data ever enters this repo (see
`docs/CONTRIBUTING.md`).

## Rebuilding

```powershell
python -m corpus.generator.build --out corpus/gold/v1 --seed 42   # rewrite
python -m corpus.generator.build --check                          # verify only
```

The build is deterministic: same specs, same seed, byte-identical output. It
runs the real pipeline over every item afterwards and refuses to write anything
if an outcome disagrees with the declared labels.
