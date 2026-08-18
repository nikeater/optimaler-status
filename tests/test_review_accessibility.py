"""BITV 2.0 posture, checked where a checker can check it (P-15).

**What this file is not.** It is not an accessibility audit. EN 301 549 V3.2.1
and WCAG 2.1 AA contain criteria no static test can decide - whether a heading
describes its section, whether an error message helps, whether a colour
contrast survives a projector - and a suite that claimed otherwise would be
worse than none, because it would produce a green tick where a human has to
look. The self-assessment lives in ``docs/accessibility-selfcheck.md`` and says
which criteria pass, which need judgment, and which need the external audit
that is pilot scope.

No pure-python axe-core equivalent exists (axe is a browser engine plus a rule
set; the Python packages that carry the name drive a real browser through
Selenium, which is not a dependency this project will take on for six pages).
So this file tests the MECHANICAL criteria, which are exactly the ones that
regress silently when somebody adds a form field:

* every form control has a programmatically associated label,
* every table has a caption and header cells with a scope,
* every page has one h1, a skip link as its first focusable element, and
  landmark elements,
* the document declares a language,
* no control is pointer-only, and no state is carried by colour alone -
  checked by asserting that every flag tone also produces words,
* no stylesheet removes a focus outline, and the design system restyles it,
* 1.4.10 reflow: every wide table scrolls inside its own container so the page
  body never scrolls sideways at 320 CSS px (part 15; this is the row that was
  open on the caseworker pages from part 10 until the redesign),
* the element colours never carry text, over a NAMED SET so that a new one
  joins the rule by being declared rather than by being remembered (part 16,
  extended in 17 and again in 21 when the amber family arrived),
* and, since part 21, the CONTRAST FLOORS THEMSELVES on every ground. The three
  phase grounds are token sets, so a ground that re-points `--ink` without
  re-pointing the surface under it is a whole page of unreadable text produced
  by four lines of CSS. Part 18 shipped a focus ring at 1.34:1 on a surface
  that had just been invented, and it was found by arithmetic rather than by
  looking; the arithmetic is in this file now, computed from the stylesheet, so
  the next ground cannot be measured only by whoever adds it.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from engine.config_loader import ConfigBundle
from engine.draft import InMemoryDraftStore, draft_case
from engine.draft.projection import facts_from
from engine.journal import InMemoryJournalStore
from engine.notify import InMemoryOutbox
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore, text_seal_detector

UNIT = "Referat_312_Renten"
ITEM = "ar-0011-ohne-rentenbeginn"
INGESTED_AT = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)

#: Every page this suite holds to the mechanical bar. The first four are part
#: 10's caseworker surface; ``metrics`` and ``inbox`` joined in part 15, when
#: the redesign gave them the same shell as the rest.
PAGES = ("overview", "queue", "clearing", "case", "metrics", "inbox")

#: Controls that need a label. ``hidden`` carries no user-visible value and
#: ``submit`` labels itself with its own text.
LABELLED_INPUT_TYPES = frozenset({"text", "checkbox", "radio", "number", "date"})


class _Page(HTMLParser):
    """A deliberately small HTML model: enough for the mechanical criteria."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.labels_for: list[str] = []
        self.controls: list[dict[str, str]] = []
        self.ids: list[str] = []
        self.headings: list[str] = []
        self.tables = 0
        self.captions = 0
        self.th_total = 0
        self.th_with_scope = 0
        self.landmarks: list[str] = []
        self.first_link: str | None = None
        self.buttons = 0
        self.lang: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: (value or "") for key, value in attrs}
        if identifier := attributes.get("id"):
            self.ids.append(identifier)
        if tag == "html":
            self.lang = attributes.get("lang")
        elif tag == "label":
            self.labels_for.append(attributes.get("for", ""))
        elif tag in ("input", "select", "textarea"):
            self.controls.append({"tag": tag, **attributes})
        elif tag == "button":
            self.buttons += 1
        elif tag in ("h1", "h2", "h3", "h4"):
            self.headings.append(tag)
        elif tag == "table":
            self.tables += 1
        elif tag == "caption":
            self.captions += 1
        elif tag == "th":
            self.th_total += 1
            if attributes.get("scope"):
                self.th_with_scope += 1
        elif tag in ("header", "nav", "main", "footer", "section"):
            self.landmarks.append(tag)
        elif tag == "a" and self.first_link is None:
            self.first_link = attributes.get("href", "")


def _parse(html: str) -> _Page:
    page = _Page()
    page.feed(html)
    return page


@pytest.fixture
def pages(config: ConfigBundle, gold_v4_dir: Path) -> Iterator[dict[str, str]]:
    journal, vault, drafts = (
        InMemoryJournalStore(),
        InMemoryVaultStore(),
        InMemoryDraftStore(),
    )
    payload = json.loads((gold_v4_dir / f"{ITEM}.json").read_text(encoding="utf-8"))
    result = run_pipeline(
        payload,
        config=config,
        journal=journal,
        vault=vault,
        now=INGESTED_AT,
        text_detector=text_seal_detector(with_ner=False),
    )
    case_id = result.decision.case_id
    draft_case(
        journal.read(case_id),
        config=config,
        journal=journal,
        vault=vault,
        drafts=drafts,
        facts=facts_from(result.extractions),
        now=INGESTED_AT,
    )
    app = create_app(
        config=config,
        journal=journal,
        vault=vault,
        text_detector=text_seal_detector(with_ner=False),
        outbox=InMemoryOutbox(),
        drafts=drafts,
    )
    with TestClient(app) as client:
        yield {
            "overview": client.get("/review").text,
            "queue": client.get(f"/review/queue/{UNIT}").text,
            "clearing": client.get("/review/queue/__clearing__").text,
            "case": client.get(f"/review/case/{case_id}?unit={UNIT}").text,
            # Part 15: the self-check has always named these two as in scope
            # (they share the stylesheet), and until the redesign gave them the
            # same shell - a skip link, a nav landmark, `main id="inhalt"` -
            # they could not be held to the same bar. Now they can.
            "metrics": client.get("/metrics").text,
            "inbox": client.get("/inbox").text,
        }


@pytest.mark.parametrize("name", PAGES)
def test_every_form_control_has_an_associated_label(
    name: str, pages: dict[str, str]
) -> None:
    """WCAG 3.3.2 / 4.1.2: a control a screen reader cannot name is unusable."""
    page = _parse(pages[name])
    labelled = set(page.labels_for)
    for control in page.controls:
        if control["tag"] == "input" and control.get("type") == "hidden":
            continue
        if control["tag"] == "input" and control.get("type") not in (
            LABELLED_INPUT_TYPES
        ):
            continue
        identifier = control.get("id", "")
        assert identifier, f"{name}: control without an id: {control}"
        assert identifier in labelled, f"{name}: no <label for> for {identifier!r}"


@pytest.mark.parametrize("name", PAGES)
def test_every_page_has_one_h1_a_skip_link_and_landmarks(
    name: str, pages: dict[str, str]
) -> None:
    """WCAG 2.4.1 / 1.3.1: bypass blocks, and structure a reader can navigate."""
    page = _parse(pages[name])
    assert page.lang == "de", f"{name}: the document must declare its language"
    assert page.headings.count("h1") == 1, f"{name}: exactly one h1"
    assert page.headings[0] == "h1", f"{name}: the h1 comes first"
    # The skip link is the FIRST focusable element and points at <main>.
    assert page.first_link == "#inhalt", f"{name}: no skip link first"
    assert "inhalt" in page.ids
    for landmark in ("header", "nav", "main", "footer"):
        assert landmark in page.landmarks, f"{name}: no <{landmark}>"


@pytest.mark.parametrize("name", PAGES)
def test_no_heading_level_is_skipped(name: str, pages: dict[str, str]) -> None:
    """WCAG 1.3.1: a jump from h1 to h3 tells a reader a section is missing."""
    levels = [int(tag[1]) for tag in _parse(pages[name]).headings]
    for previous, current in itertools.pairwise(levels):
        assert current <= previous + 1, f"{name}: {previous} -> {current}"


@pytest.mark.parametrize("name", PAGES)
def test_every_table_has_a_caption_and_scoped_headers(
    name: str, pages: dict[str, str]
) -> None:
    """WCAG 1.3.1: a data table without headers is a grid of unlabelled cells."""
    page = _parse(pages[name])
    assert page.captions == page.tables, (
        f"{name}: {page.tables} tables, {page.captions} captions"
    )
    assert page.th_with_scope == page.th_total, f"{name}: a th without a scope"


@pytest.mark.parametrize("name", PAGES)
def test_nothing_depends_on_a_script_or_a_pointer(
    name: str, pages: dict[str, str]
) -> None:
    """WCAG 2.1.1: every action is a link, a button or a submitted form.

    No ``onclick``, no ``href="#"`` acting as a button, no ``div`` with a
    handler. The pages ship htmx and use it for progressive enhancement only:
    with scripting off, a form still posts and a link still navigates.
    """
    html = pages[name]
    assert "onclick" not in html
    assert "onmouseover" not in html
    assert 'href="#"' not in html
    assert not re.search(r"<div[^>]*hx-(get|post)", html)


def test_no_stylesheet_ever_removes_the_focus_outline() -> None:
    """WCAG 2.4.7: the one CSS rule that silently breaks keyboard use.

    Swept over EVERY stylesheet in ``ui/static`` rather than over two files
    named here (part 15). The design system moved the focus ring into
    ``system.css``, and a check that named its files by hand would have to be
    edited every time one is added - which is exactly when it stops being run.
    """
    sheets = sorted(Path("ui/static").glob("*.css"))
    assert sheets, "no stylesheet found"
    for sheet in sheets:
        css = sheet.read_text(encoding="utf-8")
        assert "outline: none" not in css, sheet.name
        assert "outline: 0" not in css, sheet.name
    system = Path("ui/static/system.css").read_text(encoding="utf-8")
    assert ":focus-visible" in system
    assert ".skip-link:focus" in system
    # And it is RESTYLED rather than merely present: a visible weight and an
    # offset, so it survives on top of a tinted card.
    assert "outline: 3px solid var(--focus)" in system
    assert "outline-offset" in system


def test_a_flag_never_carries_its_meaning_in_colour_alone(
    pages: dict[str, str],
) -> None:
    """WCAG 1.4.1: every tone class sits next to a sentence saying the same."""
    for name in ("queue", "clearing"):
        html = pages[name]
        for tone in re.findall(r'class="flag flag-(\w+)"', html):
            assert tone in ("neutral", "attention")
        # A flag element always contains a <strong> label and prose after it.
        for block in re.findall(r'<li class="flag flag-\w+">(.*?)</li>', html, re.S):
            assert "<strong>" in block
            assert len(re.sub(r"<[^>]+>", "", block).strip()) > 30


# ---------------------------------------------------- 1.4.10 reflow (part 15) ---


@pytest.mark.parametrize("name", PAGES)
def test_every_page_is_built_to_reflow_at_320_css_pixels(
    name: str, pages: dict[str, str]
) -> None:
    """1.4.10, which stayed open on these pages from part 10 until part 15.

    The gap was never the layout - it is a single column with a `max-width` -
    but the WIDE TABLES: the case view's span and journal tables, the queue
    census, the eleven tables of the metrics panel. A table wider than the
    viewport drags the whole document sideways, and a criterion that says
    "content can be presented without scrolling in two dimensions" is then
    simply not met.

    A static check cannot measure a viewport, so it checks the three things
    that make reflow possible and whose absence makes it impossible: the
    viewport meta, every wide table inside its OWN scroll container so the
    container scrolls rather than the body, and no inline width anywhere. The
    same three the two citizen pages have been held to since part 13; the
    self-check row says out loud that this is a static check and not a
    measurement in a browser.
    """
    html = pages[name]
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in html
    # Counted with a pattern rather than with a literal since part 18: the
    # queue's container carries `class="scroll-x is-tall"`, which gives its own
    # sticky table head something to stick to. The assertion is the same one -
    # one scroll container per table, no table outside one - and it now also
    # holds for a container that has been modified rather than only for the
    # bare class attribute somebody happened to write first.
    containers = re.findall(r'<div class="scroll-x[^"]*">', html)
    assert html.count("<table") == len(containers), (
        f"{name}: a table outside a scroll container"
    )
    assert "style=" not in html, f"{name}: an inline style"
    assert "width:" not in html, f"{name}: a hard-coded width"


def test_the_design_system_carries_the_reflow_rules() -> None:
    """And they live in ONE place, so every page gets them (part 15).

    Before the redesign the reflow rules were in ``demo.css``, which only the
    three citizen-facing pages loaded - which is exactly why the caseworker
    row stayed open. They are in the design system now.
    """
    system = Path("ui/static/system.css").read_text(encoding="utf-8")
    assert "overflow-x: auto" in system
    assert "@media (max-width: 40rem)" in system
    # The two-column definition list is what actually overflows at 320 px.
    assert "grid-template-columns: minmax(0, 1fr)" in system
    for sheet in sorted(Path("ui/static").glob("*.css")):
        css = sheet.read_text(encoding="utf-8")
        assert not re.search(r":\s*\d{3,}px", css), f"{sheet.name}: fixed pixel width"
        # Every length that scales with the reader's font size, or the "resize
        # text to 200 percent" criterion (1.4.4) fails with it.
        assert not re.search(r"font-size:\s*\d+px", css), f"{sheet.name}: px font size"


def test_the_three_things_a_static_reflow_check_could_not_see() -> None:
    """Part 17: measured at 320 px in a browser, and three pages failed.

    THIS TEST CANNOT MEASURE A VIEWPORT and does not pretend to. It pins the
    three declarations that fixed three real two-axis scrolls, so that removing
    one has to be deliberate. The measurement itself needs a browser and was
    made in one; the row in `docs/accessibility-selfcheck.md` says so.

    What the test above checks is that wide content sits in a scroll container.
    All three failures were things that sat OUTSIDE the layout the check knows
    how to look at:

    1. `.sr-only` is `position: absolute`, and with no positioned ancestor its
       containing block was the initial one. The offscreen sentences inside a
       662px-wide queue table therefore escaped `.scroll-x` and pushed the
       document to 545 CSS px on a 320 px viewport.
    2. A `fieldset` does not shrink below its min-content width, and a `select`
       sizes to its longest option, so the case view's correction form held the
       document at 612 CSS px.
    3. `.tag` is `white-space: nowrap`, which is right for a two-word badge and
       wrong for the intake form's "wird versiegelt: ..." labels.
    """
    system = Path("ui/static/system.css").read_text(encoding="utf-8")
    scroll_x = re.search(r"\.scroll-x\s*\{([^}]*)\}", system)
    assert scroll_x and "position: relative" in scroll_x.group(1), (
        "an absolutely positioned .sr-only inside a wide table escapes an "
        "unpositioned scroll container and takes the document with it"
    )
    fieldset = re.search(r"\bfieldset\s*\{([^}]*)\}", system)
    assert fieldset and "min-width: 0" in fieldset.group(1), (
        "a fieldset holds its min-content width and a select sizes to its "
        "longest option"
    )
    narrow = system.split("@media (max-width: 40rem)")[1]
    assert re.search(r"\.tag\s*\{\s*white-space: normal;", narrow), (
        "a nowrap badge longer than the viewport pushes the page sideways"
    )


#: The selectors that may cap their width in CHARACTERS, and the reason each
#: one is allowed to (part 22).
#:
#: WHY THIS LIST EXISTS. A `ch` cap on flowing text is typographically orthodox
#: and, on this layout, wrong: the container is 80rem and a 68ch cap resolves to
#: roughly half of it, so the text renders as a column down the left of a box
#: that spans the page. Part 17 removed the caps from paragraphs, list items and
#: notices after the user read one as a broken layout; part 20 reintroduced one
#: by giving `.help` two jobs it had never been measured for (a fieldset intro
#: and a checkbox description, neither of them beside a control); part 22
#: removed it again. Three times is a class of bug, and a class of bug that
#: nothing checks comes back.
#:
#: WHAT IS STILL ALLOWED, and both are DISPLAY type on the landing page rather
#: than flowing body text: the hero headline, where a character cap is what
#: stops a three-word last line, and the hero lead directly under it. Neither
#: sits in a fieldset, a table or a form.
MEASURE_CAP_ALLOWED = {
    ".page-head-hero > h1": "display headline, capped so it breaks in three lines",
    ".hero-lead": "the one lead paragraph under the display headline",
}


def test_no_flowing_text_carries_a_character_measure_cap() -> None:
    """The part-17 rule, checked instead of remembered: the measure is the container.

    Every ``max-width`` in ``ch`` in either stylesheet has to belong to a
    selector in :data:`MEASURE_CAP_ALLOWED`. A new one is not forbidden - it has
    to be argued in that dict, next to the two that earned their place - and
    the ones this project has removed twice cannot come back by being typed
    again.
    """
    capped: dict[str, str] = {}
    for sheet in sorted(Path("ui/static").glob("*.css")):
        css = sheet.read_text(encoding="utf-8")
        # Comments carry prose about the caps that were removed; strip them so
        # the check reads declarations rather than the story of the fix.
        code = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", code):
            cap = re.search(r"max-width:\s*[\d.]+ch", block.group(2))
            if cap:
                capped[" ".join(block.group(1).split())] = cap.group(0)
    assert set(capped) == set(MEASURE_CAP_ALLOWED), (
        f"character measure caps changed: {capped}. The measure on this layout "
        "is the container (--measure on main); a ch cap inside it renders as a "
        "narrow column beside empty space, which is what part 17 removed and "
        "part 20 reintroduced. Declare a new one in MEASURE_CAP_ALLOWED with "
        "its reason, or take the cap off."
    )
    # And the class this bug came back through twice, named so the reason is
    # attached to the selector rather than to the count.
    system = Path("ui/static/system.css").read_text(encoding="utf-8")
    help_rule = re.search(r"^\.help\s*\{([^}]*)\}", system, re.MULTILINE)
    assert help_rule and "max-width" not in help_rule.group(1), (
        ".help is a fieldset intro and a checkbox description as well as a "
        "field's own sentence; it fills its container like every other prose "
        "block"
    )


#: The element colours: too weak for text against at least one surface this
#: project ships, and each with a text-weight sibling in the same family.
#: Part 17 added the third when the demo ribbon left the red family; part 21
#: added the fourth with the caseworker ground's amber.
ELEMENT_ONLY = {
    "brand": "brand-ink",  # 2.36:1 on white
    "alarm": "alarm-text",  # 4.32:1 on the darkest surface
    "caution": "caution-text",  # 4.36:1 on its own tint, 4.24:1 on the canvas
    "amber": "amber-ink",  # 3.83:1 on white, 3.06:1 on the palest amber
}


def test_the_element_colours_that_may_not_carry_text_never_do() -> None:
    """1.4.3, made structural instead of remembered (part 16).

    Every member of :data:`ELEMENT_ONLY` is a fill, a rule or an edge, and
    every one has a text-weight sibling for anything a reader has to read.
    That is a rule somebody would eventually forget, so it is checked over
    every stylesheet rather than written in a comment - and the check is over
    a NAMED SET, so a fourth element colour joins it by being added here rather
    than by being silently exempt.

    The regex is anchored so that `border-left-color: var(--brand)` does not
    read as a text colour, which is the one false positive worth avoiding: it
    is exactly the way these tokens are SUPPOSED to be used. It ends at the
    closing paren, so `color: var(--caution-text)` is the sibling and not a
    violation.
    """
    family = "|".join(sorted(ELEMENT_ONLY))
    text_colour = re.compile(rf"(?<![-\w])color:\s*var\(--({family})\)")
    for sheet in sorted(Path("ui/static").glob("*.css")):
        css = sheet.read_text(encoding="utf-8")
        found = text_colour.search(css)
        assert found is None, f"{sheet.name}: {found.group(0) if found else ''}"
    # And every sibling that carries the text exists, next to its element
    # colour, in the one place the palette is declared.
    system = Path("ui/static/system.css").read_text(encoding="utf-8")
    for element, text in ELEMENT_ONLY.items():
        assert f"--{element}:" in system, element
        assert f"--{text}:" in system, text
    assert "color: var(--brand-ink)" in system
    assert "color: var(--caution-text)" in system


# ------------------------------------------------- the three grounds (part 21) ---

#: The body class each page's phase renders on. A citizen page carries NO
#: class, because `:root` is the citizen ground - which is also why the four
#: citizen templates that extend `demo_base.html` leave its `ground` block
#: empty and their `<body>` renders the byte it always did.
GROUND_OF = {
    "overview": "ground-casework",
    "queue": "ground-casework",
    "clearing": "ground-casework",
    "case": "ground-casework",
    "metrics": "ground-machine",
    "inbox": "",
}

GROUNDS = ("citizen", "machine", "casework")

#: Selector per ground; the citizen ground IS `:root`.
GROUND_RULE = {
    "citizen": ":root",
    "machine": ".ground-machine",
    "casework": ".ground-casework",
}

#: The surface ladder every ground re-points, darkest to lightest on the light
#: grounds and the other way round on the dark one - which is the whole reason
#: a component needs no override to follow a ground.
SURFACES = (
    "--canvas",
    "--canvas-top",
    "--surface",
    "--surface-alt",
    "--surface-sunken",
)
TINTS = (
    "--tint-brand",
    "--tint-brand-soft",
    "--tint-ok",
    "--tint-alarm",
    "--tint-caution",
    "--tint-sample",
)
INKS = ("--ink", "--ink-soft", "--muted")

#: THE SYNTAX PALETTE (part 23) and the two beds it can land on. The machine
#: pages paint the working copy the way an editor paints a buffer, and a colour
#: that is a colour SCHEME is still a colour a reader has to read: every one of
#: these carries characters, so every one is held to the text floor rather than
#: to the 3:1 an edge gets. `pre` is `--surface-alt` and an inline `<code>` is
#: `--surface-sunken`, which is why both are checked and neither is assumed.
CODE_INKS = (
    "--code-ink",
    "--code-key",
    "--code-value",
    "--code-punct",
    "--code-seal",
)
CODE_BEDS = ("--surface", "--surface-alt", "--surface-sunken")

TEXT_FLOOR = 4.5
ELEMENT_FLOOR = 3.0


def _rule_body(css: str, selector: str) -> str:
    """The declarations of one top-level rule, comments stripped.

    Comments are removed before anything is read out of the block, so a ratio
    written in a comment can never be mistaken for a declared value.
    """
    start = css.index(selector + " {")
    depth, index = 0, start
    while True:
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                break
        index += 1
    return re.sub(r"/\*.*?\*/", "", css[start:index], flags=re.S)


def _declarations(css: str, selector: str) -> list[tuple[str, str]]:
    return re.findall(r"([\w-]+)\s*:\s*([^;]+);", _rule_body(css, selector))


def _tokens(css: str, ground: str) -> dict[str, str]:
    """Every custom property in force on one ground: `:root` plus its overlay."""
    resolved = {
        name: value
        for name, value in _declarations(css, ":root")
        if name.startswith("--")
    }
    if ground != "citizen":
        resolved.update(
            (name, value)
            for name, value in _declarations(css, GROUND_RULE[ground])
            if name.startswith("--")
        )
    return resolved


def _value(tokens: dict[str, str], spec: str, depth: int = 0) -> str:
    """Resolve a token through any chain of `var()` indirection."""
    assert depth < 8, f"a var() cycle at {spec!r}"
    spec = spec.strip()
    reference = re.fullmatch(r"var\((--[\w-]+)\)", spec)
    if reference:
        return _value(tokens, tokens[reference.group(1)], depth + 1)
    if spec.startswith("--"):
        return _value(tokens, tokens[spec], depth + 1)
    return spec


def _channel(value: float) -> float:
    value /= 255.0
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(colour: str) -> float:
    """WCAG 2.1 relative luminance of an `#rrggbb` value."""
    digits = colour.lstrip("#")
    red, green, blue = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def _ratio(one: str, other: str) -> float:
    first, second = _luminance(one), _luminance(other)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


def _pair(tokens: dict[str, str], foreground: str, background: str) -> float:
    return _ratio(_value(tokens, foreground), _value(tokens, background))


def _stops(tokens: dict[str, str], token: str) -> list[str]:
    """The colour stops of a gradient token."""
    found = re.findall(r"#[0-9a-fA-F]{6}", _value(tokens, token))
    assert found, f"{token} declares no colour stop"
    return found


@pytest.fixture(scope="module")
def system_css() -> str:
    return Path("ui/static/system.css").read_text(encoding="utf-8")


@pytest.mark.parametrize("ground", GROUNDS)
def test_every_ground_declares_the_whole_ladder(ground: str, system_css: str) -> None:
    """A HALF-DEFINED GROUND IS THE DEFECT THIS WHOLE PART IS EXPOSED TO.

    A ground is a token set: it re-points the surfaces, the inks, the phase
    family and the gradients that carry them, and every component then follows
    without an override. The failure mode is a ground that re-points `--ink`
    and forgets `--surface-sunken`, or re-points the surfaces and forgets
    `--focus` - four lines of CSS and a page of text nobody can read.

    So a ground that moves the canvas has to move ALL of it. `:root` is the
    citizen ground and defines everything by construction; the two others are
    checked against the same list.
    """
    tokens = _tokens(system_css, ground)
    required = (
        *SURFACES,
        *TINTS,
        *INKS,
        *CODE_INKS,
        "--brand",
        "--brand-ink",
        "--brand-ink-strong",
        "--focus",
        "--line",
        "--line-strong",
        "--band",
        "--band-ink",
        "--band-link",
        "--cta-ink",
        "--grad-cta",
        "--grad-mark",
        "--grad-rule",
        "--grad-panel",
        "--header-veil",
    )
    for token in required:
        assert token in tokens, f"{ground}: {token} is not declared anywhere"
    if ground == "citizen":
        return
    # And the overlay itself has to carry the ladder rather than inherit half of
    # it: a dark canvas under a light ground's ink is exactly the bug.
    overlay = {name for name, _ in _declarations(system_css, GROUND_RULE[ground])}
    for token in (*SURFACES, *INKS, "--focus", "--band", "--line-strong"):
        assert token in overlay, f"{ground}: {token} is inherited, not re-pointed"


@pytest.mark.parametrize("ground", GROUNDS)
def test_every_ground_meets_the_text_contrast_floor(
    ground: str, system_css: str
) -> None:
    """1.4.3, computed from the stylesheet on every ground rather than once.

    These are the pairs the components actually produce: an ink on a surface,
    an ink on a tint, a link on a card, a state colour on its own tint, the
    band's ink on the band, and the label on the button's gradient - which is
    only as good as its lightest stop, so both stops are checked.
    """
    tokens = _tokens(system_css, ground)
    for ink in INKS:
        for surface in (*SURFACES, *TINTS):
            measured = _pair(tokens, ink, surface)
            assert measured >= TEXT_FLOOR, (
                f"{ground}: {ink} on {surface} is {measured:.2f}:1"
            )
    for ink in ("--brand-ink", "--brand-ink-strong"):
        for surface in (*SURFACES, "--tint-brand", "--tint-brand-soft"):
            measured = _pair(tokens, ink, surface)
            assert measured >= TEXT_FLOOR, (
                f"{ground}: {ink} on {surface} is {measured:.2f}:1"
            )
    for ink, tint in (
        ("--ok", "--tint-ok"),
        ("--alarm-text", "--tint-alarm"),
        ("--caution-text", "--tint-caution"),
        ("--band-ink", "--band"),
        ("--band-link", "--band"),
    ):
        measured = _pair(tokens, ink, tint)
        assert measured >= TEXT_FLOOR, f"{ground}: {ink} on {tint} is {measured:.2f}:1"
    # The label on a fill, against BOTH ends of the gradient it sits on. Since
    # part 23 that fill also carries a whole card - the call to action in the
    # landing grid - so the same two numbers are the card's title and its body.
    for gradient in ("--grad-cta", "--grad-mark"):
        for stop in _stops(tokens, gradient):
            measured = _ratio(_value(tokens, "--cta-ink"), stop)
            assert measured >= TEXT_FLOOR, (
                f"{ground}: --cta-ink on {gradient} stop {stop} is {measured:.2f}:1"
            )
    # And the syntax palette, on both of the beds a machine token can land on.
    for ink in CODE_INKS:
        for bed in CODE_BEDS:
            measured = _pair(tokens, ink, bed)
            assert measured >= TEXT_FLOOR, (
                f"{ground}: {ink} on {bed} is {measured:.2f}:1"
            )


@pytest.mark.parametrize("ground", GROUNDS)
def test_the_focus_ring_is_visible_on_every_surface_of_every_ground(
    ground: str, system_css: str
) -> None:
    """2.4.7 / 1.4.11, and the part-18 lesson made into a gate.

    Part 18 added one surface and put a 1.34:1 focus ring on it - an indicator
    nobody could see, on the one band in the project that carries links. It was
    found by computing the pair and not by looking at the page. Part 21 adds
    ELEVEN surfaces at once, so the pair is computed here instead of being
    computed once by whoever wrote the ground.

    `--band` is deliberately absent from the list: the ring is white inside the
    closing band on every ground, because the band is dark on all three. That
    override is asserted just below, against the same floor.
    """
    tokens = _tokens(system_css, ground)
    for surface in (*SURFACES, *TINTS):
        measured = _pair(tokens, "--focus", surface)
        assert measured >= ELEMENT_FLOOR, (
            f"{ground}: the focus ring on {surface} is {measured:.2f}:1"
        )
    # The band's own ring, which is a literal white on all three grounds.
    assert ".site-footer :focus-visible" in system_css
    band_ring = _ratio("#ffffff", _value(tokens, "--band"))
    assert band_ring >= ELEMENT_FLOOR, (
        f"{ground}: the band's white ring is {band_ring:.2f}:1"
    )


@pytest.mark.parametrize("ground", GROUNDS)
def test_a_control_boundary_is_visible_on_every_ground(
    ground: str, system_css: str
) -> None:
    """1.4.11: `--line-strong` draws every boundary that identifies a control.

    `--line` is decorative on every ground and is deliberately not checked -
    it separates rows inside a card and identifies no component, which is the
    distinction the two weights exist to make.
    """
    tokens = _tokens(system_css, ground)
    for surface in SURFACES:
        measured = _pair(tokens, "--line-strong", surface)
        assert measured >= ELEMENT_FLOOR, (
            f"{ground}: --line-strong on {surface} is {measured:.2f}:1"
        )


def test_the_amber_family_aliases_element_to_element_and_ink_to_ink(
    system_css: str,
) -> None:
    """The caseworker ground points the blue family at the amber one, and the
    ELEMENT/TEXT SPLIT HAS TO TRAVEL WITH IT.

    `--brand: var(--amber)` is correct - both are element colours, and the
    regex above already forbids `color: var(--brand)` so the alias cannot leak
    into text. `--brand-ink: var(--amber)` would be the defect: a token whose
    entire job is to carry words, pointing at a 3.83:1 yellow, on every
    caseworker page at once, with no rule broken that anything else checks.
    """
    casework = dict(_declarations(system_css, ".ground-casework"))
    assert casework["--brand"].strip() == "var(--amber)"
    assert casework["--brand-ink"].strip() == "var(--amber-ink)"
    assert casework["--brand-ink-strong"].strip() == "var(--amber-ink-strong)"
    # An ink token may never be aliased to the element member of any family.
    elements = {f"var(--{name})" for name in ELEMENT_ONLY}
    for token, value in casework.items():
        if token.endswith(("-ink", "-ink-strong", "-text")) or token == "--focus":
            assert value.strip() not in elements, (
                f"{token} carries text and is aliased to an element colour"
            )


def test_the_machine_ground_is_anchored_on_the_editor_it_borrows_from(
    system_css: str,
) -> None:
    """Part 23: the dark ground's values ARE One Dark Pro's, and stay its.

    The claim this part makes is not "a dark theme" but "the editor a reader
    already associates with a machine at work", and the way that claim erodes
    is one plausible tweak at a time until the palette is nobody's. So the
    anchors are pinned with the role each plays in the theme, and a change to
    one fails here and says which.

    The lifted members are deliberately NOT pinned: they are derived, their
    reason is the contrast floor above, and the arithmetic that produced them is
    the test that has to keep holding.
    """
    machine = dict(_declarations(system_css, ".ground-machine"))
    for token, value, role in (
        ("--surface", "#282c34", "editor.background"),
        ("--surface-alt", "#2c313a", "list.hoverBackground"),
        ("--canvas-top", "#21252b", "sideBar.background"),
        ("--ink-soft", "#abb2bf", "editor.foreground"),
        ("--brand", "#61afef", "the blue"),
        ("--ok", "#98c379", "the green"),
        ("--alarm", "#e06c75", "the red"),
        ("--caution", "#d19a66", "the orange"),
        ("--caution-text", "#e5c07b", "the yellow"),
        ("--code-value", "#98c379", "strings"),
        ("--code-ink", "#56b6c2", "the cyan"),
        ("--band", "#3e4451", "editor.selectionBackground"),
    ):
        assert machine[token].strip() == value, f"{token} ({role})"
    # The ladder still ASCENDS on the dark ground, which is the property every
    # component depends on for its elevation to read the right way round.
    tokens = _tokens(system_css, "machine")
    rungs = ("--canvas", "--surface", "--surface-alt", "--surface-sunken")
    lit = [_luminance(_value(tokens, name)) for name in rungs]
    assert lit == sorted(lit), dict(zip(rungs, lit, strict=True))


@pytest.mark.parametrize("ground", ("citizen", "casework"))
def test_only_the_machine_ground_syntax_colours_anything(
    ground: str, system_css: str
) -> None:
    """A light page shows a working copy as plain text, and that is the design.

    Colour on this project's machine blocks means "this is machine text". The
    citizen and caseworker grounds have no machine blocks to say it about, so
    their syntax tokens resolve to the page's own ink and a block that ever
    rendered there is monospaced text rather than a palette measured against a
    surface it was never computed for.
    """
    tokens = _tokens(system_css, ground)
    for ink in CODE_INKS:
        assert _value(tokens, ink) == _value(tokens, "--ink"), ink


@pytest.mark.parametrize("ground", ("machine", "casework"))
def test_a_ground_is_a_token_set_and_never_a_component_rule(
    ground: str, system_css: str
) -> None:
    """ONE DESIGN SYSTEM, NOT THREE STYLESHEETS - held by a test rather than by
    an intention in a comment.

    The whole claim of this part is that a ground re-points tokens and that
    every component then follows for free. The moment a ground block carries a
    `padding` or a `background`, that claim is false and the next component
    added to the project renders correctly on one ground out of three.

    `color-scheme` is the one permitted exception and it is not styling: it
    tells the engine which way its OWN widgets - the caret, a scrollbar, a
    select's dropdown - should be drawn.
    """
    for name, _ in _declarations(system_css, GROUND_RULE[ground]):
        assert name.startswith("--") or name == "color-scheme", (
            f".{GROUND_RULE[ground]} declares {name}, which is a component rule"
        )


@pytest.mark.parametrize("name", PAGES)
def test_every_page_renders_on_the_ground_its_phase_belongs_to(
    name: str, pages: dict[str, str]
) -> None:
    """The page shell, pinned: a stable class on `<body>` and nothing else.

    The theming is CSS plus this class. It is UNCONDITIONAL - `/review*` and
    `/metrics` exist with the demo posture off and the ground is product
    styling in both postures - so no byte on these pages depends on a flag and
    the flag-off byte-identity suite is untouched by any of it.

    The inbox is the page this test exists to protect. It is what the applicant
    sees, so it is a CITIZEN page and stays on the light canvas, even though it
    is served from the same shell family as the caseworker screens and would be
    the easiest page in the project to theme by accident.
    """
    expected = GROUND_OF[name]
    body = re.search(r"<body[^>]*>", pages[name])
    assert body, f"{name}: no <body>"
    if expected:
        assert body.group(0) == f'<body class="{expected}">', name
    else:
        assert body.group(0) == "<body>", f"{name}: a citizen page carries no ground"


def test_the_step_indicator_never_reaches_the_caseworker_ground(
    pages: dict[str, str], system_css: str
) -> None:
    """Why the current step's number needs no amber override.

    `.phase-current .phase-mark` is the one component in the project that puts
    text on a `--brand` fill. It measures 6.74:1 on the citizen ground, 7.47:1
    on the machine ground with the override below, and would measure 4.32:1 on
    the caseworker ground - under the 4.5 a word needs.

    It cannot get there. The strip is included by `demo_base.html` alone, and
    the caseworker screens are rendered by `review_base.html`, which is a
    different shell: the caseworker UI never learns that a tour is running
    (part 13, ruling 5). This test is that argument, checked, rather than a
    rule with no user added against the day somebody changes it - and if
    somebody does change it, this fails and names the reason.
    """
    for name, html in pages.items():
        if GROUND_OF[name] == "ground-casework":
            assert "phase-strip" not in html, f"{name}: the strip reached casework"
            assert "phase-mark" not in html, f"{name}: the strip reached casework"
    # And the machine ground, where it DOES render, carries the override.
    assert ".ground-machine .phase-current .phase-mark" in system_css
