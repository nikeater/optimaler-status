"""Two languages, the header that switches them, and the chrome around it.

Five groups, and the first one is the one that keeps the other four honest.

1. **The table.** Every key is a PAIR, so "German exists and English does not"
   is not a state this project can be in. Every key a template asks for exists,
   swept out of the templates rather than listed here - a check that has to be
   edited whenever a page gains a sentence is a check that stops being run. And
   the markup rule: a phrase carrying a ``<`` is rendered through ``m()``, which
   escapes what it interpolates, and never through ``t()``.
2. **The switch.** ``?lang=`` sets a cookie and redirects back to the same URL
   without the parameter and with every other parameter intact. An unknown
   value is German rather than an error, like every other unknown value in this
   project. No JavaScript is involved.
3. **The scope.** Visitor pages are fully translated and declare their
   language; the caseworker screens stay German in BOTH settings and say so in
   one English line. Message bodies stay German always: they come from
   versioned configuration and are legal-text artifacts, not interface text.
4. **The header.** A wordmark, two links and a native ``details`` menu. It has
   to be operable with scripting off, it has to carry no demo route when the
   flag is off, and it must not put a ``<form>`` or a ``<button>`` on the inbox
   page, which is the one page in this project that has neither (ADR-005).
5. **The hero.** Five stages, five captions as REAL TEXT in the document in
   both languages, one keyframe set with five delays so the picture and the
   sentence cannot drift apart, and a ``prefers-reduced-motion`` answer that
   shows all five at once instead of freezing on an invisible one.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from api.app import create_app
from api.i18n import (
    DEFAULT_LANGUAGE,
    LANG_COOKIE,
    LANG_PARAM,
    LANGUAGES,
    TABLE,
    PageContext,
    phrase,
    resolve_language,
    strip_language,
)
from api.metrics import TEMPLATE_DIR, set_demo_posture
from engine.config_loader import ConfigBundle
from engine.demo import DEMO_MODE_ENV, INGEST_TOKEN_ENV, DemoPosture
from engine.demo import demo_posture as posture_cache
from engine.draft import InMemoryDraftStore
from engine.journal import InMemoryJournalStore
from engine.notify import InMemoryOutbox
from engine.redact import InMemoryVaultStore, text_seal_detector

TOKEN = "i18n-token"

#: The visitor-facing pages, which are translated in full.
VISITOR_PAGES = ("/", "/hinweise", "/demo/rundgang", "/demo/antrag", "/inbox")

#: The caseworker pages, which stay German in both settings.
GERMAN_PAGES = ("/review", "/review/queue/Referat_312_Renten", "/metrics")

#: Key families a template builds at render time (``t("kind." ~ kind)``). The
#: sweep below cannot see those, so every member is named here instead.
DYNAMIC_KEYS = (
    *(f"phase.{phase}" for phase in ("antrag", "maschine", "sachbearbeitung")),
    *(f"tier.{tier}" for tier in (1, 2, 3)),
    *(f"channel.{channel}" for channel in ("fit_connect", "email")),
    *(f"channel.note.{channel}" for channel in ("fit_connect", "email")),
    *(
        f"kind.{kind}"
        for kind in (
            "VSNR",
            "GEBDAT",
            "ADDR",
            "NAME",
            "ORG",
            "BNR",
            "IBAN",
            "STID",
            "AKTZ",
            "EMAIL",
            "TEL",
            "TEXT",
        )
    ),
    *(f"landing.hero.s{step}" for step in range(1, 6)),
    *(f"landing.hero.c{step}" for step in range(1, 6)),
    *(f"tour.toc.{step}" for step in range(1, 7)),
)


@pytest.fixture(autouse=True)
def restore_posture() -> Iterator[None]:
    """Leave the process the way it was found (the part-11 fixture)."""
    yield
    posture_cache.cache_clear()
    set_demo_posture(DemoPosture())


def build_client(
    config: ConfigBundle,
    monkeypatch: pytest.MonkeyPatch,
    *,
    demo: bool = True,
) -> TestClient:
    """The real app, on in-memory stores, with the deterministic union."""
    if demo:
        monkeypatch.setenv(DEMO_MODE_ENV, "1")
        monkeypatch.setenv(INGEST_TOKEN_ENV, TOKEN)
    else:
        monkeypatch.delenv(DEMO_MODE_ENV, raising=False)
    return TestClient(
        create_app(
            config=config,
            journal=InMemoryJournalStore(),
            vault=InMemoryVaultStore(),
            text_detector=text_seal_detector(with_ner=False),
            outbox=InMemoryOutbox(),
            drafts=InMemoryDraftStore(),
        )
    )


def in_english(client: TestClient, path: str) -> str:
    """One page, in English, through the switch a visitor actually uses."""
    client.get(f"{path}?{LANG_PARAM}=en", follow_redirects=True)
    return client.get(path).text


def shown(key: str, lang: str = DEFAULT_LANGUAGE, **values: object) -> str:
    """One phrase as it appears in the rendered page.

    Jinja escapes what ``t()`` returns, so a sentence containing a quotation
    mark or an apostrophe is NOT in the HTML verbatim. Comparing against the
    raw phrase would make this suite pass on the sentences without punctuation
    and fail on the ones with it, which is the wrong half to test.
    """
    return str(escape(phrase(key, lang, **values)))


def templates() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(TEMPLATE_DIR.glob("*.html"))
    }


# ---------------------------------------------------------------- 1. table ---


def test_the_table_holds_a_pair_for_every_key() -> None:
    """A key that exists in one language and not the other is unrepresentable.

    Not a comparison of two dictionaries but a property of the shape: the value
    IS the pair. This test exists to keep that shape rather than to compare
    anything, and it also catches the two ways a pair can still be wrong - an
    empty half, or an English half that was never written and is a copy of the
    German one.
    """
    assert TABLE
    same = []
    for key, pair in TABLE.items():
        assert isinstance(pair, tuple), key
        assert len(pair) == len(LANGUAGES), key
        for text in pair:
            assert isinstance(text, str) and text.strip(), key
        if pair[0] == pair[1]:
            same.append(key)
    # A handful of phrases are identical in both languages on purpose: the note
    # that is English by definition, and the words German and English happen to
    # share. Anything NOT on this list is a translation somebody forgot.
    assert sorted(same) == sorted(
        (
            "review.english_note",
            "chrome.nav.home",
            "phase.number",
            "kind.NAME",
            "pipeline.d.gaps.col2",
            "pipeline.e.anomaly.score",
        )
    ), same


def test_every_key_a_template_asks_for_exists() -> None:
    """Swept out of the templates, not listed by hand.

    A missing key does not raise - a typo must not turn a citizen-facing page
    into a 500 - so it would otherwise reach a reader as the key itself printed
    on the page. This is where it is caught instead.
    """
    asked: set[str] = set()
    for name, source in templates().items():
        for key in re.findall(r"\b[tm]\(\s*\"([^\"]+)\"\s*[,)]", source):
            asked.add(key)
        assert "{{ t(" not in source or asked, name
    assert asked, "the sweep found no keys at all"
    missing = sorted(key for key in asked if key not in TABLE)
    assert not missing, missing


def test_every_key_a_template_builds_at_render_time_exists() -> None:
    """The families a template concatenates, which the sweep cannot see."""
    missing = sorted(key for key in DYNAMIC_KEYS if key not in TABLE)
    assert not missing, missing


def test_only_the_marked_phrases_carry_markup() -> None:
    """The two halves of the escaping rule, checked against the templates.

    ``m()`` returns Markup and ESCAPES what it interpolates, so a case id or a
    unit name substituted into a sentence that carries a ``<strong>`` is still
    escaped. ``t()`` returns a plain string that Jinja escapes whole. Getting
    the two the wrong way round produces either visible tag soup or an
    unescaped interpolation, so both directions are asserted.
    """
    plain: set[str] = set()
    marked: set[str] = set()
    for source in templates().values():
        plain.update(re.findall(r"\bt\(\s*\"([^\"]+)\"\s*[,)]", source))
        marked.update(re.findall(r"\bm\(\s*\"([^\"]+)\"\s*[,)]", source))
    wrong = sorted(key for key in plain if "<" in TABLE.get(key, ("", ""))[0])
    assert not wrong, f"markup rendered through t(): {wrong}"
    flat = sorted(key for key in marked if "<" not in TABLE.get(key, ("", ""))[0])
    assert not flat, f"plain text rendered through m(): {flat}"


def test_markup_phrases_still_escape_what_they_interpolate() -> None:
    """The reason ``m()`` exists rather than a ``|safe`` filter."""
    page = PageContext()
    rendered = page.m("pipeline.d.routing.body", unit="<script>x</script>")
    assert "<strong>" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


# --------------------------------------------------------------- 2. switch ---


def test_an_unknown_language_is_german_and_never_an_error() -> None:
    """Same discipline as the unit picker and the persona picker."""
    assert resolve_language("en") == "en"
    assert resolve_language("de") == "de"
    assert resolve_language("fr") == DEFAULT_LANGUAGE
    assert resolve_language(None) == DEFAULT_LANGUAGE
    assert resolve_language("") == DEFAULT_LANGUAGE
    assert resolve_language(None, "en") == "en"


def test_stripping_the_parameter_keeps_every_other_one() -> None:
    """A toggle on a filtered page must not throw the filter away."""
    assert strip_language("/review", "") == "/review"
    assert strip_language("/review", "lang=en") == "/review"
    assert (
        strip_language("/review/queue/x", "unit=A&lang=en&highlight=c")
        == "/review/queue/x?unit=A&highlight=c"
    )
    assert strip_language("/demo/antrag", "persona=p") == "/demo/antrag?persona=p"


def test_the_parameter_sets_a_cookie_and_redirects_back(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """303 to the clean URL, with the other parameters intact."""
    client = build_client(config, monkeypatch)
    switched = client.get(
        "/demo/antrag?persona=musterfrau_statusfeststellung&lang=en",
        follow_redirects=False,
    )
    assert switched.status_code == 303
    assert (
        switched.headers["location"]
        == "/demo/antrag?persona=musterfrau_statusfeststellung"
    )
    assert client.cookies[LANG_COOKIE] == "en"
    # And the cookie governs from then on, with no parameter in sight.
    assert '<html lang="en">' in client.get("/demo/antrag").text


def test_an_unknown_language_parameter_is_not_a_redirect(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``?lang=klingon`` renders the page it asked for, in German."""
    client = build_client(config, monkeypatch)
    page = client.get("/demo/rundgang?lang=klingon")
    assert page.status_code == 200
    assert '<html lang="de">' in page.text
    assert LANG_COOKIE not in client.cookies


def test_switching_needs_no_javascript_anywhere(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The toggle is two links. No script, no handler, no form."""
    client = build_client(config, monkeypatch)
    body = client.get("/demo/rundgang").text
    assert f"?{LANG_PARAM}=en" in body
    assert "onchange" not in body
    assert "onclick" not in body
    assert "<script" not in body


# ---------------------------------------------------------------- 3. scope ---


@pytest.mark.parametrize("path", VISITOR_PAGES)
def test_a_visitor_page_is_fully_translated(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """English means English: the document says so and the German is gone."""
    client = build_client(config, monkeypatch)
    german = client.get(path).text
    assert '<html lang="de">' in german
    english = in_english(client, path)
    assert '<html lang="en">' in english
    # The chrome every page carries, in both languages.
    assert shown("chrome.menu", "en") in english
    assert shown("chrome.subtitle", "en") in english
    assert shown("chrome.subtitle") not in english


def test_the_tour_says_the_same_six_things_in_english(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page that carries the most prose, checked step by step."""
    client = build_client(config, monkeypatch)
    body = in_english(client, "/demo/rundgang")
    for step in range(1, 7):
        assert shown(f"tour.toc.{step}", "en") in body, step
        assert f'id="schritt-{step}"' in body, step
    assert shown("tour.s1.p1", "en") in body
    assert shown("tour.s1.p1") not in body


@pytest.mark.parametrize("path", GERMAN_PAGES)
def test_a_caseworker_page_stays_german_and_says_why(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """German in both settings, with one English line in English mode.

    The line is `lang="en"` inside a `lang="de"` document, which is what 3.1.2
    asks for and what makes a screen reader switch voice for one sentence
    instead of reading English with German phonemes.
    """
    client = build_client(config, monkeypatch)
    german = client.get(path).text
    assert '<html lang="de">' in german
    assert shown("review.english_note") not in german

    english = in_english(client, path)
    assert '<html lang="de">' in english, "the caseworker screens stay German"
    assert shown("review.english_note") in english
    assert f'lang="en">{shown("review.english_note")}' in english


def test_the_message_bodies_stay_german_in_english_mode(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A notification is a legal-text artifact, not interface text.

    It comes out of `config/notifications/notifications_v1.yaml` verbatim, it
    is what an applicant would actually have received, and a translated Realakt
    would be a different document. The inbox says so in English mode rather
    than leaving a reader to wonder why one block did not switch.
    """
    from engine.demo.personas import demo_personas

    client = build_client(config, monkeypatch)
    chosen = demo_personas().first
    client.post(
        "/demo/antrag",
        data={
            "persona": chosen.persona_id,
            "kanal": "fit_connect",
            **chosen.form_values(),
        },
        follow_redirects=False,
    )
    body = in_english(client, "/inbox")
    assert shown("inbox.german_note", "en") in body
    assert '<pre lang="de">' in body
    assert "Eingang Ihres Antrags" in body or "Antrag" in body


# --------------------------------------------------------------- 4. header ---


def test_the_menu_is_a_native_disclosure_and_needs_no_script(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`details`/`summary`: focusable, toggled by Enter and Space, no ARIA.

    A scripted dropdown would have needed a keydown handler, a focus trap and
    an `aria-expanded` maintained by hand, in exchange for nothing a visitor
    can see. What is asserted here is the absence of all four.
    """
    client = build_client(config, monkeypatch)
    body = client.get("/demo/rundgang").text
    assert '<details class="menu">' in body
    assert '<summary class="menu-summary">' in body
    assert 'aria-label="Hauptmenü"' in body
    for forbidden in ("onclick", "onkeydown", "aria-expanded", "tabindex="):
        assert forbidden not in body, forbidden
    # Every menu destination is a real link to a real route.
    for href in (
        "/",
        "/demo/rundgang",
        "/demo/antrag",
        "/review",
        "/inbox",
        "/metrics",
    ):
        assert f'href="{href}"' in body, href
        assert client.get(href).status_code == 200, href
    assert 'href="/hinweise"' in body


def test_the_header_carries_no_demo_route_with_the_flag_off(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag-off rule, applied to the one thing part 16 put on every page.

    The menu is the obvious way to leak a demo route onto a non-demo instance,
    because it is the same partial on every page. With the flag off it links
    the three routes that exist everywhere and nothing else, and the wordmark
    stops being a link at all - there is no start page to go to.
    """
    client = build_client(config, monkeypatch, demo=False)
    for path in ("/review", "/inbox", "/metrics"):
        body = client.get(path).text
        for demo_route in ("/demo/rundgang", "/demo/antrag", "/hinweise"):
            assert demo_route not in body, f"{demo_route} on {path}"
        assert 'href="/"' not in body, path
        assert '<span class="wordmark">' in body, path
        assert 'href="/review"' in body and 'href="/inbox"' in body
    assert client.get("/hinweise").status_code == 404


def test_the_header_adds_no_control_to_the_inbox(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The part-07 line survives a header with a menu in it (ADR-005).

    The inbox is the one page in this project with no `<form>` and no
    `<button>`, and it has to stay that way: a control there would turn a
    projection of the journal into something somebody approved. The menu is a
    disclosure and the toggle is two links, so neither element appears.
    """
    client = build_client(config, monkeypatch)
    body = client.get("/inbox").text
    assert "<form" not in body
    assert "<button" not in body
    assert '<details class="menu">' in body


def test_the_source_link_appears_only_when_it_is_configured(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A menu item pointing at github.com/OWNER/... is a broken link."""
    from engine.demo import REPO_URL_ENV, REPO_URL_PLACEHOLDER

    item = f"<span>{phrase('chrome.nav.source')}</span>"
    monkeypatch.delenv(REPO_URL_ENV, raising=False)
    unset = build_client(config, monkeypatch).get("/demo/rundgang").text
    assert REPO_URL_PLACEHOLDER not in unset
    assert item not in unset

    monkeypatch.setenv(REPO_URL_ENV, "https://example.invalid/repo")
    configured = build_client(config, monkeypatch).get("/demo/rundgang").text
    assert 'href="https://example.invalid/repo"' in configured
    assert item in configured


# ----------------------------------------------------------------- 5. hero ---


def test_the_hero_carries_five_captions_as_real_text(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selectable, searchable, translated - not glyphs inside the drawing.

    A caption baked into the SVG would be invisible to find-in-page, to a
    translation table and to anybody copying a sentence out of the page. These
    are `<p>` elements in the document, present whether or not they happen to
    be the one currently faded in.
    """
    client = build_client(config, monkeypatch)
    pages = {"de": client.get("/").text, "en": in_english(client, "/")}
    for lang in LANGUAGES:
        body = pages[lang]
        for step in range(1, 6):
            assert f'class="hero-caption hero-caption-{step}"' in body, step
            assert shown(f"landing.hero.c{step}", lang) in body, (lang, step)
            assert shown(f"landing.hero.s{step}", lang) in body, (lang, step)


def test_the_hero_is_a_picture_with_a_name_and_no_script(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`role="img"` with a title and a description, and zero JavaScript."""
    client = build_client(config, monkeypatch)
    body = client.get("/").text
    assert 'role="img"' in body
    assert 'aria-labelledby="hero-titel hero-beschreibung"' in body
    assert '<title id="hero-titel">' in body
    assert '<desc id="hero-beschreibung">' in body
    assert "<script" not in body
    assert "style=" not in body


#: Which delay every staggered hero element must carry, as a table rather than
#: as a set of values that happen to occur somewhere in the file.
#:
#: THIS TABLE EXISTS BECAUSE THE SET VERSION SHIPPED A BUG. Part 16 asserted
#: only that the four values appear in the stylesheet, which is equally true of
#: the right mapping and of its exact reverse - and the reverse is what was
#: released: the ring walked 1, 5, 4, 3, 2 while the envelope walked forward,
#: so the picture named one stage and the sentence beneath it named another.
#: A delay is only meaningful next to the selector it applies to, so that is
#: what is written down.
#:
#: The arithmetic, once: a stage is lit for the first fifth of `hero-beat`, a
#: negative delay ADVANCES the clock, and stage N is wanted in the Nth fifth of
#: a 16 second loop - so stage N needs -(16 - (N-1) * 3.2)s.
HERO_DELAYS = (
    (".hero-stage-2 .hero-ring", "-12.8s"),
    (".hero-stage-3 .hero-ring", "-9.6s"),
    (".hero-stage-4 .hero-ring", "-6.4s"),
    (".hero-stage-5 .hero-ring", "-3.2s"),
    (".hero-caption-2", "-12.8s"),
    (".hero-caption-3", "-9.6s"),
    (".hero-caption-4", "-6.4s"),
    (".hero-caption-5", "-3.2s"),
    # The padlock at stage 2 closes when the ring reaches stage 2.
    (".hero-shackle", "-12.8s"),
)


def test_the_hero_animation_is_one_timeline_and_answers_reduced_motion() -> None:
    """The timing property, and the fallback, asserted on the stylesheet.

    One keyframe set with five negative delays is what makes the ring and the
    caption of a stage share a clock; five separate keyframe sets would be five
    chances for the picture and the sentence to drift apart. And the
    reduced-motion answer has to be EXPLICIT: the design system's blanket rule
    freezes an animation at its last keyframe, which for a caption is
    `opacity: 0` - a fallback with no visible text at all.
    """
    css = Path("ui/static/demo.css").read_text(encoding="utf-8")
    assert "@keyframes hero-beat" in css
    # Both the ring and the caption ride the same keyframes.
    assert css.count("animation: hero-beat 16s linear infinite") == 2
    reduced = css.split("@media (prefers-reduced-motion: reduce)")[1]
    assert "animation: none" in reduced
    assert "opacity: 1" in reduced
    assert ".hero-captions {\n    display: block;\n  }" in reduced


def test_the_hero_stages_light_in_the_order_they_are_numbered() -> None:
    """Each staggered selector carries ITS delay, not merely some delay.

    See :data:`HERO_DELAYS`: the released part-16 stylesheet satisfied a
    value-only assertion while running the animation backwards. Reading the
    delay out of the block the selector opens is what makes a future edit have
    to disagree with a literal.
    """
    css = Path("ui/static/demo.css").read_text(encoding="utf-8")
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    for selector, delay in HERO_DELAYS:
        block = re.search(rf"(?<![-\w.]){re.escape(selector)}\s*\{{([^}}]*)\}}", rules)
        assert block, f"no rule block for {selector}"
        found = re.search(r"animation-delay:\s*(\S+?);", block.group(1))
        assert found, f"{selector} carries no animation-delay"
        assert found.group(1) == delay, (selector, found.group(1), delay)
    # Stage 1 is the reference frame and must NOT be staggered.
    assert not re.search(r"\.hero-stage-1 .hero-ring\s*\{[^}]*animation-delay", rules)
    # The envelope walks forward through the five stages on its own keyframes,
    # 192 user units apart, which is the order the rings now agree with.
    travel = re.search(r"@keyframes hero-travel\s*\{(.*?)\n\}", rules, re.DOTALL)
    assert travel
    # `translateX(0)` is unitless, the rest carry `px`; both are one stop.
    steps = [int(x) for x in re.findall(r"translateX\((\d+)(?:px)?\)", travel.group(1))]
    assert steps == sorted(steps), steps
    assert sorted(set(steps)) == [0, 192, 384, 576, 768]


def test_the_hero_survives_the_reflow_and_the_resize_rules() -> None:
    """No fixed pixel length and no px font size, hero included."""
    for name in ("system.css", "demo.css"):
        css = Path("ui/static").joinpath(name).read_text(encoding="utf-8")
        assert not re.search(r":\s*\d{3,}px", css), name
        assert not re.search(r"font-size:\s*\d+px", css), name
        assert "outline: none" not in css and "outline: 0" not in css, name


def test_the_hero_can_be_stopped_without_a_script(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WCAG 2.2.2, answered with a control on the page rather than a setting.

    The loop starts on its own, runs longer than five seconds and sits beside
    other content, so 2.2.2 is engaged and an operating-system preference is
    not a mechanism ON this page. The control is a labelled checkbox that
    precedes everything it pauses, which is what lets `:checked ~` reach the
    drawing and the captions with no script at all.

    It STOPS rather than freezing, and that distinction is the test: pausing
    the animation where it stands would hold four of the five captions at
    `opacity: 0`, and a pause button that hides the text is not a pause button.
    """
    client = build_client(config, monkeypatch)
    body = client.get("/").text
    assert '<input type="checkbox" id="hero-pause"' in body
    assert 'for="hero-pause"' in body
    assert shown("landing.hero.pause") in body
    assert "<script" not in body

    css = Path("ui/static/demo.css").read_text(encoding="utf-8")
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    assert "animation-play-state" not in rules, "a frozen caption is invisible"
    paused = [line for line in css.splitlines() if ".hero-pause:checked ~" in line]
    assert paused, "the pause control reaches nothing"
    assert ".hero-pause:checked ~ .hero-captions .hero-caption {" in css
    # The paused state IS the reduced-motion state: everything lit, all five
    # captions stacked. Both blocks say `animation: none` and `opacity: 1`.
    assert re.search(r"\.hero-pause:checked[^{]*\{\s*animation: none;", rules)
    assert re.search(r"\.hero-pause:checked[^{]*\{\s*opacity: 1;", rules)
