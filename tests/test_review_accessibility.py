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
  open on the caseworker pages from part 10 until the redesign).
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
    assert html.count("<table") == html.count('<div class="scroll-x">'), (
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


#: The element colours: too weak for text against at least one surface this
#: project ships, and each with a text-weight sibling in the same family.
#: Part 17 added the third when the demo ribbon left the red family.
ELEMENT_ONLY = {
    "brand": "brand-ink",  # 2.36:1 on white
    "alarm": "alarm-text",  # 4.32:1 on the darkest surface
    "caution": "caution-text",  # 4.36:1 on its own tint, 4.24:1 on the canvas
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
