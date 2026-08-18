# Transparency record - EingangsLotse v0.1.0

**Configuration state:** the versions in section 3, unchanged.
**Measured on:** `corpus/gold/v4`, 101 items, frozen.
**Report this document is read from:** `eval/reports/latest.json`, generated
2026-08-18T11:29:13Z, `gate_passed: true`.

This is an ATRS-style transparency record: one document per configuration
version, written for a Fachaufsicht, a data-protection officer or a reviewer who
has to decide whether an assistive triage system may be trusted with an inbound
queue. It answers four questions in order - what the system decides, what a
human decides, which numbers back the claim, and what the system does not do.

**The rule for this document.** A transparency record describes ONE
configuration state. If any version in section 3 moves, this record is
superseded by a new one rather than edited, exactly the way a frozen
configuration is superseded rather than edited. The living records it summarises
are the eval report (regenerated on every run), `docs/KNOWN-ERRORS.md` (the
current failure modes) and `docs/adr/` (the decisions). Every number below is
either read out of the eval report or fixed by an ADR; the provenance column or
sentence says which, and section 13 lists how to reproduce each one.

**What this release is.** A prototype with a complete pipeline, publicly
demonstrable over synthetic data. It is not a pilot, it has never seen real
data, and section 10 is the list of reasons it may not.

---

## 1. What the system decides, and what it does not

EingangsLotse reads an inbound item - a structured form or a free-text letter,
born digital or scanned - and assigns it a **review tier**:

| Tier | Meaning | Who acts |
| --- | --- | --- |
| 1 | Routing, completeness and the procedure's clear-cut criteria all hold | A caseworker still confirms; the system prepares, it does not grant |
| 2 | Something is missing or unproven | A caseworker works the case; a Nachforderung may be prepared for them |
| 3 | Doubt, conflict, or no matching row at all | A caseworker works the case; this is the default |

**No tier is a decision on the application.** Tier 1 does not grant, pay,
reject or notify anybody of an outcome. It is a statement about how much
oversight the item needs, and the only thing it changes is which queue a human
finds it in. Everything with a legal consequence goes the written route with a
human signature (README, "Compliance posture"; `docs/ai-act-scoping-memo.md`).

**The default is tier 3.** The decision table evaluates rows top-down, first
match wins, and no match means tier 3 - "in doubt, tier 3"
(`config/decision/table_v1.yaml`, header).

**Three procedures are configured**, and one of them ships no clear-cut
criteria at all: the Feststellung des Erwerbsstatus under par. 7a SGB IV
carries `tier1_enabled: false`, because its decision is a statutory
Gesamtwuerdigung and a checklist would fake an answer the law does not permit.
A formally complete application in that procedure still ends at tier 3, by
design (ADR-035; `config/procedures/statusfeststellung_v1.yaml`).

## 2. Where the machine may be probabilistic, and where it may not

Two planes, and only one of them decides (ADR-001):

- The **evidence plane** may be probabilistic. It extracts values, may consult
  a zero-shot unit classifier and runs an anomaly scorer.
- The **decision plane** is the versioned decision table. It reads only what is
  proven. Same evidence plus same configuration equals the same decision, every
  time.

**The valve only opens one way** (ADR-004). Qualifying conditions may reference
only the non-anomaly fields in `schemas.config.QUALIFYING_FIELDS`; the schema
rejects `anomaly.*` there. Downgrade conditions may reference anomaly fields
only, with monotone operators and a fixed target of tier 3, and the engine
applies `max(tier, to_tier)`. Uncertainty can therefore add oversight and can
never remove it. This is proved on every commit as a property test against the
real decision table and the 101 evidence records of the frozen set, not asserted
in prose.

## 3. The shipped configuration, file by file

Every file an agency edits, with the version string the file itself carries.
The five marked **frozen** are stamped into `corpus/gold/v4/MANIFEST.yaml` under
`config_versions`, and gold v4 is verified by a byte-identical rebuild - so
changing one of them either invalidates the frozen corpus or ships under a
version string that no longer describes the file. They are superseded, never
edited.

| File | Version | Frozen | What it governs |
| --- | --- | --- | --- |
| `config/taxonomy/drv_bund_v2.yaml` | `taxonomy_drv_bund_v2` | yes | The organisational units an item can be routed to |
| `config/rules/routing_v3.yaml` | `routing_v3` | yes | Which unit a rule proposes, and at what priority |
| `config/decision/table_v1.yaml` | `table_v1` | yes | The tier decision itself |
| `config/thresholds.yaml` | `risk_v0` | yes | `scorer_mode`, the downgrade-rate budget, the historical anomaly placeholder |
| (schema contracts) | `0.1.0` | yes | The record shapes in `schemas/`, exported to `schemas/artifacts/v0.1.0/` |
| `config/procedures/altersrente_v1.yaml` | `altersrente_requirements_v1` | yes | Requirements, field map, calendar bounds, clear-cut criteria |
| `config/procedures/erwerbsminderungsrente_v1.yaml` | `erwerbsminderungsrente_requirements_v1` | yes | as above |
| `config/procedures/statusfeststellung_v1.yaml` | `statusfeststellung_requirements_v1` | yes | as above; `tier1_enabled: false` |
| `config/extraction/extraction_v1.yaml` | `extraction_v1` | no | Prompt version, chunk size, the two span-match minimums |
| `config/scoring/scoring_v1.yaml` | `scoring_v1` | no | The calibrated anomaly threshold, the feature set, the audit-sampling salt, the bias advisory |
| `config/redaction/identity_fields_v1.yaml` | `identity_fields_v1` | no | Which fields are identity-classed and therefore sealed |
| `config/notifications/notifications_v1.yaml` | `notifications_v1` | no | The message catalogue and its trigger events |
| `config/drafting/drafting_v1.yaml` | `drafting_v1` | no | The Nachforderung and prepared-decision templates |
| `config/dispatch/dispatch_v1.yaml` | `dispatch_v1` | no | Ordering and delivery of notifications |
| `config/queues/queues_v1.yaml` | `queues_v1` | no | The caseworker queues and their exception paths |
| `config/classifier/classifier_v1.yaml` | `classifier_v1` | no | The zero-shot unit classifier, disabled in this release |
| `config/review/threshold_review_v1.yaml` | `threshold_review_v1` | no | The date the thresholds must be looked at again |
| `config/demo/personas_v4.yaml` | `personas_v4` | no | The demonstration journey's four fictional applicants |

The decision table is `table_v1`. It carries exactly two qualifying rows -
`tier1_clear_and_complete` and `tier2_routable_incomplete` - and a
`default_tier` of 3. Both rows require routing confidence at or above 0.9,
because the question "is the routing trustworthy" has one answer, not one per
tier (`config/decision/table_v1.yaml`; ADR-014). Its two downgrade rows,
`downgrade_anomaly_flagged` and `downgrade_anomaly_score`, both target tier 3
and are inert in this release (section 5).

## 4. Every threshold, its value, and where the number came from

Read out of the `thresholds_review` section of the eval report, which assembles
them from the files that own them - there is no second copy of any number.
Three of the seven are **not calibrated**, and the report counts them
(`uncalibrated_count: 3`) rather than letting a reader assume otherwise.

| Threshold | Value | Source file (version) | Calibrated | Where the number came from |
| --- | --- | --- | --- | --- |
| `span_match_born_digital` | 1.000 | `config/extraction/extraction_v1.yaml` (`extraction_v1`) | yes | Exact by construction; the loader refuses an exact policy below 1.0. 57 spans matched exactly in this run |
| `span_match_ocr` | 0.860 | `config/extraction/extraction_v1.yaml` (`extraction_v1`) | yes | Measured on gold v4's OCR letters (part 05): at 0.86 a twelve-character quote may differ in one character, not two. 31 fuzzy spans observed, minimum 0.9394, closest margin 0.0794; a sweep of -0.05/-0.01/+0.01/+0.05 discards nothing |
| `routing_confidence` | 0.900 | `config/decision/table_v1.yaml` (`table_v1`) | yes | ADR-014: set above the contested-conflict confidence 0.6, so an item two equal-priority rules disagree about cannot clear it. Distribution over 101 items: 94 at 1.000, 2 at 0.600, 5 at 0.000; the same 94 qualify at every swept value |
| `anomaly_gold_v4_v1` | 0.860 | `config/scoring/scoring_v1.yaml` (`scoring_v1`) | yes, **in-sample** | Chosen 2026-08-12 from the score distribution of `python -m eval.score_fit --distribution` over gold v4 (101 items, 9 `anomaly_expected`), IsolationForest over feature set `fsv_v1`, seed 42. The highest value that still reaches every anomaly this identity-blind feature set can reach; it sits in a gap (nearest included score 0.871, nearest excluded 0.851). The forest saw no labels; the THRESHOLD was chosen while looking at them, so its recall and false-flag numbers are in-sample and the file says so (ADR-024) |
| `anomaly_default_v0` | 0.850 | `config/thresholds.yaml` (`risk_v0`) | **no** | A placeholder from part 01 that was never calibrated. It is superseded as the governing threshold by the row above; `AnomalyEvidence.threshold_ref` names which of the two actually governed the item in front of you. It survives only because `risk_v0` is frozen into the gold manifest |
| `downgrade_rate_budget` | 0.150 | `config/thresholds.yaml` (`risk_v0`) | **no** | An efficiency budget chosen with the scorer's design (ADR-004), not a measurement. It is a BOUND, not a target |
| `classifier_min_confidence` | 0.900 | `config/classifier/classifier_v1.yaml` (`classifier_v1`) | **no** | Explicitly not comparable to anything the classifier currently produces. The loader refuses to enable the classifier while that is true, which is why the classifier is off in this release |

**The threshold review date is 2026-11-30** (`config/review/threshold_review_v1.yaml`,
`threshold_review_v1`), chosen as the end of the quarter after the one the
thresholds were measured in. The warning is informational: it prints in the
report and the metrics panel and never touches an exit code, because a gate must
not start failing because a calendar page turned. As of the run above it is 104
days away and not overdue (backlog P-5, par. 88 Abs. 5 Nr. 4 AO by analogy).

## 5. The scorer runs in log-only mode, and that is structural

`scorer_mode: log_only` (`config/thresholds.yaml`, `risk_v0`), confirmed in the
report's `scorer_mode` field.

In log-only mode the engine records what a downgrade WOULD have done and changes
nothing. In this run 7 of the 15 flagged items would have moved tier had the
scorer been armed, and 8 flags sat on items whose tier they could not have
changed.

Turning it on is not a switch somebody flips: `scorer_mode` lives in the
**frozen** `thresholds.yaml`, so arming the scorer means superseding a frozen
configuration version - deliberately as much friction as the decision deserves.
Even armed, nothing the scorer produces can clear an item: downgrades have a
fixed target of tier 3 and the engine applies `max(tier, to_tier)` (section 2).

The scorer is also **identity-blind by construction**. No feature is computed
from a Geburtsdatum, a Versicherungsnummer or an Anschrift, because sealing runs
before scoring and a feature over a sealed value would be a feature over a
random token. Two of the nine labelled anomalies (`ar-0042`, `ar-0044`) are out
of its reach for exactly that reason and are caught, if at all, by deterministic
cross-checks over the transient witness (ADR-024).

## 6. The four gates

`python -m eval.run` exits non-zero if any of these moves. Both CI pipelines run
it on every push - `.github/workflows/gate.yml` step "The four eval gates" and
`.gitlab-ci.yml` - and the container build runs it inside the image, so an image
that cannot pass its own gate is never produced.

| Gate | Value in this release | Budget | Report field |
| --- | --- | --- | --- |
| False-clear rate | **0.000** | zero, permanently | `false_clear_rate` |
| Deterministic redaction recall | **1.000** | 1.000, per kind | `redaction.deterministic_recall`, `redaction.deterministic_gate_passed` |
| Structured-subset invariant | **held** | no form item may move | `structured_subset.invariant_held`, `structured_subset.moved_items: []` |
| Anomaly reasons present | **all** | no flag without a feature-level reason | the anomaly section's per-item reasons |

A false clear is the only failure this system is built around: an item sent to
"no human needed" that needed a human. Its budget is zero and it is not
negotiable.

**The redaction gate is the DETERMINISTIC number.** It is 1.000 without the
optional NER model, measured per kind over the seeded German-PII set
(`corpus/pii_golden`, 81 items): ADDR, AKTZ, BNR, EMAIL, GEBDAT, IBAN, ORG,
STID, TEL and VSNR are all at recall 1.000 from pattern-and-checksum
recognizers. NAME is the one kind that needs the optional model and is
therefore **not** gated. That is why no gate in this project depends on which
wheels a machine has, and the CI asserts the optional extras really are absent
before it runs.

## 7. The measured numbers of this release

All from `eval/reports/latest.json` over `corpus/gold/v4` (101 items: 77
structured forms, 24 free-text letters, of which 8 are OCR; channels: 77
FIT-Connect, 16 e-mail, 8 scan).

| Metric | Value | Report field |
| --- | --- | --- |
| Routing accuracy | 1.000 | `routing_accuracy` |
| Tier accuracy | 1.000 | `tier_accuracy` |
| False-clear rate | 0.000 | `false_clear_rate` |
| False-flag rate (tier judgement) | 0.000 | `false_flag_rate` |
| Completeness precision / recall | 1.000 / 1.000 | `gap_precision`, `gap_recall` |
| Procedure derivation accuracy | 1.000 over hint (62), content (23) and neither (16) | `procedure_derivation` |
| Span verification | 88 proposed, 88 verified, 0 discarded | `span_verification` |
| - match modes | 57 exact, 31 fuzzy, 386 structured | `span_verification.match_modes` |
| Structured subset | 77 items, invariant held, 0 moved | `structured_subset` |
| Anomaly scorer | 15 of 101 flagged at 0.86 (rate 0.1485) | `anomaly.flagged` |
| - recall on labelled anomalies | 7 of 9 (0.7778) | `anomaly.anomaly_expected` |
| - flags on tier-1-eligible items | 1 of 13 (0.0769), budget 0.15 | `anomaly.false_flags` |
| - scorer degradations | 0 | `anomaly.degraded` |
| Anomalous subset | 9 items, tier agreement 1.000, false clears 0.000 | `anomalous` |
| Notifications | 197 messages to 101 of 101 items | `notifications` |
| - by template | 101 Eingangsbestaetigung, 96 Zuordnung | `notifications.by_template` |
| Prepared drafts | 60 (47 Nachforderung, 13 prepared decision), 41 items with none | `drafting` |
| - re-hydrated tokens | 160 resolved, 0 unresolved, 0 blocked | `drafting.tokens`, `drafting.unresolved_tokens` |
| Review queues | 101 open over 7 units, 5 unrouted to central clearing | `review` |
| - by tier | 13 tier 1, 47 tier 2, 41 tier 3 | `review.by_tier` |
| Classifier | configured, **not enabled**, not calibrated, not admitted to decisions | `classifier` |

**Zero unresolved tokens matters more than the count.** A prepared letter with
an unresolved placeholder would be a letter going out with `[[PII|...]]` where a
person's name belongs; 50 of the 60 drafts carry re-hydrated identity data, and
none of them carries a token that failed to resolve.

## 8. What the bias monitoring says, including the part that is uncomfortable

Reported, never gated - an alarm that failed a build would teach people to tune
the alarm (backlog P-2). The advisory in `config/scoring/scoring_v1.yaml` is a
maximum flag-rate ratio of 3.0 across the groups of one dimension, computed only
over groups of at least 5 items.

**All three dimensions are above the advisory in this release.**

| Dimension | Highest group | Lowest group | Ratio | Above advisory |
| --- | --- | --- | --- | --- |
| Procedure | statusfeststellung 0.240 | altersrente 0.0789 | 3.04 | yes |
| Channel | scan 0.250 | e-mail 0.000 | no finite ratio | yes |
| Item shape | ocr 0.250 | born digital 0.000 | no finite ratio | yes |

Read honestly: on this corpus the scorer flags scanned and OCR items at a
quarter and e-mail items at nothing at all, so the ratio is not merely large,
it is undefined. The channel is deliberately NOT a feature - a scorer that
learned "paper is unusual" would systematically flag the people least able to
use an online form - but the item SHAPE is one, and on every configured intake
path the channel is a function of the shape. Whether the skew is the corpus
(gold v4 puts its OCR items where they are on purpose) or the feature set is a
question 101 synthetic items cannot answer. It is recorded here so a pilot
starts from a measured suspicion rather than from a clean slate.

## 9. What holds the record together

- **The journal is the only truth.** Every event is append-only and
  version-stamped; queues, metrics and the case view are folds over it. A
  correction appends a new event, and a second confirmation of the same case is
  refused rather than overwritten (ADR-026).
- **Identity is sealed at ingest**, before an envelope or a journal event
  exists. A post-redaction sweep computes whether the working copy is clean
  instead of asserting it, and residue that survives one auto-seal round refuses
  the submission (ADR-017, ADR-019).
- **Re-hydration happens once**, at render time, in the one module that reads
  the vault (ADR-023). It is the only path on which a person's data reaches a
  printable letter, and it is one of the packages held to `mypy --strict`.
- **Every extracted value carries a double lock**: a verbatim quote and the
  character offset it stands at, both re-checked against the normalized text
  before the value may become an evidence record (ADR-020). A failed lock
  discards the value, which pushes the item toward tier 3 - the system gets more
  cautious, not more wrong.
- **No model-written sentence reaches a citizen.** Notification bodies render
  from templates in versioned configuration; a language model may read and may
  never write to an applicant (ADR-012).

## 10. What this system does NOT do

Stated as a list because a transparency record that only lists capabilities is
an advertisement.

- **It grants nothing, refuses nothing and pays nothing.** No tier is an
  outcome. There is no automated decision with legal effect anywhere in this
  repository, by construction and not by configuration (Art. 22 GDPR;
  `docs/ai-act-scoping-memo.md`).
- **Extraction in every gated path is replay, not a live model.** The four
  gates never call a model. Live extraction exists as a laboratory instrument
  behind `python -m eval.live`, and the measurement that put it there is KE-5:
  two 7B instruct models verified 0 and 3 spans of roughly 86, because a
  character offset is not something a model reads (ADR-028).
- **The zero-shot classifier is off.** It is configured, uncalibrated, and its
  suggestions are excluded from the decision plane even when it runs (ADR-021).
- **The anomaly scorer changes nothing** (section 5).
- **There is no authentication.** The unit picker in the review UI is a query
  parameter validated against the taxonomy, and every page says so. A real
  deployment puts an identity provider there before any real data exists.
- **The vault's development backend is plaintext JSONL.** It says so in its own
  docstring; production requirements are in `docs/vault-dpia-input.md`.
- **There is no database.** The stores are in-memory or JSONL behind a protocol
  that PostgreSQL implements later.
- **The demonstration instance stores its working copies in RAM** and refuses
  `POST /ingest` outright when no ingest token is set. Its state is rebuilt from
  the frozen corpus on every boot, so a restart is a complete reset and nothing
  a visitor does survives one (ADR-027, ADR-029).
- **The demonstration's two-party loop is demo-scoped.** The correlation between
  an application and the other party's statement is a drawn 96-bit token held in
  a RAM compartment with a TTL; it is never derived from case data and reaches
  no journal payload. No journal event records that a Stellungnahme was
  requested, because there is no event type for it and inventing one would be a
  contract change rather than a demonstration (ADR-036).
- **The corpus is synthetic.** Every number in this document is a measurement
  over generated data, and generated data is easier than the world.
- **The accessibility document is a self-assessment**, not a BITV 2.0 audit, and
  no assistive technology has been run against these pages.

## 11. The failure modes that are known and not fixed

Six, in `docs/known-errors/v0.1.0.md` - the snapshot taken for this release. The
one that matters most to a Fachaufsicht is **KE-1**: an OCR-mangled identifier
(a capital `O` where a zero stood) is still a Versicherungsnummer to a human
reader and no longer one to a regular expression, so it survives the sealing
boundary into the working copy. It is measured, it is demonstrated by a canary
test that asserts the mangled run survives, and it is not fixable with a
threshold; what would fix it is per-character OCR confidence at the scanner.

The living list is `docs/KNOWN-ERRORS.md` and it moves; the snapshot above is
what was true at v0.1.0 and does not.

## 12. The legal frame this was built against

Art. 22 GDPR (no fully automated decision with legal effect), the EU AI Act
(scoping in `docs/ai-act-scoping-memo.md`), par. 35 SGB I, par. 37 and par. 26
SGB X for Bekanntgabe and deadlines, par. 16 Abs. 2 SGB I for onward
transmission, and par. 88 Abs. 5 AO by analogy for the risk-management
structure, which is where the threshold review register of section 4 comes
from. Automated notifications are informational Realakte and carry no legal
consequence.

Publishing the logic rather than protecting it is deliberate. The SyRI ruling is
in `docs/research/prior-art-2026-08-11.md` as the reason: a system whose
decision rules are secret cannot be checked by the people it decides about, and
this record, the open configuration formats and the feature-level reasons are
the answer to that. Backlog row P-11 asks for exactly this document.

Still open before any pilot, and named as such: a DPIA (P-13), a FRIA under
Art. 27 AI Act (C-2), and an accessibility audit rather than a self-assessment.

## 13. Reproducing every number in this document

From a checkout at the v0.1.0 tag, with the dev extra installed and neither
optional model extra:

```bash
python -m eval.run          # writes eval/reports/latest.json; exits non-zero if a gate moved
python -m pytest            # the property tests behind sections 2 and 9
```

- Sections 4, 6, 7 and 8 are read out of `eval/reports/latest.json`: the
  `thresholds_review`, `redaction`, `structured_subset`, `span_verification`,
  `anomaly`, `bias`, `notifications`, `drafting` and `review` sections, plus the
  top-level accuracy fields.
- Section 3 is read out of the `version:` field of each configuration file, and
  the frozen five are cross-checked against `config_versions` and
  `requirements_versions` in `corpus/gold/v4/MANIFEST.yaml`.
- Sections 1, 2, 5, 9 and 10 are properties, not measurements. Each names the
  ADR that decided it, and each is asserted by a test rather than by this
  sentence.

Nothing in this document was typed from memory. If a number here disagrees with
a fresh report, the report is right and this record is stale - which means the
configuration moved and a new record was owed.
