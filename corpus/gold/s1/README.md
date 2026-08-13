# S1 pre-gold scaffolding

**This is not the gold set.** These two items are hand-written scaffolding so the
walking skeleton has something to run on and the eval harness can compute its
first number. They are superseded by the real, frozen gold set built in part 02.

Do not tune thresholds against these items, do not quote metrics computed on
them outside this repo, and do not grow the set here: additions belong in the
part-02 corpus with its labelling protocol and inter-annotator check.

## Contents

| Item | Shape | Expected |
|---|---|---|
| `s1-0001-altersrente-complete` | Regelaltersrente, all required fields present | routed to `Referat_312_Renten`, tier 1 |
| `s1-0002-altersrente-missing-vsnr` | Same, `versicherungsnummer` absent | routed to `Referat_312_Renten`, tier 2, gap `versicherungsnummer` |

Each item is a FIT-Connect-shaped submission JSON plus a `*.labels.yaml` sidecar
carrying the ground truth (unit, tier, gap list).

## Synthetic by construction

Every value is invented. The `versicherungsnummer` values are format-valid but
belong to nobody; dates and the address block are made up. No real personal data
ever enters this repo (see `docs/CONTRIBUTING.md`).
