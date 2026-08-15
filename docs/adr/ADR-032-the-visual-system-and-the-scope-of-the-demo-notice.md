# ADR-032: A Visual System With Elevation, Zones and Measured Surfaces, and a Demo Notice That Renders Where It Is About

**Status:** Accepted, 2026-08-15 (part 18, the visual overhaul)

## Context

Three parts have restyled this surface. Part 15 built a design system and a
tour, part 16 rebuilt it bilingual with one header and a hero, part 17 opened
every page in a browser for the first time and fixed what looking found. The
result is a site that is correct, accessible, honest and - in the judgement of
the person it was built for, walking it - visually generic.

That verdict is worth taking apart, because "generic" is not a synonym for
"bad" and the three earlier parts were not wasted. What they optimised for is
visible in their gates: contrast floors, reflow measurements, focus rings, key
coverage, byte identity with the flag off. Every one of those is a property you
can fail, so every one of them got a test. Nothing in the project ever asked
whether the pages were any good to look at, so nothing ever answered it.

The concrete diagnosis, written down before anything was changed and designed
against:

1. Every section of every page was the same white rectangle with the same 1px
   border, the same single shadow and the same margin. A reader scrolling had
   no way to tell a list of links from a statement of principle.
2. There was no hero. The landing page opened with a heading, a paragraph, two
   buttons and then a white card containing a diagram - the picture was the
   first item in a list rather than the page's opening statement.
3. The brand colour was almost absent. A site claiming a sky-blue identity
   showed blue as a 3px rule under the header, a 4px stripe on card edges and
   two navy buttons. `--brand` never appeared as a surface, so the page read
   grey.
4. The type ramp had no display size. `h1` was 2.375rem at every viewport and
   `h2` was 1.5rem, so a 1920px screen got a document heading rather than a
   product headline, and the gap between a heading and its body was one step.
5. One shadow was on the header, on every card and on the popover, so nothing
   sat above or below anything.
6. The footer was two to four grey paragraphs after a hairline. The page did
   not end; it stopped.
7. Numbers were not rendered as numbers. The census on `/review` - the single
   most quotable figure in the demonstration - was a clause in a sentence next
   to a machine timestamp with microseconds, and every numeric table column was
   left-aligned, so digits did not line up.
8. States were prose. "über Zielwert", "Tier 1 - klar und vollstaendig" and
   "offen, wartet auf menschliche Bestaetigung" were sentences in cells, which
   is exactly where a reader scanning for the exception finds nothing to scan.
9. Empty states were bare notices, forms were three `<select>` elements at
   three different widths, and the icon set had two stroke weights.

The constraints were inherited and none was negotiable: nothing fetched from a
third party (ADR-030), no build chain and no preprocessor, no JavaScript for
behaviour, the accessibility floor is a floor rather than a budget, the frozen
configuration and gold corpus are read-only, the eval numbers may not move, and
the part-17 corrections - the hero's forward stagger, its working pause control,
its reduced-motion still frame, the full-width measure, the 320px reflow on all
ten pages - may not regress.

Separately and at the same time, the user ruled on where the synthetic-data
ribbon belongs. Since part 11 the notice has rendered on every page of a demo
instance; the direction is that it belongs on the landing page and on the page
it links, and nowhere else.

## Decision

**A visual system with three axes the old one did not have: elevation, zone and
display type. No hue changes.**

The palette keeps the part-16 sky blue and every token in it. What is added is
five surfaces, three shadow levels, three fluid type sizes and four component
patterns - badge, statistic tile, empty state, closing band. The decision that
makes it a system rather than a set of tweaks is that all of it lives in
`system.css` as tokens, so a revision is a token edit and not a hunt.

**Depth is three levels and it means something.** `--shadow-sm` is a resting
card and the header bar; `--shadow-md` is a card under the pointer, the hero
panel and a sticky table head; `--shadow-lg` is a thing that floats over the
page, which in this project is the menu panel and nothing else. The shadows are
tinted with the brand's own blue rather than with grey, which is the difference
between a shadow that looks printed and one that looks lit.

**The page has a top and a bottom.** `body` paints a non-repeating wash from
`--canvas-top` into `--canvas` over the first 40rem, so a scrolled page has a
direction, and no template gained an element for it. The footer becomes the one
dark surface in the project: full width like the header, the wordmark on one
side, the page's own sentences laid out as columns on the other. Not one word
of those sentences changed. They are the honesty text - what this UI does not
do, what the journal guarantees, where the self-assessment lives - and the
brief's rule was to restyle their container and never their words.

**A zone is a class a template asks for, not a rule that alternates.** A tinted
section is recessed rather than raised. It is applied by hand where a block is
a statement rather than a place to go, because "every second one" is a pattern
and not a meaning.

**Fluid type is `clamp()` with a `rem` term in every preferred value.** A font
size given in `vw` alone ignores the reader's own font setting and fails WCAG
1.4.4 outright. Each of the three fluid sizes carries a `rem` term as well, so
the expression moves when a reader enlarges text and the `vw` term only decides
how much of the range a viewport takes. The floor of every ramp is a size this
project already shipped.

**A state is a badge drawn around its own words.** There is no badge in this
project that does not contain the sentence it is a badge for. Remove the words
and the tone has nothing left to render, which is how WCAG 1.4.1 is kept by
construction rather than by care. The neutral tone carries the states that are
normal - a case waiting for a human IS the product here - and the caution and
alarm tones are left for the states that are exceptions.

**Every new pair is computed before it is used, and the computation is the
gate.** Five surfaces, five badge families, three gradients and the band's ink
and link colours were measured against the WCAG relative-luminance formula and
written into `docs/accessibility-selfcheck.md` before any of them shipped.

**A gradient that carries text never touches `--brand`.** A gradient under
white text is only as good as its lightest stop; `--grad-cta` therefore runs
between `--brand-ink` (white at 6.51:1) and `--brand-ink-strong` (8.92:1). The
sky blue appears only in `--grad-rule`, which is a 3px hairline with nothing on
it.

**`--brand` stays an element colour even where it would now pass.** Against the
new dark band the sky blue measures 5.07:1 - the first surface in this project
where it would clear the 4.5 text requirement. It still never carries text. A
rule that held on every surface except one would be a rule nobody could apply
from the token's name, and the rule is enforced by a regex over every
stylesheet rather than by anybody remembering it.

**The demo ribbon renders on `/` and `/hinweise` and nowhere else.** The
partial is untouched - same `id="demo-ribbon"`, same three translation keys,
same `demo.enabled` gate, so a non-demo deployment still renders zero bytes of
it. What changed is one line in each of two base templates: `demo_base.html`
exposes a `ribbon` block, `review_base.html` includes nothing, and exactly two
page templates fill the block. Reverting the scope is putting the include back.

## Consequences

**The hero was strengthened and not rebuilt, deliberately.** Its mechanics were
corrected in part 17 and are pinned by tests that read the stylesheet: the 16
second period, the five negative delays that stagger ring and caption on one
clock, the `hero-travel` geometry in five steps of 192 user units, the
sibling-combinator pause control, the reduced-motion still frame. All of it
carried over character for character. What changed is scale, surface, elevation
and the caption's typography.

**The hero drops its in-picture stage names below 48rem.** The drawing is one
`viewBox` of 960 by 160 units and that geometry is load-bearing, so it cannot
be re-laid-out for a narrow screen without rebuilding the mechanics above. On a
390px phone a stage name inside it renders at about six CSS px - text every
checker counts as present and no person can read. The names are hidden there
and the label band cropped; the caption directly beneath names the lit stage,
which it already did. Illegible text is worse than no text, because it is noise
claiming to be information.

**One table gained an inner scroll region, and only one.** A sticky table head
needs a box with a maximum height to stick to, and `.scroll-x` is already a
scroll container in both axes. `.is-tall` is opt-in, applies to the 41-row
queue and to nothing else, engages only from 64rem up so a phone never gets an
inner scroller competing with the reader's own, and is removed for print so a
printed queue carries all its rows. Every row in it contains a link, so a
keyboard reader reaches all of them by tabbing and the box follows the focus.

**Two accessibility defects were created by this part and found by measuring
it.** The focus ring measures 1.34:1 against the new dark band and is white
inside it; and an `opacity` on the date field's calendar button dimmed the
focus ring the browser paints on that button, which this project does not own,
so the rule now sets nothing but `cursor`. Both came out of measurement rather
than inspection - the first from computing a pair before shipping it, the
second from walking the tab order of every page in a browser and reading the
computed outline colour against the computed background at each of 159 stops.
That walk is new and is now the evidence behind the 2.4.7 row.

**Three structural tests changed, and each got stricter.** The scroll-container
count is matched by pattern rather than by a literal class attribute, so a
container that has been modified still counts. The phase connector reads the
step mark's radius out of the mark's own rule instead of carrying it as a
literal in three places that have to be kept in step by hand. The ribbon scope
is asserted on both halves - present on two pages, absent on the rest - because
a scope asserted only where it is present drifts back to "on everything" the
first time somebody adds an include, and nothing fails.

**The flag-off byte-identity suite had to gain two templates to keep meaning
anything.** It compared three templates against a control environment where the
ribbon include is the empty string. Those three no longer include the ribbon at
all, so the comparison became vacuous. It now also covers the two templates
that do carry it, rendered with the posture off, which is exactly the property
the suite exists to measure.

**The ribbon's scope has a cost and it is stated rather than hidden.** A judge
who deep-links to `/review` no longer meets the synthetic-data notice at the
top of the page. What they do meet is unchanged: the picker note saying the
role choice is a demo function with no sign-in, the footer saying what this
surface does not do, and a menu that links the full disclaimer. The tests
assert that reachability on the pages that lost the ribbon, so the difference
between "said less often" and "no longer reachable" is a failing test rather
than a judgement call.

**A deployment pin moves with it.** The CI smoke test greps `id="demo-ribbon"`
off the live `/review`; after this change that grep fails there and must move
to `/`. The census prefix `101 offene(r) Vorgang` stays on `/review` and stays
contiguous - the statistic tiles were added above that sentence rather than in
place of it, precisely so the pin did not have to move twice.

**Revisions are token-level cheap, which was a design goal.** The palette, the
elevation ladder, the spacing ladder, the radii, the type ramps and the motion
tempo are all custom properties in one file. A revision round that says "less
shadow", "warmer", "tighter" or "slower" is a handful of values.

## Alternatives considered

**Vendor an OFL typeface.** ADR-030 chose the operating system's stack because
nothing may be fetched and a vendored face costs bytes on a page that must load
anywhere. Revisiting it was explicitly permitted. It was not taken: the honest
gain over a well-set system stack is smaller than the weight budget, and the
type problems here were ramp, tracking and line-height rather than the face.
Those are free.

**Alternate tinted and white sections automatically.** Rejected. It produces a
rhythm that looks designed for exactly as long as the number of sections stays
even, and it says nothing, because the alternation is not about the content.

**Give the hero a new, phone-friendly geometry.** Rejected. The `viewBox` and
its 192-unit steps are the mechanics part 17 had just finished correcting, and
a second layout would be a second copy of the content to keep in step.

**Keep the ribbon everywhere and make it quieter.** This is what a designer
would propose and it is not what was asked for. The direction is recorded above
as the user's, the implementation is two include lines, and the cost is stated
in the consequences so the decision can be reversed on the evidence.
