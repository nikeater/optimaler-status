# ADR-033: Simulated Attachments That Really Travel, Required-What-The-Persona-Arrived-With, and One Way In

**Status:** Accepted, 2026-08-18 (part 20, the intake detour)

## Context

Three user decisions of 2026-08-18 land on one page, `/demo/antrag`, and they
are decisions rather than findings. They are recorded together because they
share a surface and because two of them constrain each other.

1. **The e-mail option goes.** Part 13 built a two-tab chooser - "Formular
   (FIT-Connect)" and "E-Mail (simulierter Adapter)" - and labelled the second
   as the simulation it is. The user's judgement now is that the tab costs more
   than it teaches: a visitor meeting a chooser before they have met the form
   has to decide something before they understand anything, and the e-mail tab's
   honest caveat ("no mailbox is polled, the adapter is pilot scope") is a
   paragraph of apology on the first screen of a demonstration.
2. **A form that takes an empty answer is not a form.** Pressing "Antrag
   absenden" with a field emptied used to submit, and the missing field then
   came back as a gap three screens later. The user's sentence: the missing
   fields should go red and the submission should not proceed.
3. **An applicant encloses documents.** The intake page had no notion of an
   attachment at all, which made it a form and not an application. Part 10
   refused file upload on a public page and that refusal is not being revisited;
   what was missing was the other half of the sentence - what the demo does
   INSTEAD.

The constraint between (2) and (3) is the one worth naming. Bernd
Beispielmann's whole arc is an incomplete submission: he arrives with an empty
Rentenbeginn, and the pipeline answers with tier 2 and a Nachforderung in the
procedure configuration's own words. A naive reading of (2) - every control
`required`, full stop - takes that arc off the page, because his form could no
longer be submitted as it arrives. The conflict was put to the user, who waived
it: his persona is deprecation-pending and must not constrain the design.

## Decision

### 1. The intake page is the form, and the chooser is commented out

The "Weg waehlen" section stops rendering. It is COMMENTED OUT in
`ui/templates/demo_intake.html` rather than deleted - a Jinja comment renders
zero bytes, so the section closes up with no layout scar - and
`api/demo.py::OFFERED_CHANNELS` holds one channel id where it used to hold two.

`?kanal=email`, and a POST carrying the same parameter, resolve to the form.
That is the "never half-select something" rule the unit picker, the language
switch and the persona picker already follow: a bookmarked link from before
this part shows a page rather than a 404 or an unlinked one, and there is no
way in from outside either.

Nothing underneath is deleted. `CHANNELS` still names both ids because both are
still legal values of a submission; `build_letter_submission` still builds an
e-mail envelope and still has its unit coverage; `IntakeView.channels` still
returns both. Restoring the tab is an uncomment plus one tuple.

**This reverses a part-13 decision and is recorded as the user decision it is.**
The landing hero's caption - "Ein Antrag kommt an - als Formular, als E-Mail
oder als Scan" - STAYS, because it describes the SYSTEM's real ingest channels
and the frozen corpus carries sixteen e-mail items and eight scans. What
changed is what the demo page offers, not what the system can read.

**One consequence is a loss and is stated rather than discovered.** The
redaction refusal is no longer reachable from this page. A forged placeholder
in a form field does not refuse: ADR-017's boundary auto-seals a leaf the sweep
complained about before refusing anything, so the field becomes
`[[PII|TEXT|...]]`, the witness still carries the typed string, and the
procedure's allowed list rejects it - tier 2, `invalid`, and a Nachforderung.
That behaviour is a better demonstration than the refusal was and it is now a
hint on the page. The refusal path keeps its tests, driven by the boundary's own
refusal object rather than by a submission.

### 2. Required is what the persona arrived with

Every field the persona file gives a value for renders with the HTML `required`
attribute. A field it deliberately leaves empty does not.

That is one expression over the persona's own declaration - `bool(entry.value
.strip())` in `api/demo.py::required_for` - and not a list of field names, not
per-persona machinery, and not a rule the template knows. Rename a field, add a
persona, reorder the config file, and it still says the same thing.

It is read off the DECLARED value and never off what is currently in the box.
A page that recomputed it from the submitted values would drop the attribute
from exactly the field somebody had just emptied, which is the one moment it
exists for.

**The browser does the blocking.** No JavaScript is added anywhere. The visual
state is CSS over `:user-invalid` - never `:invalid`, which would paint a form
red before anybody touched it - and the native validation message is not
suppressed. The red family is used as the palette reserves it: `--alarm`
(5.19:1 on white) draws the edge, `--alarm-text` (9.34:1) carries the words. No
new colour pair enters the project. Colour is never the only carrier: a
sentence is PRE-RENDERED under every required field and revealed by CSS, so the
state has words as well as a tone.

**The waived conflict, and the honesty guard that survived it.** Bernd
Beispielmann's empty Rentenbeginn is the one control in the shipped demo that
the rule leaves optional. His arc therefore still runs in a browser, the hints
panel points a visitor at him for the missing-field path, and his card says out
loud that his is the only application here that can be submitted unchanged with
something missing - because a form that behaves differently on one screen has
to explain itself on that screen. He is marked deprecation-pending in the
config, next to the note that the rule does not change when he goes; it simply
has nothing left to except.

### 3. Prepared documents that become real attachment parts

Each persona brings two to four predefined synthetic documents. A visitor ticks
them; a ticked one becomes a REAL attachment on the submission - `text` plus a
`sourceType`, which is exactly how a PDF travels through this system once its
text layer has been read (`engine/ingest/envelope.py` turns "an attachment
carrying extracted `text`" into a free-text ContentPart). Sealing, the text
layer, procedure derivation and the pipeline view then run on it for real.

**The part-10 refusal stands and is now stated in words on the page.** There is
no file input, no `python-multipart`, and no ingest path around the redaction
boundary. The fieldset says so rather than leaving it to be inferred from an
absence, and it says what the documents are instead: prepared, synthetic, and
belonging to the fictional applicant the visitor picked.

**The names are researched.** Statusfeststellung par. 7a: `C0031` (Beschreibung
des Auftragsverhaeltnisses - without which, in V0027's own words, no
determination can be made), the Einzelvertrag bundle of V0027 Ziffer 16, and
"Rechnungen in Kopie", which V0027 and C0031 each require. Regelaltersrente:
`R0990` (the enclosure cover sheet R0100 Ziffer 15 names), the Geburtsurkunde of
Ziffer 16, `R0810` for the KVdR, and "Nachweise ueber Ausbildungszeiten". Two
candidates were checked and rejected: `V0028` is the guidance leaflet the DRV
supplies rather than something anybody encloses, and the Versicherungsverlauf is
an OUTBOUND document the DRV sends to the insured. Both would have read as wrong
to a caseworker.

**The text is built from the persona's own values**, as a template over that
persona's field ids, rendered once at load time. Deterministic by construction,
and an unresolved key is a load error rather than a document with a brace in it
in front of a visitor.

**Three content rules bind every document**, each with a test:

* it may not fire a second procedure's content signals, because two matching
  procedures is an ambiguity and therefore tier 3 (ADR-013);
* it may not carry a value its persona's story depends on being absent, which
  is why nothing of Bernd's names a Rentenbeginn;
* its identity appears only in shapes the deterministic detector union seals
  without the optional model, the same rule the letters follow.

The persona file supersedes to v3 because the shape changed - the house rule
for a versioned file - and only the current version stays on disk.

### 4. No `extractionFixture` on a demo attachment, and the reason is measured

The corpus ships a fixture next to every generated letter so the replay
extractor can hand the verifier proposals. It cannot do that here, and the
reason is a property of this channel rather than a shortcut: a form
submission's structured payload already fills every field the procedure's
`field_map` declares, and ADR-020's precedence rule says the schema mapper wins.
A text proposal over any of those fields is therefore discarded as
`duplicate_field` BEFORE the double lock runs - it measures nothing.

The cost is not theoretical. `extraction.discarded_count == 0` is a qualifying
condition of the tier-1 row, so one such entry takes a complete persona from
tier 1 to the default tier 3. Measured: adding a single `sealed` entry to
Renate Mustermann's attachment moves her from tier 1 to tier 3 and back again
when it is removed. A fixture here would cost two personas their arc for
nothing, so there is none - and the measurement is a test, so the reason stays
true rather than remembered.

This is a deviation from the task file, which asked for by-construction
fixtures. The half of that reasoning which holds is recorded: a PREDEFINED
document IS recordable, unlike text a visitor writes, so the objection part 13
raised does not apply. What defeats it is the channel, not the honesty.

### 5. Arcs re-pinned deliberately

With every document ticked, all four personas keep their procedure, tier, unit,
flag, completeness verdict, gap list and discard count. That is a DECISION and
not a coincidence: each persona exists to demonstrate one thing, and a demo
where enclosing the documents an agency asks for makes the case worse would
teach the opposite of what it means. The documents were designed against it.

What does move is the part the demonstration is about, and it is pinned in the
same test: more free-text parts, more sealed spans (Renate 4 -> 16, Sabine
5 -> 18), per-part counts where there were none.

One number moves without changing an arc and is reported rather than hidden:
Bernd Beispielmann's anomaly score goes from 0.109 to 0.505 against a threshold
of 0.86 as soon as any document is attached. The scorer's `freitext_vorgang`
feature reads the item's SHAPE, and an item with prose in it is a rarer shape
than a bare form. That is the scorer being honest, and the flag does not move.

## Consequences

* The demo has one way in. A judge with an old link lands on the form.
* A visitor cannot submit an incomplete application except as Bernd
  Beispielmann, and the page says which one that is and why.
* The redaction refusal is unreachable from the page; the auto-seal path
  replaces it as the demonstration and is a better one.
* The eval and the gold path are untouched by construction: everything here is
  demo surface, `config/demo/` is invisible to the config loader, and the
  part-12 numbers hold with all four gates green.
* Two defects were created by this part and found by walking it in a browser,
  both fixed before shipping: a focus rule out-specifying the invalid rule, so
  the one field a visitor was looking at was the one that stayed blue; and two
  conditional attributes on consecutive template lines rendering glued together
  under `trim_blocks`, which silently removed `required` and `aria-describedby`
  from every field that carries a help sentence. The second had shipped for the
  length of one commit and is the reason the tests now parse attribute names
  rather than searching for substrings.
* One older defect was found on the way and fixed in passing: the sealed-span
  table counts spans per TEXT PART and always did, but the projection was
  called `sealed_kinds` and looked its keys up in a table of kind labels - so
  the citizen page printed the raw key `kind.part-text-0` at a reader. It was
  only ever reachable through the e-mail tab, which is why nobody had met it.

## Alternatives considered

**Delete the chooser instead of commenting it out.** Rejected by the user's own
instruction, and it is the better engineering answer anyway: the decision
reverses a previous decision, and a reversal that can be reversed again by
uncommenting nine lines is cheaper than one that has to be rewritten.

**Per-persona required machinery**, so that Bernd's arc could survive a
blanket rule. Declined by the user as complexity, and unnecessary: a rule over
what the persona ARRIVED with produces his exception as a consequence rather
than as a special case.

**`:invalid` instead of `:user-invalid`.** Rejected: it matches from the first
paint, so a form nobody had touched would open red. That is an error message
about nothing.

**An asterisk on every required label.** Rejected: the rule is uniform, and a
marker repeated on eleven controls says less than one sentence above them does.
The exception - Bernd's one optional field - is named on his card and in its
own help text, which is where a reader is when the question arises.

**Real file upload, even gated.** Refused, again, and for the part-10 reason
unchanged: an upload control on a public page is an ingest path around the
redaction boundary, and the boundary is the product.

**Emitting fixture entries only for fields the submission does not carry.**
Rejected as tuning: it would have made the demo's own evidence conditional on
what would flatter the tier, which is the class of thing this project refuses.
The honest answer is no fixture and a written reason.
