# ADR-029: The Guided Journey Is Presentation Over the Real Machinery, and Its Working Copy Lives in RAM for Half an Hour

**Status:** Accepted, 2026-08-13 (part 13, the three-phase showcase)

## Context

Twelve parts built a system whose argument is architectural: two planes, one
direction; identity sealed before the working copy exists; every decision
readable from a journal that is never updated. All three are true and none of
them is visible. What a visitor could see after part 11 was the middle and the
end - queues, a case view, an inbox - and the beginning was a `curl` command in
a README.

The user's direction for this part was a showcase in three phases: a citizen
submits from a browser, the visitor watches the machine work on THEIR
submission, then switches hats and processes the case they just created. That
turns three latent problems into concrete ones.

**First, a public intake form collects real personal data.** The whole reason
part 11 closed `POST /ingest` is that a stranger will paste a real
Versicherungsnummer into anything that looks like a form, and this instance
seals into a plaintext JSONL vault. A blank public form would undo ADR-027 in
one commit.

**Second, the interesting part of the middle is the part the journal does not
carry.** ADR-026 left an explicit open item: the review UI cannot show the
working-copy TEXT, because the journal holds facts about a case and not case
content, and no other store holds the redacted text. "Your name became this
token" is the single most convincing thing this system can show a non-technical
audience, and there was nowhere to read it from.

**Third, a narrated view is a second place where the answer could be computed.**
A page that re-derived a routing decision to explain it would be a second
answer to "who is responsible for this case", and part 06 already found what
that costs: the evidence record carries suggestions from sources the agency has
not admitted, and a reader who sees them next to the admitted answer starts
weighing them.

## Decision

**1. The showcase is PRESENTATION over the existing machinery. There is no demo
pipeline.** `/demo/antrag` builds a submission and hands it to the same
`run_ingest` that `POST /ingest` calls - the same sealing, the same validation,
the same journal, the same inline notification and drafting folds. That
function was extracted from the route body in this part for exactly one reason:
so there is one of it. A demo of a shortcut demonstrates the shortcut.

**2. The intake page is the AUTHORIZED SERVER-SIDE CALLER of the token-gated
ingest, and it has no privilege of its own.** It presents the deployment's
`EINGANGSLOTSE_INGEST_TOKEN` to the same `DemoPosture.check_ingest` the
middleware calls. Two consequences follow and both are intended. A stranger
POSTing to `/ingest` still gets the part-11 403 before their body is read.
And on an instance with NO token configured, the intake page is closed too: the
safe state is closed for everybody, the demo app included, and the page says so
in words instead of failing. An operator who wants phase 1 has to set the
token, which is the same act of consent ADR-027 ruling 4 already required.

**3. Personas, not a blank form.** `config/demo/personas_v1.yaml` is a NEW
independently versioned file - the standing lesson from parts 06 to 12 - that
`engine.config_loader` does not read at all, so nothing in it can reach a
version stamp or a frozen corpus. Four fictional applicants cover the arcs the
showcase promises: a complete Regelaltersrente (tier 1, a Bewilligungsentwurf
waiting in phase 3), one missing its Rentenbeginn (tier 2, a Nachforderung), a
Statusfeststellung that arrives as prose and routes to the Clearingstelle, and
one whose Rentenbeginn is far enough out that the shadow scorer flags it while
the tier stays where it was.

The prefill is EDITABLE and a panel tells the visitor what to break, because a
demo you cannot break teaches nothing. Every suggestion in that panel produces a
different real behaviour: an emptied Versicherungsnummer is a gap with the
procedure's own Nachforderung wording, an implausible Geburtsdatum trips the
cross-check against the number that encodes it, a Rentenbeginn twenty years out
is exactly what the calendar bounds deliberately do not catch and the scorer
does, and `auslandsbezug: ja` lets the priority-10 rule beat the Rentenart rule
in front of the visitor.

Three constraints on persona data, all asserted rather than promised: every name
is Mustermann-class unmistakably fictional (a persona called "Michael Weber"
would be indistinguishable from a real person on a screenshot, and a demo
produces screenshots); every Versicherungsnummer is checksum-valid and encodes
its persona's birth date, because the prose recognizer is checksum-gated and the
completeness cross-check reads the real value through the witness; and no value
collides with `corpus/pii_golden/` or `corpus/gold/`, or the canary sweep over
the demo pages could not tell a leak from a persona.

**4. Two tabs, one pipeline, and the e-mail tab says what it is.** Formular
submits through the `fit_connect` channel; E-Mail presents the persona's case as
an editable letter and submits through the `email` channel, labelled in the UI
as a SIMULATED adapter. No mailbox is polled, no address is operated, and the
real adapter is pilot scope (P-14). There is no file upload and no scan channel
in the visitor UI: an upload control on a public page is an ingest path around
the redaction boundary, and the OCR limitation of KE-1 is not something to hand
a stranger with their own document.

The e-mail tab also produces the honest disappointment, and the page states it
rather than hiding it: nothing is extracted from a letter a visitor just wrote.
The reader of prose is a REPLAY of recorded model output (ADR-028), there is no
recording for a new letter, and manufacturing a fixture for the demo would be
manufacturing exactly the evidence part 12 refused to fake. What that tab does
demonstrate is stronger than an extraction table: span-by-span sealing of free
prose, and procedure derivation from CONTENT over text that has already been
through the boundary - the letter that reaches the Clearingstelle does so
because the word "Auftraggeber" survived, not because the Auftraggeber did.

**5. The pipeline view re-derives nothing.** Seven stages, each with one
plain-German sentence and the real data: Eingang, Versiegelung, Extraktion,
Evidenz, Entscheidung, Nachricht, Warteschlange. Every fact comes from
`review_state` - the same projection the caseworker UI folds - or from a store
that already exists. `admitted_routing`, arriving as the ROUTED event, is THE
routing answer and the unadmitted suggestions render as what they are. Anomaly
reasons go through `api.review.anomaly_reason_lines` and therefore through
`engine.score.render_reason`, which is the one function that turns a reason into
a German sentence and the one the eval gate checks; the citizen and the
caseworker read the same string or the system has told them two things. A
sampled case renders as Qualitaetssicherung with the calmest surface on the
page and never with anomaly styling (ADR-025).

**6. The demo store: two compartments, in RAM, for half an hour - and it is NOT
the production answer to ADR-026.** The one thing the journal cannot supply is
the working-copy text, so a demo-only store holds it, and the constraints are
the decision:

* `working_copy` is built by `DemoSubmission.from_envelope` from an `Envelope`
  and from nothing else. The envelope's documented invariant is that it carries
  only redacted content, so "placeholders in, sealed values never" is a property
  of where the data comes from rather than a check somebody has to remember.
* `echo` is what the VISITOR typed, taken off their own HTTP request. It exists
  for one moment of the tour and that moment is only honest with the value the
  visitor themselves entered. It is never obtained by unsealing anything: not
  from the vault (ADR-002 keeps that shut until outbound rendering), not from
  the transient witness (ADR-017 keeps that inside one pipeline call), not from
  a journal payload. `engine/demo/store.py` mentions no vault identifier at all
  and a test parses the module to prove it.
* Nothing here is ever written to a file. The five file-backed stores of ADR-027
  survive a process; this one does not, which makes "a restart is a complete
  wipe" true by construction one step more strongly than it is for them.
* A 30-minute TTL swept on every read and write, a capacity of 64 entries with
  the oldest evicted, and an 8000-character cap per string.
* It is constructed only when the demo posture is on. With the flag off there is
  no store object in the process, which is what "no store exists" has to mean to
  be worth asserting.

**This answers ADR-026's open item for DEMO SCOPE ONLY.** Where a redacted
working copy lives in production, under whose retention period, and with which
erasure path, remains an undecided pre-pilot question. The `echo` compartment in
particular is the part a production system must not copy: it is defensible here
because the values are synthetic by construction, held in RAM, bounded by a
timer, and shown only to the visitor who typed them. The `working_copy`
compartment is the one that could become a production answer, and it would need
the retention decision first.

**7. Phase 3 is the existing review UI plus one display-only affordance.** The
tour hands over with a link carrying `?highlight=<case id>`, which marks one row
and changes nothing else: the rows arrive from `build_queue` in the order that
function produced them, oldest first, and no branch sorts, filters or hides.
`engine/review` gains no demo branch and no knowledge that a tour exists. The
step indicator rides on demo pages only. After the caseworker acts, the
highlight block is where the loop closes: it links to the inbox, which is the
only thing that has ever been on the other end of this system.

**8. Reflow is closed for the new pages and stays open for the old ones.** The
two citizen-facing pages are built for 320 CSS px: every wide table scrolls
inside its own container so the body never scrolls sideways, the definition
lists drop to one column below 40rem, and no fixed pixel width exists in the
stylesheet they add. The caseworker UI's 1.4.10 row stays open and stays
honest - it was not fixed in this part, and a self-check that quietly closed it
because a NEIGHBOURING page was fixed would be worth less than the gap.

## Consequences

- **KE-6 is new and it is a demo-visible one.** With the optional `[redact]`
  extra installed, the model member of the detector union tags leftover context
  around a masked placeholder as a person, the sweep reports residue, and the
  boundary refuses its own output - for some persona letters and not others,
  depending on which characters the token source drew. The hosted demo cannot
  hit it, because the image installs no extras by design (ADR-027 ruling 8), and
  the gate cannot hit it, because every gate path uses the deterministic union.
  A developer running the demo locally with the extra installed sets
  `EINGANGSLOTSE_TEXT_NER=0` to reproduce the hosted posture, which BUILD.md now
  says. The refusal itself is rendered honestly on the intake page, and that
  screen is instructive rather than embarrassing: it is the boundary refusing to
  emit a working copy it could not verify.
- **A hosted demo without an ingest token has no phase 1.** `render.yaml` ships
  with ingest deliberately closed, so the shipped default is a read-only tour of
  phases 2 and 3 over the seeded corpus. That is the correct default and the
  intake page explains it; opening phase 1 is one environment variable and an
  operator's decision.
- The refusal path on a citizen-facing page follows the part-04 rule to the
  letter: kinds, paths, lengths and recognizer ids, never the residue. A page
  that printed a visitor's own data back at them, from the component whose whole
  job is to keep it out, would be the worst possible place to leak.
- `api/demo.py` and `engine/demo/store.py` are new surfaces with no engine
  privileges: neither imports a vault, a decision function or a review action.
  The one thing part 13 changed outside its own files is a display-only field on
  `QueueView`, and the queue page is asserted byte-identical without it.
- The gold numbers do not move, and cannot: nothing in this part runs inside the
  pipeline, the decision plane or the redaction boundary. The eval report over
  frozen gold v4 is the part-12 report.
