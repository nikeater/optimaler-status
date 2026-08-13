# ADR-030: One Design System in Plain CSS, and a Tour That Reads the Same Journal Every Other Page Reads

**Status:** Accepted, 2026-08-14 (part 15, the redesign and the tour page)

## Context

Fourteen parts built a system whose argument is architectural and whose surface
was, deliberately, the minimum that would render it: server-side Jinja and about
three hundred lines of plain CSS. That was the right trade while the argument
was being built. It stopped being the right trade the moment the instance went
public, because two things changed at once.

**The audience changed.** The pages are now walked by people deciding whether
this project is credible - and a system whose entire claim is that public
administration can have software that is careful will be read, partly, through
whether its own surface looks careful. That is not vanity. A visitor cannot
audit the one-way valve in ninety seconds; what they can do in ninety seconds is
form an impression, and an impression of neglect transfers to the parts they
cannot check.

**The entry point was missing.** A visitor landing on `/` could reach the
queues, the metrics and the inbox, and could start a three-phase journey - but
nothing told them what the system is for, in what order to look at it, or what
they were supposed to notice. The story existed in `docs/technical-spec.md`,
which is the wrong artifact for somebody with ten minutes and a browser.

Three constraints were already fixed before this part started and none of them
was negotiable. The part-10 refusal of a CSS build chain still holds. Nothing
this project serves may fetch anything from a third party. And the accessibility
posture - every mechanical criterion asserted by a test, every honest gap
written down - is a floor rather than a thing to be spent on a redesign.

## Decision

**1. One design system, in plain CSS, with no build chain.** `ui/static/system.css`
holds the tokens (palette, type scale, spacing ladder, radii, shadow), the base
typography, the page shell and every shared component: cards, tables and their
scroll containers, forms, buttons, badges, notices, flags, the step indicator,
the skip link, the focus ring. Every page loads it first and then at most one
small page-specific sheet - `metrics.css` (the gate verdict, the disclosure
widget), `review.css` (the fenced log-only panel), `demo.css` (the persona grid,
the placeholder token, the before/after panels, the tour's own components).

The part-10 reasoning is unchanged and got stronger rather than weaker: a
toolchain for a dozen server-rendered pages is a thing an agency inherits and
has to keep alive, and it would not have changed one rendered pixel here. What
the redesign needed was not a framework but a decision made once - what a card
is, what a heading step is, what a border weight means - which is exactly what
custom properties are for.

Two consequences worth stating. The card layout needed **no template class at
all**: the markup was already `main > section` on every page, which is what a
card wants to be, so the visual regrouping cost zero markup churn and could not
disturb a test that pins structure. And the three old stylesheets shrank from
roughly 250 lines of overlapping rules to under 120 lines of genuine leftovers,
because most of what they held was the same thing said three times.

**2. Nothing is fetched, and that includes the font.** No CDN, no Google Fonts,
no remote icon, no `@import` of anything outside `ui/static/`. The ruling
permitted a vendored OFL font as an alternative; the decision is the **system
font stack**, and not only because it is simpler. A vendored typeface adds
weight to every first load, a licence file to ship, an attribution to maintain,
and a rendering that has to be checked on three platforms - to buy a look that
the operating system's own UI face already provides on the machines this is
read on. On a German administrative workstation the stack resolves to Segoe UI;
the same stack renders as SF Pro and Roboto elsewhere. All three are excellent,
all three cost nothing, and none of them can fail to load.

This is the same reasoning as ADR-027's extra-free image, one layer out: the
fewer things a data-protection demonstration needs in order to render, the
fewer things it can be wrong about.

**3. The palette is measured, not chosen.** Sober govtech: slate ink, one
restrained institutional blue, three status hues. Every pair the stylesheet
actually ships was computed against the WCAG 2.1 relative-luminance formula
before it was used, and the ratios are written into
`docs/accessibility-selfcheck.md` rather than asserted in the abstract. Body
text sits between 6.2:1 and 18:1 on every surface it appears on; anything that
identifies a control - an input border, a button, the focus ring - is at or
above 3:1; two border weights exist precisely so the decorative one cannot be
used where the floor applies.

Dark mode was assessed and **not shipped**. It doubles the surface that has to
be measured, and a second scheme that had not been verified pair by pair would
be exactly the kind of "probably fine" this project does not do. `color-scheme:
light` is declared so form controls render consistently rather than being
half-inverted by the browser.

**4. Focus is restyled, never removed, and the check that says so is now a
sweep.** The old test named two stylesheets by hand. A check that has to be
edited whenever a file is added is a check that eventually stops being run, so
it now walks every `*.css` in `ui/static` and additionally asserts that the
design system carries a visible ring with an offset. Same for the "no fixed
pixel length" rule, which is what keeps text resizable - the pill radius is
`62em` rather than the conventional `999px` for that reason alone.

**5. The redesign CLOSES 1.4.10 for the caseworker pages, and it closes it by
fixing the cause.** The reflow row had been open since part 10 and was
half-closed in part 13, and the asymmetry was never about layout: the shared
reflow rules lived in `demo.css`, which only the citizen-facing pages loaded.
Moving them into the design system fixes the whole site by construction. Every
wide table on `/review`, the queue pages, the case view and the metrics panel is
now wrapped in its own `overflow-x: auto` container, so the container scrolls
and the document body never does; the two-column definition list drops to one
column below 40rem, which is the rule that actually overflows at 320 px.

The self-check row flips to `automated (static)` and says, in the same words the
part-13 rows use, that this is a check of the markup and the CSS and **not a
measurement in a browser at 320 CSS px**. Nobody has read these pages on a real
phone. The external audit stays a pilot prerequisite and nothing here
substitutes for it.

`/metrics` and `/inbox` also gained the shell the rest of the site has - a skip
link, a nav landmark, `main id="inhalt"` - and therefore joined the mechanical
accessibility suite, which now covers six pages rather than four. The
self-check has always named them as in scope; until this part they were in
scope without being tested.

**6. The tour is a page, not a document, and it derives nothing.**
`/demo/rundgang` tells the whole system in six steps - the problem and the
two-plane answer, phase 1, what the machine did, phase 3, the closed loop, why
to trust it - with each step linking to the page where that step actually
happens. German leads and every step carries a short English aside in one
consistent treatment, because the audience for this page is not uniformly
German-reading and a page that switched languages ad hoc would be harder than
either language alone.

It is demo surface like everything else under `/demo`: absent from the route
table and from the OpenAPI document when the flag is off, and it carries the
synthetic-data banner. It reads the journal exactly once, for the seeded case it
points at, and takes that case's unit and tier from `review_state` - the same
projection the caseworker UI and the pipeline view fold. Two answers to "which
unit is responsible" is one too many, and a page written to impress is the last
place that should be allowed a second one.

**7. Step 3 points at a SEEDED case, because the tour must work on an instance
that accepts nothing.** The glass pipeline is the most convincing screen this
system has, and until this part it could only be reached by submitting - which
an instance with no ingest token cannot do (ADR-027 ruling 4, ADR-029 ruling 2).
The tour links `case-ar-0011-ohne-rentenbeginn` from the frozen gold set: a
Regelaltersrente form that arrived without its Rentenbeginn, chosen because it
is the case where every stage has something in it - sealed identity kinds,
extracted values with verified offsets, one gap carrying the procedure's own
Nachforderung wording, a routing rule that fired, a tier the table can justify
line by line, and two delivered notifications. A complete case would show an
empty gap table; a Statusfeststellung would end at tier 3 without demonstrating
that the tiers differ at all.

Two honesty requirements come with that and both are met in words on the page.
A seeded case has **no working copy in the demo store** - that compartment holds
what a visitor typed, for half an hour, and nobody typed this one - so the tour
says why the before/after panel is absent over there rather than letting a
reader think it broke. And on an instance whose journal was never seeded, the
tour prints no case id at all: it says the state is empty, names the command
that fills it, and links `/review`. Printing the id anyway, because a deployed
instance would have it, is how a demo greets its first visitor with a 404.

**8. Both intake postures are first-class on the tour.** With a token
configured, step 2 invites a visitor to run their own case end to end. Without
one, it states that the ingest is closed, explains that this is the safe state
rather than a fault, and still offers the intake page for reading. Neither
wording apologises and neither promises something the instance cannot do. This
is ADR-027's posture rendered one page earlier than part 13 rendered it.

**9. Zero JavaScript was added.** The tour is static HTML with in-page anchors;
the metrics disclosure is a native `<details>`. htmx stays where part 10 put it,
as progressive enhancement on the review and metrics pages, and every action on
every page still works with scripting switched off.

## Consequences

- **Nothing behavioural moved and no number moved.** This part contains no
  engine change, no new derivation and one new route. The eval report over
  frozen gold v4 is the part-12 report, number for number, with all four gates
  green - it could not be otherwise, because nothing here runs inside the
  pipeline, the decision plane or the redaction boundary.
- **Three tests were changed on purpose rather than worked around**, and each
  change made the check stronger: the focus-outline check became a sweep over
  every stylesheet instead of two named files; the citizen-page reflow check
  reads the design system where the shared rules now live; and the mechanical
  accessibility suite covers six pages instead of four. The one thing that got
  weaker on paper is that `review.css` no longer contains `:focus-visible` - it
  is in `system.css`, and the test asserts that, which is where a reader looking
  for the focus ring would now go.
- **The step indicator disappears from exactly one page.** It marks where a
  visitor is in the three phases; the tour is the map rather than a position on
  it, so `demo_base.html` renders the indicator only when a view passes a phase.
  That is a one-line conditional and it is the only template-level branch the
  tour introduced.
- **The landing page leads with the tour.** The three promises stay, one section
  lower. Somebody with ninety seconds should spend them walking the system, not
  reading a description of it.
- **A vendored font remains possible and is now a smaller decision than it
  was.** Everything type-related is one custom property; swapping the stack for
  a subset woff2 would be one token, one `@font-face` block and the licence
  file, with no other file touched. It was not worth the weight today.
- **The self-check's open rows are unchanged in substance.** Reflow flips to a
  static-check verdict for the remaining pages; the contrast row gains measured
  ratios instead of an estimate. The external accredited BITV 2.0 test, the test
  with users of assistive technology, the par. 12b BGG accessibility statement,
  the Leichte-Sprache question and the queue search all stay open, and a
  redesign is not evidence about any of them.
