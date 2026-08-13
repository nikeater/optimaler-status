# Accessibility Self-Check: EN 301 549 V3.2.1 / WCAG 2.1 AA (P-15)

**Status:** SELF-ASSESSMENT by the implementing engineer, 2026-08-12, last
revised 2026-08-14. Not an audit. No person with a disability has used these
pages, no assistive technology has been run against them, and no BITV-Test has
been performed. An accessibility statement under par. 12b BGG and BITV 2.0
par. 7 may NOT be derived from this document; it needs the external test that is
a pilot prerequisite.

**Scope:** the four pages part 10 ships - `/review`, `/review/queue/{id}` (both
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

## Measured contrast ratios (part 15)

Every pair below was computed with the WCAG 2.1 relative-luminance formula from
the hex values in `ui/static/system.css`. These are the pairs the stylesheets
actually produce; a token combination that does not occur on a rendered page is
not listed, because a matrix of everything against everything invites the
reader to check the wrong cell.

Requirements: **4.5:1** for body text, **3:1** for large text and for anything
that identifies a user-interface component or a state.

Surfaces: `--surface` `#ffffff`, `--surface-alt` `#f4f7fa`, `--surface-sunken`
`#e9eef4`, `--canvas` `#eaeef3`, and the four note tints `--tint-accent`
`#e7eef7`, `--tint-ok` `#eaf3ed`, `--tint-alarm` `#fbeeee`, `--tint-sample`
`#eef2fa`.

| Foreground | Where it is used | vs `--surface` | vs `--surface-alt` | vs `--surface-sunken` / `--canvas` | worst tint | Requirement | Verdict |
|---|---|---|---|---|---|---|---|
| `--ink` `#101720` | body text, headings, table cells | 18.02 | 16.76 | 15.45 / 15.46 | 15.42 | 4.5 | pass |
| `--ink-soft` `#3d4757` | definition terms, the header subtitle, the English aside | 9.39 | 8.73 | 8.05 / 8.06 | 8.03 | 4.5 | pass |
| `--muted` `#4d5866` | help text, `.muted`, the footer, table captions | 7.23 | 6.73 | 6.20 / 6.21 | 6.19 | 4.5 | pass |
| `--accent` `#12518c` | links, the nav pills, the current tab | 8.14 | 7.57 | 6.97 / 6.98 | 6.96 | 4.5 | pass |
| `--accent-strong` `#0d3d6b` | hovered links, the button hover state | 11.07 | 10.30 | 9.49 / 9.50 | 9.47 | 4.5 | pass |
| `--ok` `#0d5730` | the gate-passed verdict | 8.67 | 8.06 | 7.43 / 7.44 | 7.42 (on `--tint-ok`: 7.65) | 4.5 | pass |
| `--alarm` `#8c1616` | the gate-failed verdict, the demo banner's rule | 9.37 | 8.71 | 8.03 / 8.04 | 8.02 (on `--tint-alarm`: 8.28) | 4.5 | pass |
| `--warn` `#7a4a06` | reserved for an advisory state | 7.47 | 6.95 | 6.41 / 6.41 | 6.40 | 4.5 | pass |
| `#ffffff` on `--accent` | button label, the call-to-action link, the skip link (on `--accent-strong`) | 8.14 (11.07 on `--accent-strong`) | - | - | - | 4.5 | pass |
| `--line-strong` `#78899d` | input, select and textarea borders; the button and table frame; the tag border | 3.58 | 3.33 | 3.07 / 3.07 | 3.06 | 3.0 | pass |
| `--focus` `#0d3d6b` | the 3px focus ring, offset 2px | 11.07 | 10.30 | 9.49 / 9.50 | 9.47 | 3.0 | pass |
| `--line` `#d7dee6` | row separators and card edges, DECORATIVE only | 1.36 | - | - | - | none | not a component boundary; see 1.4.11 |

Two caveats that belong next to the numbers rather than under them. This is
arithmetic on hex values, not a measurement of a rendered display: subpixel
rendering, a projector, a cheap panel and a bright room all change what a
person actually sees. And a passing ratio says nothing about whether the
colours communicate - that is 1.4.1, which is answered separately and answered
by requiring words next to every tone.

## Perceivable

| Criterion | Verdict | Note |
|---|---|---|
| 1.1.1 Non-text content | automated | There is no non-text content: no images, no icons, no canvas, and after the part-15 redesign still none - the tour's step markers are text in a bordered span, marked `aria-hidden` because the heading beside them already says which step it is. Nothing is fetched from anywhere, so there is no remote image to fail to load either. Every state is text. |
| 1.3.1 Info and relationships | automated | Every table has a `<caption>` and every `<th>` a `scope`; headings are `<h1>`/`<h2>`/`<h3>` with no level skipped; the case view uses `<dl>` for field/value pairs; landmarks are `<header>`, `<nav>`, `<main>`, `<footer>`, `<section aria-labelledby=...>`. |
| 1.3.2 Meaningful sequence | reviewed | Source order is reading order everywhere. The layout uses flexbox for the nav, the picker and the before/after panels and grid for the definition lists and the persona cards; none of them reorders content, and no `order` or `grid-row` declaration exists in any stylesheet. |
| 1.3.3 Sensory characteristics | reviewed | No instruction refers to shape, size or position. |
| 1.3.4 Orientation | reviewed | No orientation lock; the layout is a single column with `max-width`. |
| 1.3.5 Identify input purpose | open | The inputs collect no personal data about the USER (unit, reason, note), so the WCAG input-purpose list has nothing to map to. Stated rather than claimed as passed. |
| 1.4.1 Use of colour | automated | Every queue flag carries its meaning in `<strong>` label plus a sentence; the tone class only changes a border and a tint. The test asserts each flag block has a label and more than 30 characters of prose. |
| 1.4.3 Contrast (minimum) | reviewed, with measured ratios | The palette is in `ui/static/system.css` as custom properties, and every pair that actually ships was computed before it was used. The full matrix is the section "Measured contrast ratios" below. The lowest text ratio anywhere on any surface is **6.19:1** (`--muted` on the accent tint), against a 4.5:1 requirement. **Measured by calculation against the WCAG 2.1 relative-luminance formula, not with a tool, and not verified on a real display.** |
| 1.4.4 Resize text | automated (partly) | All sizes are `rem`, `em`, `ch` or unitless; the type scale is seven `rem` tokens. A test asserts that no stylesheet in `ui/static` contains a `px` font size or a fixed pixel length of three digits or more - the pill radius is `62em` rather than the conventional `999px` for exactly that reason. Whether the pages are USABLE at 200 percent is a browser measurement nobody has made. |
| 1.4.5 Images of text | automated | There are no images. |
| 1.4.10 Reflow | automated (static), every page | **Closed for the caseworker pages in part 15, and closed by fixing the cause rather than the symptom.** The rules that make reflow possible used to live in `demo.css`, which only the three citizen-facing pages loaded - which is precisely why this row was open on the others. They are in the design system now, so every page gets them: every wide table sits in its own `overflow-x: auto` container so the container scrolls and the document body never does, `dl` drops from two columns to one below 40rem (the two-column definition list is what actually overflows at 320 px), `overflow-wrap: break-word` on `body` keeps a case id or a placeholder token from pushing the page wider, and no stylesheet carries a fixed pixel length. Asserted per page in `tests/test_review_accessibility.py` (`/review`, both queue variants, the case view, `/metrics`, `/inbox`) and in `tests/test_demo_journey.py` (`/demo/rundgang`, `/demo/antrag`, the pipeline view). **This is a static check of the markup and the CSS and NOT a measurement in a browser at 320 CSS px.** Nobody has read these pages on a real phone; that stays in the open list. |
| 1.4.11 Non-text contrast | reviewed, with measured ratios | Two border weights exist so the floor cannot be missed by accident. `--line-strong` (`#78899d`) draws every control boundary - inputs, selects, the button outline, the table frame - and is at or above **3.06:1** against every surface it is used on, white included (3.58:1). `--line` (`#d7dee6`, 1.36:1 on white) is decorative only: it separates rows inside a card and identifies no component. The focus ring is `--focus` (`#0d3d6b`) at 3px with a 2px offset, at or above **9.47:1** on every surface. One honest note: the `.tag` badge's border sits at 2.39:1 against the accent tint when a tag is inside a selected persona card - a badge is not an interactive component and its text carries 15:1, so 1.4.11 is not engaged, but it is stated rather than left to be found. |
| 1.4.12 Text spacing | reviewed | No fixed heights, no `!important` on line-height; `line-height: 1.5` on body. |
| 1.4.13 Content on hover or focus | automated | Nothing appears on hover or focus; the test asserts no `onmouseover` anywhere. |

## Operable

| Criterion | Verdict | Note |
|---|---|---|
| 2.1.1 Keyboard | automated | Every control is a native `<a>`, `<button>`, `<select>` or `<input>`. The test asserts no `onclick`, no `href="#"` acting as a button and no `hx-get`/`hx-post` on a `<div>`. htmx is progressive enhancement only: with scripting off, forms post and links navigate. |
| 2.1.2 No keyboard trap | reviewed | No modal, no focus management script, no `tabindex` above 0 anywhere. |
| 2.1.4 Character key shortcuts | automated | There are none. |
| 2.2.1 Timing adjustable | reviewed | No timeout, no auto-refresh, no polling. The queue clocks are display-only and never expire a page. |
| 2.2.2 Pause, stop, hide | reviewed | Nothing moves, blinks or auto-updates. |
| 2.4.1 Bypass blocks | automated | A real skip link is the first focusable element on every page and targets `<main id="inhalt">`; the test asserts both. |
| 2.4.2 Page titled | automated | Every page sets a distinct `<title>` naming the queue or the case. |
| 2.4.3 Focus order | automated (partly) | DOM order is: skip link, nav, unit picker, main content, actions. The test pins the skip link's position; the rest is DOM order with no `tabindex` overrides, which is the only way to get it right. |
| 2.4.4 Link purpose (in context) | reviewed | Link text is the queue name or the case id; there is no "here" or "more". |
| 2.4.5 Multiple ways | reviewed | A case is reachable from its queue and from a direct URL; the nav is on every page. There is no search, which for a demo with one journal is defensible and for a pilot is not. |
| 2.4.6 Headings and labels | reviewed | Section headings name their content in German administrative vocabulary; labels name what is entered. Whether they are USEFUL to a caseworker is exactly what the pilot has to tell us. |
| 2.4.7 Focus visible | automated | `:focus-visible` in the design system sets a 3px outline in `--focus` with a 2px offset, which survives on top of every tinted surface (9.47:1 or better). The test sweeps EVERY stylesheet in `ui/static` for the two rules that switch an outline off, rather than checking two files named by hand, and additionally asserts the ring is restyled rather than merely present. |
| 2.5.1 Pointer gestures | automated | No gesture; every action is a click or a keypress on a native control. |
| 2.5.2 Pointer cancellation | reviewed | Native buttons only; the browser's own down-then-up semantics apply. |
| 2.5.3 Label in name | reviewed | The visible label text IS the accessible name: no `aria-label` overrides a visible string anywhere. |
| 2.5.4 Motion actuation | automated | There is none. |

## Understandable

| Criterion | Verdict | Note |
|---|---|---|
| 3.1.1 Language of page | automated | `<html lang="de">`, asserted per page. |
| 3.1.2 Language of parts | reviewed | The pages are German throughout. The few English tokens are identifiers (`nachforderung`, `prepared_decision`, `fit_connect`) rendered as code, not as prose. |
| 3.2.1 On focus | reviewed | Nothing happens on focus. |
| 3.2.2 On input | reviewed | No `onchange` submits a form; the unit picker has an explicit submit button. |
| 3.2.3 Consistent navigation | automated | Two shared base templates - one for the caseworker pages, one for the citizen pages - carry the nav and the picker, and since part 15 `/metrics` and `/inbox` carry the same shell and the same nav treatment. The skip link is the first focusable element on all six pages, which the test asserts per page. |
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

## The two citizen-facing pages (part 13)

`/demo/antrag` and `/demo/case/{id}/pipeline`. Every criterion above applies to
them and is met the same way, through the same shared stylesheet and the same
markup discipline; the rows below are the ones whose ANSWER is different, plus
the ones that only exist because these pages have a form and an audience that
was not trained on anything.

| Criterion | Verdict | Note |
|---|---|---|
| 1.3.1 Info and relationships | automated | Same bar as the caseworker pages, asserted separately for these two: one `h1`, no skipped heading level, `<header>`/`<nav>`/`<main>`/`<footer>`, a `<caption>` on every table and no `<th>` without a `scope`. The step indicator is an ordered list with `aria-current="step"`, because "which phase am I in" is a list position. |
| 1.3.5 Identify input purpose | open, and more open here than on the caseworker pages | The intake form collects fields that ARE on the WCAG input-purpose list (name, address, birth date) and carries no `autocomplete` attributes. That is a deliberate omission with an uncomfortable trade-off: `autocomplete="name"` on a public demo form invites a browser to fill in the visitor's REAL name, on the one instance whose entire posture is that it must never receive real personal data. The right answer for a production intake is the opposite of the right answer here, and neither this file nor the code should pretend the question is settled. |
| 1.4.1 Use of colour | automated | The highlighted queue row says "Ihr Vorgang" in words plus an offscreen sentence; the tint repeats it. The current step in the indicator is `aria-current` plus a border plus offscreen text. A placeholder token is bordered and monospaced, not coloured. |
| 1.4.10 Reflow | automated (static) | See the row above: scroll containers, a one-column `dl` below 40rem, no fixed pixel width, and the viewport meta. Not a browser measurement. |
| 2.4.2 Page titled | automated | Distinct titles naming the phase. |
| 3.3.2 Labels or instructions | automated | Every input and the letter textarea has a `<label for>`; fields with a help sentence carry `aria-describedby`. A field that will be sealed says which KIND it becomes, next to its label, before it is submitted. |
| 3.3.1 Error identification | reviewed | A refused submission re-renders the page with the refusal in a `role="alert"` block at the top, the visitor's edits preserved, and the findings as a list of kinds and places. It never echoes the value that caused the refusal. |
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
| 3.1.2 Language of parts | reviewed | **This is the row the tour exists for.** Every step carries a short English aside, and each one is a `<p class="aside" lang="en">` - the language change is declared on the element rather than left for a screen reader to guess, so the voice switches instead of reading English with German phonemes. The German is the primary content and the asides are supplementary; nothing is available only in English. |
| 2.4.1 Bypass blocks | automated | The skip link, plus the in-page table of contents: six anchors to the six steps, so a keyboard user does not tab through step 2 to reach step 5. No anchor is a bare `#`, which the test asserts. |
| 2.4.4 Link purpose (in context) | reviewed | Every link says where it goes in words - "Zum Rundgang", "Antrag stellen (Phase 1)", "Zum Postfach (nur Ansicht)". There is no "hier" and no "mehr". The call-to-action links are LINKS styled as buttons, not buttons pretending to navigate: they work with scripting off and appear once each in the tab order. |
| 1.4.1 Use of colour | automated | The step number is a bordered, monospaced-weight chip marked `aria-hidden`, because the heading next to it already names the step; the intake-posture note says "gesperrt" or invites a submission in words, and the tone only repeats it. |
| 3.3.x Errors | not applicable | The page has no form and no control that can fail. |
| 3.1.5 Reading level | open | Same gap as the other citizen pages and arguably sharper here, because this page is written FOR somebody unfamiliar: it is plain German and avoids jargon where the domain allows, but there is no Leichte-Sprache or Einfache-Sprache version and no readability measurement has been made. |

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
   what it replaced rather than gone.
4. **A contrast check on a real display.** The ratios in this document are
   arithmetic on hex values. A photometer, a projector and a bad panel are three
   different answers.
5. **An accessibility statement** under par. 12b BGG with a feedback mechanism
   and a link to the Schlichtungsstelle. It needs the audit first, and it needs
   a controller to name.
6. **Leichte Sprache / Einfache Sprache** for the citizen-facing pages,
   including the tour. Plain German is not the same thing, and for a
   public-administration surface this is a real gap rather than a nice-to-have.
7. **A search or filter over the queues.** Defensible to omit in a demo, not in
   a pilot with a real backlog.
