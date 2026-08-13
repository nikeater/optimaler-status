# AI Act Scoping Memo - EingangsLotse

**DRAFT FOR LEGAL REVIEW. This is engineering input, not legal advice.** It is written by the team that built the system, to be checked by people qualified to check it. Everything below marked [analysis] is legal application rather than settled law; everything marked [verify] carries a residual uncertainty this repository could not close.

**Status:** draft, 2026-08-12, part 09. Sources: `docs/research/legal-implementability-map-2026-08-11.md` (the research pass this memo transcribes and structures), the ADRs it cites, and the measurements in `docs/ENGINEERING_LOG.md`. Compliance backlog row C-1.

**What changed in part 09, and why this memo exists now:** until part 09 the component that would be an AI system did not exist. It does now (`engine/score/`, ADR-024), it runs on every item, and it is log-only. The scoping question stops being hypothetical at that moment.

---

## 1. What this system is, in the terms the Regulation uses

Two planes, and the distinction is the whole memo:

| | Deterministic plane | Probabilistic plane |
|---|---|---|
| What it is | Config-declared rules: routing predicates, completeness requirements, the tier decision table | A zero-shot similarity classifier (part 06, off by default) and the shadow scorer (part 09) |
| Who wrote the logic | People, in YAML an agency edits | An unsupervised model fitted on a frozen synthetic reference population |
| What it decides | Everything. The tier, the addressee, whether a draft exists | Nothing. It produces evidence |
| Where it can act | The tier decision table | One syntactic slot, downgrade conditions only, tier 3 only |

The **decision** is always the deterministic plane's. Every rule is a sentence somebody wrote in a config file; nothing in that plane infers anything from data.

## 2. Is the deterministic engine an "AI system"?

**Position: no.** [analysis]

Art. 3(1) requires a machine-based system that infers, from the input it receives, how to generate outputs. Recital 12 says explicitly that systems based on rules defined solely by natural persons to automatically execute operations are outside the definition.

The routing rules, the completeness requirements, the derivation signals, the clear-cut criteria and the tier decision table are exactly that: declarative predicates in `config/`, authored by people, interpreted by a small evaluator (`engine/predicate.py`, `engine/decide/interpreter.py`). No weights, no fit, no inference. A change in behaviour requires somebody to edit a rule.

**What could weaken this position, stated honestly.** The system as a whole contains model-backed components (redaction NER, the classifier, the scorer, and optionally an LLM extractor). If "the AI system" is scoped as the whole product rather than per component, the answer is different. The per-component reading is the one the two-plane architecture was built for and the one this memo takes - and the architecture is what makes the reading checkable rather than asserted: the tier is computed by a pure function whose inputs are an evidence record and a config, and a Hypothesis property proves on every commit that no model output can improve it.

## 3. The shadow scorer IS an AI system

No hedging here. `engine/score/` fits an IsolationForest on a reference population and infers, from an item's features, an output (an anomaly score) that influences a physical or virtual environment (a caseworker's queue). That is Art. 3(1).

The question is therefore not whether the AI Act applies to it, but whether it is **high-risk**.

## 4. Annex III classification

### 4.1 Annex III point 5(a): essential public assistance benefits and services

The relevant entry covers AI systems intended to be used by public authorities to **evaluate the eligibility** of natural persons for essential public assistance benefits and services, or to **grant, reduce, revoke or reclaim** such benefits.

Statutory pension (par. 35 ff. SGB VI), Erwerbsminderungsrente (par. 43 SGB VI) and the Statusfeststellung under par. 7a SGB IV are within the subject-matter this entry is about. So the question is what the scorer DOES with respect to those benefits.

**The scorer cannot grant, reduce, revoke or reclaim anything**, and this is structural rather than operational:

- `QualifyingCondition` rejects any field beginning `anomaly.` - the contract refuses to parse a rule that would let an anomaly score qualify an item for a better tier;
- `DowngradeCondition` accepts `anomaly.*` only, with monotone operators only, and a target tier fixed at 3;
- the engine applies `max(tier, to_tier)`, so a downgrade cannot improve a tier;
- `DecisionRecord` refuses to persist a tier better than its own `pre_downgrade_tier`;
- a Hypothesis property proves end to end, against the real decision table and (since part 09) against the 101 real evidence records of the frozen gold set, that raising anomaly evidence never lowers a tier - in log-only mode and in an enforcing config.

**Does it "evaluate eligibility"?** [analysis] This is the harder half and the honest answer is: it evaluates whether an item is UNUSUAL, and the only thing it can cause is that a person looks at it. It never scores eligibility, produces no eligibility statement, and its output is not an input to any eligibility determination - the completeness checker and the decision table make that determination and cannot read the score. Against that: a system that decides who gets extra scrutiny is not nothing, and a reading that "evaluate" covers "triage for evaluation" is available.

**Verdict: not squarely within Annex III 5(a), and not safely outside the scope of the argument.**

### 4.2 The Art. 6(3) derogation, and the profiling exception

Art. 6(3) provides that an Annex III system is NOT high-risk where it does not pose a significant risk of harm to health, safety or fundamental rights, including by not materially influencing the outcome of decision-making. Four conditions are listed; two are relevant here:

- **Art. 6(3)(c)**: the system is intended to perform a **preparatory task** to an assessment relevant for the purposes of the use cases listed in Annex III.
- **Art. 6(3)(d)**: the system is intended to improve the result of a previously completed human activity - not applicable here.

Point (c) fits the scorer well. Its output is preparatory in the strict sense: it prepares nothing more than the order and the depth of human attention, and the assessment itself is performed by a person on evidence that never includes the score. (A narrow-procedural-task reading under Art. 6(3)(a) is also arguable and weaker: "is this item unusual" is not obviously procedural, so this memo does not lean on it.)

**The profiling exception, engaged rather than waved away.** Art. 6(3) subparagraph 2 provides that an Annex III system is ALWAYS high-risk where it performs profiling of natural persons.

Profiling under Art. 4(4) GDPR is any automated processing of personal data to evaluate certain personal aspects relating to a natural person, in particular to analyse or predict aspects concerning that person's economic situation, reliability, behaviour, and so on.

This is the point where the memo must not be comfortable. The scorer processes personal data (the item is an application by a named person, even though the identifiers are sealed) and evaluates aspects of that person's submission. Two things are true at once and both belong in the record:

1. **The features are about the FILE, not the person.** The identity-blind feature set (ADR-024, `engine/score/features.py`) contains: the distance between a stated date and the arrival date, the share of recorded par. 7a Indizien pointing one way, the stated revenue share with a main client, how much of the form was filled in, whether the item is a letter, whether it came off a scanner. There is no per-applicant history, no prior-flag feature, no name, no address, no birth date, no insurance number - and this is enforced by a property test over the input TYPE and by a runtime guard that refuses a sealed value rather than computing over it. Two of the frozen gold set's own labelled anomalies (an implausible age, an unissued area number) are consequently OUT of this scorer's reach, which is the strongest available evidence that the blindness is real rather than claimed.
2. **Some of those features are nevertheless about the person's economic situation.** The revenue share with a main client and the Indizienbuendel of par. 7a SGB IV are, by design, indications about how somebody works and earns. Calling that "not profiling" because the name is sealed would be a re-identification argument, and the DPIA input already states plainly that sealing is pseudonymisation and not anonymisation.

**Verdict [analysis]: the derogation is defensible; the profiling subparagraph is a real argument against it, strongest for the Statusfeststellung feature family. This memo does not claim the question is closed.**

### 4.3 What follows either way

The prudent posture, and the one this repository already implements:

| Duty | If the derogation holds | If it does not | Status here |
|---|---|---|---|
| Art. 49(2) registration in the EU database before placing on the market or putting into service | **Required** - a provider claiming Art. 6(3) must still register | Full Annex III registration | **Open. Not done, and cannot be done from a repository: it is a provider act at deployment.** |
| Documented Art. 6(3) assessment | Required | n/a | This memo is its engineering half |
| Art. 26 deployer duties (human oversight by trained persons, log retention >= 6 months, monitoring) | Voluntary | Mandatory | Journal design done (part 01+); roles and retention are part-10/deployment items (C-3) |
| Art. 27 FRIA | Voluntary | Mandatory for public bodies | Open, pre-pilot (C-2) |
| Art. 26(7) information of worker representatives | Voluntary | Mandatory | Open, pre-pilot (C-4, and BPersVG par. 80 requires it independently of the AI Act) |
| Art. 14 human oversight | Voluntary | Mandatory | The valve, the tier system and the confirm step are the design; measured rubber-stamp metrics are P-6, part 10 |

**Recommendation: build as if Annex III applied.** The obligations are ones a public body should meet anyway, the derogation is an argument rather than a fact, and a pilot outlives a deferral.

## 5. Timing: what applies when

- **Art. 5 prohibitions, Art. 4 AI literacy**: in application since February 2025.
- **GPAI obligations**: since August 2025.
- **Art. 50 transparency**: 2 August 2026.
- **Annex III standalone high-risk obligations (including Art. 26 and Art. 27)**: **2 December 2027**, moved by the Digital Omnibus amending Regulation (EU) 2024/1689, published in the Official Journal on 24 July 2026, in force 27 July 2026. [verify] The date was established via secondary legal commentary during the research pass; the OJ text itself was not fetched and must be checked before any external citation.

The project concept (user-owned, canonical) still states 2 August 2026. That was flagged to the user rather than edited.

**GDPR, BPersVG and SGB apply now**, so the FRIA-equivalent analysis, the DPIA and the Personalrat involvement are not deferred by anything above.

## 6. Art. 50 transparency

Satisfied without needing a carve-out, and by measurement rather than by argument: **no model-generated text reaches a citizen on any path in this system.** Notifications render from `config/notifications/`; drafts assemble sentences the procedure configs author; both close with a statement that no language model was used, and both are frozen as golden files. The scorer produces reasons for CASEWORKERS, never for applicants, and no draft or notification reads them.

The condition reopens if LLM-assisted drafting is ever proposed, at which point the Art. 50(4) carve-out has to be EARNED by a review a caseworker demonstrably performs (compliance rows C-13, P-6).

## 7. Art. 22 GDPR, and why it is the sharper question today

Independently of the AI Act, and applicable now:

- Receipt and status notifications are Realakte with no Regelungswirkung: outside Art. 22 entirely [settled].
- The tier-1 "prepared decision plus one-click confirm" path IS an automated decision unless the human step is meaningful. CJEU C-634/21 (SCHUFA) makes an automated preparatory step the decision itself where the human step is a rubber stamp.
- **The shadow scorer does not change this analysis**, because it cannot reach tier 1 and cannot produce a decision. What it changes is the evidence base: an item it flags gets MORE human attention, never less.

The rubber-stamp rebuttal has to be a measured artifact, not a design claim. That is P-6 and part 10.

## 8. What changes if the scorer ever enforces

Today `scorer_mode: log_only` lives in `config/thresholds.yaml`, whose version string is frozen into the gold set's manifest, so switching to `enforcing` requires a deliberate config supersession. That friction is the point. If it is ever taken:

1. **The system's behaviour changes for real.** A flagged item that the rules cleared to tier 1 or 2 moves to tier 3. On gold v4 that would be 7 of 101 items. The AI Act analysis does not change - the direction is still "more human review" - but the "does not materially influence the outcome of decision-making" limb of Art. 6(3) becomes materially harder to argue, because the scorer would then determine which cases a human sees.
2. **Art. 22 stays untouched** in direction: enforcing can only add human involvement.
3. **The in-sample calibration stops being adequate.** The threshold was chosen while looking at the nine labelled anomalies of one frozen synthetic corpus (ADR-024, stated there in the same words). Enforcing on that basis would be enforcing on a number nobody has validated out of sample. The prerequisite is reviewed flag precision from a pilot, which is what ADR-004 said in part 01.
4. **The Fachbereich has to own the threshold.** "Review the most unusual 15 percent" is a workload decision with a Personalrat dimension (BPersVG par. 80), not an engineering setting.
5. **A FRIA (Art. 27) and an updated DPIA are due before, not after.**

## 9. P-1 audit sampling and its own legal footing

Part 09 also ships deterministic random sampling into full review (`AgencyRiskConfig.audit_sample_rate`, shipped at 0.0). It is modelled on the par. 88 Abs. 5 Nr. 1 AO risk-management-system pattern, which the research pass identified as the operational template for par. 31a SGB X's "kein Anlass" filter.

It is **not** an AI system: a keyed hash of the case id, uniformly mapped, with no data about the person in it at all. It only ever adds review. It matters legally because "kein Anlass, den Einzelfall durch Amtstraeger zu bearbeiten" (par. 31a S. 1 SGB X) is not credible without a mechanism that pulls cases nobody suspected - and because a sample nobody can recompute is not an audit measure, the draw is reproducible from the case id and the configured salt in one line.

## 10. Open items and who owns them

| # | Item | Owner | Backlog |
|---|---|---|---|
| 1 | Verify the Digital Omnibus dates against the OJ text before any external citation | Legal | this memo [verify] |
| 2 | Decide whether the Art. 6(3)(c) derogation is claimed - and if so, register under Art. 49(2) | Legal + provider | C-1 |
| 3 | FRIA (Art. 27) before the pilot | Legal + Fachbereich | C-2 |
| 4 | Art. 26 deployer package: named oversight roles, log retention >= 6 months, monitoring plan | Deployment | C-3 |
| 5 | Art. 26(7) / BPersVG par. 80 information of worker representatives; Dienstvereinbarung | HR + Personalrat | C-4 |
| 6 | Whether the profiling subparagraph is engaged by the par. 7a Indizien features specifically | Legal | this memo, section 4.2 |
| 7 | Measured override and rubber-stamp rates as the Art. 22 evidence base | Part 10 | P-6, C-5 |
| 8 | If the scorer is ever fitted on real intake, the reference population becomes a derived personal-data set and must enter the DPIA | Data protection | C-5 |

## 11. What a reviewer should check first

Three things carry most of the weight of this memo, and all three are executable:

1. `tests/test_config_valve_properties.py` and `tests/test_decide_properties.py` - the contract refuses an anomaly field in a qualifying condition, and the tier is monotone in anomaly evidence.
2. `tests/test_score_monotonicity.py` - the same, against the real decision table and the 101 real evidence records of the frozen gold set, in log-only and in an injected enforcing config.
3. `tests/test_score_feedback_guard.py` - the feature set admits no per-applicant history and no prior flag, checked on the input type rather than promised in prose.

If any of those is red, this memo is describing a system that does not exist.
