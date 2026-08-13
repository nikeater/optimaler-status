# Gold set v3 - FROZEN, current

The evaluation corpus for the EingangsLotse triage pipeline: 77 synthetic
inbound items across three procedures, each with a label sidecar carrying the
ground truth. Generated, not hand-written; see `MANIFEST.yaml` for the exact
generator version, seed, config versions and per-item hashes.

v3 supersedes v2 (57 items). v2 and v1 stay in git, unedited, and every number
ever quoted against them keeps its meaning. `corpus/gold/REGISTRY.yaml` records
which set is current and how each one is verified.

## Why v2 was superseded (ADR-010, ADR-015)

1. **A third procedure landed.** Statusfeststellung nach par. 7a SGB IV
   (Scheinselbststaendigkeit, real vs. false freelancer) adds 20 `sf-` items.
   A frozen set does not grow; it is superseded by one that contains the new
   items alongside the old.
2. **`xx-0005` lost its declared divergence.** The Widerspruch had no routing
   rule since v1, so the corpus declared that today's rules got the unit wrong.
   Part 03b added the rule (compliance backlog C-9, config half), the item now
   routes to the Widerspruchsstelle, and keeping the declaration would have
   been a lie in the other direction. v3 carries **no declared divergences at
   all** - the first set of which that is true.

Everything else is byte-identical in content: the other 56 v2 scenarios are
unchanged, and their labels are unchanged.

## The freeze policy (ADR-010)

- Gold items are **never trained on**. They measure the system; a model that has
  seen them can no longer measure anything.
- Gold items are **never edited**. Not to fix a typo, not to fix a label. A set
  whose labels move whenever the system disagrees with them measures nothing.
- A label that turns out to be wrong is fixed by **superseding the whole set**
  with a new versioned directory (`corpus/gold/v4/`), built from corrected
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
`sf-` Statusfeststellung, `xx-` an item whose channel declared no configured
procedure.

## Coverage

| Scenario kind | Items | Expected outcome |
|---|---|---|
| `complete_clear` | 12 | Altersrente: tier 1. Erwerbsminderungsrente and Statusfeststellung: tier 3, because `tier1_enabled: false` |
| `missing_field` | 14 | tier 2 with the missing requirement as a gap |
| `invalid_field` | 19 | tier 2 with the requirement flagged `invalid` |
| `ambiguous_conflicting` | 11 | tier 3: complete but not clear-cut, Auslandsbezug, or signals that contradict each other |
| `hint_missing` | 7 | the channel declared nothing, the form does: derivation from content |
| `unknown_procedure` | 5 | tier 3: `not_evaluable` is never `complete` |
| `anomalous_rule_passing` | 9 | the tier today's rules produce, plus `anomaly_expected: true` |

By procedure: 30 altersrente, 19 erwerbsminderungsrente, 20 statusfeststellung,
8 without a configured procedure. By expected tier: 10 / 37 / 30. By derivation
source: 60 `hint`, 7 `content`, 10 `none`.

## The Statusfeststellung block (`sf-`, 20 items)

Par. 7a Abs. 2 S. 1 SGB IV orders a **Gesamtwuerdigung aller Umstaende des
Einzelfalles**. There is no constellation of form fields that makes such an
outcome klar, so the procedure ships with `tier1_enabled: false` and **no
`clear_cut` block at all** - not even an inert one, because unlike
erwerbsminderungsrente there is no documented target state to record.

The consequence is the point of the block: a **complete** Statusantrag matches
no row of the decision table (tier 1 is closed, tier 2 needs an incomplete
verdict) and lands on `default_tier: 3`. Four items are labelled exactly that
way - `complete_clear`, tier 3, no gaps, no divergence. That is not a gap in the
rules, it is the honest answer for a procedure whose decision is a judgment.

11 of the 20 sit at tier 3, 9 at tier 2. Three carry `anomaly_expected: true`
and are, unusually, **tier no-ops**: they already sit at tier 3, so a downgrade
would move nothing. Their value is the flag and the reason in the journal, which
tell a caseworker where to look in a file that otherwise reads like every other
complete application.

## Anomalous subset

Nine items carry `anomaly_expected: true`: internally consistent, format-valid,
statistically absurd (a Rentenbeginn 13 years out, an applicant aged 118, an
unissued Bereichsnummer, a Scheinselbststaendigkeits-Indizienbuendel, a
Honorarhoehe far below the Vergleichslohn, a Taetigkeit that began 17 years
ago). They are labelled with the tier the rules produce **today**, because the
shadow scorer that should catch them does not exist yet (part 06). Their
`anomaly_pattern` field names the feature that ought to fire. Deliberately not
coded as rules: the Fuenf-Sechstel-Umsatz heuristic and any Vergleichslohn are
Verwaltungs- and Marktwissen this repository cannot cite, and an invented
threshold would be worse than none.

## Declared divergences

None. v2's single divergence (`xx-0005`) was closed by the Widerspruchs-Routing
rule. The mechanism stays in place (ADR-011): a divergence is declared,
enforced by the build, marked `DECL` in the eval report and **never** excluded
from a metric, and it may never point at tier 1.

## Synthetic by construction

Every value is invented. Versicherungsnummern follow the real structure
(Bereichsnummer, Geburtsdatum TTMMJJ, Anfangsbuchstabe, Seriennummer,
Pruefziffer) and belong to nobody; firms, dates and addresses are made up; no
real submission, case file or person is represented here. No real personal data
ever enters this repo (see `docs/CONTRIBUTING.md`).

## Rebuilding

```powershell
python -m corpus.generator.build --out corpus/gold/v3 --seed 42   # rewrite
python -m corpus.generator.build --out corpus/gold/v3 --check     # verify only
python -m corpus.generator.build --out corpus/gold/v2 --check     # integrity only
```

The build is deterministic: same specs, same seed, byte-identical output. It
runs the real pipeline over every item afterwards and refuses to write anything
if an outcome disagrees with the declared labels, including the declared
derivation source.
