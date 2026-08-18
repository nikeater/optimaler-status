# Accessibility Self-Check: EN 301 549 V3.2.1 / WCAG 2.1 AA (P-15)

**Status:** SELF-ASSESSMENT by the implementing engineer, 2026-08-12, last
revised 2026-08-18. Not an audit. No person with a disability has used these
pages, no assistive technology has been run against them, and no BITV-Test has
been performed. An accessibility statement under par. 12b BGG and BITV 2.0
par. 7 may NOT be derived from this document; it needs the external test that is
a pilot prerequisite.

**Scope (part 16: eleven pages).** The four pages part 10 ships - `/review`, `/review/queue/{id}` (both
the unit and clearing variants) and `/review/case/{id}` - plus the two pages
they share a stylesheet with, `/metrics` and `/inbox`.

**Extended 2026-08-13 (part 13)** by the two citizen-facing pages of the guided
showcase, `/demo/antrag` and `/demo/case/{id}/pipeline`, plus the demo landing
page `/`, which now loads the same stylesheet and therefore the same reflow
rules. They matter differently
from the rest: the caseworker pages are read by somebody at a desk who was
trained on them, and these are read by a member of the public on whatever device
they have. The tests behind their `automated` verdicts live in
`tests/test_demo_journey.py`; where a criterion is answered differently for the
two page sets, the row says so and names both.

**Revised 2026-08-15 (part 18, the visual overhaul).** The redesign changed how
every page looks and was held to the rule that it may change nothing about what
any page guarantees. Five things are worth a reader's attention here, and two
of them are defects the redesign created and then found.

1. **The focus ring failed on the one surface that did not exist before.** Part
   18 added a dark closing band, and `--focus` measures 1.34:1 against it. The
   ring is white inside the band (11.96:1). This came out of computing the pair
   before shipping it, and was then confirmed by a new measurement: every tab
   stop on all nine pages was focused in a browser and its computed outline
   colour compared with the computed background behind it. See 1.4.11 and
   2.4.7.
2. **A styling of the date field dimmed a focus ring this project does not
   own.** `input[type="date"]` carries a calendar button in the engine's shadow
   tree with its own tab stop; `opacity: 0.65` on it dimmed the ring the engine
   paints there. The rule sets nothing but `cursor` now. The tab walk found it.
3. **The fluid type is `clamp()` with a `rem` term, never bare `vw`.** Three
   sizes became fluid and a `vw`-only font size would have failed 1.4.4
   outright. See that row.
4. **1.4.10 was re-measured after the redesign** at 320 and 390 CSS px on all
   ten pages: every one of them still measures exactly the viewport. The one
   new thing that can clip content - a maximum height on the 41-row queue table
   so its sticky header has a box to stick to - engages only from 64rem up and
   is removed for print.
5. **The demo ribbon renders on `/` and `/hinweise` only.** This is a product
   decision by the user, not an accessibility one, and it is recorded here
   because this document is where the honesty properties of these pages are
   written down. Nothing the ribbon SAYS changed and nothing else about the
   notice moved: the full disclaimer is still one page, the ribbon still links
   it, the site menu still links it from every page, and the pages that no
   longer carry the ribbon still carry their own statements about what this
   instance is - the picker note about the role model on the caseworker
   screens, the footer on all of them. The tests assert both halves: that the
   notice renders on those two pages, and that it is REACHABLE from every page
   that lost it.

**Revised 2026-08-18 (part 20, the intake detour).** The intake page changed in
three ways and one of them is an accessibility change rather than a product
one, so it is written down here.

1. **The form validates in the browser now.** Every field a persona arrived
   with a value for carries the HTML `required` attribute, so the browser
   blocks the submission, moves focus to the field it stopped at and speaks its
   own message. The page adds a pre-rendered sentence per field, revealed by
   CSS on `:user-invalid`, plus a red edge (`--alarm`, an element colour) and a
   red label (`--alarm-text`, the text colour of the same family). No new
   colour pair enters the project and no JavaScript enters it either; the
   native message is not suppressed. See 3.3.1 and 3.3.3, both rewritten.
2. **`:user-invalid`, never `:invalid`.** The second would paint a form red
   before anybody had touched it, which is an error message about nothing.
3. **The e-mail tab stopped rendering** (a product decision by the user). The
   textarea it carried is gone with it, so the rows that named it name the
   inputs and selects that remain. Nothing else about the page's structure
   moved: same landmarks, same heading levels, same labels, same reflow rules.

**Revised 2026-08-18 (part 21, the phase theming).** The three phases of the
demonstration now render on three grounds - the citizen pages unchanged, the
two machine pages on a deep blue-slate, the caseworker pages on warm amber
paper. This is the largest single addition of colour pairs the project has
made, and five things belong here. (Every ratio in this note is part 21's own
and the machine ground's values moved in part 23, above; the current numbers
are the tables further down, which are the computed ones.)

1. **Part 15 refused dark mode because it was unmeasured, and that refusal is
   intact.** It was never an argument against a dark surface; it was a
   requirement on how one ships. There is still no `prefers-color-scheme`
   switch and no reader-chosen scheme. A ground is a property of the PAGE -
   which phase it belongs to - and renders identically for every reader.
2. **Every pair on both new grounds was computed before it shipped**, and the
   computation is now a TEST rather than a note. Until this part the ratios
   were worked out by whoever changed the palette and written down here;
   nothing failed if the next person forgot.
   `tests/test_review_accessibility.py` parses the three token blocks out of
   `ui/static/system.css`, resolves the `var()` chains and computes every pair
   the components produce on all three grounds against the WCAG 2.1
   relative-luminance formula. It is checked against the two mistakes it exists
   to catch: a dark focus ring on the dark ground fails at 1.95:1, and a text
   token aliased to an element colour fails at 3.24:1.
3. **Two components broke exactly the way the part-18 band broke, and both were
   found by arithmetic rather than by looking.** The current step's number is
   `--ink` on a `--brand` fill: 6.74:1 on the citizen ground, and 2.05:1 on the
   machine ground once `--ink` goes light - two light values on top of each
   other, on the one number that says which phase a visitor is in. And the
   primary button is a dark gradient measuring 2.30:1 against a dark card, which
   is a control not distinguishable from its surroundings. See 1.4.3 and 1.4.11.
4. **The tab order was walked again, on all eleven pages: 215 stops.** No focus
   ring on either new ground is below 3:1; the lowest is 8.71:1 on the machine
   ground and 8.25:1 on the caseworker ground. Separately, every element that
   renders text on all eleven pages was swept for its computed colour against
   its effective background - compositing translucent layers and reading
   gradient stops - which is 5767 text nodes with none below its floor. The
   browser and the arithmetic agree to the second decimal. The two stops that
   report no ring are the `input[type="date"]` calendar buttons on the intake
   page, which is the engine's own shadow-tree control described in the part-18
   note above and unchanged by this part.
5. **1.4.10 was re-measured** at 320 and at 390 CSS px on all eleven pages
   after the theming: every page measures exactly the viewport. Nothing about
   the layout changed - a ground re-points colours and nothing else - and the
   measurement was made anyway.

**Revised 2026-08-18 (part 23, One Dark and the homepage).** The machine
ground's values were re-anchored on the One Dark Pro editor theme and the
landing page lost three lines and gained a painted card. Four things belong
here.

1. **The machine ground got QUIETER and every pair was recomputed.** Body text
   went from 13.01:1 to 10.00:1, which is deliberate - One Dark is a low-glare
   theme - and it makes this the tightest of the three grounds rather than the
   most comfortable: worst text 4.78:1 against 4.99 and 5.06 on the other two,
   worst element 3.22:1. The table below is the recomputation, not a copy with
   a new hex in it, and the test that computes it from the stylesheet was
   extended over the new tokens rather than left measuring the old set.
2. **A syntax palette entered the project, and every member of it carries
   characters.** Five tokens paint the working copy on the pipeline page. They
   are held to 4.5 and not to 3.0 for that reason, on BOTH beds a machine token
   can land on, and the two One Dark accents that could not reach the floor at
   their published value were lifted along their own hue instead of being used
   below it. What is deliberately not coloured is written down with them.
3. **The call to action on the landing page had an invisible focus ring and it
   was found by arithmetic.** The card is painted with the button's own fill,
   the ring is drawn at `outline-offset: 2px` and therefore lands on that fill,
   and `--focus` measures 1.37:1 against it. Overridden to `--cta-ink` at
   6.51:1. This is the part-18 band failure in a new place - see "the three
   pairs a fill broke" below.
4. **Both sweeps were run again after the change.** 192 tab stops over eleven
   pages with no ring below 3:1 (lowest 6.51:1, which is that card's
   overridden ring); 4877 text nodes swept for their computed colour against
   their effective background with none below its floor, the lowest on a
   machine page being 4.96:1 on the sealed-placeholder chip and 5.12:1 on an
   inline `<code>` - the same two numbers the arithmetic predicts. The two
   stops that report no ring are still the `input[type="date"]` calendar
   buttons on the intake page, which is the engine's own shadow-tree control,
   on a page this part did not touch.

**Revised 2026-08-15 (part 17, the browser pass).** Four things changed, and
the reason for all four is the same: this document described pages that had
been asserted about but never opened. Everything below was found by rendering
them in a real browser and looking.

1. **2.2.2 was answered on paper only and is now answered in fact.** The pause
   control shipped inside a wrapper `<div>`, which made it a cousin rather than
   a sibling of the drawing it was meant to stop; every `:checked ~` rule
   matched nothing and the checkbox did nothing in any browser. The test
   asserted that the stylesheet CONTAINED those rules, which was true
   throughout. The checkbox is now a direct sibling of what it pauses, the
   test parses the rendered page and asserts that relationship, and the control
   was operated by mouse and by keyboard in a real browser. The hero's stage
   order was wrong in the same release and for a related reason - see the
   engineering log; 1.4.1 is unaffected, because the captions were always real
   text and always said which stage they belonged to.
2. **The demo ribbon left the red family for a caution orange.** Red means
   warning, refusal and alarm everywhere else here, and readers applied that
   meaning to a bar that says "this is a demonstration". Three new tokens,
   measured like every other pair and added to the table below. The red is
   unchanged everywhere it still means alarm.
3. **1.4.10 was measured in a browser for the first time, and three pages
   failed it.** Every page in the suite was rendered at 1920, 390 and 320 CSS
   px in both languages and looked at, and `document.scrollWidth` was compared
   against the viewport on each. Three real two-axis scrolls came out, all of
   them older than this part and all of them invisible to a check that reads
   markup:
   - `/review` reached **545 CSS px** on a 320 px viewport. `.sr-only` is
     `position: absolute`, and with no positioned ancestor its containing block
     was the initial one - so the offscreen sentences inside a 662px queue
     table escaped their `overflow-x: auto` box and took the document with
     them. `.scroll-x` is positioned now.
   - `/review/case/{id}` reached **612 CSS px**. A `fieldset` does not shrink
     below its min-content width and a `<select>` sizes to its longest option
     ("Geschaeftsbereich Versicherung und Rente"), so the correction form held
     the page open. `fieldset` takes `min-width: 0` now.
   - `/demo/antrag` reached **325 CSS px**, from a `white-space: nowrap` badge
     reading "wird versiegelt: Organisation / Auftraggeber". Badges wrap below
     40rem now.

   All ten pages measure exactly the viewport width at 320 and at 390 px after
   the fix. The static check stays, because it catches a different mistake; it
   is no longer the only thing behind this row. The step indicator's connector
   geometry was measured the same way at 320, 768, 1024, 1440 and 1920 px.

4. **The reading measure is now the container.** Every flowing text element
   carried a `ch` cap - 68ch on a paragraph, 78ch on a notice, 72ch on a hero
   caption - which on an 80rem container put every page's text in a column down
   the left half of a wide screen. The caps are gone and `body` line-height
   rises from 1.55 to 1.65 to carry the longer line. Tables, form fields and
   field help keep their widths. This is a legibility trade made deliberately
   and against the orthodoxy: a measure of 100-odd characters is longer than a
   typographer would choose, and the layout reading as broken to every person
   who opened it was the larger failure.

**Revised 2026-08-14 (part 16, the bilingual overhaul).** Four things changed
and one of them is a new criterion this project had never engaged before.

1. **The contrast table is re-measured from scratch** for the part-16 palette
   and REPLACES the part-15 one. The brand colour is now a sky blue that
   measures 2.36:1 on white, so the token names carry the arithmetic: `--brand`
   is an element colour and never carries text, `--brand-ink` is the text
   sibling. The reserved red splits the same way, and so does the caution
   orange part 17 added. A test greps every stylesheet for `color:
   var(--brand)`, `color: var(--alarm)` and `color: var(--caution)` so the rule
   is structural rather than remembered.
2. **Two pages joined the suite**: the landing page `/` and the new disclaimer
   page `/hinweise`. Eleven pages are now checked in total.
3. **2.2.2 Pause, stop, hide is ENGAGED for the first time** and answered with
   a control on the page rather than with an operating-system preference. The
   landing hero animates on a 16-second loop; there is a labelled checkbox
   above it that stops it, in CSS alone, and `prefers-reduced-motion` reaches
   the same still frame without being asked. The row below says why the control
   stops rather than freezes.
5. **The inline English asides are gone**, replaced by a server-side language
   toggle. 3.1.2 changed shape rather than verdict: a visitor page is now in
   ONE language and declares it on `<html>`, and the two places where languages
   still meet - the English note on the German caseworker screens, and the
   German message bodies in an English inbox - carry `lang` on the element.

**Revised 2026-08-14 (part 15, the redesign and the tour).** Three things
changed here and all three are in the direction of more evidence rather than
less.

1. **One design system** (`ui/static/system.css`, ADR-030) now carries the
   palette, the type scale, the spacing ladder and every shared component for
   every page. Where a row below used to describe a rule in `metrics.css` or
   `review.css`, it describes the same rule in one place.
2. **1.4.10 reflow is no longer open for the caseworker pages.** The gap was
   never the layout: the shared reflow rules lived in the stylesheet only the
   citizen pages loaded. They are in the design system now, every wide table on
   every page sits in its own `overflow-x: auto` container, and a test asserts
   it per page. The verdict is `automated (static)`, which is deliberately not
   the same claim as "measured in a browser at 320 CSS px" - see the row.
3. **`/metrics` and `/inbox` are now TESTED and not merely in scope.** They
   gained the shell the rest of the site has - a skip link as the first
   focusable element, a `<nav>` landmark, `<main id="inhalt">` - and joined
   `tests/test_review_accessibility.py`, which now parametrises over six pages
   rather than four. The tour page `/demo/rundgang` joined the demo-journey
   suite on the same criteria.

The contrast row carries MEASURED ratios from this part on: every foreground
and background pair the stylesheets actually ship was computed against the
WCAG 2.1 relative-luminance formula before it was used. Still a calculation
rather than a photometer on a real display, and the row says so.

Three verdicts are used, and the middle one is the honest one:

| Verdict | Meaning |
|---|---|
| **automated** | A test in `tests/test_review_accessibility.py` fails if it regresses. |
| **reviewed** | Checked by reading the markup and the CSS. A human judgment that a machine cannot make; a real test would need a person or a browser. |
| **open** | Not established. Needs the external audit, a real browser, or assistive technology. |

## Why there is no axe-core run

axe-core is a browser engine plus a rule set. The Python packages carrying the
name drive a real browser through Selenium or Playwright, which means a browser
binary, a driver and a headless runtime in CI - a dependency chain larger than
the nine pages it would check, on a project whose whole posture is that a gate
must not depend on what is installed on a machine. The mechanical criteria are
tested directly against the rendered HTML instead, and the criteria a static
check cannot decide are listed as `reviewed` or `open` rather than assumed.

Running axe (or a BITV-Test) against a deployed instance is the right thing to
do and belongs in the pilot, together with a test by users of assistive
technology, which is the only thing that actually answers the question.

## Measured contrast ratios (part 16, extended in parts 17, 18 and 21)

Every pair below was computed with the WCAG 2.1 relative-luminance formula from
the hex values in `ui/static/system.css`. These are the pairs the stylesheets
actually produce; a token combination that does not occur on a rendered page is
not listed, because a matrix of everything against everything invites the
reader to check the wrong cell.

Requirements: **4.5:1** for body text, **3:1** for large text and for anything
that identifies a user-interface component or a state.

Surfaces: `--surface` `#ffffff`, `--surface-alt` `#f5f7f9`, `--surface-sunken`
`#eaeaea`, `--canvas` `#eef2f5`, the five note tints `--tint-brand` `#e4f2fb`,
`--tint-ok` `#e8f3ec`, `--tint-alarm` `#fbeaea`, `--tint-caution` `#fff3e6`
(part 17), `--tint-sample` `#eef2f8`, and one fill that carries text:
`--brand` `#4db2ec`, used for the current step's circle and for nothing else
that a word sits on.

The "worst tint" column is still `--tint-alarm` in every row: `--tint-caution`
is the lightest of the five, so adding it moved no existing number.

| Foreground | Where it is used | vs `--surface` | vs `--surface-alt` | vs `--surface-sunken` | vs `--canvas` | worst tint | vs `--brand` fill | Requirement | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| `--ink` `#222222` | body text, headings, table cells, the current step's number on its brand fill | 15.91 | 14.82 | 13.22 | 14.13 | 13.68 | 6.74 | 4.5 | pass |
| `--ink-soft` `#3f4a52` | definition terms, the page lead, the step circle's number | 9.08 | 8.46 | 7.55 | 8.07 | 7.81 | 3.85 | 4.5 | pass |
| `--muted` `#57646d` | help text, `.muted`, the footer, table captions, the wordmark subtitle | 6.09 | 5.67 | 5.06 | 5.41 | 5.24 | 2.58 | 4.5 | pass |
| `--brand-ink` `#106393` | links, the nav pills, the menu button, buttons, the caption's stage prefix | 6.51 | 6.06 | 5.41 | 5.78 | 5.59 | (never on brand) | 4.5 | pass |
| `--brand-ink-strong` `#0c4e73` | hovered links and buttons, the skip link's background | 8.92 | 8.31 | 7.42 | 7.93 | 7.68 | 3.78 | 4.5 | pass |
| `--ok` `#10683c` | the gate-passed verdict | 6.85 | 6.37 | 5.69 | 6.08 | 5.89 | - | 4.5 | pass |
| `--alarm-text` `#8f1010` | the gate-failed verdict, the refusal block's prose | 9.34 | 8.69 | 7.76 | 8.29 | 8.03 | - | 4.5 | pass |
| `--caution-text` `#7a3d00` (part 17) | the demo ribbon's link and icon; **7.70 on `--tint-caution`, which is the only surface it appears on** | 8.42 | 7.84 | 7.00 | 7.48 | 7.24 | 3.57 | 4.5 | pass |
| `--warn` `#7a4a06` | reserved for an advisory state | 7.47 | 6.96 | 6.21 | 6.64 | 6.43 | - | 4.5 | pass |
| `#ffffff` on `--brand-ink` | button and menu labels, the call-to-action link, the completed step's checkmark | 6.51 (8.92 on `--brand-ink-strong`) | - | - | - | - | - | 4.5 | pass |
| **`--brand` `#4db2ec`** | **element colour ONLY**: the header rule, card and stage edges, the hero ring, the current step's fill | 2.36 | 2.20 | 1.96 | 2.10 | 2.03 | - | 3.0 | pass as an element, **FAILS as text and is never used as text** |
| **`--alarm` `#dc0000`** | **element colour ONLY**: the refusal block's edge, the anomaly flag's edge, the gate-failed frame. No longer the demo ribbon (part 17) | 5.19 | 4.84 | 4.32 | 4.61 | 4.47 | - | 3.0 | pass as an element; **below 4.5 on two surfaces, so text uses `--alarm-text`** |
| **`--caution` `#b45a00`** (part 17) | **element colour ONLY**: the demo ribbon's bottom rule, and nothing else. **4.36 against `--tint-caution`, the surface it is actually drawn beside** | 4.77 | 4.44 | 3.96 | 4.24 | 4.10 | 2.02 | 3.0 | pass as an element; **below 4.5 on three surfaces, so text uses `--caution-text`** |
| `--line-strong` `#6f7c85` | input, select and textarea borders; the button, table and menu frame; the tag border | 4.29 | 3.99 | 3.56 | 3.81 | 3.69 | 1.82 | 3.0 | pass |
| `--focus` `#0c4e73` | the 3px focus ring, offset 2px | 8.92 | 8.31 | 7.42 | 7.93 | 7.68 | 3.78 | 3.0 | pass |
| `--line` `#d8dee3` | row separators and card edges, DECORATIVE only | 1.36 | - | - | - | - | - | none | not a component boundary; see 1.4.11 |

The lowest TEXT ratio on this ground is **5.06:1** (`--muted` on the sunken
surface, which is where a `.muted` paragraph wraps a `<code>` element), against
a 4.5:1 requirement. The lowest ELEMENT ratio is **3.56:1** (`--line-strong` on
the sunken surface), against 3:1. Part 18 added five surfaces and neither floor
moved; the table below shows why.

Part 21 added two further GROUNDS, each with its own surfaces, inks and phase
family, and part 23 re-anchored the machine one on the One Dark Pro editor
theme. This table is the citizen ground and is unchanged by either part; the
two others have their own tables in "The three phase grounds" below, and both
clear the same floors - **4.78:1** and 4.99:1 for text, **3.22:1** and 3.06:1
for elements. The machine ground now carries the tightest text margin of the
three, which is what a low-glare theme costs and is stated rather than
smoothed over.

### The part-18 surfaces

Part 18 changed no hue in the palette and added five surfaces: a wash at the
top of the page, a pale member of the blue family for large zones, and the one
dark band in the project with its own ink and link colour. They are listed
separately rather than as five more columns on the matrix above, for the reason
that matrix already gives: a pair that does not occur on a rendered page is not
listed, and most of these tokens meet most of these surfaces nowhere.

Surfaces: `--canvas-top` `#e2eef8` (the top of the page wash, which is what the
page head, the hero lead, the back-links and the call-to-action row sit on),
`--tint-brand-soft` `#f2f8fd` (a tinted section, a statistic tile's gradient
start, a gate row, a hovered table row, a `legend`, the empty-state disc), and
`--band` `#0c3a56` (the closing footer).

| Foreground | Where it is used on the new surface | vs `--canvas-top` | vs `--tint-brand-soft` | vs `--band` | Requirement | Verdict |
|---|---|---|---|---|---|---|
| `--ink` `#222222` | page and card text, table cells, a statistic's value | 13.50 | 14.87 | - | 4.5 | pass |
| `--ink-soft` `#3f4a52` | the hero lead, a definition value, the default badge | 7.70 | 8.48 | - | 4.5 | pass |
| `--muted` `#57646d` | the back-link row, a tile's label, `.stat-source` | 5.17 | 5.69 | - | 4.5 | pass |
| `--brand-ink` `#106393` | links, the empty-state glyph | 5.52 | 6.08 | - | 4.5 | pass |
| `--brand-ink-strong` `#0c4e73` | a `legend`, the brand badge, the hero caption chip | 7.57 | 8.34 | - | 4.5 | pass |
| `--ok` `#10683c` | the gate-passed verdict on a tinted row | 5.81 | 6.40 | - | 4.5 | pass |
| `--caution-text` `#7a3d00` | a caution badge that lands on a tinted row | 7.14 | 7.86 | - | 4.5 | pass |
| `--alarm-text` `#8f1010` | the gate-failed verdict on a tinted row | 7.92 | 8.72 | - | 4.5 | pass |
| `--band-ink` `#e8f1f7` | every sentence in the closing band | - | - | 10.46 | 4.5 | pass |
| `--band-link` `#a9d8f2` | every link in the closing band | - | - | 7.86 | 4.5 | pass |
| `#ffffff` | the band's wordmark, and its focus ring | - | - | 11.96 | 4.5 | pass |
| `--focus` `#0c4e73` | the 3px ring, offset 2px | 7.57 | 8.34 | **1.34 - NOT USED THERE** | 3.0 | pass; see 1.4.11 |
| `--line-strong` `#6f7c85` | a control boundary on a tinted zone | 3.64 | 4.01 | - | 3.0 | pass |
| `--brand` `#4db2ec` | the 3px accent rule at the top of the band and the header | 2.00 | 2.21 | 5.07 | 3.0 | element only; see below |

Two of these rows are the reason the arithmetic is done rather than eyeballed.

**The focus ring is white inside the closing band.** `--focus` is `#0c4e73` and
measures **1.34:1** against `--band` - a focus indicator on the one dark
surface in the project that nobody could see, on a band that carries links.
`.site-footer :focus-visible` overrides the ring to `#ffffff`, which measures
**11.96:1** there. This was found by computing the pair, not by looking at the
page, and then confirmed by walking the tab order of all nine pages in a
browser and reading the computed outline colour against the computed background
at every stop.

**The sky blue finally has a surface it could carry text on, and still does
not.** `--brand` measures 5.07:1 against the band, which is above the 4.5
requirement - the first surface in this project where the brand colour would
pass as text. It remains an element colour: it draws the 3px accent rule at the
top of the header and of the band and nothing else. The rule that `--brand`
never carries text is structural (a regex over every stylesheet, in
`tests/test_review_accessibility.py`), and an exception that held on exactly
one surface would be a rule nobody could apply from the token's name.

### The badges (part 18)

A badge is a bordered pill drawn around a sentence. Its text is measured
against its own fill, and its border against the same fill, because that is
where both are actually drawn.

| Badge | Text on its fill | Border on its fill | Verdict |
|---|---|---|---|
| default (`--ink-soft` on `--surface-alt`) | 8.46 | 3.99 (`--line-strong`) | pass |
| `.badge-ok` (`--ok` on `--tint-ok`) | 6.02 | 6.02 (`--ok`) | pass |
| `.badge-warn` (`--caution-text` on `--tint-caution`) | 7.70 | 4.36 (`--caution`) | pass |
| `.badge-brand` (`--brand-ink-strong` on `--tint-brand`) | 7.82 | 5.70 (`--brand-ink`) | pass |

There is no alarm badge and the omission is deliberate rather than pending.
Nothing in this UI is a small inline state meaning "something went wrong"; the
red family's three places - the refusal notice, the anomaly flag and the failed
gate - are all blocks. A fourth tone would be a rule with no user and a row of
measurements here for a pair that never renders.

A badge is not an interactive component, so 1.4.11's 3:1 is not strictly
engaged by its border; every one of them clears it anyway, and the text
requirement of 4.5 is what actually governs and is met with margin. What the
badges do NOT do is carry meaning: each one is drawn around the sentence that
was already there - "über Zielwert", "Tier 1 - klar und vollständig", "offen,
wartet auf menschliche Bestätigung" - so removing the tone removes a box and
no information (1.4.1).

### The gradients (part 18)

Three gradients ship. Two of them have text on top and are therefore only as
good as their lightest stop, which is why neither of them touches `--brand`.

| Gradient | Stops | Text on it | Worst stop | Verdict |
|---|---|---|---|---|
| `--grad-cta` | `#106393` to `#0c4e73` | `#ffffff` on the primary button, the call to action, the menu control, a completed step | 6.51 | pass |
| `--grad-mark` | `#106393` to `#0c4e73` | the wordmark's white glyph (decorative, `aria-hidden`) | 6.51 | pass |
| `--grad-panel` | `#f2f8fd` to `#ffffff` | card and tile text in `--ink`, `--ink-soft`, `--muted` | 14.87 / 8.48 / 5.69 | pass |
| `--grad-rule` | `#0c4e73` to `#4db2ec` to `#0c4e73` | none - a 3px hairline, decorative | - | carries no text |

### The three phase grounds (part 21)

Part 21 gives each phase of the demonstration its own ground. A ground is a
TOKEN SET applied by a class on `<body>`: it re-points the surface ladder, the
ink ladder, the phase family, the two gradients that carry them and the closing
band, and it declares nothing else. Every component above renders on all three
without a component-level override, which is why this section adds surfaces and
values rather than new components.

The citizen ground is `:root` and is the table at the top of this document,
unchanged - `/`, `/demo/antrag`, `/demo/rundgang`, `/hinweise` and `/inbox`
carry no class at all. The two below are new.

Nothing here is a reader preference. There is no `prefers-color-scheme` switch;
a ground says which phase a page belongs to and renders the same way for
everyone.

#### The machine ground: `/demo/case/{id}/pipeline` and `/metrics`

Re-anchored in part 23 on the One Dark Pro editor theme. The pages this ground
paints are the ones that show what the machine did inside, and the surface a
reader already associates with a machine at work is an editor, so the ladder is
that editor's own chrome. Nothing about the MECHANISM changed - the light ladder
is still INVERTED, `--canvas` is still the darkest value, the surfaces still
step UP from it so a card sits above its canvas and a `<code>` token sits inside
its card, and no component gained an override.

Surfaces: `--canvas` `#1b1d23` (the theme's peek/widget bed), `--canvas-top`
`#21252b` (its side bar, used here as the wash), `--surface` `#282c34` (the
editor background itself), `--surface-alt` `#2c313a` (its hovered list row) and
`--surface-sunken` `#31363f`. The six tints are `--tint-brand` `#2d3745`,
`--tint-brand-soft` `#2a313c`, `--tint-ok` `#2b3a31`, `--tint-alarm` `#3d2b2e`,
`--tint-caution` `#3d3324` and `--tint-sample` `#2a2f39`; the worst of them for
every row is `--tint-ok`, which is the lightest, so that is the column shown.

This ground is deliberately LOWER in contrast than the one it replaces - body
text went from 13.01:1 to 10.00:1 - because One Dark is a low-glare theme and a
page that reads like an editor should not shout. Every pair still clears its
floor and the margins are the tightest of the three grounds, which is stated
rather than hidden: 4.78:1 against the citizen ground's 5.06 and the caseworker
ground's 4.99.

The foreground family is anchored on `#abb2bf`, One Dark's own editor
foreground. It sits in the MIDDLE of the ink ladder rather than at the top of
it, because in a syntax-coloured buffer that colour is the calm one - what
plain identifiers and punctuation get - so the ladder steps up to a lighter
member for headings and body text and down to a dimmer one for labels.

| Foreground | Where it is used | vs `--canvas` | vs `--canvas-top` | vs `--surface` | vs `--surface-alt` | vs `--surface-sunken` | worst tint | Requirement | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| `--ink` `#d7dae0` | body text, headings, table cells, a statistic's value | 12.03 | 10.99 | 10.00 | 9.33 | 8.67 | 8.56 | 4.5 | pass |
| `--ink-soft` `#abb2bf` | the page lead, a definition value, the default badge, the step circle | 7.90 | 7.22 | 6.57 | 6.13 | 5.69 | 5.62 | 4.5 | pass |
| `--muted` `#9ba3b2` | help text, `.muted`, table captions, a tile's label, the wordmark subtitle | 6.64 | 6.07 | 5.52 | 5.15 | **4.78** | 4.72 | 4.5 | pass - the lowest text ratio on this ground |
| `--brand-ink` `#79bbf2` | links, the empty-state glyph, a focused field's edge | 8.18 | 7.48 | 6.80 | 6.35 | 5.90 | 5.82 | 4.5 | pass |
| `--brand-ink-strong` `#a6d3f7` | hovered links, a `legend`, the brand badge | 10.65 | 9.74 | 8.85 | 8.26 | 7.68 | 7.58 | 4.5 | pass |
| `--ok` `#98c379` | the gate-passed verdict, the ok note | 8.36 | 7.64 | 6.94 | 6.48 | 6.02 | 5.94 | 4.5 | pass |
| `--alarm-text` `#ef9a9f` | the gate-failed verdict, the refusal block's prose. The theme's red LIFTED along its own hue, because the red itself cannot carry a word here | 7.86 | 7.18 | 6.53 | 6.09 | 5.66 | 5.59 | 4.5 | pass |
| `--caution-text` `#e5c07b` | the caution badge's words. One Dark's yellow, which is the family the theme itself would use | 9.75 | 8.91 | 8.10 | 7.56 | 7.03 | 6.94 | 4.5 | pass |
| `--warn` `#e5c07b` | reserved for an advisory state, still unused | 9.75 | 8.91 | 8.10 | 7.56 | 7.03 | 6.94 | 4.5 | pass |
| **`--brand` `#61afef`** | **element colour ONLY**: card and stage edges, the sample rule, the header rule. It would pass as text on every surface here and still may not carry any - the rule is structural | 7.13 | 6.51 | 5.92 | 5.53 | 5.14 | 5.07 | 3.0 | pass as an element; the rule is structural, not arithmetic |
| **`--alarm` `#e06c75`** | **element colour ONLY**: the refusal edge, the anomaly flag, the failed-gate frame | 5.27 | 4.82 | 4.38 | 4.09 | 3.80 | 3.75 | 3.0 | pass as an element; **below 4.5 on four of six surfaces, which is exactly why the text weight above exists** |
| **`--caution` `#d19a66`** | **element colour ONLY**: the caution badge's edge | 6.84 | 6.25 | 5.68 | 5.30 | 4.93 | 4.87 | 3.0 | pass as an element |
| `--line-strong` `#7a8496` | every control boundary: inputs, selects, the table frame, the tag and badge border | 4.47 | 4.08 | 3.71 | 3.46 | **3.22** | 3.18 | 3.0 | pass - the lowest element ratio on this ground |
| `--focus` `#a6d3f7` | the 3px focus ring, offset 2px | 10.65 | 9.74 | 8.85 | 8.26 | 7.68 | 7.58 | 3.0 | pass |
| `--line` `#3a404b` | row separators and card edges, DECORATIVE only | 1.62 | 1.48 | 1.34 | 1.25 | 1.16 | 1.15 | none | not a component boundary; see 1.4.11 |

The band on this ground is `--band` `#3e4451` - the theme's own selection
colour, and the lightest neutral it paints. On a light page the closing band is
the darkest thing on the screen (10.63:1 against the canvas); on a dark one a
navy stops being a band at all, so it goes LIGHTER than the page instead, which
keeps the principle that the band is the surface differing most from the page it
ends, at 1.73:1 against the canvas. Its ink measures 6.97:1, its link 6.18:1 and
its white focus ring 9.77:1.

`--grad-cta` runs `#a6d3f7` to `#79bbf2` here and carries `--cta-ink` `#1b1d23`
at 10.65:1 and 8.18:1 - the pair the phase-3 button on the pipeline page uses
since part 23, where the hand-off stopped being a link inside a panel.
`--grad-panel` runs `#2c313a` to `#282c34` and carries `--ink` at 9.33:1 and
10.00:1.

##### The syntax palette (part 23)

The working copy on the pipeline page is painted the way an editor paints a
buffer: the dotted path, the separator, the value the machine holds, and the
sealed placeholder that replaced an identity value. Five tokens carry it, they
are One Dark's own accents, and **every one of them carries characters** - so
every one is held to the 4.5 a word needs rather than to the 3:1 an edge gets.
Two beds are possible: a block is `--surface-alt` and an inline `<code>` is
`--surface-sunken`, so both are measured and neither is assumed.

The two accents that could not reach the floor at their published value are
LIFTED along their own hue rather than used below it - the same discipline the
sky blue, the red and the amber have been held to since part 16. On the citizen
and caseworker grounds all five resolve to that page's own ink, so a machine
block that ever rendered on one of them is plain monospaced text rather than a
palette measured against a surface it was never computed for.

| Token | What it paints | One Dark's own value | vs `--surface` | vs `--surface-alt` (a block) | vs `--surface-sunken` (a chip) | Requirement | Verdict |
|---|---|---|---|---|---|---|---|
| `--code-ink` `#56b6c2` | an inline `<code>`: a rule id, a part id, a case id, a configuration version | the cyan, verbatim | 5.91 | 5.52 | 5.12 | 4.5 | pass |
| `--code-key` `#e2858d` | the dotted path left of the `=` | `#e06c75` (variables and properties), lifted - it measures 4.09:1 on a block | 5.32 | 4.96 | **4.61** | 4.5 | pass - the lowest ratio in the palette |
| `--code-value` `#98c379` | what the machine holds right of it | the green, verbatim | 6.94 | 6.48 | 6.02 | 4.5 | pass |
| `--code-punct` `#9ba3b2` | the `=` itself | the calm foreground, as in the editor | 5.52 | 5.15 | 4.78 | 4.5 | pass |
| `--code-seal` `#cd8ee3` | a `[[PII\|KIND\|token]]` placeholder, so the seal pops | `#c678dd` (keywords and storage), lifted - it measures 4.44:1 on a block | 5.72 | 5.33 | 4.96 | 4.5 | pass |

**What is deliberately NOT coloured, on any ground**: prose paragraphs, tables,
headings, the Anhoerung letter the counterparty receives, and the free text a
visitor typed. Colour here means "this is machine text"; spending it on a
sentence somebody wrote would say the opposite of what the page is for. The
line is drawn on the SHAPE the demo store already recorded for each working-copy
part rather than on a guess about its content, and the letter is set in the body
face on the same dark ground, where it stays a document.

#### The caseworker ground: `/review`, `/review/queue/*`, `/review/case/*`

Warm amber paper: a cream canvas, near-white cards, and a family of
amber-browns for everything a caseworker reads. Surfaces: `--canvas` `#f6ebd6`,
`--canvas-top` `#fdf3dc`, `--surface` `#fffdf9`, `--surface-alt` `#fbf4e5`,
`--surface-sunken` `#f0e5cd`. `--tint-brand` `#fbe8c1` and `--tint-brand-soft`
`#fdf5e3` move with the phase family and `--tint-sample` becomes a warm grey
`#f1efe7`; the three state tints are UNCHANGED, which is the point rather than
an omission - this ground is light, so the light values still measure what they
measured, and a state that looked one way on the citizen pages looks the same
way here.

| Foreground | Where it is used | vs `--canvas` | vs `--canvas-top` | vs `--surface` | vs `--surface-alt` | vs `--surface-sunken` | worst tint | Requirement | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| `--ink` `#241e14` | body text, headings, table cells, a statistic's value | 13.99 | 14.97 | 16.27 | 15.09 | 13.22 | 13.70 | 4.5 | pass |
| `--ink-soft` `#4a4032` | the page lead, the case-id chip, the default badge | 8.58 | 9.19 | 9.98 | 9.26 | 8.11 | 8.41 | 4.5 | pass |
| `--muted` `#6b5f4c` | help text, `.muted`, the back-link row, table captions, the wordmark subtitle | **5.28** | 5.65 | 6.14 | 5.70 | 4.99 | 5.17 | 4.5 | pass - 4.99 is the lowest pair the token set produces, and the lowest one that actually RENDERS is 5.28 |
| `--brand-ink` `#8a4f0a` (`--amber-ink`) | links, the nav pills, a focused field's edge | 5.55 | 5.94 | 6.45 | 5.99 | 5.24 | 5.44 | 4.5 | pass |
| `--brand-ink-strong` `#5f3705` (`--amber-ink-strong`) | hovered links, a `legend`, the brand badge, the button fill | 8.73 | 9.35 | 10.16 | 9.42 | 8.25 | 8.56 | 4.5 | pass |
| `--ok` `#10683c` | the ok note, a state badge - UNCHANGED | 5.79 | 6.20 | 6.74 | 6.25 | 5.48 | 5.68 | 4.5 | pass |
| `--alarm-text` `#8f1010` | the anomaly flag's prose, an invalid field's label - UNCHANGED | 7.90 | 8.46 | 9.19 | 8.52 | 7.47 | 7.74 | 4.5 | pass |
| `--caution-text` `#7a3d00` | the caution badge's words - UNCHANGED | 7.12 | 7.63 | 8.28 | 7.68 | 6.73 | 6.98 | 4.5 | pass |
| `--warn` `#7a4a06` | reserved for an advisory state, still unused | 6.33 | 6.77 | 7.36 | 6.82 | 5.98 | 6.20 | 4.5 | pass |
| **`--brand` `#b07700`** (`--amber`) | **element colour ONLY**: card and stage edges, the sample rule, the gate row's rule. **This is the yellow, and the arithmetic is why it may not carry text** | 3.24 | 3.47 | 3.77 | 3.50 | **3.06** | 3.18 | 3.0 | pass as an element; **CANNOT reach 4.5 on any surface on this ground, so every word in the family uses `--amber-ink`** |
| **`--alarm` `#dc0000`** | **element colour ONLY** - UNCHANGED | 4.40 | 4.71 | 5.11 | 4.74 | 4.15 | 4.31 | 3.0 | pass as an element |
| **`--caution` `#b45a00`** | **element colour ONLY** - UNCHANGED | 4.04 | 4.32 | 4.69 | 4.35 | 3.81 | 3.96 | 3.0 | pass as an element |
| `--line-strong` `#7e7053` | every control boundary: inputs, selects, the table frame, the tag and badge border | 4.10 | 4.40 | 4.77 | 4.43 | 3.88 | 4.02 | 3.0 | pass |
| `--focus` `#5f3705` | the 3px focus ring, offset 2px | 8.73 | 9.35 | 10.16 | 9.42 | 8.25 | 8.56 | 3.0 | pass |
| `--line` `#e6d8bc` | row separators and card edges, DECORATIVE only | 1.19 | 1.28 | 1.39 | 1.29 | 1.13 | 1.17 | none | not a component boundary; see 1.4.11 |

The band here is `--band` `#3d2a12`, a warm brown at 11.55:1 against the
canvas - the same object as the light ground's navy, in this world's colour,
because a navy band under amber paper reads as a footer borrowed from another
site. Its ink measures 11.64:1, its link `#f3cf95` 9.21:1 and its white focus
ring 13.65:1. `--band-link` is an amber that would fail on paper and passes
here, which is the same licence the light ground's `#a9d8f2` has always had:
the band is its own surface with its own measured ink.

`--grad-cta` runs `#8a4f0a` to `#5f3705` and carries white at 6.56:1 and
10.32:1. `--grad-panel` runs `#fdf5e3` to `#fffdf9` and carries `--ink` at
15.22:1 and 16.27:1.

#### The three pairs a fill broke, and the one a ground cannot reach

**The current step's number.** `.phase-current .phase-mark` fills a disc with
`--brand` and writes the number on it in `--ink`. That is 6.74:1 on the citizen
ground, where the sky blue is a light fill under a near-black ink, and
**1.69:1** on the machine ground, where `--brand` is the theme's blue and
`--ink` has gone light. It is the one number on the strip that says which phase
a visitor is in, on the one page in the project where phase 2 is the current
phase. `.ground-machine .phase-current .phase-mark` sets it to `--cta-ink`,
which measures **7.13:1** there.

**The call-to-action card's focus ring (part 23).** The first card in the
landing grid is painted with `--grad-cta` and reads its label out of
`--cta-ink`, which is the same arithmetic every button on the site already
ships: white on the gradient's pale stop is 6.51:1 and on its deep stop 8.92:1.
The ring is the pair that broke. `:focus-visible` is drawn at `outline-offset:
2px`, so on this card it lands ON the blue rather than beside it, and `--focus`
`#0c4e73` measures **1.37:1** against `#106393` - no ring at all on the one call
to action the page makes, for the reader most dependent on seeing it.
`.card-cta :focus-visible` sets it to `--cta-ink`, which measures **6.51:1**.
This is the closing band's failure from part 18, in the same shape, found the
same way: by computing the pair rather than by looking at the page.

On the caseworker ground the same pair would measure **4.32:1**, under the 4.5
a word needs - and it is not overridden, because the component cannot render
there. The step indicator is included by `demo_base.html` alone and the
caseworker screens are rendered by `review_base.html`: the caseworker UI never
learns that a tour is running (part 13, ruling 5). That absence is asserted by
a test rather than left as an assumption, and the test names this row when it
fails. This is the same treatment the `--focus` ring on the band gets above: a
pair that does not render is written down with the reason, not with a rule that
has no user.

**The primary button.** `--grad-cta` runs `#106393` to `#0c4e73` on the light
grounds and measures **2.30:1** and **1.68:1** against a dark card - a control
that is not distinguishable from its surroundings, which is what 1.4.11
forbids. No dark blue fixes it: white text needs the fill dark and the dark
card needs the fill light, and the two requirements do not overlap. So on the
machine ground the fill goes light and its ink goes dark with it, through one
token (`--cta-ink`) rather than six component overrides.

Three colours in this palette cannot carry text and the table says so twice:
once in their row and once in their name. `--brand` is the reference sky blue
and it is the identity of the site - which is exactly why the temptation to put
a link in it had to be closed structurally rather than by discipline.
`tests/test_review_accessibility.py::test_the_element_colours_that_may_not_carry_text_never_do`
greps every stylesheet, with the regex anchored so that `border-left-color:
var(--brand)` - the correct use - does not read as a false positive, and it
ends at the closing paren so that `color: var(--caution-text)` is read as the
text sibling it is. The test checks a NAMED SET rather than two hard-coded
tokens, so a fourth element colour joins the rule by being added to the set.

The relevant number for the caution family is the one against its own tint,
because that is the only place either token appears: the ribbon's rule
measures **4.36:1** against the `#fff3e6` band it sits under (requirement 3.0)
and its link and icon measure **7.70:1** on that band (requirement 4.5). The
ribbon's bold sentence stays `--ink`, at **14.56:1** on the same band.

Two caveats that belong next to the numbers rather than under them. This is
arithmetic on hex values, not a measurement of a rendered display: subpixel
rendering, a projector, a cheap panel and a bright room all change what a
person actually sees. And a passing ratio says nothing about whether the
colours communicate - that is 1.4.1, which is answered separately and answered
by requiring words next to every tone.

## Perceivable

| Criterion | Verdict | Note |
|---|---|---|
| 1.1.1 Non-text content | automated | **Part 16 added non-text content for the first time, and both kinds carry their meaning in words anyway.** (1) An inline-SVG icon set: fifteen glyphs on a 24-unit grid in `currentColor`, every one `aria-hidden="true"` with `focusable="false"` (not redundant - older engines put an `<svg>` into the tab order) and every one sitting next to a word that says the same thing, so removing every icon would remove no information. An unknown icon name renders NOTHING rather than a placeholder box. (2) The landing hero, one inline SVG with `role="img"`, a `<title>` and a `<desc>`, so assistive technology reads one sentence about the picture instead of walking a drawing - and its five stage names are repeated as the bold prefix of five real text captions in the document, so nothing is available only inside the image. Still nothing is fetched from anywhere, so there is still no remote image to fail to load. |
| 1.3.1 Info and relationships | automated | Every table has a `<caption>` and every `<th>` a `scope`; headings are `<h1>`/`<h2>`/`<h3>` with no level skipped; the case view uses `<dl>` for field/value pairs; landmarks are `<header>`, `<nav>`, `<main>`, `<footer>`, `<section aria-labelledby=...>`. |
| 1.3.2 Meaningful sequence | reviewed | Source order is reading order everywhere. The layout uses flexbox for the nav, the picker and the before/after panels and grid for the definition lists and the persona cards; none of them reorders content, and no `order` or `grid-row` declaration exists in any stylesheet. |
| 1.3.3 Sensory characteristics | reviewed | No instruction refers to shape, size or position. |
| 1.3.4 Orientation | reviewed | No orientation lock; the layout is a single column with `max-width`. |
| 1.3.5 Identify input purpose | open | The inputs collect no personal data about the USER (unit, reason, note), so the WCAG input-purpose list has nothing to map to. Stated rather than claimed as passed. |
| 1.4.1 Use of colour | automated | Every queue flag carries its meaning in a `<strong>` label plus a sentence; the tone class only changes a border and a tint. The test asserts each flag block has a label and more than 30 characters of prose. Part 16 added two more places a colour could have been the only carrier and neither is: the step indicator marks the current circle with `aria-current="step"` plus an offscreen "Phase 2 - aktuelle Phase", and a completed circle carries a checkmark GLYPH plus an offscreen "abgeschlossen", so a reader who gets neither the fill nor the icon still gets the state in words. The ribbon says "Demo - synthetische Daten" in text; the tone repeats it and carries nothing on its own - which is why part 17 could change that tone from red to a caution orange without touching a word of it. Part 18 added the badge, which is the same rule made into a component: a badge is a bordered pill drawn AROUND the sentence that was already in the cell - "über Zielwert", "im Zielwert", "Tier 1 - klar und vollstaendig", "offen, wartet auf menschliche Bestaetigung" - so a reader who gets no colour at all loses a box and no information. The gate verdict on `/metrics` gained a glyph for the same reason and under the same rule: it is `aria-hidden`, it repeats the verdict the sentence beside it states in words, and it is a second carrier rather than the only one. |
| 1.4.3 Contrast (minimum) | automated, with measured ratios | The palette is in `ui/static/system.css` as custom properties, and every pair that actually ships was computed before it was used. The full matrix is the section "Measured contrast ratios" below. On the citizen ground the lowest text ratio anywhere is **5.06:1** (`--muted` on the sunken surface), against a 4.5:1 requirement. (This row said 6.19:1 until part 17: a part-15 number that the part-16 re-measurement replaced in the table below without updating the sentence up here. Corrected against the table, which is the computed one.) **Part 21 added two more grounds and the verdict moves from `reviewed` to `automated`, which is the only verdict change this row has ever had.** The ratios used to be arithmetic somebody did once and wrote down here; `tests/test_review_accessibility.py` now parses the three token blocks out of the stylesheet, resolves the `var()` chains, and computes every pair the components produce - inks on surfaces and tints, links, state colours on their own tints, the band's ink, and the label on BOTH ends of the button gradient, since a gradient under a label is only as good as its lightest stop - on all three grounds against the same formula. The lowest text ratio is 5.06:1 on the citizen ground, **4.78:1** on the machine ground and **4.99:1** on the caseworker ground; the lowest that actually renders on a caseworker page is 5.28:1. Two pairs failed this criterion when the grounds were computed and both were fixed before shipping - the current step's number at 1.69:1 and, under 1.4.11, the primary button; see "The three pairs a fill broke". **Part 23 re-anchored the machine ground on the One Dark Pro editor theme and added a five-token syntax palette to it.** The pairs were recomputed rather than carried over, the ground is deliberately quieter (body text 13.01:1 to 10.00:1) and therefore now holds the tightest margin of the three, and the palette is held to this criterion and not to 1.4.11 because every one of its tokens carries characters: two One Dark accents that could not reach 4.5 at their published value were lifted along their own hue rather than shipped below the floor. **Still measured by calculation and not with a photometer on a real display**, which is what keeps the caveat at the bottom of this document and the open item at the end. |
| 1.4.4 Resize text | automated (partly) | All sizes are `rem`, `em`, `ch` or unitless; the type scale is eight `rem` tokens. A test asserts that no stylesheet in `ui/static` contains a `px` font size or a fixed pixel length of three digits or more - the pill radius is `62em` rather than the conventional `999px` for exactly that reason. **Part 18 added three fluid sizes and they are the one place this criterion could have been lost.** A font size given in `vw` alone ignores the reader's font setting entirely and fails outright. Each of `--text-title`, `--text-hero` and `--text-stat` is a `clamp()` whose preferred value carries a `rem` term as well as a `vw` term - `clamp(1.875rem, 1.55rem + 1.05vw, 2.75rem)` - so the whole expression moves when a reader enlarges text, and the `vw` term only decides how much of the range a given viewport takes. The floor of every ramp is a size this project already shipped. Whether the pages are USABLE at 200 percent is still a browser measurement nobody has made. |
| 1.4.5 Images of text | automated | There are no images. |
| 1.4.10 Reflow | automated (static) plus a measured browser pass, every page | **Closed for the caseworker pages in part 15, and closed by fixing the cause rather than the symptom.** The rules that make reflow possible used to live in `demo.css`, which only the three citizen-facing pages loaded - which is precisely why this row was open on the others. They are in the design system now, so every page gets them: every wide table sits in its own `overflow-x: auto` container so the container scrolls and the document body never does, `dl` drops from two columns to one below 40rem (the two-column definition list is what actually overflows at 320 px), `overflow-wrap: break-word` on `body` keeps a case id or a placeholder token from pushing the page wider, and no stylesheet carries a fixed pixel length. Asserted per page in `tests/test_review_accessibility.py` (`/review`, both queue variants, the case view, `/metrics`, `/inbox`) and in `tests/test_demo_journey.py` (`/demo/rundgang`, `/demo/antrag`, the pipeline view). **Part 17 added the measurement the previous sentence used to say did not exist**: every page was rendered at 320 and 390 CSS px and `scrollWidth` compared against the viewport. Three pages failed and were fixed - see the part-17 note at the top for the three causes, none of which a markup check could have seen. All ten now measure exactly the viewport width at both sizes. **Re-measured after the part-18 redesign: all ten pages still measure exactly 320 at 320 and exactly 390 at 390.** Part 18 added one thing that can clip content and it is bounded on purpose. `.scroll-x.is-tall` gives the 41-row queue table a maximum height so its own sticky header has a box to stick to; it applies only from 64rem up, so the single-column phone layout never gets an inner scroller competing with the reader's own scroll, and a `@media print` rule removes the cap so a printed queue carries all its rows rather than the 78vh of them that were in view. Every row in that box contains a link, so a keyboard reader reaches all of them by tabbing, and the box scrolls to follow the focus. What is still open is a real phone in a real hand: this is a headless engine at a set viewport, not a device test, and no person has read these pages on one. |
| 1.4.11 Non-text contrast | reviewed, with measured ratios | Two border weights exist so the floor cannot be missed by accident. `--line-strong` (`#6f7c85`) draws every control boundary - inputs, selects, the button and menu outline, the table frame - and is at or above **3.56:1** against every surface it is used on, white included (4.29:1). `--line` (`#d8dee3`, 1.36:1 on white) is decorative only: it separates rows inside a card and identifies no component. The focus ring is `--focus` (`#0c4e73`) at 3px with a 2px offset, at or above **7.42:1** on every surface. Two honest notes rather than one. The `.tag` badge's border sits below 3:1 against the brand tint when a tag is inside a selected persona card - a badge is not an interactive component and its text carries 13:1 or better, so 1.4.11 is not engaged, but it is stated rather than left to be found. And the current step's circle is a `--brand` fill inside a `--brand-ink` border: the border measures 6.51:1 against the card BEHIND it, which is the comparison this criterion asks for (is the component distinguishable from its surroundings), and only 2.76:1 against the fill it encloses, which is not - stated here because the second number is the one a reader computing from the table would find first. **Part 18 added a dark band and with it the one place the ring failed.** `--focus` measures 1.34:1 against `--band`, so `.site-footer :focus-visible` sets the ring to white, which measures 11.96:1 there; the finding came out of computing the pair, and the fix was then verified by walking the tab order of all nine pages in a browser and reading the computed outline colour against the computed background at every stop - 159 stops, all at or above 3:1. One further note that belongs next to that walk. `input[type="date"]` has a calendar button inside the engine's own shadow tree with its own tab stop and its own focus ring, which this project can neither restyle nor read; a first pass dimmed it with `opacity: 0.65` and thereby dimmed the ring the engine paints on it, which the walk caught. The rule now sets nothing but `cursor`. **Part 21 added two grounds, which is eleven new surfaces each, and the same two questions were asked of every one of them by arithmetic before anything was opened.** The control boundary holds: `--line-strong` is at or above 3.22:1 on the machine ground and 3.88:1 on the caseworker ground against every surface it is drawn on. The ring holds: at or above 7.58:1 and 8.25:1 respectively, on every surface AND every tint, and the band keeps its white ring on all three grounds because the band is dark on all three (9.77:1 and 13.65:1 on the new ones). One pair failed this criterion outright and is the second of the two the theming broke: `--grad-cta` is a dark gradient and measures **2.30:1 and 1.68:1 against a dark card**, so the primary button, the call to action and the menu control were not distinguishable from their surroundings on the machine ground. There is no dark blue that fixes it - white text needs the fill dark and a dark card needs the fill light - so the fill inverts and its ink inverts with it through one token. The `--brand` sky blue keeps the honest note it has always had: it draws card and stage edges and the accent rules, it is not a component boundary, and it measures 2.36:1 on the citizen ground, 5.92:1 on the machine ground and 3.77:1 as amber on the caseworker ground - the rule that it never carries text is structural rather than a consequence of where it happens to measure well. The tab walk was repeated over all eleven pages after the theming: **215 stops, none below 3:1 on any ground**, plus a sweep of every text-rendering element on every page against its effective background (5767 nodes, none below its floor). **Part 23 produced a third pair of this kind and found it the same way.** The landing page's call-to-action card is painted with the button's own fill, so the ring - drawn at `outline-offset: 2px` - lands on that fill and `--focus` measures **1.37:1** against it. `.card-cta :focus-visible` sets it to `--cta-ink` at 6.51:1. Both sweeps were run again on the re-anchored ground: 192 stops with no ring below 3:1 (lowest 6.51:1, which is that override), and 4877 text nodes with none below its floor. |
| 1.4.12 Text spacing | reviewed | No fixed heights, no `!important` on line-height. `body` carries `line-height: 1.65`, raised from 1.55 in part 17 to carry the wider measure; every heading keeps 1.2. |
| 1.4.13 Content on hover or focus | automated | Nothing appears on hover or focus; the test asserts no `onmouseover` anywhere. |

## Operable

| Criterion | Verdict | Note |
|---|---|---|
| 2.1.1 Keyboard | automated | Every control is a native `<a>`, `<button>`, `<select>` or `<input>`. The test asserts no `onclick`, no `href="#"` acting as a button and no `hx-get`/`hx-post` on a `<div>`. htmx is progressive enhancement only: with scripting off, forms post and links navigate. |
| 2.1.2 No keyboard trap | reviewed | No modal, no focus management script, no `tabindex` above 0 anywhere. |
| 2.1.4 Character key shortcuts | automated | There are none. |
| 2.2.1 Timing adjustable | reviewed | No timeout, no auto-refresh, no polling. The queue clocks are display-only and never expire a page. |
| 2.2.2 Pause, stop, hide | automated (structural) plus a browser check | **Engaged for the first time in part 16, and ANSWERED for the first time in part 17.** The part-16 control did not work: it sat inside a wrapper `<div>`, and since every pause rule is written with the general sibling combinator `~`, the checkbox reached nothing at all. The only assertion about it was that the stylesheet contained those rules, and it stayed green for the whole life of the defect - which is the lesson this row now carries: a rule that exists is not a rule that applies. The checkbox is a direct sibling of the drawing and the captions, a test parses the rendered document and asserts exactly that relationship in both languages, and the control was operated in a real browser with a mouse and with the keyboard. The landing hero animates on a 16-second loop that starts on its own and sits beside other content, which is exactly the three conditions of this criterion. The answer is a control ON THE PAGE - a labelled checkbox above the figure - rather than an operating-system preference, because `prefers-reduced-motion` is a setting a reader made somewhere else and this criterion asks for a mechanism here. It is CSS alone: the checkbox precedes everything it pauses and `:checked ~` reaches them, so it works with scripting off like everything else. It STOPS rather than freezes, and that is the decision worth stating: `animation-play-state: paused` would hold whatever frame was showing, which for four of the five captions is `opacity: 0` - a pause button that hides the text is not a pause button. The paused state is therefore the same still frame the reduced-motion answer produces, with every stage lit and all five captions stacked and readable. The test asserts the control, the absence of `animation-play-state`, and that the paused block says `animation: none` and `opacity: 1`. Every other page in the project still has nothing that moves, blinks or auto-updates. |
| 2.4.1 Bypass blocks | automated | A real skip link is the first focusable element on every page and targets `<main id="inhalt">`; the test asserts both. |
| 2.4.2 Page titled | automated | Every page sets a distinct `<title>` naming the queue or the case. |
| 2.4.3 Focus order | automated (partly) | DOM order is: skip link, nav, unit picker, main content, actions. The test pins the skip link's position; the rest is DOM order with no `tabindex` overrides, which is the only way to get it right. |
| 2.4.4 Link purpose (in context) | reviewed | Link text is the queue name or the case id; there is no "here" or "more". |
| 2.4.5 Multiple ways | reviewed | A case is reachable from its queue and from a direct URL; the nav is on every page. There is no search, which for a demo with one journal is defensible and for a pilot is not. |
| 2.4.6 Headings and labels | reviewed | Section headings name their content in German administrative vocabulary; labels name what is entered. Whether they are USEFUL to a caseworker is exactly what the pilot has to tell us. |
| 2.4.7 Focus visible | automated, plus a measured browser walk | `:focus-visible` in the design system sets a 3px outline in `--focus` with a 2px offset, which survives on top of every tinted surface (7.57:1 or better on the citizen ground) and is overridden to white inside the closing band, where `--focus` would measure 1.34:1 - see 1.4.11. The test sweeps EVERY stylesheet in `ui/static` for the two rules that switch an outline off, rather than checking two files named by hand, and additionally asserts the ring is restyled rather than merely present. **Part 18 added the measurement**: every tab stop on all nine pages was focused in a browser and its computed outline colour compared against the computed background behind it. **Part 21 made the ring's floor a test rather than a measurement somebody repeats**, because two new grounds move `--focus` twice and move every surface it can land on: the ring is computed against all eleven surfaces of all three grounds from the stylesheet, and the check is verified against the mistake it exists to catch (a dark ring on the dark ground fails at 1.95:1). The walk was repeated anyway, over all eleven pages: 215 stops, lowest ring 7.57:1 on the citizen ground, 8.71:1 on the machine ground and 8.73:1 on the caseworker ground. **Part 23 re-anchored the machine ground and painted one card in the button's own fill, which is where the ring failed next**: it is drawn at a 2px offset and therefore lands on that fill at 1.37:1, and is overridden to the button's ink at 6.51:1 - the same shape of finding as the band, found the same way. The walk after that change is 192 stops over the eleven pages, lowest ring 6.51:1 (that override), 7.62:1 on the machine ground and 8.73:1 on the caseworker ground. Two stops report no ring at all and both are the `input[type="date"]` calendar button described in 1.4.11 - the engine's own shadow-tree control, whose ring this project can neither draw nor read, on a page whose ground did not change. |
| 2.5.1 Pointer gestures | automated | No gesture; every action is a click or a keypress on a native control. |
| 2.5.2 Pointer cancellation | reviewed | Native buttons only; the browser's own down-then-up semantics apply. |
| 2.5.3 Label in name | reviewed | The visible label text IS the accessible name: no `aria-label` overrides a visible string anywhere. |
| 2.5.4 Motion actuation | automated | There is none. |

## Understandable

| Criterion | Verdict | Note |
|---|---|---|
| 3.1.1 Language of page | automated | `<html lang="de">`, asserted per page. |
| 3.1.2 Language of parts | reviewed | **Reshaped in part 16.** A page is now in ONE language and declares it on `<html>`; the inline English asides that used to ride under every German paragraph are gone, replaced by the header toggle. Three places where two languages still meet all carry `lang` on the element: the English note on the German caseworker screens (`<p lang="en">`), the site header on those screens when the toggle is set to English (`<header lang="en">` inside `<html lang="de">`), and the German message bodies in an English inbox (`<pre lang="de">`, with a sentence saying why they did not switch). The few English tokens that are not prose are identifiers (`nachforderung`, `prepared_decision`, `fit_connect`) rendered as code. |
| 3.2.1 On focus | reviewed | Nothing happens on focus. |
| 3.2.2 On input | reviewed | No `onchange` submits a form; the unit picker has an explicit submit button. |
| 3.2.3 Consistent navigation | automated | **One header for every page since part 16**: the same partial carries the wordmark, the language toggle and the menu on the caseworker screens, the citizen pages, `/metrics` and `/inbox`, so the navigation is identical everywhere rather than merely similar. The menu is a native `<details>`/`<summary>`: focusable by construction, toggled by Enter and Space, its state exposed by the element rather than by an `aria-expanded` we maintain. The page's own title, the step indicator and the unit picker moved into `.page-head` inside `<main>`, which is also what keeps the `h1` the first heading. The skip link is still the first focusable element on every page, asserted per page. |
| 3.2.4 Consistent identification | reviewed | The same action has the same label on every page. |
| 3.3.1 Error identification | reviewed | A refused action redirects back to the case view with the reason in a `notice` inside an `aria-live="polite"` region, in words. |
| 3.3.2 Labels or instructions | automated | Every non-hidden control has a `<label for>`; the test fails on a missing one. The reason field carries an `aria-describedby` help text saying what the reason is used for. |
| 3.3.3 Error suggestion | reviewed | Each refusal names what to do instead ("a correction after confirmation is a new decision"). |
| 3.3.4 Error prevention (legal, financial, data) | reviewed | **This is the criterion that matters most here**, because confirming a Nachforderung stamps a legal deadline. The confirm form states the deadline arithmetic, names the Land whose holiday set will be used and how many holidays are configured, before the button. The action is reversible in the only way an append-only journal can be reversible: a new event, never an edit - and the page says so. What is NOT there is a confirmation dialog; a second click that everyone learns to make is not error prevention. |

## Robust

| Criterion | Verdict | Note |
|---|---|---|
| 4.1.2 Name, role, value | automated (partly) | Native elements throughout, so role and value come from HTML; names are asserted by the label test. No custom widget exists, which is why there is no ARIA to get wrong. |
| 4.1.3 Status messages | reviewed | The action result renders inside `aria-live="polite"`. Whether a screen reader announces it as intended is not something a static test can answer. |

## The citizen-facing pages (part 13, extended in part 16)

`/demo/antrag` and `/demo/case/{id}/pipeline`, joined in part 16 by the landing
page `/` and the disclaimer page `/hinweise`. Every criterion above applies to
them and is met the same way, through the same shared stylesheet and the same
markup discipline; the rows below are the ones whose ANSWER is different, plus
the ones that only exist because these pages have a form and an audience that
was not trained on anything. The mechanical bar is asserted for all five in
`tests/test_demo_journey.py`, which parametrises over them.

| Criterion | Verdict | Note |
|---|---|---|
| 1.3.1 Info and relationships | automated | Same bar as the caseworker pages, asserted separately for these two: one `h1`, no skipped heading level, `<header>`/`<nav>`/`<main>`/`<footer>`, a `<caption>` on every table and no `<th>` without a `scope`. The step indicator is an ordered list with `aria-current="step"`, because "which phase am I in" is a list position. |
| 1.3.5 Identify input purpose | open, and more open here than on the caseworker pages | The intake form collects fields that ARE on the WCAG input-purpose list (since part 16 the name is two boxes, which maps to `family-name` and `given-name`; plus address and birth date) and carries no `autocomplete` attributes. That is a deliberate omission with an uncomfortable trade-off: `autocomplete="name"` on a public demo form invites a browser to fill in the visitor's REAL name, on the one instance whose entire posture is that it must never receive real personal data. The right answer for a production intake is the opposite of the right answer here, and neither this file nor the code should pretend the question is settled. |
| 1.4.1 Use of colour | automated | The highlighted queue row says "Ihr Vorgang" in words plus an offscreen sentence; the tint repeats it. The current step in the indicator is `aria-current` plus a border plus offscreen text. A placeholder token is bordered and monospaced, not coloured. |
| 1.4.10 Reflow | automated (static) | See the row above: scroll containers, a one-column `dl` below 40rem, no fixed pixel width, and the viewport meta. Not a browser measurement. |
| 2.4.2 Page titled | automated | Distinct titles naming the phase. |
| 3.3.2 Labels or instructions | automated | Every input and select has a `<label for>`; fields with a help sentence carry `aria-describedby`. A field that will be sealed says which KIND it becomes, next to its label, before it is submitted - once per row rather than once per input, because four identical tags beside four address boxes crowd the label they belong to. **Part 16 gave each new control its own instruction**: a `type="date"` field carries a format hint for browsers that render it as a text box, and a `<select>` says that its options come from the procedure configuration and fill the field exactly as typing would. **Part 20 states the required rule in words** above the fields ("everything already filled in here is a required field"), because the rule is uniform and an asterisk repeated on eleven controls says less than one sentence does. |
| 3.3.1 Error identification | reviewed | Two error paths now, and neither carries its meaning in colour alone. **Client-side (part 20):** every prefilled field carries the HTML `required` attribute, so the browser blocks the submission and announces its own message on the field it stopped at. The page adds a pre-rendered sentence per field ("Diese Angabe fehlt...") that CSS reveals on `:user-invalid`, plus a red edge and a red label. `:user-invalid` rather than `:invalid` is deliberate: the second would mark a form nobody had touched yet. No JavaScript is involved and the native message is not suppressed. **Server-side (part 13):** a refused submission re-renders the page with the refusal in a `role="alert"` block at the top, the visitor's edits preserved, and the findings as a list of kinds and places. It never echoes the value that caused the refusal. |
| 3.3.3 Error suggestion | reviewed | The client-side sentence says what is wrong AND what follows from it ("this answer is missing; the application will not be sent without it"), next to the field it is about. The server-side refusal names the kind of content and where it was found. Neither suggests a value, which on this surface would mean a page guessing at a person's data. |
| 3.1.5 Reading level | open | Both pages are written in plain German and avoid jargon where the domain allows it, but no Leichte-Sprache or Einfache-Sprache version exists and no readability measurement has been made. For a citizen-facing public-administration surface this is a real gap, not a nice-to-have; it belongs with the external audit. |
| 2.2.1 Timing adjustable | reviewed, with one honest caveat | Nothing on either page times out, auto-refreshes or polls. The demo store behind the pipeline view DOES expire after 30 minutes, and the page then renders a sentence saying the working copy is no longer held while everything from the journal stays readable. No interaction is lost and no input has to be re-entered, so this is not a 2.2.1 time limit; it is stated here because a reader deserves to know a timer exists. |

## The tour page (part 15)

`/demo/rundgang`, the guided walk a first-time visitor is handed. It is built
from the same base template and the same design system as the other two citizen
pages, so every row above applies unchanged and is met the same way; the rows
below are the ones this page answers differently, plus the one thing it does
that nothing else in the project does.

| Criterion | Verdict | Note |
|---|---|---|
| 1.3.1 Info and relationships | automated | Same bar as the other citizen pages, asserted in `tests/test_demo_journey.py`: one `h1`, no skipped heading level, `lang="de"`, the skip link ahead of the header, all four landmarks. The six steps are six `<section>` elements in source order, each labelled by its own heading; the table of contents at the top is a `<nav>` with an ordered list, because "step 3 of 6" is a list position. |
| 3.1.2 Language of parts | reviewed | **Part 16 removed the asides this row used to be about.** The tour carried a short English paragraph under every German one; it is now one page in one language, switched in the header, with `<html lang>` following the choice. That is better for both readers and it removes the mixed-language document entirely: a page that carries both at once is longer than either, and the second one always reads as a summary of the first. `tests/test_i18n.py` asserts that no `class="aside"` and no "In English" survives anywhere, and that the six step titles appear in the requested language. |
| 2.4.1 Bypass blocks | automated | The skip link, plus the in-page table of contents: six anchors to the six steps, so a keyboard user does not tab through step 2 to reach step 5. No anchor is a bare `#`, which the test asserts. |
| 2.4.4 Link purpose (in context) | reviewed | Every link says where it goes in words - "Zum Rundgang", "Antrag stellen (Phase 1)", "Zum Postfach (nur Ansicht)". There is no "hier" and no "mehr". The call-to-action links are LINKS styled as buttons, not buttons pretending to navigate: they work with scripting off and appear once each in the tab order. |
| 1.4.1 Use of colour | automated | The step number is a bordered, monospaced-weight chip marked `aria-hidden`, because the heading next to it already names the step; the intake-posture note says "gesperrt" or invites a submission in words, and the tone only repeats it. |
| 3.3.x Errors | not applicable | The page has no form and no control that can fail. |
| 3.1.5 Reading level | open | Same gap as the other citizen pages and arguably sharper here, because this page is written FOR somebody unfamiliar: it is plain German and avoids jargon where the domain allows, but there is no Leichte-Sprache or Einfache-Sprache version and no readability measurement has been made. |

## The landing page and the disclaimer page (part 16)

`/` and `/hinweise`. Both are built from the same base template and the same
design system as the other citizen pages, so every row above applies unchanged;
these are the rows they answer differently, plus the one thing the landing page
does that nothing else in the project does.

| Criterion | Verdict | Note |
|---|---|---|
| 1.1.1 Non-text content | automated | The hero is the project's only image, and it is an inline SVG with `role="img"`, a `<title>` and a `<desc>`. Its five stage names are ALSO the prefix of five real text captions below it, so nothing in the picture is available only in the picture. **Part 18 relied on that property rather than adding to it.** The drawing is one `viewBox` of 960 by 160 user units whose geometry is load-bearing - the envelope travels in steps of 192 units, a fifth of the width - so it cannot be re-laid-out for a narrow screen without rebuilding mechanics that had just been corrected. On a 390 px phone the SVG renders about 350 px wide and a stage name inside it comes out at roughly six CSS px: text that every checker counts as present and no person can read. Below 48rem the in-picture names are therefore hidden and the label band is cropped off with a negative margin; the stage's name is read from the caption directly beneath, whose chip carries "1. Eingang" and whose sentence belongs to the lit stage. Nothing left the document, the accessible name or the translation table - only the illegible copy of it left the picture. See the criterion row above for the icon set. |
| 2.2.2 Pause, stop, hide | automated (static) | The one page in the project where this criterion is engaged at all. A labelled checkbox above the figure stops the loop, in CSS alone; the paused state shows every stage lit and all five captions at once, which is also what `prefers-reduced-motion: reduce` produces without being asked. See the criterion row above for why it stops rather than freezes. |
| 1.4.10 Reflow | automated (static) | Same three checks as the other citizen pages - viewport meta, every table in its own scroll container, no inline width - plus the hero, which is a `viewBox` SVG at `width: 100%` and therefore scales rather than overflowing. The menu panel stops floating below 40rem for the same reason. Not a browser measurement. |
| 2.4.2 Page titled | automated | Distinct titles, translated with the rest of the page. |
| 3.3.x Errors | not applicable | Neither page has a form that can fail. The landing page's only control is the animation pause, whose two states are both valid. |
| 3.1.5 Reading level | open | Same gap as the other citizen pages. The hero captions are the shortest and plainest prose in the project - one sentence per step - which helps and is not the same thing as Einfache Sprache. |

## What is open, and who owns it

1. **External BITV 2.0 / EN 301 549 test** on a deployed instance, by an
   accredited tester. Pilot prerequisite (P-15). Nothing in this document
   substitutes for it.
2. **A test with users of assistive technology.** The only method that finds the
   problems a conformance checklist does not have a row for.
3. **A browser measurement at 320 CSS px, and at 200 percent text size.** The
   1.4.10 row is closed as a STATIC check on every page since part 15 - the
   scroll containers, the one-column definition list, the absence of any fixed
   pixel length - and nobody has opened these pages on a phone or zoomed one to
   200 percent. That is the honest remainder of the row, and it is smaller than
   what it replaced rather than gone. Part 16 added two things to measure there
   rather than closing any of it: the sticky header at 320 px, and the menu
   panel, which un-floats below 40rem precisely so that it cannot produce the
   two-axis scroll this criterion forbids.
4. **The animation on a real machine.** The hero's pause control and its
   reduced-motion answer are asserted against the stylesheet, which is a check
   of the rules and NOT a check that a browser honours them, that the loop is
   comfortable to sit next to, or that 16 seconds is the right length for a
   reader who needs longer. A person with vestibular sensitivity has not looked
   at this page.
5. **A contrast check on a real display.** The ratios in this document are
   arithmetic on hex values. A photometer, a projector and a bad panel are three
   different answers.
6. **An accessibility statement** under par. 12b BGG with a feedback mechanism
   and a link to the Schlichtungsstelle. It needs the audit first, and it needs
   a controller to name.
7. **Leichte Sprache / Einfache Sprache** for the citizen-facing pages,
   including the tour. Plain German is not the same thing, and for a
   public-administration surface this is a real gap rather than a nice-to-have.
8. **A search or filter over the queues.** Defensible to omit in a demo, not in
   a pilot with a real backlog.
