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
Selenium, which is not a dependency this project will take on for four pages).
So this file tests the MECHANICAL criteria, which are exactly the ones that
regress silently when somebody adds a form field:

* every form control has a programmatically associated label,
* every table has a caption and header cells with a scope,
* every page has one h1, a skip link as its first focusable element, and
  landmark elements,
* the document declares a language,
* no control is pointer-only, and no state is carried by colour alone -
  checked by asserting that every flag tone also produces words.
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
        }


@pytest.mark.parametrize("name", ["overview", "queue", "clearing", "case"])
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


@pytest.mark.parametrize("name", ["overview", "queue", "clearing", "case"])
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


@pytest.mark.parametrize("name", ["overview", "queue", "clearing", "case"])
def test_no_heading_level_is_skipped(name: str, pages: dict[str, str]) -> None:
    """WCAG 1.3.1: a jump from h1 to h3 tells a reader a section is missing."""
    levels = [int(tag[1]) for tag in _parse(pages[name]).headings]
    for previous, current in itertools.pairwise(levels):
        assert current <= previous + 1, f"{name}: {previous} -> {current}"


@pytest.mark.parametrize("name", ["overview", "queue", "clearing", "case"])
def test_every_table_has_a_caption_and_scoped_headers(
    name: str, pages: dict[str, str]
) -> None:
    """WCAG 1.3.1: a data table without headers is a grid of unlabelled cells."""
    page = _parse(pages[name])
    assert page.captions == page.tables, (
        f"{name}: {page.tables} tables, {page.captions} captions"
    )
    assert page.th_with_scope == page.th_total, f"{name}: a th without a scope"


@pytest.mark.parametrize("name", ["overview", "queue", "clearing", "case"])
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


def test_the_stylesheet_never_removes_the_focus_outline() -> None:
    """WCAG 2.4.7: the one CSS rule that silently breaks keyboard use."""
    for name in ("metrics.css", "review.css"):
        css = (Path("ui/static") / name).read_text(encoding="utf-8")
        assert "outline: none" not in css
        assert "outline: 0" not in css
    review = Path("ui/static/review.css").read_text(encoding="utf-8")
    assert ":focus-visible" in review
    assert ".skip-link:focus" in review


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
