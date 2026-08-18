# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Two records belong next to every entry and are not repeated in it: the
configuration versions, thresholds and measured numbers a release shipped with
are in [`docs/transparency-record.md`](docs/transparency-record.md), and the
failure modes that were known when it shipped are in
[`docs/known-errors/`](docs/known-errors/) as a snapshot per version.

## [Unreleased]

Nothing yet.

## [0.1.0] - 2026-08-18

**The first release.** A complete S1-S10 intake pipeline for German
public-administration mass procedures: it reads an inbound item, seals the
identity data out of it, proves every extracted value against the document it
came from, and lets a versioned decision table decide whether a human has to
look at it. It never decides the application itself.

This is a prototype with a full pipeline, publicly demonstrable over synthetic
data. It is not a pilot and has never seen real data; the reasons are under
"Known limitations" below and, at length, in the transparency record.

### Added

**The decision architecture.**

- Two planes, and only one of them decides: a probabilistic evidence plane and a
  deterministic, versioned decision table that reads only what is proven
  (ADR-001). Same evidence plus same configuration equals the same decision.
- A one-way valve: qualifying conditions may not reference anomaly fields, and
  downgrades may only target tier 3 with monotone operators, so uncertainty can
  add oversight and never remove it. Proved on every commit as a property test
  against the real table and the 101 evidence records of the frozen set
  (ADR-004).
- A declarative predicate AST for the table's conditions, so a rule is data
  rather than code (ADR-007).
- Three configured procedures: Altersrente, Erwerbsminderungsrente, and the
  Feststellung des Erwerbsstatus nach par. 7a SGB IV. The last carries
  `tier1_enabled: false` because its decision is a statutory Gesamtwuerdigung; a
  formally complete application still ends at tier 3, by design (ADR-035).

**Privacy at the boundary.**

- Identity sealed at ingest, before an envelope or a journal event exists, into
  a vault behind a store protocol; the working copy carries randomized
  placeholders from a reserved alphabet (ADR-002, ADR-017, ADR-018).
- A post-redaction sweep that COMPUTES whether the working copy is clean instead
  of asserting it. Residue surviving one auto-seal round refuses the submission
  before a single journal event exists (ADR-019).
- Re-hydration exactly once, at render time, in the one module that reads the
  vault (ADR-023). It is one of the packages held to `mypy --strict`.

**Evidence, and the locks on it.**

- A text layer with offset maps over the redacted text, and normalization that
  the offsets survive (ADR-019).
- Two extractors and one verifier: every value carries a verbatim quote and the
  character offset it stands at, and both are re-checked before the value may
  become an evidence record. A failed lock discards the value, which pushes the
  item toward tier 3 (ADR-020).
- Completeness against the procedure's own requirements, with gaps citing
  requirement ids and span evidence rather than inference.
- Rule-based routing with explicit arbitration: two equal-priority rules that
  disagree lower the winner's confidence to 0.6, below the 0.9 both qualifying
  rows require (ADR-014).
- Procedure derivation from a channel hint, from content, or from neither
  (ADR-013).

**Oversight that can only add.**

- A shadow anomaly scorer (IsolationForest, feature set `fsv_v1`, seed 42) in
  `log_only` mode, identity-blind by construction because sealing runs first,
  with at most four feature-level reasons per flag and a fallback that keeps a
  flag from ever shipping without one (ADR-024).
- A zero-shot unit classifier, configured, log-only and excluded from the
  decision plane. It is disabled in this release because its minimum confidence
  is not calibrated and the loader refuses to enable it while that is true
  (ADR-021).
- Audit sampling that anybody with the configuration can recompute, with a
  sampled case carrying its own reason kind so it is never mistaken for a case
  something was found in (ADR-025, backlog P-1).
- Bias monitoring across procedure, channel and item shape: reported in the
  eval report and never gated, because an alarm that failed a build would teach
  people to tune the alarm (backlog P-2).

**The journal, and the surfaces over it.**

- An append-only, version-stamped journal behind a store protocol; queues,
  metrics and the case view are folds over it (ADR-008).
- A review UI that appends only: confirm, re-route and escalate are events, a
  correction appends, and a second confirmation of the same case is refused
  rather than overwritten (ADR-026).
- Notifications as journal projections from versioned templates, with a defined
  dispatch ordering. No model-written sentence reaches a citizen (ADR-005,
  ADR-012, ADR-022).
- Prepared decisions and Nachforderungen drafted after the decision, never
  before it (ADR-003).

**Contracts, configuration and corpus.**

- `schemas/` as the single source of truth, exported to JSON Schema artifacts
  under `schemas/artifacts/v0.1.0/`.
- Agency-editable configuration with a version string in every file. Five of
  those versions are frozen into `corpus/gold/v4/MANIFEST.yaml` and verified by
  a byte-identical rebuild, so a frozen version cannot change without a
  supersession (ADR-009, ADR-010, ADR-015).
- `corpus/gold/v4`: 101 synthetic items - 77 structured forms and 24 free-text
  letters, 8 of them OCR - generated deterministically from scenario specs, with
  declared divergences (ADR-011).
- `corpus/pii_golden`: a seeded German-PII set, 81 items, behind the redaction
  recall measurement.

**Evaluation.**

- `python -m eval.run`, with four gates that exit non-zero if they move:
  false-clear rate 0.000, deterministic redaction recall 1.000, the
  structured-subset invariant, and a readable reason behind every anomaly flag.
- A threshold review register with a dated next review and, in the report, a
  measured operating point and a sweep per threshold - including which of them
  are not calibrated.
- `python -m eval.live` as a laboratory instrument for live extraction, and the
  measurement that keeps it out of every gated path (ADR-028).

**API and interface.**

- FastAPI: `POST /ingest`, `/review` with confirm, escalate and override,
  `/metrics`, `/inbox`, `/drafts/{case_id}`, `/cases/{case_id}`, `/healthz`, and
  the OpenAPI page at `/docs`.
- Server-rendered templates and one plain-CSS design system: no build step and
  **no external fetch of any kind** - no CDN, no web font, no remote icon
  (ADR-030). Every colour pair was computed against the WCAG formula before it
  shipped, and the ratios are in `docs/accessibility-selfcheck.md`.
- Bilingual German and English, switched server-side by a cookie with no
  JavaScript. Visitor pages are translated in full; the caseworker screens stay
  German in both settings and carry one English line saying why (ADR-031).
- Three grounds - visitor, machine and caseworker - in one design system, the
  machine pages re-derived from the One Dark editor palette so that machine text
  looks like what it is (ADR-032, ADR-034).

**The showcase.**

- `/demo/rundgang`: a six-step guided tour that tells the whole system for
  somebody who has never seen it, walkable on an instance that accepts no
  submissions at all.
- `/demo/antrag`: a three-phase demonstration journey - pick one of four
  unmistakably fictional applicants, break their application on purpose, watch
  the seven pipeline stages narrate what happened to YOUR submission, then
  follow the case into the caseworker queue and see the loop close in the
  applicant's inbox. The intake page hands its submission to the same ingest
  machinery the API route calls (ADR-029).
- `/demo/gegenpartei`: the two-party loop. The Auftraggeber is heard and answers
  through the same door, and the statement travels the one real ingest path as
  its own sealed, redacted, span-verified, routed and journaled case. The
  correlation is a drawn 96-bit token in a RAM compartment with a TTL, never
  derived from case data and never in a journal payload (ADR-036).
- Simulated attachments that really travel with the submission, and one way in
  for every intake path (ADR-033).
- A demonstration posture that resets by restart and refuses `POST /ingest`
  outright when no token is configured (ADR-027).

**Deployment and CI.**

- A Dockerfile and `docker-compose.yml` producing one slim image with core
  dependencies only. The build runs the evaluation, so an image that cannot pass
  its own gate is never produced; the container seeds itself from the frozen
  corpus on boot.
- `render.yaml`, a blueprint for the free-plan demonstration instance in
  Frankfurt with a health check.
- Two CI pipelines running the same gate: `.github/workflows/gate.yml` (which
  also builds the image and smoke-tests the container) and `.gitlab-ci.yml` for
  openCode.

**Documentation.**

- `README.md`, `docs/technical-spec.md` as the consolidated specification,
  `docs/BUILD.md` as the build-and-run specification the CI implements, 36 ADRs
  with a one-line index, the notification catalogue, the AI Act scoping memo,
  the vault DPIA input, the accessibility self-check and `docs/PUBLISHING.md`.
- New with this release: `publiccode.yml` (the openCode metadata standard),
  `docs/transparency-record.md` (the ATRS-style record of what this release
  actually ships, backlog P-11), `docs/known-errors/v0.1.0.md` (the per-release
  snapshot that closes the open half of backlog P-12) and this changelog.

### Security

- `POST /ingest` is refused for everybody when no ingest token is configured,
  and the hosted demonstration deliberately ships without one. Its absence is
  the setting.
- Identity-classed values never reach the working copy, the journal or a log
  line. Canary tests assert it rather than a comment claiming it.
- The demonstration's working-copy store is in RAM with a TTL, and every restart
  rebuilds the whole state from the frozen corpus.
- CI asserts that the optional model extras are NOT importable, so no gate can
  come to depend on which wheels a machine has.
- The audit-sampling salt in `config/scoring/scoring_v1.yaml` is a demo value
  and says so in its own `note`. Rotate it per deployment.

### Known limitations

Eight, with what each one costs and what would actually fix it, in
[`docs/known-errors/v0.1.0.md`](docs/known-errors/v0.1.0.md). The ones that
decide whether this may run on real data:

- **No authentication.** The unit picker in the review UI is a query parameter
  validated against the taxonomy, and every page says so.
- **The vault's development backend is plaintext JSONL**, and there is no
  database - the stores are in-memory or JSONL behind a protocol.
- **An OCR-mangled identifier can evade the detector union entirely** (KE-1).
  Measured, demonstrated by a canary test, and not fixable with a threshold.
- **The corpus is synthetic**, so every number is a measurement over generated
  data.
- **The accessibility document is a self-assessment**, not a BITV 2.0 audit, and
  no assistive technology has been run against these pages.

[Unreleased]: https://gitlab.opencode.de/Olajide/eingangslotse/-/compare/v0.1.0...main
[0.1.0]: https://gitlab.opencode.de/Olajide/eingangslotse/-/tags/v0.1.0
