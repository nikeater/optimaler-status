# ADR-036: The Two-Party Loop Is Demo-Scoped, and the Correlation Is a Drawn Token

**Status:** Proposed, 2026-08-18 (part 19, the counterparty loop)

## Context

Feststellung des Erwerbsstatus nach par. 7a SGB IV has two parties by law. The
Clearingstelle decides "auf Grund einer Gesamtwuerdigung aller Umstaende des
Einzelfalles" (Abs. 2 S. 1) and hears the Auftraggeber before it decides
(Abs. 4, with par. 24 SGB X). Since part 22 all four intake personas file that
procedure, so the second party is not an edge case of the demonstration - it is
the shape of every submission a visitor can make.

The user's flowchart (`tasks/part-19-two-party-journey/flowchart-conversion.md`)
draws it as one box: "Asks Antragsteller II for a statement (prototype: a popup
that the letter to Antragsteller II is being processed)", with a second box for
"Antragsteller II - unique identifier so the response links back to Antragsteller
I's case" and a rhombus for "feedback may or may not come".

Until this part the system had none of it. One case was one submission; a second
inbound correlated to an existing case did not exist; and the only thing a
visitor could see of the counterparty was the word "Auftraggeber" surviving into
the working copy as a placeholder.

Four things made the obvious implementation the wrong one.

**First, "a statement was requested" has no event type.** The journal's
`EventType` is a contract (`schemas/events.py`), it is what every projection
folds, and it is exported to `schemas/artifacts/`. Adding `STATEMENT_REQUESTED`
and `STATEMENT_RECEIVED` is a contract change with an ADR, a schema export, a
migration story for journals written before it, and a decision about what the
queue projection does with a case that is waiting. That is pilot-grade work and
this part is not it.

**Second, a correlation identifier is a chance to leak.** The obvious token is
the case id, or a hash of it, or something derived from the submission. Every
one of those is a second name for a thing that already has one, and the first
thing a derived identifier does is tell its holder what it was derived from.

**Third, a counterparty surface is a second place to show somebody's data.** The
Auftraggeber has to be told enough to answer, and "enough" is the question. A
page that handed the counterparty the applicant's Versicherungsnummer would be a
data-protection demonstration doing the thing it exists to argue against.

**Fourth, a popup is JavaScript.** Nothing on this site needs any, by design and
by ADR-030 / ADR-031. A modal announcing that a letter is being processed would
also be a notice ABOUT an artifact instead of the artifact.

## Decision

### 1. The loop is demo-scoped, the production shape is named, and the page says which it is

`/demo/gegenpartei` and everything behind it live in the demo layer: `api/demo`,
`engine/demo/`, the RAM store. **No journal event records the request and none
records the answer.** The two-party section on the pipeline page states that in
its own first sentences - "dieser Schritt ist der einzige auf dieser Seite, den
die Demo-Schicht simuliert ... fuer 'Stellungnahme angefordert' gibt es keinen
Ereignistyp, und einen zu erfinden waere eine Vertragsaenderung und keine
Vorfuehrung" - and so does the caseworker cross-link.

The production shape is deliberately deferred and named here so it can be picked
up rather than rediscovered: two new event types, a correlation the journal
carries, a queue projection that knows what "waiting for a statement" means, and
the A-II half of the notifications (below). What is NOT deferred is the part that
was worth building: the statement travels the real pipeline.

### 2. The statement is a REAL submission through the ONE ingest path

`POST /demo/gegenpartei` is the authorized server-side caller of the token-gated
ingest, exactly as `/demo/antrag` is (ADR-029 ruling 2): the same
`DemoPosture.check_ingest`, the same `run_ingest`, one call, no second body and
no second sealing path. The statement becomes **its own case** - sealed,
redacted, span-verified, completeness-checked, routed, decided, journaled and
notified - and the raw `POST /ingest` keeps its 403 for direct callers.

That is the demonstration this part exists for. The counterparty's own contact
person (NAME), company (ORG), Betriebsnummer (BNR - a kind no other demo surface
has ever shown) and address (ADDR) leave the raw plane at the same boundary the
applicant's data left it at. **The seal is a property of the system, not a
courtesy extended to one party**, and the way to show that is to run the second
party through the same machinery rather than to say so.

The statement is shaped as what it legally is: a Statusantrag filed by the
Auftraggeber (`antragsteller_rolle: auftraggeber`, which par. 7a Abs. 1 S. 1 SGB
IV allows and which gold item `sf-0004` already covers), carrying the
Gesamtwuerdigung Indizien the corpus scenarios use and a free-text Stellungnahme
that becomes a real text part.

### 3. The correlation is a token DRAWN from `secrets`, and it is the whole of the link

`engine/demo/store.py::new_token` draws 96 bits and formats them as 24 hex
characters. It is not derived from the case id, not from the submission id, not
from a sealed value and not from a hash of any of them. It is stored as the KEY
of a `StatementLink` in the same RAM/TTL/capacity compartment ADR-029 defined,
under the same rules: swept on every read and write, 30-minute TTL, 64 entries
oldest-evicted, wiped by `reset()`, absent when the flag is off because the store
itself is absent.

Three consequences, all asserted rather than promised:

* An unknown, expired or evicted reference renders **a page** - the one that
  explains what this surface is and offers the intake - never a 404 and never
  somebody else's request. Expiry and absence are indistinguishable on purpose,
  which is the same choice `DemoStore.get` already made for the working copy.
* The token reaches **no journal payload**. A test walks every event of both
  cases and asserts it.
* Answering does not extend any lifetime: the link keeps its `created_at`, so
  the whole correlation expires when the request it belongs to would have.

### 4. Nothing is injected server-side out of the store

The counterparty page RENDERS the carried values into form controls and the
visitor's browser posts them back, so what reaches `run_ingest` is a posted form
exactly like the intake's. The demo store is a source for a PAGE, never a source
for a submission. Rewriting the company name in the form proves it: the sealed
working copy carries what was posted, while the stored link still says what the
applicant wrote, because that is what the letter was about.

### 5. Data minimisation, with the line drawn where it is real

The statement carries neither the applicant's Versicherungsnummer nor their
Geburtsdatum. A Stellungnahme does not need them, so the counterparty surface
never shows them and the second submission never contains them.

The consequence is real, is visible, and is the honest half of the
demonstration: the completeness check reports two gaps, the case lands on tier 2,
and the Clearingstelle asks for what is missing in the procedure's own
Nachforderung wording. Nobody wrote a branch for "this one came from the
counterparty" - it is treated like every other incomplete item, which is the
point.

The applicant's NAME is in the letter, and that is a deliberate asymmetry rather
than an oversight. An Anhoerung has to say which Auftragsverhaeltnis it is about,
and the Auftraggeber is by definition the other party to that relationship;
withholding a name the recipient already knows would be theatre rather than data
minimisation, and the page says exactly that.

### 6. The popup is a letter

The flowchart's popup renders as a simulated Anhoerung letter on the page - on
the applicant's pipeline view and again on the counterparty surface. Zero
JavaScript, and a letter is the artifact a real hearing produces rather than a
notice that one is being produced. Like every other Behoerdenschreiben on this
site it **stays German in both languages** (the `/inbox` precedent), with one
`lang="en"` line saying why; the page around it is translated in full.

### 7. "May or may not come" gates nothing, and the page says so in those words

While no statement has arrived, the pipeline page says so and the case proceeds
untouched: no queue is reordered, no clock is started, no tier moves, nothing
waits. "Geht keine Stellungnahme ein, wird nach Aktenlage entschieden; der
Vorgang wartet nicht auf Sie" is in the letter itself.

A case with no second party to hear gets **no section at all** rather than an
announcement of an absence: a seeded corpus item nobody submitted, a
non-Statusfeststellung procedure, and the submission where a visitor followed the
hint that empties the Auftraggeber. The request is recorded only when the
pipeline's OWN derived procedure is `statusfeststellung` and an Auftraggeber was
actually named - two facts read off the submission that was just made, never
re-derived here.

### 8. The caseworker cross-link is display only, in the class the highlight is in

`review_case.html` gains one demo-gated section naming the case at the other end
of the correlation, in both directions. It re-derives nothing, changes no queue,
writes no event, gates no decision, and is `None` outside demo mode because the
store that holds the correlation is only constructed there. The flag-off page is
asserted **byte-identical** against the neutralised-include control group, which
is the part-11 method applied to the one caseworker page that grew.

The section is rendered only once a second case EXISTS. A waiting hearing has
nothing to link, and the caseworker surface says nothing rather than announcing
an absence it does not act on either - which is the mirror of the citizen page,
where the absence IS the subject and is stated in words.

### 9. Two vocabularies, two sources, neither invented

`antragsteller_rolle` is a `one_of` on a mapped requirement, so the select is fed
at render time from `config/procedures/statusfeststellung_v1.yaml` and cannot
offer a value the completeness checker would reject. The Gesamtwuerdigung
Indizien are deliberately NOT requirements - that config's own `field_map`
comment says why: they are Abwaegungsmaterial and not a checklist - so no
`one_of` exists to read, and their options are READ from
`corpus/generator/scenarios/statusfeststellung.yaml` verbatim. A test asserts
every option this module offers occurs in that file, so the copy is checked
rather than promised.

`config/demo/` was NOT touched and personas stay at v4. The statement form is one
form for every case rather than persona data, so it belongs beside the code that
renders it; a v5 supersession for something no persona carries would have been a
version bump that describes nothing.

### 10. The contradiction is the payoff, and it is why this procedure has no tier 1

Every default on the counterparty form states the OPPOSITE of what Sabine's own
C0031 annex says: her Anlage says the working time is hers to divide, the
workplace is hers and there is no Weisungsbindung im Einzelnen; the form arrives
claiming the hours, the place and the equipment are the client's and that she is
integrated into their organisation. Two sealed, span-verified statements about
one relationship, contradicting each other, both readable off two working copies
and both in the same queue.

No checklist resolves that, which is precisely the Gesamtwuerdigung par. 7a Abs.
2 S. 1 SGB IV reserves for a human and precisely why this procedure ships
`tier1_enabled: false`. A visitor who wants agreement instead changes five
selects.

## Consequences

- **The A-II outcome notification is OUT of this part and is filed as the
  follow-up it is.** Telling the Auftraggeber what was decided needs a
  supersession of `config/notifications/notifications_v1.yaml` (a new template, a
  new recipient class that is not the applicant) plus a second inbox surface, and
  a frozen versioned config is not something a demo part edits. The loop this
  part closes is the INBOUND half.
- **One wording imprecision is exposed and deliberately not fixed here.** The
  statement's Nachforderung says "Bitte teilen Sie uns Ihre Versicherungsnummer
  mit", because `statusfeststellung_v1.yaml` was written for the applicant and
  the requirement genuinely is the Auftragnehmer's number. To an Auftraggeber the
  possessive is imprecise. Fixing it is a frozen-config supersession with its own
  review, not a demo-part edit; it is recorded in the log as a parked item.
- **The demo store gains a third compartment**, named and reasoned in the
  module's own header (`engine/demo/store.py`) beside the two ADR-029 defined.
  ADR-029 itself is not amended - an accepted record describes what was decided
  then - and this ADR is where the third one is decided. It holds no value the
  visitor did not type on their own form, it is bounded exactly like the other
  two, and the module still imports no vault.
- **The gold numbers cannot move**: nothing here runs inside the pipeline, the
  decision plane or the redaction boundary, and the eval report over frozen gold
  v4 is the part-12 report unchanged.
- **Two defects the browser walk found before ship**, both wording rather than
  code, both now pinned by tests. The counterparty form had borrowed the intake's
  required-field note, which names the three fields the INTAKE hints tell a
  visitor to delete - an exemption this form does not have and fields it does not
  carry. And the generic "die Auswahl kommt aus der Verfahrenskonfiguration"
  sentence printed under all six Indizien selects in a column, which stops being
  an explanation and becomes wallpaper; it is now said once above them, and the
  one select whose vocabulary really is a procedure requirement says so in its
  own more specific words.
- **`_field.html` is new and both forms render through it.** The counterparty
  page was the second surface to render a `FieldView`, and thirty lines of
  duplicated markup would have lost the whitespace discipline this project has
  already shipped as a bug once (`requiredaria-describedby`). The macro is
  imported `with context`, which is load-bearing: `t` is an environment global in
  ONE language and a per-render context in the reader's, so a context-free import
  would put German labels on every field of an English page.
