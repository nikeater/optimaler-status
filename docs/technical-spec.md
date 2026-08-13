# EingangsLotse: Consolidated Technical Specification (S1-S10, as built)

> **Note added when this document was copied into the public repository (part
> 11).** Three of the records it cross-references are the build workspace's own
> and are deliberately not published here: the per-part engineering log
> (`ENGINEERING_LOG.md`), the compliance backlog (`compliance-backlog.md`) and
> the task board (`../tasks/BOARD.md`). They are a build diary rather than
> documentation of the system, and links to them below will not resolve.
> Everything else this document points at is in
> [`docs/`](README.md). Nothing else was changed.

Final consolidated documentation for the completed S1-S10 build sequence
(parts 01 through 10, gated 2026-08-10 to 2026-08-12; repo `eingangslotse/`,
main at `070ccd1`). This document is the synthesis: it states what was built,
what was measured, and where every deeper record lives. It restates as little
as possible - the per-part narrative is [ENGINEERING_LOG.md](ENGINEERING_LOG.md),
the decisions are [adr/](adr/) via the index [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md),
the compliance record is [compliance-backlog.md](compliance-backlog.md), and the
build and run instructions are [BUILD.md](BUILD.md).

Audience: a new engineer joining the project, or a reviewing Fachbereich that
needs to know what the system does, what it refuses to do, and which claims are
measurements rather than intentions.

Canonical sources of truth remain `eingangslotse-concept.md` (what and why) and
`eingangslotse-implementation-plan.md` (when and how), one directory up. On any
conflict, the plan wins.

---

## 1. System purpose and legal-technical boundaries

EingangsLotse is an open-source, API-first administrative triage engine for
public-sector mass procedures (demo target: Deutsche Rentenversicherung Bund).
It ingests multi-channel inbound payloads (FIT-Connect JSON, e-mail text,
scanned OCR text), performs PII-safe span-verified extraction, evaluates
procedure completeness and routing rules, runs an unsupervised ML shadow-risk
check, routes items to role-based organizational queues, and notifies
applicants automatically through informational Realakte. It prepares decisions;
it never takes one unattended.

The legal-technical boundaries, each of which is enforced in code rather than
promised in prose:

1. **Two planes, hard boundary** ([ADR-001](adr/ADR-001-two-plane-architecture.md)).
   Probabilistic components (extraction, classifier, shadow scorer) only
   produce *evidence*. Every *decision* (tier, routing, drafting) is executed
   deterministically by versioned config logic. Same evidence + same config =
   same decision, reproducibly.
2. **The one-way valve** ([ADR-004](adr/ADR-004-one-way-valve.md)). Fully
   automated administrative acts require a legal basis and no discretion
   (par. 35a VwVfG / par. 31a SGB X); the default posture is
   prepared-plus-human-confirm. ML anomaly evidence appears exclusively in
   downgrade conditions: it can add human oversight, it can never rescue a
   failed deterministic check or force a tier-1 outcome. This is encoded in
   the contract schemas (qualifying conditions reject `anomaly.*` fields) and
   proved by Hypothesis property tests against the real decision table on
   every commit.
3. **Identity boundary** ([ADR-002](adr/ADR-002-identity-vault.md),
   [ADR-017](adr/ADR-017-seal-at-ingest-transient-witness.md)). PII is sealed
   into a vault at ingest, before the envelope exists; randomized placeholders
   pass through all model layers; re-hydration happens strictly at outbound
   template rendering, in code, round-trip checked.
4. **Span-verified extraction** ([ADR-020](adr/ADR-020-two-extractors-one-verifier.md)).
   Every extracted value must survive a double lock - verbatim quote and
   offset, verified independently - against the normalized text layer.
   Unverifiable values are discarded toward tier 3.
5. **Organizational routing only** (BPersVG). Items route exclusively to unit
   queues, never to named individuals; review metrics aggregate at unit level,
   and the contract's `Actor` type (kind + unit id, no third field) makes a
   per-person metric inexpressible rather than merely forbidden (C-4,
   property-tested).
6. **Notifications are informational Realakte**
   ([ADR-005](adr/ADR-005-notifications-as-journal-projections.md)). Receipt
   and status messages are automated projections of the case journal,
   deliberately not Verwaltungsakte, and never pass through the review UI.
   Anything with procedural consequence goes through the human-confirmed
   drafting path.
7. **Non-negotiable gates** (Section 6). False-clear 0 percent on the frozen
   gold set, valve monotonicity, vault canaries, placeholder round-trip,
   readable reasons on every anomaly flag. These never move under schedule
   pressure and did not move in ten parts.

Three demo procedures are configured: Altersrente (par. 35 ff. SGB VI),
Erwerbsminderungsrente (par. 43 SGB VI), and Statusfeststellung nach par. 7a
SGB IV. The Statusfeststellung deliberately ships **no clear-cut criteria at
all**: its decision is a statutory Gesamtwuerdigung des Einzelfalles, so a
formally complete Statusantrag ends at tier 3 by design, and the triage value
is completeness, routing to the Clearingstelle, and anomaly evidence. The full
legal reasoning per scenario is in [docs/research/](research/)
(legal implementability map, Statusfeststellung blueprint, prior-art review).

---

## 2. Architecture overview

The stack-annotated component diagram is in
[technical-design.md](technical-design.md); the conceptual diagrams are in the
implementation plan. The shape in one paragraph: inbound submissions are
normalized into an envelope, identity is sealed at the boundary, the evidence
plane assembles extraction, completeness, derivation, routing and anomaly
evidence over the redacted working copy, the decision plane evaluates a
versioned tier decision table over that evidence, and everything downstream -
notifications, drafts, queues, metrics, the correction pool - is a projection
of the append-only case journal ([ADR-008](adr/ADR-008-journal-store-protocol.md)).
There is no other state: the review UI renders the journal and appends to it
and does nothing else ([ADR-026](adr/ADR-026-review-ui-appends-only.md)).

Where the as-built phase-0 state differs from the diagram's annotated target
stack, the diagram states the target and this document states the delta:

- **Stores.** Journal, vault, outbox, draft store and dispatch directory run on
  protocol interfaces with in-memory and JSONL backends. PostgreSQL 16 with
  encryption at rest is the documented production design
  ([ADR-018](adr/ADR-018-vault-store-protocol.md)); no PostgreSQL deployment
  exists yet, and the file vault backend is plaintext and says so.
- **Routing evidence.** The fallback classifier is zero-shot embedding
  similarity against the taxonomy config
  ([ADR-021](adr/ADR-021-zero-shot-classifier-log-only.md)); no pgvector index
  is involved in phase 0.
- **UI.** Server-rendered Jinja2 with vendored htmx and plain CSS (Tailwind
  was deliberately not adopted; [ADR-026](adr/ADR-026-review-ui-appends-only.md)).
- **Correction pool.** Exported as labelled training data
  (`python -m engine.journal.corrections`); nothing consumes it yet. The
  diagram's training-pool edges are pilot-phase scope.
- **LLM extraction.** The deterministic replay extractor exercises the whole
  verification machinery in the gate; a live OpenAI-compatible endpoint is an
  opt-in measured by `python -m eval.live` and never gated. No local model
  endpoint existed on the build workstation, so no live-model numbers are
  quoted anywhere in the record.

Python 3.12+ (built on CPython 3.13), FastAPI, pydantic v2, pytest +
Hypothesis, ruff, mypy (`--strict` on `engine.decide`, `engine.redact`,
`engine.textlayer`, `engine.extract`, `engine.draft`). Contracts in `schemas/`
are the single source of truth and change only via ADR; JSON Schema artifacts
are exported and committed, and the export is idempotent.

---

## 3. The pipeline as built

Each stage below names its key ADRs, its config files as they stand at the
final gate, and the measured numbers from the final state (Section 5 collects
them). Config versioning follows one rule
([ADR-009](adr/ADR-009-procedure-config-composition.md)): one editable home per
value, the loader composes contract shapes; and one standing lesson (part 06):
a config file whose version string is frozen into a gold-set manifest is never
bumped outside a gold supersession - new subsystems get independently
versioned files instead.

### 3.1 Ingest and normalization

`POST /ingest` -> envelope builder -> the rest of the pipeline. All three
channels (fit_connect form, e-mail letter, scanned letter) ride the same
submission JSON shape on purpose (`bodyText`, per-attachment `text` /
`sourceType`), so what a real IMAP/MIME or FIT-Connect event-log adapter must
produce is a fixed, tested envelope shape rather than adapter-specific parsing
(P-14; the real adapters are pilot scope). Every stage writes a
version-stamped journal event (`received`, `redacted`, `extracted`,
`evidence_assembled`, `tier_decided`, `routed`, ...). A submission the
redaction boundary refuses produces a sanitized 422 and no case at all - the
missing "rejected by destination" mapping on the FIT-Connect side is an
honestly flagged pilot item (P-14).

### 3.2 Identity vault, redaction boundary, canaries

ADRs: [002](adr/ADR-002-identity-vault.md),
[017](adr/ADR-017-seal-at-ingest-transient-witness.md),
[018](adr/ADR-018-vault-store-protocol.md).
Config: `config/redaction/identity_fields_v1.yaml` (one policy row answers
kind, subtree, witness participation and value visibility per path).

Identity-classed payload paths are sealed **before the envelope exists**, so
the envelope contract's "carries only redacted content" is computed, not
asserted. A request-scoped, never-serialized witness lets deterministic
validators keep computing on real values (VSNR checksum against the encoded
birth date, absolute date bounds, cross-field year distances) without
dereferencing the vault; `VaultStore.fetch` remained uncalled outside tests
until part 08's renderer. Two detector profiles with opposite error costs:
REDACT is recall-first (a failed checksum still identifies a person), VERIFY
is precision-first (checksum-gated, no bare dates or eight-digit numbers).
Residue is auto-sealed once, then the submission is refused. Problem strings
for sealed fields are value-free, and the `/ingest` 422 paths no longer echo
input.

Measured (P-7): redaction recall 1.000 by containment on all ten deterministic
kinds without the optional `[redact]` extra, and 1.000 on all kinds including
NAME with it (Presidio + spaCy `de_core_news_lg`), on the seeded German-PII
set `corpus/pii_golden/` (81 snippets, 79 labelled spans, 12 hard negatives).
Precision is reported per kind and never gated. The canary suite sweeps seeded
fake identities out of the envelope, the journal, every API response, every
rendered page, the logs, the exports and the eval report; the only two places
canaries MUST appear are the vault and the draft store (the two by-design
PII stores), asserted in both directions. DPIA input material:
[vault-dpia-input.md](vault-dpia-input.md) - its first residual risk is the
honest one: this is pseudonymization, not anonymization.

### 3.3 Text layer, extraction, double-lock span verification

ADRs: [019](adr/ADR-019-text-layer-over-redacted-text.md),
[020](adr/ADR-020-two-extractors-one-verifier.md).
Config: `config/extraction/extraction_v1.yaml`.

The text layer is built over the **redacted** text: spans live in redacted
coordinates and the raw never re-enters the model path. Normalization is
unicode NFC, whitespace handling and line de-hyphenation with an exact offset
map. Two extractors, one verifier that cannot tell them apart: the
deterministic replay extractor (from corpus sidecar fixtures) exercises the
whole machinery in the gate; the live client is configured explicitly, never
probes, and degrades every failure to a discard. The double lock (P-8): the
verbatim quote must stand at the offset it claims AND the value must be in the
quote, checked independently; a disagreement is a discard toward tier 3, never
a repair. Born-digital text matches exactly, OCR text with a bounded fuzzy
score (floor 0.86) that feeds extraction confidence. A placeholder in prose is
a correct extraction; the witness validates it, and nothing unseals for
matching.

Measured on gold v4: span verification 88/88 (57 exact, 31 fuzzy, 0
discarded); match modes over the whole set 386 structured / 57 exact / 31
fuzzy; six fuzzy scores below 1.0 (0.939-0.977) are the seeded OCR corruptions
that make the 0.86 bound a measurement rather than a setting. Failure kinds
are covered by unit tests and properties because the gold build refuses a
corpus with verification failures. Verification statistics ride in the
EXTRACTED journal payload and an eval section (P-12), reported and never gated
(the reasoning is [KNOWN-ERRORS.md](KNOWN-ERRORS.md) KE-3).

### 3.4 Evidence: completeness, derivation, routing, classifier

ADRs: [007](adr/ADR-007-declarative-predicate-ast.md),
[013](adr/ADR-013-content-based-procedure-derivation.md),
[014](adr/ADR-014-explicit-routing-arbitration.md),
[021](adr/ADR-021-zero-shot-classifier-log-only.md).
Config: `config/procedures/{altersrente,erwerbsminderungsrente,statusfeststellung}_v1.yaml`,
`config/rules/routing_v3.yaml`, `config/taxonomy/drv_bund_v2.yaml`,
`config/classifier/classifier_v1.yaml`.

- **Completeness** evaluates declarative requirement lists with a constraint
  vocabulary of pattern / one_of / length / real-calendar `date` bounds /
  `cross_field` checks (including birthdate-in-VSNR). Bounds are absolute
  dates, never "today minus n": completeness is a pure function of the item
  and never reads the wall clock. Every gap names its field, payload path and
  failed constraint, and renders to a caseworker-readable German Nachforderung
  sentence authored in the procedure config.
- **Procedure derivation** reads declarative content signals over payload and
  `text.*` context. Precedence refuses toward "unknown": ambiguous content and
  a hint contradicted by content both yield None, never a guess.
- **Routing arbitration** is an explicit integer priority forming a total
  order (shuffle-invariance is a Hypothesis property); an unresolved
  equal-priority conflict is recorded on the evidence record, drops confidence
  to 0.6 and lands the item at tier 3 - still routed, because a contested
  Vorgang with no addressee waits in nobody's queue.
- **The fallback classifier** is zero-shot cosine similarity against taxonomy
  name + responsibilities, consulted only when no rule fired, and log-only by
  a decision-plane rail: the plane admits only configured routing sources
  (default: rules alone). A raw cosine is not a confidence; the loader refuses
  enablement without a fitted calibration and its provenance, and the shipped
  config deliberately carries no calibration block because the only available
  fit population (rule-routed items) is not the population the classifier
  serves.

Measured on gold v4: derivation accuracy 1.000 in all three source buckets
(hint 62, content 23, none 16) and both item shapes; routing accuracy 1.000;
classifier (optional `[classify]` extra, e5-small): suggestions for 5/5
rule-less items, agreement 0.708, calibration machinery ECE 0.2212 raw ->
0.0104 fitted; every gated number identical with the model running.

### 3.5 Decision plane: table, valve, audit sampling

ADRs: [004](adr/ADR-004-one-way-valve.md),
[025](adr/ADR-025-sampled-reason-kind.md).
Config: `config/decision/table_v1.yaml`, `config/thresholds.yaml` (risk_v0,
scorer_mode log_only, downgrade budget 0.15, audit_sample_rate 0.0).

A pure functional interpreter over a versioned decision table. Tier 1
(prepared positive decision) requires a clear-cut-capable procedure, zero
discarded spans and routing confidence at or above 0.9; tier 2 requires an
incomplete verdict with the same routing confidence; everything else defaults
to tier 3 with a DEFAULTED reason. Downgrade rows accept only `anomaly.*`
fields with monotone operators and a fixed tier-3 target; `DecisionRecord`
refuses tiers better than `pre_downgrade_tier`. Deterministic audit sampling
(P-1, par. 88 Abs. 5 Nr. 1 AO analog) draws `blake2b(case_id, key=salt)`
below `audit_sample_rate`, applies only to tiers 1-2 as `max(tier, 3)`, is
recomputable by hand, and journals with `ReasonKind.SAMPLED` so a random draw
can never be read as a suspicion (the toeslagenaffaire lesson at the smallest
scale). The shipped rate is 0.0; the salt is a deployment secret.

### 3.6 The shadow scorer

ADR: [024](adr/ADR-024-shadow-scorer-two-readings.md).
Config: `config/scoring/scoring_v1.yaml` (threshold id `anomaly_gold_v4_v1`,
value 0.86, marked in-sample) plus the committed reference population
`config/scoring/reference_gold_v4.json` (101x8 matrix, rebuilt and checked by
`python -m eval.score_fit --check`; no pickled model binary).

Eight identity-blind features over the working copy, read twice: an
IsolationForest for unusual combinations and a tail share for a single value
far out; the score is the percentile of the larger reading in the reference
population. Two structural rules, both property-tested: no feature may
restate a decision-table qualifying field (the mirror image of the valve -
the first fit violated it and spent its whole anomaly budget re-discovering
tier-3 items), and a sealed value is refused rather than masked, degrading the
item to no-evidence with a journaled reason. That refusal puts two of gold
v4's nine labelled anomalies (implausible age, unissued Bereichsnummer)
permanently out of the scorer's reach and back with the rule plane, which can
see those values through the witness - recorded as a finding, not patched.

Measured on gold v4 at threshold 0.86 (log-only): 15 flags, recall 7/9
overall = 7/7 of the identity-blind-reachable set, false-flag rate on
tier-1-eligible items 0.077 against the 0.15 budget, 8 flags without tier
movement (already tier 3 - measured separately because their whole value is
the reason in the journal), 26 readable German feature-level reasons, 0
degradations, 7 items would move in enforcing mode, 0 moved. Enforcement is
structurally log-only: `scorer_mode` sits inside a config version frozen into
the gold manifest, so switching it costs a deliberate supersession. Bias
monitoring (P-2) reports flag rates per procedure, channel and shape, never
gated; procedure skew 3.04 is above the 3.0 advisory and explained by the
corpus construction. Feedback-loop guard (P-3) is one normative suite over
scorer AND classifier: no vault, witness, journal, prior-flag or history
input is even type-expressible.

The AI Act analysis of exactly this component - the deterministic engine is
not an AI system, the scorer is, Annex III 5(a) both ways, the Art. 6(3)(c)
derogation with the profiling exception engaged honestly - is
[ai-act-scoping-memo.md](ai-act-scoping-memo.md), marked DRAFT FOR LEGAL
REVIEW.

### 3.7 Notifications

ADRs: [005](adr/ADR-005-notifications-as-journal-projections.md),
[022](adr/ADR-022-notification-dispatch-ordering.md).
Config: `config/notifications/notifications_v1.yaml`.
Companion document: [notifications.md](notifications.md).

A pure, replay-safe fold over the journal: `received` owes the instant
receipt, `routed` owes the status update; the notification id is a pure
function of source event id and template id, so any number of worker runs
sends each message exactly once. Delivery-before-journal fixes the guarantee
at at-least-once deduplicated by a deterministic id. PII-free by construction:
the context builder reads only the case id, journal timestamps and
config-resolved display names - the submission is never read - and the
renderer refuses any output containing placeholder syntax. A loader tripwire
refuses Nachforderung trigger words in templates (a cheap check, stated as
such; the boundary holds by topology and by the contract-enforced
`informational_only=True`). The receipt carries the Art. 13/14 notice block
with clearly marked controller placeholders, and both texts close with the
sentence that no language model was used - which is literally true on every
citizen-facing path in this system (C-13: Art. 50 satisfied without any
carve-out).

Measured on gold v4: 197 notifications over 101 items, coverage 1.000 (five
items route nowhere and honestly get the receipt only); latency medians 1.1 /
1.3 ms are machine-local journal-delta measurements, reported and never gated.

### 3.8 Drafting and re-hydration

ADRs: [003](adr/ADR-003-drafting-after-decision.md),
[023](adr/ADR-023-rehydration-at-render-time.md).
Config: `config/drafting/drafting_v1.yaml`.

`engine/draft/` is the first and only production caller of
`VaultStore.fetch`. Tier 2 with gaps owes a Nachforderung that assembles the
gap sentences the procedure configs author (never re-worded), framed with the
par. 60 Abs. 1 S. 1 Nr. 1 SGB I anchor, a relative response window, the reply
channel and the C-7 softening; tier 1 owes a Bewilligungsentwurf with
unmissable ENTWURF framing; tier 3 owes nothing by design. The par. 66 Abs. 3
SGB I Rechtsfolgenhinweis is a per-case caseworker opt-in with no config
switch; requirements the DRV can determine itself (C-7, Amtsermittlung) are
softened citing par. 20 SGB X and excluded from every par. 66 scope. Absolute
deadline math (par. 37 Abs. 2 SGB X Bekanntgabefiktion + par. 26 Abs. 3
SGB X) ships tested and is called at dispatch time by part 10, with an
injectable Land holiday set that is empty by default rather than invented.
Re-hydration is per token, hard-errors on any unknown or malformed token with
no partial output, and round-trips against the raw as-received value; the
placeholder round-trip Hypothesis property is a permanent gate and found two
real defects before ship (a right-truncated token that evaded both regexes,
and a substring-based object check). Drafts are PII-bearing by design: the
draft store is the second and last member of the canary exception list.

Measured on gold v4: 60 drafts (47 Nachforderungen, 13 Bewilligungsentwuerfe),
41 tier-3 items with none, 0 blocked, 160 tokens re-hydrated (ADDR 47, GEBDAT
58, VSNR 55), 0 unresolved, 12 of 54 requested requirements softened, 0
dispatched (dispatch belongs to part 10's confirm).

### 3.9 Review UI, confirm and dispatch, correction capture

ADR: [026](adr/ADR-026-review-ui-appends-only.md).
Config: `config/queues/queues_v1.yaml`, `config/dispatch/dispatch_v1.yaml`.
Companion document: [accessibility-selfcheck.md](accessibility-selfcheck.md).

Queues, metrics and the correction pool are all folds over the journal; there
is no review store. `machine_tier` / `machine_unit_id` never move; the
effective tier and unit replay the human's OVERRIDDEN events, and the
difference is exactly what the correction pool exports (labelled training
data whose header says it is not a gold set) and what the Art. 22 override
rate counts. The routing answer is the ROUTED event - never the evidence
record's unadmitted suggestions; the classifier ranking renders in a panel
that says it decided nothing. Four display-only queue clocks (Widerspruch
Frist-laeuft with the Aktenzeichen as presence-never-value, the par. 14 SGB IX
two-week Reha period, the clearing-queue SLA labelled operational, per-tier
latency budgets) gate, hide and re-order nothing. Confirm stamps the dispatch
facts and the absolute Nachforderung deadline; the par. 66 opt-in re-renders
the letter as its own superseding draft. Escalation to tier 3 is one click
without a mandatory justification - friction in front of the safe direction is
the mistake the valve exists to prevent - while re-route and tier change
require a written reason. Every action is idempotent by refusal, never by
overwrite. The xdomea-shaped dispatch stub carries identifiers and shapes,
no letter text and no person. Rubber-stamp metrics (P-6) aggregate per unit
with a five-confirmation floor; time-to-confirm is defined honestly as queue
dwell. The unit picker is a documented demo, not authentication: it gates
exactly the re-hydration surface, and a real IdP plus Berechtigungskonzept is
a named pilot prerequisite. Accessibility is a self-check with 22 automated
checks and the external BITV audit named as pilot scope (P-15).

Measured on gold v4: the eval carries a queue census - 101 open items over 7
queues (5 clearing, 2 Widerspruch, 3 under the par. 14 clock, 0 sampled) -
and deliberately not P-6 rates, because a gold run contains no human
confirmations and inventing them would put fictional behaviour into the
gating artifact.

---

## 4. Frozen sets: the registry story

[ADR-010](adr/ADR-010-gold-set-freeze-and-versioning.md) (frozen, versioned,
never trained on), [ADR-011](adr/ADR-011-declared-divergences.md) (divergences
declared, enforced, still counted),
[ADR-015](adr/ADR-015-frozen-sets-are-verified-by-integrity.md) (supersession
lives in `corpus/gold/REGISTRY.yaml`, outside every frozen directory; a
current set is verified by byte-identical rebuild including the live
self-check, a superseded set by SHA-256 integrity against its own manifest).

| Set | Items | Frozen | Superseded because |
|---|---|---|---|
| s1 | 2 | part 01 | pre-gold scaffolding, no manifest, nothing to verify |
| v1 | 41 | part 02 | Versicherungsnummern were structurally wrong (birth date at the wrong positions); no structural check could ever have passed |
| v2 | 57 | part 03 | part 03b added the Statusfeststellung procedure and closed xx-0005's Widerspruch routing divergence |
| v3 | 77 | part 03b | part 05 added the text path; v4 contains the 77 form items byte-identical plus 24 letters |
| v4 | 101 | part 05 | **current** (77 forms + 16 e-mail letters + 8 OCR scans; zero declared divergences since v3) |

Labels are true by construction (rendered from the same facts object as the
payload, never re-parsed) and the build is a pipeline of refusals: an
undeclared mismatch with the real pipeline, a declared divergence that failed
to occur, or any label that would clear an oversight item to tier 1 aborts
with nothing written. Two further frozen artifacts sit deliberately outside
the gold registry: `corpus/pii_golden/` (measures the redactor, not the
triage) and `config/scoring/reference_gold_v4.json` (the scorer's reference
population, an inspectable matrix rather than a model binary).

**Parked for the next gold supersession** (each is test-pinned or recorded so
it cannot be forgotten):

- `risk_v1` consolidation: `review_due` inline in the risk config instead of
  the independently versioned `config/review/` file (part 06's structural
  workaround for the frozen-manifest constraint).
- `table_v1`'s downgrade literal `anomaly.score >= 0.85` predates the score
  scale and is one notch looser than the calibrated 0.86 flag threshold; align
  them or drop the score row for `anomaly.flagged` (two already-tier-2 items
  sit between the values; a test names them).
- The classifier calibration-population gap: gold v4 contains no item that
  lacks a routing rule AND carries a gold unit, which is exactly the fit
  population the classifier's calibration needs.
- `honorar_monatlich` occurs on exactly two items, both labelled anomalies, so
  an honest scorer feature over it is impossible until ordinary items carry
  Honorarangaben.
- No item carries `antragsteller.name`, so no draft has ever printed a letter
  head name; and the tier-1 population is Altersrente only, so the
  prepared-decision template has never rendered for another procedure.
- A second labelled set is the only thing that can turn the scorer's
  in-sample threshold into an out-of-sample estimate.
- `ar-0042` (implausible age) and `ar-0044` (unissued Bereichsnummer) belong
  to the rule plane now (via witness-computed cross-field checks), pending a
  procedure-config supersession and, for the Bereichsnummer, an agency-supplied
  issued list this repository cannot cite.

---

## 5. The final measured state (part 10 gate, 2026-08-12, main at 070ccd1)

All numbers from the verification gate recorded in
[ENGINEERING_LOG.md](ENGINEERING_LOG.md) and [../tasks/BOARD.md](../tasks/BOARD.md).

| Measurement | Value |
|---|---|
| Tests | 1257 passed |
| Coverage (combined, floor 95 percent) | 98.86 percent; `engine/decide` and `engine/score` 100 percent |
| Lint / format / types | ruff clean (176 files), mypy clean (169 files) |
| Eval corpus | gold v4, 101 items (77 forms, 16 e-mail, 8 OCR) |
| Routing accuracy | 1.000 |
| Tier accuracy | 1.000 |
| False-clear rate | 0.000 (gate) |
| False-flag rate | 0.000 |
| Gap exact match / completeness P/R/F1 | 1.000 / 1.000 (a property of the label-by-construction corpus, stated as such since part 02) |
| Procedure derivation | 1.000 in all source buckets and both shapes |
| Span verification | 88/88 (57 exact, 31 fuzzy, 0 discarded) |
| Redaction recall (deterministic kinds, gate) | 1.000 |
| Notifications | 197 over 101 items, coverage 1.000 |
| Drafts | 60 (47 Nachforderungen, 13 Entwuerfe), 160 tokens re-hydrated, 0 unresolved |
| Shadow scorer (log-only, threshold 0.86, in-sample) | recall 7/9 = 7/7 reachable; false-flag rate 0.077 (budget 0.15); 0 tiers moved |
| Review queues | 101 open over 7 queues (5 clearing, 2 Widerspruch, 3 par. 14 SGB IX, 0 sampled) |
| Frozen-set checks | v4 byte-identical rebuild; v1/v2/v3 integrity; pii_golden and reference population green |
| Schema export | idempotent; no `schemas/` or `corpus/gold/` diff across parts 04-10 |

---

## 6. The non-negotiable gates, and where each is enforced

| Gate | Enforcement point |
|---|---|
| False-clear rate 0 percent on the frozen gold set | `python -m eval.run` exit code (wired since part 01; held on every set including the superseded ones) |
| One-way valve monotonicity | Hypothesis properties against the real interpreter and the real `table_v1` (part 01), re-proved item by item on the 101 real evidence records in both scorer modes (`tests/test_score_monotonicity.py`, part 09); plus schema-level enforcement (`anomaly.*` rejected in qualifying conditions) |
| Vault canaries | canary suite over envelope, journal, API responses, rendered pages, logs, exports and eval report; the two-member exception list (vault, draft store) asserted in both directions |
| Placeholder round-trip | Hypothesis property over arbitrary sealed payloads (`tests/test_draft_rehydrate.py`, permanent since part 08); the notification renderer refuses placeholder-bearing output independently |
| Readable reasons on every anomaly flag | fourth wired eval gate (part 09): eval fails on a bare flag |
| Deterministic redaction recall 1.000 | second wired eval gate (part 04), measured on `corpus/pii_golden/` |
| Structured-subset identity | third wired eval gate (part 05, exit-code wiring fixed in part 06): the 77 frozen form items must score exactly their historical values |
| Frozen-set discipline | `python -m corpus.generator.build --check` per registry mode (byte-identical rebuild for current, SHA-256 integrity for superseded); `.gitattributes` LF pinning; `eval.score_fit --check` for the reference population |

The regression identity held across every part: sealing (04), the text path
(05), the classifier (06), notifications (07), drafting (08), the scorer (09)
and the review UI (10) each landed with every previously gated number exactly
unchanged.

---

## 7. The compliance record

Full row-by-row detail with reasoning lives in
[compliance-backlog.md](compliance-backlog.md); the research reports behind it
are in [research/](research/). Summary of what shipped and what remains:

### Closed by the build (with their parts)

| Row | What shipped |
|---|---|
| C-1 | AI Act scoping memo ([ai-act-scoping-memo.md](ai-act-scoping-memo.md)), DRAFT FOR LEGAL REVIEW (09) |
| C-3 | Journal retention design + oversight assigned to roles in the running system; queue census, P-6/P-10 monitoring numbers (01/08/10) |
| C-4 | Aggregate-only override metrics, structural (unit-scoped `Actor`) and property-tested (10) |
| C-5 | DPIA input material (04), Art. 13/14 notice block with marked placeholders (07), measured Art. 22 override rate + documented demo Berechtigungskonzept (10) |
| C-6 | Hardened Nachforderung: par. 60 SGB I anchor, relative window, Bekanntgabefiktion math, par. 66 Abs. 3 as caseworker opt-in (08), deadline stamped at dispatch (10) |
| C-7 | Amtsermittlung guard: softened wording citing par. 20 SGB X, excluded from par. 66 scopes (08) |
| C-8 | Channel/formality mapping executable in config on both paths (07/08); the physical print and qualified-electronic dispatch remain pilot scope |
| C-9 | Widerspruch routing to the Widerspruchsstelle (03b) + Frist-laeuft queue flag with no admissibility text, test-enforced (10) |
| C-10 | par. 14 SGB IX clock + clearing SLA, display-only (10) |
| C-11 | par. 31a SGB X errata in ADR-003 and the procedure config headers (03b) |
| C-13 | Zero model-generated text reaches a citizen on any path; Art. 50 satisfied without a carve-out, test-asserted (07/08) |
| P-1..P-8, P-10, P-15 (self-check), P-16 (harness) | See Sections 3.2-3.9; every row's detail is in the backlog |
| P-12 | [KNOWN-ERRORS.md](KNOWN-ERRORS.md) seeded and journaled failure statistics (05); the per-release publication practice stays open |

### Open: pre-pilot deliverables (not engineering artifacts of this phase)

- **Dienstvereinbarung** with the Personalrat and the **Art. 26(7)** worker
  information in the same step (C-4).
- **FRIA** (Art. 27 AI Act) before any pilot (C-2), and **P-13**: DSFA plus
  legal-basis memo as named pre-pilot deliverables.
- **External accredited BITV 2.0 audit** on a deployed instance, an
  assistive-technology user test, and the par. 12b BGG statement (P-15).
- **Real identity provider and Berechtigungskonzept** replacing the demo unit
  picker (C-5; seam: `api/review.resolve_unit`).
- **PostgreSQL backends with encryption at rest**, the vault `purge()`
  operation for Art. 17 erasure and retention (ADR-018,
  [vault-dpia-input.md](vault-dpia-input.md) section 6), deploy/compose
  packaging, and the `[classify]`-image decision (torch triples the image).
- **Print and qualified-electronic dispatch paths** - the first message that
  actually leaves the building (C-8), and the DISPATCHED event shape when
  dispatch becomes asynchronous (recorded in ADR-026).
- **FIT-Connect adapter reject side**: mapping a boundary refusal onto
  "rejected by destination" (P-14), plus OCR confidence at the scan source
  (the only real fix for KE-1).
- **Controller placeholders**: legal basis, Art. 30 entry, retention periods,
  key management, Art. 28 - section 9 of the DPIA input, four of which are
  exactly the visible placeholders in the receipt's notice block.
- **P-11**: ATRS-style transparency record per config version at release.
- **C-12**: the Altersrente flip package (the only doctrinally reachable
  full-automation case), frozen until every named prerequisite exists.
- Also open: P-9 (gap-citation property test), P-17 (pitch framing), and
  ADR-006 (license choice, a user decision).

---

## 8. Known limitations, stated honestly

These caveats are load-bearing; quoting the Section 5 numbers without them
misrepresents the system.

1. **The scorer threshold is in-sample.** 0.86 was chosen while looking at
   gold v4's nine labels; recall 7/7-reachable and false-flag 0.077 are not
   out-of-sample estimates of anything. The corpus also over-represents
   anomalies by construction (9 of 101), so the false-flag rate is an upper
   bound on a curated set, not an intake estimate. Every artifact that carries
   the number says so.
2. **Sealing is pseudonymization, not anonymization.** The vault holds the
   mapping; the DPIA input opens with this
   ([vault-dpia-input.md](vault-dpia-input.md) section 2), and the AI Act
   memo's profiling analysis depends on it.
3. **Completeness precision/recall 1.000 is a property of the corpus**, not a
   claim about the checker: gap labels are declared per scenario and enforced
   by the build's self-check. On record since part 02 so nobody quotes it as a
   quality claim.
4. **The role model is a demo.** A query parameter validated against the
   taxonomy, no authentication, every page says so. It gates exactly the
   re-hydration surface and nothing else.
5. **Stores are JSONL/in-memory dev backends.** Journal, vault (plaintext,
   documented), outbox, drafts, dispatch directory. The production design is
   documented, not deployed.
6. **Latency numbers are machine-local wall clock**, reported and never gated
   (notification medians, queue ages). The only cross-run eval diff at the
   final gate was this line.
7. **The case view does not render the working-copy text.** The journal
   deliberately carries no case content and no store retains the redacted
   envelope; showing the text requires a decision about where a redacted
   working copy lives, under whose retention period and erasure path - an open
   deployment decision recorded in ADR-026, not an oversight.
8. **Known errors** ([KNOWN-ERRORS.md](KNOWN-ERRORS.md)): KE-1 OCR-mangled
   identity evades the detector union (fix is OCR confidence at the source);
   KE-2 a field with no requirement is never asked of a live model, so
   live-only Altersrente items cannot reach tier 1 (Fachbereich decision);
   KE-3 span-verification failure rates are reported, not gated (deliberate,
   ADR-020); KE-4 `extraction.min_confidence` separates prose-read from
   key-read, not scan from e-mail - the knob is wired and unadopted, the
   number is the Fachbereich's; KE-5 a 7B model cannot produce a character
   offset the double lock accepts, which is what makes live extraction
   unusable on this hardware (part 12, measured).
9. **Live LLM numbers now exist, and they are bad** (part 12, 2026-08-13,
   ADR-028). Two open-weights 7B instruct models at Q4 on a local RTX 5070,
   measured on the 24 free-text letters of gold v4: `mistral:7b-instruct-v0.3`
   verified 0 of 86 proposed spans, `qwen2.5:7b-instruct` 3 of 85, against the
   replay extractor's 88 of 88; field recall 0.000 and 0.020 against 1.000; 4.1
   and 4.4 seconds per letter against 0.012. Both answer in German with
   schema-valid JSON and frequently the right values, and both fail the
   offset half of the double lock. A blind control produced the same tier as
   the live run on 24 of 24 letters, so on this corpus a 7B model is
   indistinguishable from no extractor at all. **The extraction gate still runs
   on the deterministic replay extractor and always did** - live numbers are
   measured and never gated (ADR-020), the switch defaults to replay
   everywhere, and the hosted demo has no model endpoint.
10. **The corpus is synthetic.** Labels are true by construction, the
    taxonomy's unit numbers and cuts are honest placeholders shaped like a
    published Organisationsplan, and no real applicant data has ever entered
    the system.

---

## 9. Document map

| Document | What it holds |
|---|---|
| [ENGINEERING_LOG.md](ENGINEERING_LOG.md) | The full per-part record: every landed feature, defect found, gate run and number, chronologically |
| [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) + [adr/](adr/) | ADR-001..026 (006 license pending, a user decision) |
| [compliance-backlog.md](compliance-backlog.md) | C-1..C-13 and P-1..P-17 with full per-row status reasoning |
| [research/](research/) | Legal implementability map, Statusfeststellung blueprint, prior-art review (2026-08-11) |
| [technical-design.md](technical-design.md) | Stack-annotated component diagram (target architecture; Section 2 above lists the as-built deltas) |
| [vault-dpia-input.md](vault-dpia-input.md) | DPIA input: sealed fields, data flow, storage, TOMs, residual risks, controller items |
| [ai-act-scoping-memo.md](ai-act-scoping-memo.md) | AI Act analysis, DRAFT FOR LEGAL REVIEW |
| [notifications.md](notifications.md) | Notification channels, formality mapping, Art. 50, acknowledgement semantics |
| [accessibility-selfcheck.md](accessibility-selfcheck.md) | WCAG 2.1 AA / EN 301 549 self-check with per-criterion verdicts |
| [KNOWN-ERRORS.md](KNOWN-ERRORS.md) | KE-1..KE-4: real, reproducible, unfixed limits |
| [BUILD.md](BUILD.md) | Build, run, corpus, eval, extras, every CLI |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Toolchain, style, mypy strictness map, commit conventions |
| [../tasks/BOARD.md](../tasks/BOARD.md) | Per-part gate results |
| `../eingangslotse/schemas/` | The contracts (single source of truth; changes only via ADR) |
| `../eingangslotse/corpus/gold/REGISTRY.yaml` | Which gold sets exist and how each is verified |

---

*Consolidated 2026-08-12 at the final documentation milestone (execution
protocol Section 4: consolidated technical documentation at the end of each
work package; this is the post-part-10 final consolidation). Markdown is the
primary format; a LaTeX export is an optional artifact pending a user
decision.*
