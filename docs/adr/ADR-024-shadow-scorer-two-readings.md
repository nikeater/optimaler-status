# ADR-024: The Shadow Scorer - Two Readings of an Identity-Blind Vector, Calibrated on the Frozen Gold Set

**Status:** Accepted, 2026-08-12

## Context

ADR-004 fixed the valve semantics in part 01: anomaly evidence may appear in exactly one syntactic place, may only add oversight, and every flag carries feature-level reasons. What it did not fix is what produces the number. Eight parts later the machinery was still fed by a parameter nobody passed.

Four constraints shape what the scorer can be, and three of them arrived after ADR-004 was written:

1. **No outcome data exists.** Phase 1 has no labelled "this application was wrong" set and will not have one before a pilot. Unsupervised is the only defensible mode.
2. **The scorer is identity-blind by construction, not by promise.** Part 04 seals identity-classed paths at ingest, so the working copy carries a random placeholder where a Versicherungsnummer used to be. Part 04's own finding: anything computed over a sealed value is a feature over noise - which part 06 then hit as an actual bug when placeholder tokens reached an embedder.
3. **The one labelled set is frozen and small.** Gold v4 is 101 items with nine `anomaly_expected` labels. The implementation plan's phase-1 instruction is to calibrate on it.
4. **Enforcement must stay hard to reach.** `scorer_mode: log_only` lives in `config/thresholds.yaml`, whose version string is frozen into the gold manifest.

## Decision

### 1. Two feature families, two readings, one score

The vector is eight identity-blind features. Three of them are the **consistency features** - the deliberate hand-offs earlier parts wrote down: the signed distance between an item's leading date and its arrival (which the absolute, deliberately wide calendar bounds in `config/procedures/` cannot catch), the share of recorded par. 7a Indizien pointing at Beschaeftigung, and the stated revenue share with the main client. The other five describe which population an item belongs to: how much of the procedure's schema it filled in, whether it is a letter, whether it came off a scanner, whether the Indizien fields were filled in at all, and whether a leading date exists.

Two readings run over that vector:

- an **IsolationForest** (scikit-learn, fixed `random_state`, 200 trees) answers "is this COMBINATION unusual";
- a **tail-share deviation** over the consistency features only answers "is this single number far out": how small a tail of the reference population lies at or beyond this item's value.

Both are mapped through the empirical distribution of the reference population, and the score is the percentile of the larger of the two. So a score reads as exactly what it is: **more unusual than N percent of the reference corpus**, on the more alarming of the two readings.

The second reading is not decoration. It was added after measuring: an isolation tree splits uniformly inside a feature's range, so a single extreme value in a heavy-tailed column is isolated no faster than a point in a crowd. With the forest alone, gold v4's date anomalies - the exact cases part 03 handed to the scorer - scored mid-field while the Indizienbuendel scored at the top.

### 2. No feature may restate a decision-table qualifying field

The mirror image of the one-way valve. ADR-004 keeps anomaly evidence out of qualifying conditions; this keeps qualifying fields out of the feature vector.

The reason is not tidiness. A downgrade can only ADD oversight, so a feature that restates a qualifying field can only ever re-flag items the table has already sent to review: flags that are true by construction and carry no information. It also makes "the scorer flagged this" stop being independent of "the rules were unhappy with this", which is precisely the independence a caseworker reading both is entitled to assume.

Measured, not reasoned. The first fit of this part carried five such echoes (routing confidence, gap count, completeness verdict, minimum extraction confidence, discarded count). The whole top of its distribution was letters with nothing extracted and items with no derivable procedure - every one already at tier 3 - and it found none of the nine labelled anomalies. The rejected echoes are named in `engine/score/features.py::QUALIFYING_FIELD_ECHOES` and pinned by a test.

### 3. Identity-blindness is a refusal, not a mask

Every string on the way to a number or to a rendered reason passes a guard that masks with part 04's single masking definition and then REFUSES anything still shaped like a placeholder - including the bare `[[PII` opener that matched neither regex and that part 08's round-trip property caught on its way into a letter. Refusing rather than masking is deliberate: a masked value in a numeric feature is a feature over blanks, and a masked value in a caseworker's reason is a sentence with a hole in it.

PRESENCE is a separate function from VALUE, and only presence may touch a sealed field. "Did this person write a Versicherungsnummer down" is a fact about the form; what it says is a fact about the person. Part 04 already pinned that presence survives sealing.

**What this costs, stated rather than discovered.** Two of gold v4's nine labelled anomalies are structurally out of this scorer's reach. `ar-0042` (an applicant aged 118) needs the Geburtsdatum and `ar-0044` (Bereichsnummer 99) needs the Versicherungsnummer, and part 04 seals both before the scorer runs. The original design brief asked for a Bereichsnummer-rarity feature; it cannot be built without the witness, and the P-3 feature-set contract forbids the witness for exactly the reason that makes it tempting.

That is not a gap to be worked around. It is a hand-off moving back: implausible age and an unissued Bereichsnummer are deterministic checks over the witness, next to the birthdate-in-VSNR cross-check `engine/evidence/completeness.py` already performs. Part 03 sent them to the scorer because the repository cannot cite the list of issued Bereichsnummern; part 04 changed the constraint set, and the honest answer today is that the rule plane can see them and the scorer cannot.

### 4. Channel is not a feature; item shape is

On every configured intake path the channel is a function of the item shape, so a separate channel feature would weigh one signal twice. More importantly, a scorer that learned "paper is unusual" would systematically flag the people least able to use the online form. Shape stays in the vector because it genuinely changes what normal looks like for the extraction features; both channel and shape are dimensions of the P-2 bias section, so a skew is visible rather than inferred.

### 5. The reference population is a committed artifact, not a pickle

`config/scoring/reference_gold_v4.json` carries the 101x8 feature matrix as rounded numbers with its provenance - feature-set version, seed, corpus, installed scikit-learn version, and the score each row produced. The forest is re-fitted from it at load time in a few milliseconds, cached by content digest.

A committed model binary would be unreadable, unreviewable, and would silently keep working after the feature set changed under it. The matrix diffs; a pickle does not. `python -m eval.score_fit --check` rebuilds it from the frozen corpus and compares byte for byte, which makes "the reference population is a pure function of (corpus, feature set, seed, engine)" a gate rather than a claim.

Determinism is machine-local and says so: fixed seed, fixed feature order, no clock, no dict-order dependence, and the library version recorded next to every number. A tree ensemble is only reproducible against the library that grew it.

### 6. Reasons come from the model, by ablation

For every feature, the item's value is replaced by the reference median and the item is re-scored; the drop in score is that feature's contribution. It is a measurement on the real model rather than a plausible-sounding story told next to it, it is deterministic, and it costs one extra batch of nine rows per item.

A flagged item always carries at least one reason. When no feature clears the configured minimum - which happens when an item is unusual only as a COMBINATION - the feature furthest from the reference median is reported with its measured contribution, whatever that is. A flagged item with an empty reason list is the one output this system may not produce, so the fallback lives in the renderer rather than in a caller that might forget.

### 7. The threshold was tuned on the frozen gold set, and this says so

`anomaly_gold_v4_v1 = 0.86`, in `config/scoring/scoring_v1.yaml`. `config/thresholds.yaml` stays frozen and `anomaly_default_v0` remains what it always was: the uncalibrated placeholder part 01 had to pick to make the system run. `AnomalyEvidence.threshold_ref` names which of the two governed an item.

**The forest sees no labels** - the corpus is a reference population, not a training set with targets, so ADR-010's "never trained on" holds. **The threshold was chosen while looking at the nine labels**, so the recall and false-flag numbers on gold v4 are IN-SAMPLE and are not an out-of-sample estimate of anything. Phase 1 has no outcome data and no second labelled set; the plan's own instruction is to calibrate here, and hiding that would be worse than doing it.

Why 0.86 and not the quieter alternatives, off `python -m eval.score_fit --distribution`:

| threshold | flags | recall (of 9) | false flags | tier-1 false flags | rate | would move |
|---|---|---|---|---|---|---|
| 0.75 | 26 | 7 | 19 | 1 | 0.077 | 13 |
| 0.80 | 22 | 7 | 15 | 1 | 0.077 | 11 |
| 0.84 | 17 | 7 | 10 | 1 | 0.077 | 9 |
| **0.86** | **15** | **7** | **8** | **1** | **0.077** | **7** |
| 0.88 | 13 | 6 | 7 | 0 | 0.000 | 6 |
| 0.90 | 13 | 6 | 7 | 0 | 0.000 | 6 |
| 0.93 | 8 | 3 | 5 | 0 | 0.000 | 4 |
| 0.97 | 4 | 3 | 1 | 0 | 0.000 | 2 |

0.86 is the HIGHEST threshold that still reaches every anomaly this identity-blind feature set can reach (7 of 9; the other two need sealed values). Rejected:

- **0.88 / 0.90** select the same 13 items and drop `sf-0041`, the Honorarhoehe case BSG 31.03.2017 (B 12 R 7/15 R) calls a gewichtiges Indiz. They buy one fewer tier-1 false flag, and the downgrade-rate budget is a BOUND, not a target: 0.077 is half of the 0.15 in the frozen risk config.
- **0.93 and above** drop the Indizienbuendel as well - the scorer would stop finding the thing part 03b built three items for.
- **0.84 and below** find nothing further and add two to eleven more false flags. Strictly dominated.

It also sits in a gap rather than on a cliff: the nearest included score is 0.871, the nearest excluded 0.851.

### 8. P-1 sampling is arithmetic a caseworker can redo

`blake2b(case_id, key=salt)`, first eight bytes big-endian over 2**64, sampled when the draw is below `AgencyRiskConfig.audit_sample_rate`. Rate and salt live apart on purpose: the rate is agency policy and rides the risk config every DecisionRecord already names by version, the salt is operational and must be rotatable without superseding a frozen config version. The shipped rate is 0.0, so gold behaviour is unchanged.

The reason text says out loud that a drawn case is NOT an Auffaelligkeitsbefund. An applicant whose case was pulled at random and who is then treated as suspect has been harmed by a control that exists to protect them.

## Consequences

- **The score means something a Fachbereich can argue about.** "Review the most unusual 15 of 101" is a workload decision; "the model said 0.86" is not.
- **The scorer never blocks the pipeline.** Every failure - a missing reference population, a matrix fitted on another feature set, a sealed value reaching a feature, a model that raises - produces no anomaly evidence and a journaled degradation. That is the state the decision plane was in for eight parts, so a broken scorer costs the extra oversight and nothing else. It can never produce tier 1 (it produces no tier) and can never silence an item (the degradation is an event, not a hole).
- **The in-sample numbers cannot justify enforcement.** They are the reason `scorer_mode` stays where the friction is.
- **Two labelled anomalies are permanently out of scope for this component**, and the honest fix is a rule over the witness rather than a feature over a token. Recorded as a finding for whoever supersedes the procedure configs.
- **A finding this part could not fix**: `table_v1`'s second downgrade row fires on `anomaly.score >= 0.85`, a literal written in part 01 before any score scale existed. On the calibrated percentile scale it is one notch LOOSER than the flag threshold, so an enforcing run would downgrade two items the scorer did not flag (both already tier 2, so it adds oversight to items nobody was clearing - the valve working as designed). `table_v1`'s version is frozen into the gold manifest, so aligning them costs a table supersession. Pinned by a test rather than worked around.
- **Cost of the artifact approach**: re-fitting is a deliberate command with a checked-in diff, so a feature-set change is visible in review as 101 changed rows. That is the point, and it is also friction.
