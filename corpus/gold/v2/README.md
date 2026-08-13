# Gold set v2 - FROZEN, superseded by v3

The evaluation corpus for the EingangsLotse triage pipeline: 57 synthetic
inbound items across two procedures, each with a label sidecar carrying the
ground truth. Generated, not hand-written; see `MANIFEST.yaml` for the exact
generator version, seed, config versions and per-item hashes.

v2 supersedes v1 (41 items) and was itself superseded by v3 (77 items) when the
Statusfeststellung nach par. 7a SGB IV landed and `xx-0005`'s declared
divergence was closed. Every item, label and hash in this directory is
unchanged - only this header sentence was added, because a set that says
"current" about itself after it stopped being current is the one kind of
staleness this corpus cannot afford. `corpus/gold/REGISTRY.yaml` remains the
authority on which set is current and how each one is verified.

## Why v1 was superseded (ADR-010, ADR-015)

1. **The Versicherungsnummern were structurally wrong.** v1 wrote the birth
   date into the first six digits; the real DRV number carries it in positions
   3 to 8, after the Bereichsnummer. No structural check could ever have passed
   against v1, so part 03's format and cross-field checks needed corrected
   values. Same people, same dates, digits in the right places.
2. **Four labels needed correcting.** v1 declared divergences for `ar-0033`,
   `em-0031` and `xx-0004`; procedure derivation and routing arbitration closed
   all three, so keeping the declarations would have been a lie in the other
   direction. `ar-0043` moved from "anomalous, rule-passing, tier 1" to
   "invalid, tier 2", because the contradiction between its Versicherungsnummer
   and its stated birth date is now caught deterministically.
3. **There was no ground truth for procedure derivation.** It did not exist
   yet. Every v2 item declares `derivation_source` (hint / content / none) and
   the procedure the engine must arrive at.

## The freeze policy (ADR-010)

- Gold items are **never trained on**. They measure the system; a model that has
  seen them can no longer measure anything.
- Gold items are **never edited**. Not to fix a typo, not to fix a label. A set
  whose labels move whenever the system disagrees with them measures nothing.
- A label that turns out to be wrong is fixed by **superseding the whole set**
  with a new versioned directory (`corpus/gold/v3/`), built from corrected
  scenario specs.
- The **generator is the only writer** of this directory. Editing a file here by
  hand is detected by `python -m corpus.generator.build --check` and by
  `tests/test_corpus_generator.py`.

## Contents

| File | What it is |
|---|---|
| `<item>.json` | FIT-Connect-shaped submission payload |
| `<item>.labels.yaml` | Ground truth: unit, tier, gaps, derivation, anomaly marker, paraphrase provenance |
| `MANIFEST.yaml` | Generator version, seed, counts, per-item SHA-256, freeze policy |

Item ids encode the procedure: `ar-` Altersrente, `em-` Erwerbsminderungsrente,
`xx-` an item whose channel declared no configured procedure.

## Coverage

| Scenario kind | Items | Expected outcome |
|---|---|---|
| `complete_clear` | 8 | Altersrente: tier 1. Erwerbsminderungsrente: tier 3, because `tier1_enabled: false` |
| `missing_field` | 10 | tier 2 with the missing requirement as a gap |
| `invalid_field` | 15 | tier 2 with the requirement flagged `invalid` |
| `ambiguous_conflicting` | 8 | tier 3: complete but not clear-cut, Auslandsbezug, or signals that contradict each other |
| `hint_missing` | 5 | the channel declared nothing, the form does: derivation from content |
| `unknown_procedure` | 5 | tier 3: `not_evaluable` is never `complete` |
| `anomalous_rule_passing` | 6 | the tier today's rules produce, plus `anomaly_expected: true` |

By derivation source: 43 `hint`, 5 `content`, 9 `none`.

Six items carry `anomaly_expected: true`: internally consistent, format-valid,
statistically absurd (a Rentenbeginn 13 years out, an applicant aged 118, an
unissued Bereichsnummer). They are labelled with the tier the rules produce
**today**, because the shadow scorer that should catch them does not exist yet
(part 06). Their `anomaly_pattern` field names the feature that ought to fire.

## Declared divergences

One item declares `known_divergence`: `xx-0005` (a Widerspruch) has no routing
rule, because that rule belongs with the Widerspruchs-Workflow and inventing it
early would only improve a metric. The gold label states what a caseworker would
do, and the corpus records that today's rules do something else, with the reason
in `divergence_reason`. Divergences are **not** excluded from any metric - a
documented error is still an error, and the eval report marks them `DECL` so a
reader knows the difference between a known gap and a fresh regression. No
divergence may point at tier 1: a gold item that expects oversight and gets
cleared fails the build, always.

## Synthetic by construction

Every value is invented. Versicherungsnummern follow the real structure
(Bereichsnummer, Geburtsdatum TTMMJJ, Anfangsbuchstabe, Seriennummer,
Pruefziffer) and belong to nobody; dates and addresses are made up; no real
submission, case file or person is represented here. No real personal data ever
enters this repo (see `docs/CONTRIBUTING.md`).

## Rebuilding

```powershell
python -m corpus.generator.build --out corpus/gold/v2 --seed 42   # rewrite
python -m corpus.generator.build --out corpus/gold/v2 --check     # verify only
python -m corpus.generator.build --out corpus/gold/v1 --check     # integrity only
```

The build is deterministic: same specs, same seed, byte-identical output. It
runs the real pipeline over every item afterwards and refuses to write anything
if an outcome disagrees with the declared labels, including the declared
derivation source.
