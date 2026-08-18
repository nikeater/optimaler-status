# EingangsLotse

**An intake assistant for German public-administration mass procedures that
prepares decisions and never takes one.** It reads an inbound item, seals the
identity data out of it before anything else touches it, proves every extracted
value against the document it came from, and then lets a deterministic,
versioned decision table decide whether a human has to look at it. Anything the
machine is unsure about moves toward a caseworker; nothing moves away from one.

Built end to end - ingest and sealing, text layer, extraction, evidence, the
decision plane, notifications, drafting, review - and measured on a frozen
synthetic corpus of 101 items: **1613 tests, 99.01% coverage over the gated
packages, four eval gates green, zero false clears.**

> **Live demo: <https://eingangslotse-demo.onrender.com>** - a public instance
> over synthetic data only, which accepts no submissions from outside. Start at
> **[`/demo/rundgang`](https://eingangslotse-demo.onrender.com/demo/rundgang)**,
> the guided tour: the whole system from the first submission to the closed loop
> in six steps, each one linking to the page where it actually happens. The
> state resets on every restart. On a free plan the first load after an idle
> period can take about a minute.

Licensed under the [EUPL-1.2](#license). Contracts in `schemas/`,
agency-editable policy in `config/`, decisions in
[`docs/adr/`](docs/adr/), the whole system in
[`docs/technical-spec.md`](docs/technical-spec.md).

**This release is v0.1.0**, and three documents say what that means rather than
leaving it to be inferred: [`CHANGELOG.md`](CHANGELOG.md) is what it contains,
[`docs/transparency-record.md`](docs/transparency-record.md) is what it actually
ships - every configuration version, every threshold, and where each number came
from - and [`docs/known-errors/v0.1.0.md`](docs/known-errors/v0.1.0.md) is what
was known to be broken on the day it shipped. [`publiccode.yml`](publiccode.yml)
at the root is the openCode software-directory metadata.

---

## Why it is built this way

Administrative triage software fails in one specific direction: it clears
something to "no human needed" that needed a human. Every structural choice
here is aimed at that failure mode.

**Two planes, and only one of them decides.** The evidence plane is allowed to
be probabilistic - extraction, a zero-shot unit classifier, an anomaly scorer.
The decision plane is a versioned decision table that reads only what is
proven. Same evidence plus same config equals the same decision, every time
([ADR-001](docs/adr/ADR-001-two-plane-architecture.md)).

**The valve only opens one way.** Uncertainty can add oversight and can never
remove it. That is not a convention: it is proved on every commit against the
real decision table and the 101 real evidence records of the frozen set
([ADR-004](docs/adr/ADR-004-one-way-valve.md)).

**Identity is sealed at ingest, before the envelope exists.** Identity-classed
fields go into a vault and the working copy carries randomized placeholders
from a reserved alphabet. A post-redaction sweep *computes* whether the item is
clean instead of asserting it, and residue that survives one auto-seal round
refuses the submission before a single journal event exists. Re-hydration
happens once, at render time, in the one module that reads the vault
([ADR-017](docs/adr/ADR-017-seal-at-ingest-transient-witness.md),
[ADR-023](docs/adr/ADR-023-rehydration-at-render-time.md)).

**No model-written sentence reaches a citizen.** Notifications render from
templates in versioned config. Prepared letters wait for a human. A language
model may read; it may never write to an applicant
([ADR-012](docs/adr/ADR-012-optional-llm-paraphrase.md)).

**The journal is the only truth.** Every event is append-only and
version-stamped; queues, metrics and the case view are folds over it. A
correction appends, and a second confirmation is refused rather than
overwritten ([ADR-026](docs/adr/ADR-026-review-ui-appends-only.md)).

## The measured numbers

From `python -m eval.run` over the frozen gold set `corpus/gold/v4`
(101 items: 77 structured forms, 24 free-text letters). Four of these are
GATES - the command exits non-zero if any of them moves.

| Metric | Value | Note |
| --- | --- | --- |
| False-clear rate | **0.000** | **gate**, budget zero, permanently |
| Deterministic redaction recall | **1.000** | **gate**, measured per kind against a seeded German-PII set |
| Structured subset | **HELD** | **gate**: the text path moved no form item |
| Anomaly reasons | **all present** | **gate**: no flag without a readable feature-level reason |
| Routing accuracy | 1.000 | |
| Tier accuracy | 1.000 | |
| Completeness precision / recall | 1.000 / 1.000 | |
| Procedure derivation | 1.000 | over hint, content and prose |
| Span verification | 88 / 88 | double-checked: the quote stands at the offset, and the value is in the quote |
| Notifications | 197 to 101 / 101 items | |
| Prepared drafts | 60, 0 unresolved tokens | 160 tokens re-hydrated |
| Anomaly scorer | 15 / 101 flagged at 0.86 | log-only; nothing it produces can lower a tier |
| Review queues | 101 open over 7 queues | 5 unrouted to central clearing |
| Tests | 1613 passing | 99.01% coverage over the gated packages |

Every number in that table, plus the configuration versions and thresholds it
was measured under, is traced to its source in
[`docs/transparency-record.md`](docs/transparency-record.md).

The redaction recall is the DETERMINISTIC number: it is 1.000 without the
optional NER model, which is why no gate in this project depends on which
wheels a machine has. The same rule holds for the classifier's embedding model
and for the live LLM extractor - neither is ever loaded by a gate.

**Both platforms run that gate on every push.**
[`.gitlab-ci.yml`](.gitlab-ci.yml) runs it on openCode and
[`.github/workflows/gate.yml`](.github/workflows/gate.yml) on GitHub, from the
same command list in [`docs/BUILD.md`](docs/BUILD.md): tests with the coverage
floor, ruff, mypy, a byte-identical rebuild of all four gold corpora and the PII
golden set, the four eval gates, a re-export of the JSON Schema artifacts, and a
clean-tree assertion. The GitHub run also builds the container image and smoke
tests it.

## Quickstart

### From a virtual environment

```bash
python -m venv .venv
. .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

python -m pytest              # 1613 tests
python -m eval.run            # the four gates, over frozen gold v4
python -m uvicorn api.app:app --reload
```

Then `http://127.0.0.1:8000/review` for the caseworker surface, `/metrics` for
the numbers, `/inbox` for what an applicant would have received, and `/docs` for
the OpenAPI page.

**The visitor side of the site is behind a switch, and `/` is part of it.**
Demo mode is a deployment posture, default OFF: with the flag unset, `GET /` is
not in the route table at all, and `/hinweise` and every `/demo` page answer 404
([ADR-027](docs/adr/ADR-027-demo-posture-and-reset-by-restart.md)). Set the flag
and the landing page, the synthetic-data banner and the ingest gate arm
together:

```bash
export EINGANGSLOTSE_DEMO_MODE=1      # PowerShell: $env:EINGANGSLOTSE_DEMO_MODE = "1"
python -m uvicorn api.app:app --reload
```

The guided tour at `/demo/rundgang` is walkable with nothing else set. Only
SUBMITTING needs `EINGANGSLOTSE_INGEST_TOKEN` set to a non-empty string as well;
the hosted demo deliberately runs without one, which is precisely what makes
`POST /ingest` refuse everybody there.

### With Docker

```bash
docker compose up --build     # http://localhost:8000/
docker compose down -v        # and the state with it
```

One slim image, core dependencies only. The build runs the eval, so an image
that cannot pass its own gate is never produced; the container seeds itself
from the frozen corpus on boot, which makes a restart a complete reset. Full
instructions, including the optional `[redact]` and `[classify]` extras and
what they change: [`docs/BUILD.md`](docs/BUILD.md).

## What you are looking at

```text
schemas/    the contracts. Single source of truth, exported as JSON Schema
engine/     ingest and sealing, text layer, extraction, evidence, the
            decision plane, notifications, drafting, the shadow scorer,
            the review actions, the demo posture
api/        FastAPI: /ingest, /review, /metrics, /inbox, /cases/{id},
            /drafts/{id}, and the demo-only /demo journey
config/     what an agency edits: taxonomy, routing rules, requirements,
            decision table, thresholds, templates. Versioned, and a frozen
            version cannot change without a supersession
corpus/     the synthetic gold sets and the generator that builds them
eval/       the harness that produces the numbers above
ui/         server-rendered templates and one plain-CSS design system.
            No build step, and nothing fetched from anywhere
```

The interface is plain CSS with custom properties for the palette, the type
scale and the spacing ladder, applied to every page from one stylesheet
([ADR-030](docs/adr/ADR-030-design-system-and-the-tour.md)). There is no build
chain and **no external fetch of any kind** - no CDN, no web font, no remote
icon: a data-protection demonstration must not phone anywhere, so the type
stack is the operating system's own and the icon set is inline SVG. Every
colour pair that ships was computed against the WCAG contrast formula before it
was used, and the ratios are in
[`docs/accessibility-selfcheck.md`](docs/accessibility-selfcheck.md).

**The site is bilingual, and the switch happens on the server**
([ADR-031](docs/adr/ADR-031-two-languages-one-header-one-hero.md)). `?lang=de`
or `?lang=en` in the header sets a cookie and redirects back; the cookie
governs from then on, and nothing about it needs JavaScript, because a public
administration UI that only works with scripting is a UI that excludes people.
The visitor-facing pages are translated in full. **The caseworker screens stay
German in both settings** and carry one English line saying so: they are the
working surface of a German agency, and half-translated administrative
vocabulary would be less usable than the German rather than more. Message
bodies are never translated at all - they come from versioned configuration and
are legal-text artifacts.

Two consequences of the palette are worth knowing before editing a stylesheet.
The brand sky blue `#4db2ec` measures 2.36:1 on white, so it is an ELEMENT
colour - a fill, a rule, an edge - and never carries text; `#106393` does. The
red `#dc0000` family is reserved for warning, refusal and alarm and is never
decorative. Both rules are enforced by a test that greps every stylesheet
rather than by anybody remembering them.

Three procedures are configured (Altersrente, Erwerbsminderungsrente, and the
Feststellung des Erwerbsstatus under par. 7a SGB IV). The last of them ships no
clear-cut criteria at all, because its decision is a statutory
Gesamtwuerdigung: a formally complete application still ends at tier 3, by
design. It is also the procedure the demonstration journey walks, all four
personas and both parties, because a system that only looks good on the easy
procedure has not been shown.

## The guided showcase

**Start at `/demo/rundgang`.** The tour tells the whole system in six steps for
somebody who has never seen it - the problem and the two-plane answer, what
happens when you submit, what the machine made of it, how a caseworker decides,
what the applicant receives, and why any of it can be trusted. Each step links
to the page where it actually happens, and step 3 points at a case from the
frozen gold set, so the seven stages of the glass pipeline are walkable before
you have submitted anything - including on an instance that accepts no
submissions at all.

Before the tour there is `/`, whose headline names the procedure the
demonstration walks rather than the software category, and which opens with the
pipeline told as a picture: five stages on a rail, a sealed envelope travelling
between them, and one sentence per stage that fades in with it. It is one inline SVG, one 16-second
CSS loop and zero JavaScript; the captions are real text in the document rather
than glyphs in the drawing, there is a checkbox above the figure that stops the
loop, and `prefers-reduced-motion` reaches the same still frame without being
asked.

With `EINGANGSLOTSE_DEMO_MODE=1` and an ingest token set, `/demo/antrag` opens
a three-phase journey that is the architecture told as a story. All four
personas file the same procedure - the Feststellung des Erwerbsstatus under par.
7a SGB IV - so the journey walks one procedure end to end instead of sampling
three ([ADR-035](docs/adr/ADR-035-one-procedure-end-to-end-and-hints-that-can-be-carried-out.md)).
**Phase 1:** you pick one of the four unmistakably fictional applicants, edit or
deliberately break their prefilled application, and send it as a form or as a
letter. The page names five things to try and says what each one will do: delete
the Versicherungsnummer and a gap opens with the Nachforderung the procedure
configuration itself writes; set the Geburtsdatum to 1902 and the cross-check
against the insurance number fires without anything being unsealed; put the
Beginn der Taetigkeit in 2035 and the shadow scorer marks it and says why, in
words. The form asks the way an administrative form asks -
Nachname before Vorname, native date pickers, dropdowns whose options are read
from the procedure configuration's own allowed values - and what it submits is
byte for byte what it always submitted. **Phase 2:** `/demo/case/{id}/pipeline` narrates the seven stages that
just ran on YOUR submission, with the real data at each one - including the
working copy with your own name replaced by a placeholder, side by side with
what you typed, and the sentence that goes with it: the machine never saw your
name. **Phase 3:** you follow your case into the caseworker queue it landed in,
confirm or re-route it, and watch the loop close in the applicant's inbox.
Nothing about this is a separate demo path: the intake page hands its
submission to the same ingest machinery the API route calls, the narrated view
reads the journal and re-derives nothing, and phase 3 is the ordinary review UI
with one row marked. The full reasoning, including why the demo's in-memory
working-copy store is explicitly not the production answer to where a working
copy lives, is in
[ADR-029](docs/adr/ADR-029-demo-journey-and-working-copy-in-ram.md); the
commands are in [`docs/BUILD.md`](docs/BUILD.md).

**The loop has two parties, because par. 7a SGB IV does.** A Statusfeststellung
is decided about two people, so the pipeline page offers the Anhoerung letter
that goes to the other party, and `/demo/gegenpartei` is where that party
answers - prefilled to contradict the application, which is the interesting
case. The statement is not a note attached to the first case: it travels the one
real ingest path as a case of its own, sealed, redacted, span-verified, routed
and journaled like anything else, and it lands at tier 2 with two gaps because
minimising what the form asks for costs something and the demo shows the cost.
The two cases are correlated by a drawn 96-bit token held in a RAM compartment
with a TTL - never derived from the case data, never in a journal payload, and
gone on the next restart
([ADR-036](docs/adr/ADR-036-the-two-party-loop-is-demo-scoped-and-correlated-by-a-drawn-token.md)).

## What it does not do

Stated here rather than in a footnote, because a system that claims to be
trustworthy has to be honest about its edges.

- **It is not authenticated.** The unit picker in the review UI is a query
  parameter validated against the taxonomy, and every page says so. A real
  deployment puts an identity provider there before any real data exists.
- **The vault's development backend is plaintext JSONL.** It says so in its own
  docstring. Production is encrypted at rest; the requirements are in
  [`docs/vault-dpia-input.md`](docs/vault-dpia-input.md).
- **An OCR-mangled identifier can evade the detector union entirely.** That is
  measured, documented and not fixable with a threshold. It is one of eight
  known errors, each with what it costs and what would actually fix it: the
  living list is [`docs/KNOWN-ERRORS.md`](docs/KNOWN-ERRORS.md) and the snapshot
  taken for this release is
  [`docs/known-errors/v0.1.0.md`](docs/known-errors/v0.1.0.md).
- **The accessibility document is a self-assessment**, not a BITV 2.0 audit, and
  two focus stops on the intake page draw no ring at all (KE-8).
- **The corpus is synthetic.** Every number above is a measurement over
  generated data, and generated data is easier than the world.
- No database yet: the stores are in-memory or JSONL behind a protocol that
  PostgreSQL implements later.

## Compliance posture

The system is designed against Art. 22 GDPR (no fully automated decision with
legal effect), the EU AI Act (the reasoning is recorded in
[`docs/ai-act-scoping-memo.md`](docs/ai-act-scoping-memo.md)), par. 35 SGB I,
par. 37 and par. 26 SGB X for Bekanntgabe and deadlines, par. 16 Abs. 2 SGB I
for onward transmission, and par. 88 Abs. 5 AO by analogy for the risk-
management structure. Automated notifications are informational Realakte and
carry no legal consequence; everything with one goes the written route.

## License

EUPL-1.2 (European Union Public Licence), SPDX `EUPL-1.2`, decided in
[ADR-006](docs/adr/ADR-006-license-eupl-1-2.md). The [`LICENSE`](LICENSE) file
is the official English text, fetched verbatim from the European Commission and
not retyped.

The EUPL exists in all official EU languages and **every version is equally
authoritative**: the German text is as binding as the English one. The other
language versions are published by the Commission at
<https://interoperable-europe.ec.europa.eu/collection/eupl/eupl-text-eupl-12>.
Article 5's compatibility clause lists the copyleft licenses a derivative may
be distributed under, and the Appendix in `LICENSE` carries that list.

Per-file license headers are deliberately absent; REUSE-style annotation stays
the one known refinement (ADR-006), and it is the only openCode-facing item left
open now that the repository is published there. Contributions are accepted
under the same license and there is no CLA - see
[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md).
