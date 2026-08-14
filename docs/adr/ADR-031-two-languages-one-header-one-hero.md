# ADR-031: Two Languages Resolved on the Server, One Header for Every Page, and a Hero That Explains Itself

**Status:** Accepted, 2026-08-14 (part 16, the bilingual UI overhaul)

## Context

Part 15 gave the project one design system and a tour. Three months of the same
argument later, the surface has a different problem: it is being read by people
who are deciding whether the work is credible, and it was doing four things
that got in the way of that.

**It was bilingual by interleaving.** Every German paragraph on the tour and the
landing page carried a short English aside underneath. That was the right answer
while the site was monolingual and somebody had to be able to follow it anyway.
It is the wrong answer once the site is genuinely for two audiences: a page that
carries both languages at once is longer than either, the second language always
reads as a summary of the first, and a reader in either language spends half
their attention skipping.

**The banner was a paragraph of small print above every screen.** Part 11 put
the synthetic-data notice on every page, which was right, and made it six lines,
which stopped being read after the first page. A notice everybody scrolls past
is a notice that is not doing its job on the pages where it matters most.

**Navigation was a row of pills that differed per page.** The caseworker
templates carried one nav, the citizen templates another, `/metrics` and
`/inbox` a third, and none of them agreed about what this site contains.

**The intake form asked eleven questions in eleven identical text boxes,**
including a date the visitor had to type in the one format the pipeline accepts
and two fields whose only valid values are enumerated in a configuration file
three directories away.

The constraints were all inherited and none was negotiable: nothing is fetched
from a third party (ADR-030), no build chain, no JavaScript requirement for any
action, the accessibility floor is a floor rather than a budget, the frozen
configs and the gold corpus are read-only, and the eval numbers may not move.

## Decision

**1. Two languages, resolved on the server, carried by a cookie.**
`?lang=` sets a cookie and redirects back to the same URL without the
parameter; the cookie governs from then on; the default is German and stays
German for any unknown, missing or malformed value. A `<select>` with an
`onchange` would have been one line shorter and would have excluded everybody
browsing with scripting off, on the surface of a project whose whole posture is
that a public-administration UI must work without it.

The switch is MIDDLEWARE rather than a parameter on eleven routes. Eleven
copies of one decision is ten chances to forget the twelfth, and a switch that
runs before routing works on every URL the toggle can appear on - including the
ones that carry their own query (`?unit=`, `?persona=`, `?highlight=`), which
the redirect preserves verbatim.

**2. The translation table is a PAIR per key, not two dictionaries.**
`TABLE: dict[str, tuple[str, str]]`, German first. A key that exists in one
language and not the other is not representable, which is a stronger guarantee
than any test comparing two dictionaries could give. What the tests then add is
the two things the shape cannot enforce: that every key a template asks for
exists (swept out of the templates rather than listed by hand), and that a
translation was actually written rather than copied - the six keys where German
and English legitimately coincide are named explicitly.

Two rendering methods, and the difference is a security property rather than a
convenience. `t()` returns a plain string that Jinja escapes whole. `m()`
returns `Markup` for the handful of sentences that need a `<strong>` or a
`<code>` in the middle - and `Markup.format` ESCAPES the values it
interpolates, so a case id or a unit name substituted into one of those
sentences is escaped even though the sentence around it is not. A `|safe`
filter on the formatted result would have escaped neither. Both directions are
asserted.

**3. Scope: visitor pages are translated in full; the caseworker screens stay
German in both settings and say so in one line.**
`/review*` and `/metrics` are the working surface of a German agency.
Half-translated administrative vocabulary - Nachforderung, Bekanntgabefiktion,
Zentrale Klaerung - would be less usable than the German rather than more, and
a competition judge needs to see the real screen rather than a rendering of it.
So those pages keep `<html lang="de">` even in English mode and carry one
`lang="en"` sentence under the title saying why. The site header around them is
chrome and does follow the toggle, which is why the header element carries its
own `lang`.

Message bodies, gap sentences and letter texts are never translated. They come
from versioned configuration, they are legal-text artifacts, and a translated
Realakt would be a different document. The inbox says that in English mode
rather than leaving a reader to wonder why one block did not switch.

**4. The banner becomes a one-line ribbon plus a page that carries everything.**
Same demo gate, same mechanism (ADR-027), one line instead of six: "Demo -
synthetische Daten. [Mehr dazu]". The link goes to `/hinweise`, which carries
the deployment's own notice verbatim, the reset model, the ingest posture, the
licence and source, an accessibility summary and the "no real data - ever"
guidance. `/hinweise` is demo surface like `/` and `/demo/*`: absent from the
route table and from the OpenAPI document when the flag is off.

The notice on that page is the ONE block that is not translated. It is the
string the running process carries (`engine/demo/mode.py`), it already contains
its own English sentence, and re-typing it into the translation table would
create a second wording of the one sentence that has to be exactly what the
instance says about itself.

**5. One header for every page, and the menu is a native disclosure.**
Wordmark and subtitle on the left; language toggle and a "Menue" button on the
right; sticky. The menu is `<details>`/`<summary>`: the summary is focusable by
construction, Enter and Space toggle it, the disclosure state is exposed to
assistive technology by the element itself, and clicking it again closes it. A
scripted dropdown would have needed a keydown handler, a focus trap, an
`aria-expanded` maintained by hand and a decision about what happens with
scripting off - four ways to get it wrong in exchange for nothing a visitor can
see. The test pins the absence of all four.

Two consequences worth stating. The demo-gated items sit behind the posture, so
a non-demo instance links `/review`, `/inbox` and `/metrics` and nothing else,
and the wordmark stops being a link because there is no start page to go to.
And the source link renders only when `EINGANGSLOTSE_REPO_URL` is CONFIGURED:
the placeholder is never rendered as a link, because a menu item pointing at
`github.com/OWNER/...` is a broken link in the one place a reader looks for the
code.

The page title, the step indicator and the unit picker moved OUT of the header
and into a `.page-head` block inside `<main>`. They are properties of the page
rather than of the site, and putting them there is also what keeps the `h1` the
first heading in the document.

**6. The palette is the reference sky blue, and the token names carry the
arithmetic.** `--brand` is `#4db2ec`. It measures **2.36:1 on white**, so it
carries no text at all: it is a fill, a rule, an edge and a focus accent, and
everything textual in the blue family uses `--brand-ink` `#106393` (6.51:1 on
white, 5.41:1 on the darkest surface here) or `--brand-ink-strong` `#0c4e73`
(8.92:1). The reserved red splits the same way for the same reason: `--alarm`
`#dc0000` measures 4.32:1 on the sunken surface, which is a comfortable element
colour and a failing body-text colour, so `--alarm-text` `#8f1010` (8.03:1 or
better everywhere) carries any small text in the family. Ink is `#222222`.

That rule is now STRUCTURAL rather than remembered: a test greps every
stylesheet for `color: var(--brand)` and `color: var(--alarm)` with the regex
anchored so that `border-left-color` - which is exactly how those two tokens
are supposed to be used - does not read as a false positive. The full measured
matrix replaces the part-15 table in `docs/accessibility-selfcheck.md`.

The red family keeps the meaning it already had in this application: warning,
refusal, alarm, and the synthetic-data ribbon. It is never decorative.

**7. Desktop-first by BREAKPOINT, never by user agent.** The container widens
to 80rem and the pages gain columns at 48rem, 64rem and 80rem: the landing
page's card sections, the personas four across (which is exactly how many there
are, so the picker becomes one row a visitor reads without scrolling), the tour
steps as a two-column layout with the heading sticky beside its prose, the
address and name fields side by side. Nothing reads a user-agent string.

The 320 CSS px discipline is unchanged and still tested. Two additions were
needed for the new chrome: the menu panel stops floating and pushes the page
below 40rem, because an absolutely positioned panel wider than a 320 px
viewport is exactly the two-axis scroll 1.4.10 forbids; and the phase strip
turns from a row of circles into a stack.

**8. The step indicator is numbered circles on a connector line.** The current
phase is filled in brand with ink text (6.74:1) and carries `aria-current="step"`;
completed phases carry an inline-SVG checkmark; labels sit beneath. What did
NOT change is where the meaning lives: it is still an ordered list, the circle
contents are `aria-hidden` because a number that is also the list position
would be announced twice, and every circle still has an offscreen sentence
naming its number and its state. A reader who gets neither the fill nor the
checkmark gets "Phase 2 - aktuelle Phase" in words.

**9. One inline-SVG icon set, and no icon carries meaning.** Fifteen glyphs on
a 24-unit grid in `currentColor`, drawn in `ui/templates/_icons.html` and
fetched from nowhere. Every one is `aria-hidden="true"` with `focusable="false"`
- the second attribute is not redundant, because older engines put an `<svg>`
into the tab order and that would add empty stops to every page - and every one
sits next to a word that says the same thing. An unknown name renders NOTHING
rather than a placeholder box, so a missing icon degrades to the label that was
always going to carry the meaning.

**10. The landing hero is one inline SVG, five captions and zero JavaScript.**
Five stages on a rail - arrival, sealing, decision, queue, reply - a sealed
envelope travelling between them, and a highlight ring that walks along. The
loop is 16 seconds. Every animated property is `opacity` or `transform`.

The captions are REAL TEXT IN THE DOCUMENT, not glyphs in the drawing:
selectable, searchable, translated by the same table as everything else, and
present in the markup whether or not they are the one currently faded in. They
are stacked in ONE grid cell, so the block is as tall as the tallest of them and
nothing jumps when they swap - no magic minimum height, no absolute
positioning.

**One keyframe set does all of it and the stagger is five negative
`animation-delay` values.** That is the load-bearing detail: the ring of a stage
and the caption of a stage share a clock, so the sentence a visitor reads always
belongs to the stage that is lit. Five separate keyframe sets would have been
five chances for the picture and the text to drift apart.

`prefers-reduced-motion: reduce` is answered EXPLICITLY in `demo.css` rather
than left to the design system's blanket rule. That rule sets
`animation-duration: 0.01ms`, which freezes an animation at its LAST keyframe -
and the last keyframe of a caption is `opacity: 0`. A fallback relying on it
would have shown no text at all. The explicit answer stops the loop, lights all
five stages and stacks all five captions under their stage names.

The SVG carries `role="img"` with a title and a description, so assistive
technology reads one sentence about the picture instead of walking a drawing.
The stage names are drawn in it for a sighted reader AND repeated as the bold
prefix of each caption, so nothing is available only inside the image.

**11. The intake form asks the way a form asks, and submits what it always
submitted.** Three changes, all presentation, and the identity of the
submission is the claim.

The name splits into two boxes, **Nachname first and then Vorname**, because
that is how a German administrative form asks. Both declare the same payload
path and carry a `join_order`; `build_form_submission` joins them into the one
"Vorname Nachname" string the envelope has always carried. The order on the
screen and the order in the value are different questions and the persona file
is where they are kept apart. A blank half is dropped rather than joined, so
emptying the Vorname submits a surname alone instead of a string with a stray
space in it. The proof is a literal: the v1 payload is typed out in the test,
key order included, so a future edit has to disagree with a literal rather than
with another derivation of itself - and the four persona arcs (procedure, tier,
unit, flag, through the real pipeline) did not move.

The two dates become `<input type="date">`, which submits the same ISO string
that used to be typed, plus a format hint for browsers that render it as a text
box. Tampering still works: any date is selectable, and the implausible-date
suggestion in the hints panel now names the calendar instead of the keyboard.

Antragsart, "Wer stellt den Antrag" and "Bezeichnung der Taetigkeit" become
selects. The first two read their options from the procedure configuration's own
`one_of` through the requirement's `field_map` entry, so an option this page
offers is BY CONSTRUCTION an option the completeness checker accepts; a
hand-written list would be a second vocabulary, and the first thing a second
vocabulary does is drift. The third has no `one_of` to read - its requirement
carries only a length bound - so its options are read from the corpus scenario
file and carried in the persona config, in that file's spelling, because they
are data rather than interface text. Two degradations are deliberate: a select
whose vocabulary resolves empty falls back to a text input rather than
rendering a dropdown nobody can use, and a value outside the vocabulary is KEPT
and offered as the selected option, because a select must never quietly submit
something other than what it was given.

The Rentenart stays a TEXT box on purpose. The hints panel invites a visitor to
type an unknown value and watch the clear-cut path fall away, and that is a
demonstration a dropdown would remove.

**12. Personas supersede to v2 rather than being edited.** The SHAPE of a
persona changed - `name` became `nachname` + `vorname`, fields grew a `control`,
and every visitor-facing string grew an English sibling - and a v1 reader handed
this file would render an empty name box. The house rule for a versioned file
whose shape changes is to supersede it, which is what produced `routing_v3` and
`taxonomy_drv_bund_v2`, and like those only the current version stays on disk.
Nothing in the DATA changed: every value, every letter and every persona id is
the v1 value, byte for byte.

**13. Umlauts in the UI, transliteration where it is data.** Every template
string, label, hint and translation uses real umlauts and Eszett. Three things
deliberately keep their transliterated spelling and the seam is accepted rather
than papered over: the frozen configuration texts (gap sentences, notification
and letter bodies), which are legal-text artifacts awaiting their own
supersession; the sentences `engine/` produces (`render_reason`, the queue flag
labels, `zu wenige Vorgaenge`), because `engine/` is outside this part's scope
and two spellings of one sentence would be worse than one old one; and the
persona letters, which are submission DATA that content-derivation rules read.
Identifiers, routes, case ids and the repository's markdown stay ASCII.

The paragraph sign was NOT introduced. `par. 16 Abs. 2 SGB I` reads that way
throughout the engine, the configs and the documents, and changing the UI alone
would have produced two conventions instead of one.

## Consequences

- **Nothing behavioural moved and no number moved.** No engine change, no new
  derivation, one new route (`/hinweise`) and one new middleware. The eval
  report over frozen gold v4 is the part-12 report, number for number, with all
  four gates green - it could not be otherwise, because nothing here runs inside
  the pipeline, the decision plane or the redaction boundary.
- **The flag-off surface grew and so did the check on it.** `/hinweise` joins
  the route table assertion and the OpenAPI assertion, and a new test asserts
  that no demo route reaches the header of a non-demo page - which is the
  obvious way a shared partial could leak one.
- **The byte-identity control group needed one honest change.** It neutralises
  `_demo_ribbon.html` instead of `_demo_banner.html`, and it copies the real
  environment's globals, because every page now reads its language context from
  there and a control group missing it would raise on an undefined callable
  instead of measuring what it exists to measure. The `demo` global is
  deliberately NOT neutralised: the posture is off in both, so the header's
  demo-gated items have to render nothing in both.
- **Four pinned test strings changed because the words changed** (umlauts), and
  the tour's aside assertions were replaced by their inverse: the suite now
  asserts there is no `class="aside"` and no "In English" anywhere.
- **The accessibility suite grew from nine pages to eleven** (`/` and
  `/hinweise` joined the citizen set) and gained two checks that are properties
  rather than samples: the two colours that may not carry text never do, and the
  hero's reduced-motion answer is explicit rather than inherited.
- **The one thing that got harder to review is the translation table.** It is
  about six hundred lines of prose in one file, and a reviewer reading a diff of
  it is reading content rather than code. The mitigation is the key naming: keys
  name the PLACE rather than the sentence, so a re-wording is a value change and
  a re-structuring is a key change, which is exactly the distinction a reviewer
  wants to see.
- **A third language is now cheap and would still be a decision.** The pair
  would become a triple, `LANGUAGES` would gain a member and every value would
  need a third element - mechanical, and the compiler would find every place.
  What it would NOT be is free: somebody has to write six hundred sentences and
  somebody has to keep them true.
- **The self-check's open rows are unchanged in substance.** The contrast table
  is re-measured for the new palette; `/` and `/hinweise` gain rows; the
  external accredited BITV 2.0 test, the test with users of assistive
  technology, the browser measurement at 320 px and 200 percent, the par. 12b
  BGG statement, the Leichte-Sprache question and the queue search all stay
  open. A redesign is not evidence about any of them, and neither is a
  translation.
