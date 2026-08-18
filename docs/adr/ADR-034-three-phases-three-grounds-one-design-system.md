# ADR-034: Three Phases, Three Grounds, One Design System

**Status:** Accepted, 2026-08-18 (part 21, the phase-theming detour). Amended
2026-08-18 (part 23): the machine ground's VALUES are re-anchored on the One
Dark Pro editor theme and the ground gains a syntax palette. The decision below
is unchanged - see "Amendment (part 23)" at the end for what moved and why it
did not need a decision of its own.

## Context

The demonstration has three phases and the product has named them since part
13: `PHASES = ("antrag", "maschine", "sachbearbeitung")` in `api/demo.py`, with
a step indicator that counts a visitor through them and a hand-off block at the
bottom of the pipeline page that says which one is next. Until this part all
three rendered on the same sky-blue canvas, so the only thing that told a
reader which world they were in was the sentence at the top of the page.

The user's direction for this detour was two-thirds of a design brief: the
pages that show what the machine does internally should go "a bit dark and
lovely", and the caseworker screens should go warm yellow. The citizen pages
stay exactly as part 18 left them.

Three inherited constraints shaped every decision below and none was
negotiable.

1. **Part 15 refused dark mode, and the refusal was about rigour rather than
   darkness.** ADR-030 records it: a second colour scheme that had not been
   verified pair by pair is exactly the "probably fine" this project does not
   do. That refusal is not an argument against a dark surface; it is a
   requirement on how one ships.
2. **Part 18 shipped a focus ring at 1.34:1 on a band it had just invented.**
   Nobody could have seen it - it was found by computing the pair, and only
   then confirmed by walking 159 tab stops in a browser. One new surface
   produced one invisible indicator. This part adds eleven surfaces per new
   ground.
3. **The theming may change zero words**, the census sentence on `/review` and
   the ribbon `id` on `/` are CI pins that must keep rendering verbatim, the
   flag-off byte-identity suite must stay green, and the intake that part 20
   had just reworked may not be restyled.

## Decision

**A phase is a GROUND, and a ground is a token set on a body class.** Three
classes exist and one of them is the absence of a class: `:root` is the citizen
ground, `.ground-machine` is the deep blue-slate of the two pages that show the
machine's own work (`/demo/case/{id}/pipeline` and `/metrics`), and
`.ground-casework` is the warm amber paper of `/review`, `/review/queue/*` and
`/review/case/*`.

**One design system, not three stylesheets.** A ground re-points the surface
ladder, the ink ladder, the phase family, the two gradients that carry them and
the closing band. It declares nothing else. Every part-18 component - cards,
tiles, tables, badges, the footer band, the focus ring, forms, the empty state
- follows without a component-level override, because every one of them already
read its colours from tokens. This is checked rather than intended: a test
asserts that the two ground blocks contain only custom properties, so the first
`padding` or `background` that appears in one fails the build.

**The surface ladder INVERTS rather than being replaced.** On the machine
ground `--canvas` is the darkest value and `--surface`, `--surface-alt` and
`--surface-sunken` step up from it; on the light grounds they step down. That
one property is what keeps a card sitting above its canvas and a `<code>` token
sitting inside its card on all three grounds with no rule written twice.

**Element colours never carry text, and the rule gains a fourth family.**
`--amber` `#b07700` measures 3.83:1 on white and 3.06:1 on the palest amber
surface: it clears the 3:1 an edge needs and cannot reach the 4.5:1 a word
needs. So yellow draws surfaces and edges, `--amber-ink` `#8a4f0a` and
`--amber-ink-strong` `#5f3705` carry every letter, and the caseworker ground
points `--brand`, `--brand-ink` and `--brand-ink-strong` at their amber
siblings - which is what makes a link, a legend, a badge, a button and a hover
state change world together. The split is held by the same regex over every
stylesheet that has held the blue, the red and the caution orange since part
16, extended by adding a name to a set. A second test asserts the ALIAS
DIRECTION, because `--brand-ink: var(--amber)` would put a 3.83:1 yellow on
text duty across every caseworker page without breaking any rule the first test
knows about.

**Every pair was computed before it shipped, and the arithmetic is now a
test.** Until this part the ratios were computed by whoever changed the palette
and written into a comment and into the self-check; nothing failed if the next
person forgot. `tests/test_review_accessibility.py` now parses the three token
blocks out of the stylesheet, resolves `var()` chains and computes every pair
the components produce against the WCAG 2.1 relative-luminance formula on all
three grounds - inks on surfaces and on tints, links, state colours on their
own tints, the band's ink, the label on both ends of the button gradient, the
control boundary, and the focus ring on all eleven surfaces of each ground.

**Two components broke exactly the way the part-18 band broke, and both were
found by arithmetic.**

- *The current step's number.* `.phase-current .phase-mark` fills a disc with
  `--brand` and writes the number on it in `--ink`. That is 6.74:1 on the
  citizen ground, where the sky blue is a light fill under a near-black ink -
  and 2.05:1 on the machine ground, where `--brand` is the same sky blue and
  `--ink` has gone light. Two light values on top of each other, on the one
  number that says which phase a visitor is in, on the one page in the project
  where phase 2 is current. The fix is `--cta-ink`, which measures 7.47:1
  there.
- *The primary button.* `--grad-cta` runs `#106393` to `#0c4e73` and measures
  2.30:1 and 1.68:1 against a dark card - a control not distinguishable from
  its surroundings, which is what 1.4.11 forbids. There is no dark blue that
  fixes it: white text needs the fill dark and a dark card needs the fill
  light, and the two requirements do not overlap. So on that ground the fill
  goes light and its ink goes dark with it, through one new token (`--cta-ink`)
  rather than six component overrides.

**The closing band is inverted rather than kept.** The band exists because a
page that fades into its own background has no end, and what makes it a band is
being the surface that differs MOST from the page it closes. The part-18 navy
measures 10.63:1 against the light canvas and 1.46:1 against the machine
canvas, so on that ground it goes one step lighter and bluer than the page; on
the caseworker ground it becomes a warm brown at 11.55:1 against the cream,
because a navy band under amber paper reads as a footer borrowed from another
site. Its focus ring stays a literal white on all three grounds, since the band
is dark on all three.

**The semantic hues keep their MEANING everywhere and change value only where
a light value would disappear.** Green is the good state, red is the refusal
and orange is the caution on all three grounds. On the machine ground each one
inverts weight (a `#10683c` green measures 1.4:1 on that canvas); on the
caseworker ground they are unchanged, tints included, because that ground is
light and the light values still measure what they measured.

**The grounds are product styling, not a demo feature.** `/review*` and
`/metrics` exist with the demo posture off, so the class on their `<body>` is
unconditional and no byte on those pages depends on a flag.

## Consequences

**Measured, on both new grounds.** Worst text pair 5.36:1 on the machine ground
(`--muted` on `--surface-sunken`) and 4.99:1 on the caseworker ground, against
4.5. Worst element pair 3.75:1 and 3.06:1, against 3.0. The focus ring is at or
above 8.71:1 on every surface of the machine ground and 8.25:1 on every surface
of the caseworker ground. The machine ground's own worst numbers are BETTER
than the light ground's (5.06 and 3.56). The full matrix is in
`docs/accessibility-selfcheck.md`.

**Confirmed in a browser, twice over.** The tab order of all eleven pages was
walked - 215 stops - reading the computed outline colour against the computed
background behind it at every stop; no ring on either new ground is below 3:1.
Separately, every element rendering text on all eleven pages was swept for its
computed colour against its effective background, compositing translucent
layers and reading gradient stops: 5767 text nodes, none below its floor. The
browser and the arithmetic agree to the second decimal.

**The citizen ground did not move, and that is proved in pixels.** The landing
page, the tour, the disclaimer and the intake are byte-for-byte identical
screenshots against a HEAD baseline, at 1920 and 390 CSS px, in both languages
- sixteen shots, zero changed pixels.

**1.4.10 is unaffected and re-measured anyway**: all eleven pages measure
exactly the viewport at 320 and at 390 CSS px.

**Zero words changed.** No translation key was added, removed or edited; the
theming is a stylesheet and a static class. The census sentence and the ribbon
`id` render verbatim, and the flag-off byte-identity suite is untouched because
nothing conditional was added to any template.

**One component is documented as unrenderable rather than overridden.** `--ink`
on the amber disc would measure 4.32:1, under the 4.5 a word needs. The step
indicator is included by `demo_base.html` alone and the caseworker screens are
rendered by `review_base.html`, so it cannot reach that ground - the caseworker
UI never learns that a tour is running (part 13, ruling 5). A test asserts the
absence rather than a rule with no user being added against the day somebody
changes it, and it names the reason when it fails.

**The cost is a real one and belongs here.** Three grounds are three times the
surface area for any future palette change, and the mitigation is the computed
test rather than discipline. A reviewer adding a component that hard-codes a
RELATION between two tokens - as the step indicator does - will not be caught
by that test, because the test measures tokens and not components; the sweep
that finds those is the browser pass, which is not in CI.

**Revisions stay token-level cheap**, which was a design goal inherited from
ADR-032. "Warmer", "less dark", "more contrast in the band" are a handful of
values in one block.

## Alternatives considered

**Three stylesheets, one per phase.** Rejected, and it is the obvious shape.
It makes every future component a decision made three times and guarantees
drift the first time somebody fixes a card on one page only. The token set is
strictly less code and the "one design system" claim survives it.

**A `prefers-color-scheme` dark mode instead of a machine ground.** Rejected
and out of scope. This is not a reader preference; it is a statement about
which phase a page belongs to, and it must render the same way for every
reader. ADR-030's refusal of dark mode is untouched by this part.

**Warm the state tints on the caseworker ground** so the cool green of an
`.ok-note` does not sit on amber paper. Rejected after looking at it. A warm
"good" tint on a warm ground stops being visible AS a state, which is the one
thing a status colour has to do; the slight coolness is what makes the state
legible against the ground rather than part of it. `--tint-sample` is the one
exception and it goes the other way for the same reason - a cool patch on warm
paper reads as a HIGHLIGHT, and ADR-025 is that a random draw must never look
like a suspicion.

**Give the three step circles the three phase colours** (blue, dark, amber), as
the brief allowed. Rejected on the screenshots. The strip renders on the
citizen and machine grounds, so an amber third circle would import a third
family onto two grounds to decorate a component whose job is to say "you are
here" - and the state it must communicate is position, which the fill, the
border, the checkmark glyph and `aria-current` already carry four ways over.

**Let the caseworker ground keep the navy band.** Rejected: measured fine at
its own floors and visually a footer from a different site. The band's job is
to be the page's end, and it does that by belonging to the page.

## Amendment (part 23): the machine ground is the editor it shows

**Why this is an amendment and not ADR-037.** The decision above is that a
phase is a GROUND and that a ground is a token set on a body class. Part 23
changes values inside one of those token sets and adds five tokens to it. The
mechanism is untouched: the ladder is still the light one inverted, no
component gained an override, and the "a ground declares only custom
properties" test still passes on the block. The Consequences section above
anticipated exactly this - "revisions stay token-level cheap" - so recording it
as a second architectural decision would inflate a re-paint into a precedent.

**What moved.** The machine ground was this project's own navy. The pages it
paints are the ones that show what the machine did internally, and the surface
a reader already associates with a machine at work is an editor, so the ladder
is re-anchored on One Dark Pro's own chrome: `#1b1d23` as the canvas, `#21252b`
as the wash, `#282c34` - the editor background itself - as the card, `#2c313a`
and `#31363f` above it. The foreground family is anchored on `#abb2bf`, that
theme's editor foreground, sitting in the MIDDLE of the ink ladder rather than
at the top of it, because in a syntax-coloured buffer that colour is the calm
one. The semantic families become the theme's own accents and keep their
meanings.

**The ground is deliberately quieter, and that is a cost that is stated.** Body
text goes from 13.01:1 to 10.00:1 and the worst text pair from 5.36:1 to
4.78:1, which makes this the tightest of the three grounds instead of the most
comfortable. Every pair still clears its floor, every pair was recomputed
rather than carried over, and the test that computes them from the stylesheet
was extended over the new tokens.

**THE ELEMENT/TEXT SPLIT IS THE RULE THAT TRAVELLED, AND IT COST TWO ACCENTS.**
One Dark's red `#e06c75` measures 4.38:1 on the editor bed and 3.80:1 on the
surface above it - it cannot carry a word here - so it draws edges and a lifted
sibling carries the sentence, exactly as the sky blue did in part 16 and the
amber in part 21. The same happened inside the new syntax palette: the red used
for keys and the purple used for the sealed placeholders were both lifted along
their own hue until they cleared 4.5, rather than being used at their published
value because a theme file says so. A palette borrowed from somewhere else does
not import an exemption.

**The syntax palette is five tokens and it obeys the same rule as everything
else.** `--code-ink`, `--code-key`, `--code-value`, `--code-punct` and
`--code-seal` are declared in `:root` resolving to that page's own ink, and
re-pointed by `.ground-machine` alone. So the colouring is a property of the
ground, a machine block that ever rendered on a citizen or caseworker page is
plain monospaced text rather than an unmeasured palette, and no selector names
a ground in order to paint something.

**WHERE THE COLOUR STOPS IS THE DECISION WORTH RECORDING.** Colour on these
blocks means "this is machine text". It is spent on the structured working-copy
dump - a dotted path, the separator, the value - on the sealed placeholders
wherever they occur, and on inline identifiers. It is NOT spent on prose, on
tables, on headings, on the free text a visitor typed, or on the Anhoerung
letter the counterparty receives, which is a document and is set in the body
face on the same dark ground. The line is drawn on the SHAPE
`engine/demo/store.py` already records for each working-copy part, so it is
read rather than guessed at from content, and over-colouring is the failure
mode the brief named.

**The spans are written server-side and they wrap, never alter.** There is no
script and no highlighting library: `api/demo.py` splits the text into runs and
the template writes an element around each one, so Jinja escapes every run and
nothing is marked safe. A test asserts that the concatenation of every segment
is the input, and a second asserts that no segment boundary ever falls inside a
placeholder token.

**That last property is load-bearing for something outside this ADR, and the
canary sweep was strengthened rather than trusted.** A substring search over
markup cannot see a value that a tag fell inside of, so a leak check could have
gone green by no longer being able to look - the worst failure mode such a
check has. `assert_no_canary` now reads every blob twice: as served, and with
every tag removed, which is what reaches a reader. Two existing suites that
probed the working copy as markup now probe it as text for the same reason.
Any future part that wraps rendered text in elements inherits this obligation.

**One control changed shape rather than colour.** The hand-off to phase 3 was a
link inside a tinted panel and is now the primary button the intake submits
with, through the same `--grad-cta` / `--cta-ink` inversion this ADR already
records. The panels stay on the two-party side roads beside it, which is the
hierarchy the page should have had.

**And the landing page's call to action found the part-18 failure again.** The
first card in the grid is painted with the button's fill; the focus ring is
drawn at a 2px offset and therefore lands ON that fill, where `--focus`
measures 1.37:1. Overridden to `--cta-ink` at 6.51:1, in the same shape and for
the same reason as the closing band's white ring - and found the same way,
which is by computing the pair rather than by looking at the page.
