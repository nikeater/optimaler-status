# Accessibility Self-Check: EN 301 549 V3.2.1 / WCAG 2.1 AA (P-15)

**Status:** SELF-ASSESSMENT by the implementing engineer, 2026-08-12. Not an
audit. No person with a disability has used these pages, no assistive technology
has been run against them, and no BITV-Test has been performed. An accessibility
statement under par. 12b BGG and BITV 2.0 par. 7 may NOT be derived from this
document; it needs the external test that is a pilot prerequisite.

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
the four pages it would check, on a project whose whole posture is that a gate
must not depend on what is installed on a machine. The mechanical criteria are
tested directly against the rendered HTML instead, and the criteria a static
check cannot decide are listed as `reviewed` or `open` rather than assumed.

Running axe (or a BITV-Test) against a deployed instance is the right thing to
do and belongs in the pilot, together with a test by users of assistive
technology, which is the only thing that actually answers the question.

## Perceivable

| Criterion | Verdict | Note |
|---|---|---|
| 1.1.1 Non-text content | automated | There is no non-text content: no images, no icons, no canvas. Every state is text. |
| 1.3.1 Info and relationships | automated | Every table has a `<caption>` and every `<th>` a `scope`; headings are `<h1>`/`<h2>`/`<h3>` with no level skipped; the case view uses `<dl>` for field/value pairs; landmarks are `<header>`, `<nav>`, `<main>`, `<footer>`, `<section aria-labelledby=...>`. |
| 1.3.2 Meaningful sequence | reviewed | Source order is reading order; the only layout uses flexbox for the nav and the picker, neither of which reorders. |
| 1.3.3 Sensory characteristics | reviewed | No instruction refers to shape, size or position. |
| 1.3.4 Orientation | reviewed | No orientation lock; the layout is a single column with `max-width`. |
| 1.3.5 Identify input purpose | open | The inputs collect no personal data about the USER (unit, reason, note), so the WCAG input-purpose list has nothing to map to. Stated rather than claimed as passed. |
| 1.4.1 Use of colour | automated | Every queue flag carries its meaning in `<strong>` label plus a sentence; the tone class only changes a border and a tint. The test asserts each flag block has a label and more than 30 characters of prose. |
| 1.4.3 Contrast (minimum) | reviewed | Palette from `metrics.css`: ink `#14171a` on `#ffffff` (about 16:1), muted `#4a5158` on `#ffffff` (about 8:1), `#8a1414` and `#10502c` on white (about 8:1 and 8:1), white on ink for buttons. All well above 4.5:1. **Measured by calculation, not by a tool, and not verified on a real display.** |
| 1.4.4 Resize text | reviewed | All sizes are `rem` or unitless; no `px` font size; `max-width: 62rem` scales with the root size. |
| 1.4.5 Images of text | automated | There are no images. |
| 1.4.10 Reflow | open for the caseworker pages, automated for the two part-13 pages | **Caseworker pages: unchanged and still open.** No 320 CSS px test performed; the layout is single-column with `max-width` and wrapping flex containers, but the wide tables (the case view's span and journal tables) will scroll horizontally on a narrow viewport, and part 13 did not touch them. **The two citizen-facing pages are built for 320 px and tested for the three things that make it possible**: every wide table sits in its own `overflow-x: auto` container so the container scrolls and the body never does, `dl` drops from `max-content 1fr` to one column below 40rem (the two-column definition list is precisely what overflows at 320 px), and `ui/static/demo.css` contains no fixed pixel width. Still not a browser measurement, and the row says so. |
| 1.4.11 Non-text contrast | reviewed | Control borders are `--muted` (`#4a5158`) on white, above 3:1; the focus outline is `#0b4f8a` at 3px with a 2px offset. |
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
| 2.4.7 Focus visible | automated | `:focus-visible` sets a 3px outline with an offset; the test asserts neither stylesheet contains `outline: none` or `outline: 0`. |
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
| 3.2.3 Consistent navigation | automated | The nav and the picker come from one shared base template. |
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

## What is open, and who owns it

1. **External BITV 2.0 / EN 301 549 test** on a deployed instance, by an
   accredited tester. Pilot prerequisite (P-15). Nothing in this document
   substitutes for it.
2. **A test with users of assistive technology.** The only method that finds the
   problems a conformance checklist does not have a row for.
3. **1.4.10 Reflow at 320 CSS px** and the horizontal scroll of the wide tables.
4. **An accessibility statement** under par. 12b BGG with a feedback mechanism
   and a link to the Schlichtungsstelle. It needs the audit first, and it needs
   a controller to name.
5. **A search or filter over the queues.** Defensible to omit in a demo, not in
   a pilot with a real backlog.
