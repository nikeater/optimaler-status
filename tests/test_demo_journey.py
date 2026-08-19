"""The guided showcase, end to end, and the promises around it.

Seven groups, and the first two are the ones that would be prose in a worse
repository.

1. **Flag-off identity.** With ``EINGANGSLOTSE_DEMO_MODE`` unset there is no
   ``/demo`` route, none in the OpenAPI document, no demo store in the process
   and no byte of difference in the caseworker queue page. Part 11 set the
   precedent; part 13 has more to hide and therefore more to assert.
2. **The token posture, from both sides.** A direct caller of ``POST /ingest``
   still gets 403; the intake page, holding the deployment's own token, gets
   the pipeline. And on an instance with NO token the intake page is closed
   too - the safe state is closed for everybody, including the demo app.
3. **The journey**, per persona and per channel, through the REAL app: submit,
   read the seven stages, follow the hand-off, confirm as a caseworker, see the
   loop close in the inbox.
4. **The demo store**: TTL, capacity, reset, and the structural guarantee that
   its working copy comes off the envelope and can therefore not hold a sealed
   value.
5. **The canary sweep** over every new page: a visitor sees their own typed
   values and nobody else's, and the working copy is placeholders throughout.
6. **Accessibility and reflow** for the citizen-facing pages, plus the two
   lines that must stay true forever: the queue is never reordered and the
   inbox never grows a control.
7. **The tour** (part 15): six steps in order, the intake posture stated
   honestly in BOTH of its states, a link into a SEEDED case so the seven
   stages are walkable before anybody submits, and no dead link on an instance
   where nothing was seeded. The inline English asides it used to carry were
   removed in part 16 and replaced by the header's language toggle, which
   ``tests/test_i18n.py`` covers.

Every client here injects the DETERMINISTIC detector union. That is the part-10
precedent (``tests/test_review_no_person.py``) and it is also the shipped demo's
posture: the container installs no ``[redact]`` extra (ADR-027 ruling 8), so a
test that ran with the optional model would be testing a configuration the demo
never runs. See KE-6 for what the model member does to a demo letter.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from jinja2 import Environment
from markupsafe import escape

from api import app as app_module
from api import demo as demo_view
from api import inbox as inbox_view
from api import review as review_view
from api.app import REFUSED_ENVELOPE, REFUSED_REDACTION, create_app
from api.i18n import phrase
from api.metrics import TEMPLATE_DIR, environment, set_demo_posture
from engine.config_loader import ConfigBundle
from engine.demo import DEMO_MODE_ENV, INGEST_HEADER, INGEST_TOKEN_ENV, DemoPosture
from engine.demo import demo_posture as posture_cache
from engine.demo.personas import (
    CHANNEL_EMAIL,
    CHANNEL_FORM,
    Persona,
    demo_personas,
)
from engine.demo.store import (
    DEFAULT_CAPACITY,
    DEFAULT_TTL,
    MAX_CHARS,
    DemoStore,
    DemoSubmission,
    TypedValue,
)
from engine.draft import InMemoryDraftStore
from engine.journal import InMemoryJournalStore
from engine.notify import InMemoryOutbox
from engine.redact import (
    InMemoryVaultStore,
    RedactionRefusedError,
    default_policy,
    redact_payload,
    text_seal_detector,
)
from engine.redact.placeholders import PLACEHOLDER_RE

TOKEN = "demo-journey-token"
BASE_TIME = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)

#: The arcs, as the showcase promises them: which queue the visitor is handed
#: to, and what phase 3 has waiting there. Since part 22 all four file a
#: Statusfeststellung, so all four are handed to the Clearingstelle - and the
#: two tiers are the par. 7a shape: complete lands on the table's default tier
#: 3 (tier 1 is disabled for this procedure and no other row matches), an
#: incomplete one matches the tier-2 row.
ARCS = {
    "schliebermann_statusfeststellung": ("Referat_340_Clearingstelle", "Tier 3"),
    "beispielmann_ohne_taetigkeitsbeginn": ("Referat_340_Clearingstelle", "Tier 2"),
    "musterfrau_statusfeststellung": ("Referat_340_Clearingstelle", "Tier 3"),
    "musterkind_taetigkeitsbeginn_voraus": ("Referat_340_Clearingstelle", "Tier 3"),
}

#: Every citizen-facing page behind the demo flag. The tour joined in part 15,
#: the landing page and the disclaimer page in part 16, and the counterparty
#: surface in part 19; all six are held to the same mechanical bar as the two
#: part-13 pages.
#:
#: ``gegenpartei`` is swept WITHOUT a reference, which is its own decision: the
#: page a stranger reaches from the menu is the one that has to explain itself,
#: and it is also the state that is hardest to notice being broken. The states
#: with a live and an answered hearing are walked in
#: ``tests/test_demo_gegenpartei.py``.
CITIZEN_PAGES = ("start", "hinweise", "rundgang", "antrag", "gegenpartei", "pipeline")

PATHS = {
    "start": "/",
    "hinweise": "/hinweise",
    "rundgang": "/demo/rundgang",
    "antrag": "/demo/antrag",
    "gegenpartei": "/demo/gegenpartei",
}


def citizen_path(page: str, case_id: str) -> str:
    """The URL for one of :data:`CITIZEN_PAGES`."""
    return PATHS.get(page) or f"/demo/case/{case_id}/pipeline"


@pytest.fixture(autouse=True)
def restore_posture() -> Iterator[None]:
    """Leave the process the way it was found (the part-11 fixture)."""
    yield
    posture_cache.cache_clear()
    set_demo_posture(DemoPosture())


def build_client(
    config: ConfigBundle,
    *,
    demo: bool = True,
    token: str | None = TOKEN,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> TestClient:
    """The real app, on in-memory stores, with the deterministic union."""
    if monkeypatch is not None:
        if demo:
            monkeypatch.setenv(DEMO_MODE_ENV, "1")
        else:
            monkeypatch.delenv(DEMO_MODE_ENV, raising=False)
        if token:
            monkeypatch.setenv(INGEST_TOKEN_ENV, token)
        else:
            monkeypatch.delenv(INGEST_TOKEN_ENV, raising=False)
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


def persona(persona_id: str) -> Persona:
    found = demo_personas().get(persona_id)
    assert found is not None
    return found


def form_data(
    persona_id: str, channel: str = CHANNEL_FORM, **edits: str
) -> dict[str, str]:
    """What the intake form posts for one persona, with optional tampering."""
    chosen = persona(persona_id)
    data: dict[str, str] = {"persona": persona_id, "kanal": channel}
    if channel == CHANNEL_EMAIL:
        data["body"] = edits.pop("body", chosen.letter)
    else:
        data.update(chosen.form_values())
        data.update(edits)
    return data


def submit(client: TestClient, data: Mapping[str, str]) -> str:
    """Post the intake form and return the case id it redirected to."""
    posted = client.post("/demo/antrag", data=dict(data), follow_redirects=False)
    assert posted.status_code == 303, posted.text[:2000]
    location = posted.headers["location"]
    assert location.startswith("/demo/case/")
    return location.split("/")[3]


def identity_strings(persona_id: str) -> tuple[str, ...]:
    """A persona's identity values, long enough to search for."""
    chosen = persona(persona_id)
    return (
        *(
            entry.value
            for entry in chosen.fields
            if entry.identity and len(entry.value) >= 4
        ),
        chosen.display_name,
    )


# ------------------------------------------------------ 1. flag-off identity ---


def test_with_the_flag_off_there_is_no_demo_route_anywhere(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Route table AND OpenAPI document, because an integrator reads the latter."""
    monkeypatch.delenv(DEMO_MODE_ENV, raising=False)
    app = create_app(
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        outbox=InMemoryOutbox(),
        drafts=InMemoryDraftStore(),
    )
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not [path for path in paths if path.startswith("/demo")]
    assert not [path for path in app.openapi()["paths"] if path.startswith("/demo")]
    client = TestClient(app)
    assert client.get("/demo/antrag").status_code == 404
    assert client.post("/demo/antrag", data={}).status_code == 404
    assert client.get("/demo/case/anything/pipeline").status_code == 404
    # Part 15's tour is demo surface like everything else under /demo.
    assert client.get("/demo/rundgang").status_code == 404
    # And part 16's disclaimer page, which lives outside /demo but is gated by
    # the same flag for the same reason: a notice about a demonstration
    # instance is meaningless on an instance that is not one.
    assert client.get("/hinweise").status_code == 404
    assert "/hinweise" not in paths
    assert "/hinweise" not in app.openapi()["paths"]


def test_with_the_flag_off_no_demo_store_exists(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not an empty store - no store. The TTL store is demo-only by construction."""
    monkeypatch.delenv(DEMO_MODE_ENV, raising=False)
    off = create_app(
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        outbox=InMemoryOutbox(),
        drafts=InMemoryDraftStore(),
    )
    assert off.state.demo_store is None
    monkeypatch.setenv(DEMO_MODE_ENV, "1")
    on = create_app(
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        outbox=InMemoryOutbox(),
        drafts=InMemoryDraftStore(),
    )
    assert isinstance(on.state.demo_store, DemoStore)


def stripped_environment() -> Environment:
    """The part-11 control group: the demo include neutralised to nothing.

    The globals come from the real environment for the reason
    ``tests/test_demo_mode.py`` states at length: since part 16 every page also
    reads its language context from there, and a control group without one
    would raise on an undefined callable instead of measuring the bytes the
    demo include costs.
    """
    from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

    stripped = Environment(
        loader=ChoiceLoader(
            [DictLoader({"_demo_ribbon.html": ""}), FileSystemLoader(TEMPLATE_DIR)]
        ),
        autoescape=environment().autoescape,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    stripped.globals.update(environment().globals)
    return stripped


def test_the_queue_page_is_byte_identical_without_the_highlight(
    config: ConfigBundle,
) -> None:
    """Part 13's only footprint on the caseworker UI costs zero bytes when unused.

    Two checks in one: the demo banner include still adds nothing with the
    posture off (the part-11 property, extended to the template part 13
    touched), and the highlight block renders nothing when no highlight was
    passed - which is every request outside the tour.
    """
    set_demo_posture(DemoPosture())
    view = review_view.build_queue_view(
        InMemoryJournalStore(),
        config=config,
        queue_id="Referat_312_Renten",
        unit_id="Referat_312_Renten",
        now=BASE_TIME,
    )
    live = environment().get_template("review_queue.html").render(view=view)
    control = stripped_environment().get_template("review_queue.html").render(view=view)
    assert live == control
    assert "queue-highlight-note" not in live
    assert "Ihr Vorgang" not in live
    assert "tour-row" not in live


# --------------------------------------------------------- 2. token posture ---


def test_the_raw_endpoint_stays_403_while_the_intake_page_works(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both sides of the ruling, in one test, because they are one ruling.

    The demo app is the AUTHORIZED caller of its own ingest: it presents the
    deployment's token server-side. What changes for a stranger is nothing at
    all - the middleware still refuses before the body is read.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    refused = client.post("/ingest", json={"submissionId": "x"})
    assert refused.status_code == 403
    assert INGEST_HEADER in refused.json()["detail"]
    # And a malformed body is still refused without being decoded.
    assert client.post("/ingest", content=b"{ not json").status_code == 403
    # The page, holding the token, gets the whole pipeline.
    case_id = submit(client, form_data("schliebermann_statusfeststellung"))
    assert client.get(f"/cases/{case_id}").status_code == 200


def test_with_no_token_configured_the_intake_page_is_closed_too(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unset token is the safe state and it means closed for EVERYBODY.

    Including the demo app: this page has no privilege of its own, it only has
    a token, and where there is no token there is no submission (ADR-027,
    ruling 4). The page says so in words rather than failing.
    """
    client = build_client(config, token=None, monkeypatch=monkeypatch)
    page = client.get("/demo/antrag")
    assert page.status_code == 200
    assert phrase(demo_view.CLOSED_NOTE) in page.text
    assert phrase("intake.submit") not in page.text
    refused = client.post(
        "/demo/antrag",
        data=form_data("schliebermann_statusfeststellung"),
        follow_redirects=False,
    )
    assert refused.status_code == 200
    assert "ingest is disabled" in refused.text
    assert client.get("/review").text.count("case-demo") == 0


# -------------------------------------------------------------- 3. the journey ---


@pytest.mark.parametrize("persona_id", sorted(ARCS))
def test_the_whole_three_phase_journey_for_each_persona(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch, persona_id: str
) -> None:
    """Phase 1 -> 2 -> 3 -> the closed loop, through the real app."""
    client = build_client(config, monkeypatch=monkeypatch)
    unit, tier_prefix = ARCS[persona_id]

    # Phase 1: the picker, the prefill, the hints panel.
    intake = client.get(f"/demo/antrag?persona={persona_id}")
    assert intake.status_code == 200
    assert persona(persona_id).display_name in intake.text
    assert phrase("intake.hints.heading") in intake.text
    # The ribbon is not on this page since part 18; the route to the notice is,
    # and that is what the journey has to keep - see the scope test below.
    assert 'href="/hinweise"' in intake.text
    assert 'aria-current="step"' in intake.text
    assert phrase("phase.antrag") in intake.text

    case_id = submit(client, form_data(persona_id))

    # Phase 2: seven stages, in order, over this case.
    page = client.get(f"/demo/case/{case_id}/pipeline")
    assert page.status_code == 200
    headings = re.findall(r'id="([a-g])-heading"', page.text)
    assert headings == ["a", "b", "c", "d", "e", "f", "g"]
    assert case_id in page.text
    assert phrase(demo_view.SEAL_SENTENCE) in page.text
    assert tier_prefix in page.text
    assert unit in page.text
    # (b) the working copy is placeholders, and the pairing shows both sides.
    assert PLACEHOLDER_RE.search(page.text) is not None
    assert "Von Ihnen eingegeben" in page.text
    # (f) the receipt is LINKED, never actionable.
    assert f'href="/inbox/{case_id}"' in page.text
    # (g) the hand-off carries the highlight and nothing else.
    assert f'href="/review/queue/{unit}?highlight={case_id}' in page.text

    # Phase 3: the queue, the visitor's own row, the case, the confirmation.
    queue = client.get(f"/review/queue/{unit}?highlight={case_id}&unit={unit}")
    assert queue.status_code == 200
    assert "Ihr Vorgang" in queue.text
    assert client.get(f"/review/case/{case_id}?unit={unit}").status_code == 200
    confirmed = client.post(
        f"/review/case/{case_id}/confirm", data={"unit": unit}, follow_redirects=False
    )
    assert confirmed.status_code == 303

    # The loop closes in the citizen inbox.
    inbox = client.get(f"/inbox/{case_id}")
    assert inbox.status_code == 200
    assert inbox.json()["notifications"]
    assert client.get("/inbox").status_code == 200


def test_the_channel_chooser_is_gone_and_an_old_link_lands_on_the_form(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part 20, and the part-13 test it deliberately replaces.

    The user's decision of 2026-08-18 reverses the part-13 fork: the intake
    page is the form, full stop. What used to be asserted here - that the
    e-mail tab renders, says SIMULIERTER Adapter, offers a textarea and posts a
    letter - is asserted in its NEGATIVE now, on all four of the surfaces that
    used to carry it, because "the tab is gone" is only worth testing if
    nothing renders any half of it:

    * neither tab label and neither channel note is in the document,
    * the section heading the chooser sat under is gone with it,
    * no link on the page carries a ``kanal`` parameter,
    * and the letter's own control, the textarea, is not rendered either.

    The FALLBACK is the other half. A judge with a bookmarked
    ``?kanal=email`` gets the form rather than a 404 or an unlinked page, and
    a POST that carries the old parameter submits the form too - so there is no
    way in from the outside either. The envelope builder underneath is not
    deleted and keeps its unit coverage in ``tests/test_demo_personas.py``.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    for path in (
        "/demo/antrag",
        "/demo/antrag?persona=musterfrau_statusfeststellung&kanal=email",
    ):
        body = client.get(path).text
        for gone in (
            phrase("channel.email"),
            phrase("channel.fit_connect"),
            phrase("intake.channel.heading"),
            phrase("channel.note.email"),
            'id="kanal-heading"',
            '<ul class="navbar tabs">',
            "<textarea",
        ):
            assert gone not in body, f"{gone!r} still renders on {path}"
        # No LINK offers the parameter any more either. The language toggle is
        # excluded and only it: it echoes the URL the visitor asked for, which
        # is the part-16 rule that a toggle must not throw a parameter away.
        for href in re.findall(r'href="(/demo/antrag[^"]*)"', body):
            assert "kanal=" not in href or "lang=" in href, href
        # It IS the form: the persona's own fields are the controls.
        assert '<input type="text" id="feld-versicherungsnummer"' in body, path
        assert phrase("intake.form.heading") in body, path

    # The view layer says the same thing, so a caller that never renders the
    # template cannot reach the letter variant either.
    assert demo_view.resolve_channel("email") == CHANNEL_FORM
    assert demo_view.resolve_channel(CHANNEL_EMAIL) == CHANNEL_FORM
    assert demo_view.resolve_channel(None) == CHANNEL_FORM
    assert demo_view.resolve_channel(CHANNEL_FORM) == CHANNEL_FORM
    assert demo_view.OFFERED_CHANNELS == (CHANNEL_FORM,)

    # And a POST carrying the old parameter is a FORM submission: the persona's
    # structured payload arrives, the letter's `body` is ignored.
    case_id = submit(
        client,
        {
            **form_data("musterfrau_statusfeststellung"),
            "kanal": CHANNEL_EMAIL,
            "body": "dies waere frueher als Anschreiben durchgegangen",
        },
    )
    pipeline = client.get(f"/demo/case/{case_id}/pipeline")
    assert pipeline.status_code == 200
    assert "Referat_340_Clearingstelle" in pipeline.text
    assert "dies waere frueher" not in pipeline.text
    assert "Ihr Anschreiben, vorher und nachher" not in pipeline.text
    # The form fills the payload, so the fields ARE extracted - which is the
    # sentence about an unextractable letter no longer applying.
    assert phrase(demo_view.NO_EXTRACTION_NOTE) not in pipeline.text


def test_a_tampered_submission_fires_the_gap_and_the_flag(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hints panel promises real behaviour; this is the behaviour.

    All four probes below are what a visitor can do in a browser with nothing
    but the keyboard, because part 22's panel asks for three deletions and one
    date and ``demo_view.HINT_DELETED_FIELDS`` makes the first three possible.
    Each one is a different half of the machine: a missing field, an invalid
    one produced by a CROSS-field check, the shadow scorer, and the second gap
    the derivation is not allowed to paper over.
    """
    client = build_client(config, monkeypatch=monkeypatch)

    # Hint 1. Delete the Versicherungsnummer: a gap, the procedure's own
    # Nachforderung sentence, tier 2. Absent is MISSING and never invalid.
    gap_case = submit(
        client, form_data("schliebermann_statusfeststellung", versicherungsnummer="")
    )
    gap_page = client.get(f"/demo/case/{gap_case}/pipeline").text
    assert "Tier 2" in gap_page
    assert "versicherungsnummer" in gap_page
    assert "Sozialversicherungsausweis" in gap_page

    # Hint 2. A birth date the Versicherungsnummer does not carry: the
    # cross-field check fires, and it names its reason without unsealing
    # anything - the comparison happens through the transient witness.
    cross_case = submit(
        client, form_data("schliebermann_statusfeststellung", geburtsdatum="1902-01-01")
    )
    cross_page = client.get(f"/demo/case/{cross_case}/pipeline").text
    assert "Tier 2" in cross_page
    assert "Stellen 3 bis 8" in cross_page
    assert "cross_field.birthdate_in_vsnr" in cross_page
    # "ohne dass irgendetwas entsiegelt wird": the check reads the typed value
    # through the transient witness, so the WORKING COPY still holds a
    # placeholder where the date was. (The visitor's own echo beside it is not
    # the working copy - it is what they typed, held for half an hour.)
    store = client.app.state.demo_store  # type: ignore[attr-defined]
    held = store.get(cross_case)
    assert held is not None
    working = "\n".join(part.text for part in held.working_copy)
    assert "1902-01-01" not in working
    assert "[[PII|GEBDAT|" in working

    # Hint 3. Push the start of the activity out, INSIDE the calendar bounds:
    # the shadow scorer flags it and moves nothing.
    flag_case = submit(
        client,
        form_data("schliebermann_statusfeststellung", taetigkeit_beginn="2035-01-01"),
    )
    flag_page = client.get(f"/demo/case/{flag_case}/pipeline").text
    assert "Merkmal leitdatum_abstand_jahre" in flag_page
    assert phrase(demo_view.LOG_ONLY_NOTE) in flag_page
    # And it is NOT a completeness gap, which is the whole point of the hint:
    # the bounds are absolute and wide and let this through on purpose.
    assert "Tier 3" in flag_page
    # Both halves of the sentence the flagged persona's card promises: the tier
    # an armed scorer would have set, and that this decision was reached
    # without it. The number is READ from the decision table's downgrade rows.
    assert "Ein scharfgestellter Scorer" in flag_page
    assert demo_view.armed_scorer_tier(config) == 3

    # Hint 4. Empty the Auftraggeber: the requirement is missing and the
    # Nachforderung asks for exactly it.
    client_case = submit(
        client, form_data("schliebermann_statusfeststellung", auftraggeber_name="")
    )
    client_page = client.get(f"/demo/case/{client_case}/pipeline").text
    assert "Tier 2" in client_page
    assert "auftraggeber_name" in client_page
    assert "Firmenname und Anschrift" in client_page


def redaction_refusal(canary: str) -> RedactionRefusedError:
    """A REAL refusal, produced by the boundary rather than constructed here.

    Built by handing the boundary prose that imitates the reserved placeholder
    syntax, which is the one way to produce a refusal without the optional
    model (ADR-019, ruling 4). The object - findings, kinds, paths, lengths -
    is the boundary's own, so a test that renders it is rendering the thing the
    page would actually be handed.
    """
    with pytest.raises(RedactionRefusedError) as raised:
        redact_payload(
            {},
            policy=default_policy(),
            case_id="case-refusal-probe",
            created_at=BASE_TIME,
            texts={"part-text-0": f"[[PII|VSNR|nope]] {canary}"},
            text_detector=text_seal_detector(with_ner=False),
        )
    return raised.value


def test_a_forged_placeholder_in_a_form_field_is_sealed_rather_than_refused(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part 20's finding: the form cannot reach the refusal, and that is right.

    With the letter tab gone (work item 1) the only text a visitor controls is
    a structured leaf, and ADR-017's boundary auto-seals a leaf the sweep
    complained about before it refuses anything - "auto-seal once, then
    refuse". So placeholder-shaped input in a form field is sealed as a whole
    leaf and the submission goes through, with the auto-seal recorded.

    That is the behaviour, so that is what is pinned. The refusal RENDERING is
    pinned separately, over the boundary's own refusal object, because a page
    that could not render a refusal it may still be handed would be a 500
    waiting for the first submission the sweep cannot rescue.

    The field is the Antragsart on purpose: it is NOT identity-classed, so the
    forgery survives the policy's own sealing and reaches the sweep, which is
    what makes this the auto-seal path rather than the ordinary one. What the
    visitor then sees is the whole chain being honest with them - the leaf is a
    placeholder, the witness still holds the forged string, it is not in the
    procedure's allowed list, and the case is incomplete and goes to a human.
    Reaching it needs a POST rather than the page (the control is a select
    since part 16), which is exactly why it is pinned here and no longer
    suggested to a visitor as a hint.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(
        client,
        form_data("schliebermann_statusfeststellung", antragsart="[[PII|VSNR|nope]]"),
    )
    page = client.get(f"/demo/case/{case_id}/pipeline")
    assert page.status_code == 200
    store = client.app.state.demo_store  # type: ignore[attr-defined]
    held = store.get(case_id)
    assert held is not None
    working = "\n".join(part.text for part in held.working_copy)
    assert "[[PII|VSNR|nope]]" not in working, "the forgery reached the working copy"
    assert "antrag.antragsart = [[PII|TEXT|" in working
    # And the evidence plane read the REAL value through the witness: it is not
    # one the procedure allows, so the field is invalid and a human gets it.
    assert "invalid" in page.text
    assert "Tier 2" in page.text


def test_a_refused_submission_renders_the_refusal_on_the_page(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boundary refusing its own output is a real behaviour worth showing.

    Driven by making the pipeline raise the boundary's OWN refusal object (see
    :func:`redaction_refusal`) rather than by a submission, because part 20
    removed the one surface a visitor could forge prose on - see the test above
    for what a form field does instead. What is asserted is unchanged and is
    the page's half of the contract: 200 rather than 500, the refusal wording,
    the alert block, and nothing in the journal.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    with patch.object(
        app_module, "run_pipeline", side_effect=redaction_refusal("egal")
    ):
        refused = client.post(
            "/demo/antrag",
            data=form_data("schliebermann_statusfeststellung"),
            follow_redirects=False,
        )
    assert refused.status_code == 200
    assert phrase(REFUSED_REDACTION) in refused.text
    assert 'id="refusal"' in refused.text
    # Nothing was journaled: the refusal happened before a case existed.
    assert client.get("/review").text.count("case-demo") == 0


def test_no_refusal_ever_echoes_what_the_visitor_typed(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The part-04 rule, on the one page a citizen sees a refusal on.

    A refusal names kinds, paths, lengths and recognizer ids. It never names
    the residue - which on this page would be the visitor's own data, printed
    back at them by the component whose whole job is to keep it out.

    Through the boundary's own refusal object since part 20, for the reason
    given on the test above: the canary rides INSIDE the text the boundary
    refused, so what is checked is still that the page does not print back
    what it was handed.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    canary = "KANARIENVOGEL-4711"
    with patch.object(
        app_module, "run_pipeline", side_effect=redaction_refusal(canary)
    ):
        refused = client.post(
            "/demo/antrag",
            data=form_data("schliebermann_statusfeststellung"),
            follow_redirects=False,
        )
    assert refused.status_code == 200
    assert phrase(REFUSED_REDACTION) in refused.text
    block = refused.text.split('id="refusal"')[1].split("</div>")[0]
    assert canary not in block
    assert "nope" not in block
    # The envelope refusal has its own wording, and it is not this one.
    assert phrase(REFUSED_ENVELOPE) not in refused.text


def test_an_out_of_shape_value_becomes_a_gap_rather_than_an_error(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrong date is a question for the applicant, never a rejection.

    Completeness reports it as INVALID with the procedure's own wording, and
    the case goes to a human. Nothing about it is a schema failure, which is
    why the envelope-refusal branch is unreachable from this form.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(
        client,
        form_data("musterfrau_statusfeststellung", taetigkeit_beginn="irgendwann"),
    )
    page = client.get(f"/demo/case/{case_id}/pipeline").text
    assert "taetigkeit_beginn" in page
    assert "invalid" in page
    assert "Tier 2" in page or "Tier 3" in page


def test_the_pipeline_view_404s_on_a_case_it_does_not_know(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = build_client(config, monkeypatch=monkeypatch)
    assert client.get("/demo/case/nope/pipeline").status_code == 404


def persona_cards(page: str) -> list[tuple[str, bool]]:
    """Every persona card in RENDER ORDER: its id, and whether it is chosen.

    Two different claims live in this markup and part 17 makes both, so both
    are read: the chosen card carries `persona-current`, and every other card
    links to itself. Reading only "the name is somewhere on the page" would
    pass for any persona, since the picker lists all of them - which is exactly
    what the assertion below used to do.
    """
    by_name = {p.display_name: p.persona_id for p in demo_personas().personas}
    cards: list[tuple[str, bool]] = []
    for classes, body in re.findall(
        r'<li class="persona([^"]*)">(.*?)</li>', page, re.DOTALL
    ):
        for name, persona_id in by_name.items():
            if name in body:
                cards.append((persona_id, "persona-current" in classes))
                break
    return cards


def test_an_unknown_persona_or_channel_falls_back_instead_of_failing(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale bookmark shows the picker, never a stack trace."""
    client = build_client(config, monkeypatch=monkeypatch)
    page = client.get("/demo/antrag?persona=ghost&kanal=telepathie")
    assert page.status_code == 200
    # The FALLBACK is the one the page defaults to, not merely a name that
    # appears somewhere in a picker that lists every persona.
    assert persona_cards(page.text)[0] == (demo_view.LEAD_PERSONA, True)
    # The channel is no longer a choice (part 20), so the fallback is checked
    # on the view rather than on a tab label that no longer renders.
    view = demo_view.build_intake_view(
        DemoPosture(), demo_personas(), persona_id="ghost", channel="telepathie"
    )
    assert view.channel == CHANNEL_FORM
    assert view.is_email is False
    assert demo_view.resolve_channel("telepathie") == CHANNEL_FORM
    assert demo_view.resolve_channel(None) == CHANNEL_FORM


def test_the_lead_persona_opens_the_picker_and_the_others_stay_reachable(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part 17: a visitor lands on the Statusfeststellung persona.

    Pinned as a RULE rather than as a name: the persona the view layer calls
    the lead is first in the row and is the one selected when no `?persona=`
    is given, in both languages and on both channel tabs. Renaming a persona
    therefore cannot silently move the default, and neither can re-ordering
    the frozen config file - which this does not touch, and which is why the
    ordering lives in the view.

    The rest of the picker is unchanged and that is half the point: all four
    personas are present, exactly one is marked, and every other one is a
    link that selects it.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    everyone = {p.persona_id for p in demo_personas().personas}
    assert demo_view.LEAD_PERSONA in everyone

    for path in ("/demo/antrag", "/demo/antrag?kanal=email", "/demo/antrag?kanal=x"):
        cards = persona_cards(client.get(path).text)
        expected = [p.persona_id for p in demo_view.ordered_personas(demo_personas())]
        assert [c for c, _ in cards] == expected, path
        assert cards[0][0] == demo_view.LEAD_PERSONA, path
        assert [c for c, current in cards if current] == [demo_view.LEAD_PERSONA], path
        assert {c for c, _ in cards} == everyone, path

    # English renders the same template and therefore the same running order.
    client.get("/demo/antrag?lang=en", follow_redirects=True)
    english = client.get("/demo/antrag").text
    assert '<html lang="en">' in english
    assert persona_cards(english)[0] == (demo_view.LEAD_PERSONA, True)
    client.get("/demo/antrag?lang=de", follow_redirects=True)

    # Every other persona is one click away and selecting it still works.
    for persona_id in sorted(everyone - {demo_view.LEAD_PERSONA}):
        page = client.get("/demo/antrag").text
        assert f'href="/demo/antrag?persona={persona_id}' in page, persona_id
        chosen = persona_cards(client.get(f"/demo/antrag?persona={persona_id}").text)
        assert [c for c, current in chosen if current] == [persona_id], persona_id
        # ... and it is still the leftmost card, so the row never reshuffles.
        assert chosen[0][0] == demo_view.LEAD_PERSONA, persona_id


def attributes(tag: str) -> set[str]:
    """The attribute NAMES of one start tag, as a browser would parse them.

    Written because a substring check is not one. The environment runs with
    `trim_blocks` and `lstrip_blocks`, so two conditional attributes on
    consecutive template lines render glued together - and
    ``" required" in tag`` is perfectly happy with
    ``requiredaria-describedby="..."``, which is a single attribute nobody has
    ever heard of and means the control has neither. That shipped once; the
    browser walk found it because an empty required date field submitted.
    """
    return {
        name.lower()
        for name in re.findall(
            r"(?:^|\s)([A-Za-z-]+)(?==|[\s>])", tag[tag.index(" ") :]
        )
    }


def test_what_the_persona_arrived_with_is_a_required_field(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule and its declared exemptions, pinned in both directions.

    Prefilled implies required; empty by design implies not; a field a HINT
    tells the visitor to delete implies not, however full it arrives (part 22,
    ``demo_view.HINT_DELETED_FIELDS``). Asserted over every persona and every
    field of every persona, so a renamed field, a new persona or a reordered
    config file cannot quietly exempt anything, and a fourth exemption has to
    be declared rather than acquired.

    The blocking itself is the browser's. What is checkable from here is that
    the attribute is on the control the visitor uses, that the sentence the
    CSS reveals is PRE-RENDERED next to it (nothing is inserted at the moment
    it is needed, because nothing here runs), and that no script arrived with
    any of it.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    for chosen in demo_personas().personas:
        body = client.get(f"/demo/antrag?persona={chosen.persona_id}").text
        assert "<script" not in body, chosen.persona_id
        for entry in chosen.fields:
            control = re.search(
                rf'<(input|select)[^>]*id="feld-{entry.field_id}"[^>]*>', body
            )
            assert control, f"{chosen.persona_id}.{entry.field_id} is not rendered"
            names = attributes(control.group(0))
            expected = (
                bool(entry.value.strip())
                and entry.field_id not in demo_view.HINT_DELETED_FIELDS
            )
            assert demo_view.required_for(entry) is expected
            assert ("required" in names) is expected, (
                f"{chosen.persona_id}.{entry.field_id}: required attribute "
                f"{'missing' if expected else 'present'} ({sorted(names)}) - the "
                "rule is that a field the persona arrived with a value for must "
                "be sent with one unless a hint says to delete it"
            )
            # And the help sentence is still ADDRESSED, which is the attribute
            # `required` was glued to the first time this shipped. Checked
            # against the sentence the page actually rendered, because the view
            # adds one to every date and every select.
            described = f'id="hilfe-{entry.field_id}"' in body
            assert ("aria-describedby" in names) is described, (
                f"{chosen.persona_id}.{entry.field_id}: {sorted(names)}"
            )
        # The sentence exists once per required field, before anybody submits.
        required = sum(1 for entry in chosen.fields if demo_view.required_for(entry))
        assert body.count('<span class="field-error">') == required
        assert phrase("intake.required.error") in body
        assert phrase("intake.required.note") in body

    # THE EXEMPTION LIST AS IT LANDS ON THE PAGE, both halves. Every field the
    # list names is a field some persona actually has (an exemption for a field
    # nobody carries would outlive its reason unnoticed), and the only controls
    # the whole demonstration leaves optional are those three plus the one that
    # arrives empty by design - which is what lets Bernd's card name his.
    all_fields = {
        entry.field_id for chosen in demo_personas().personas for entry in chosen.fields
    }
    assert all_fields >= demo_view.HINT_DELETED_FIELDS
    optional = {
        (chosen.persona_id, entry.field_id)
        for chosen in demo_personas().personas
        for entry in chosen.fields
        if not demo_view.required_for(entry)
    }
    assert {field_id for _persona_id, field_id in optional} == (
        demo_view.HINT_DELETED_FIELDS | {"taetigkeit_beginn"}
    )
    assert ("beispielmann_ohne_taetigkeitsbeginn", "taetigkeit_beginn") in optional
    assert (
        sum(1 for _persona_id, field_id in optional if field_id == "taetigkeit_beginn")
        == 1
    )


def test_the_prepared_documents_are_offered_and_say_what_they_are(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part 20: an upload area that is honest about not being one.

    Three things have to be on the page together, because any two of them
    without the third would be misleading: the documents with the names an
    agency really uses, the sentence saying they are prepared and synthetic,
    and the sentence saying that uploading a file of your own is deliberately
    absent here. The last one is the part-10 refusal stated in words rather
    than left to be inferred from the absence of a control - and the absence
    of the control is asserted too, on both halves of what would make one.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    for chosen in demo_personas().personas:
        body = client.get(f"/demo/antrag?persona={chosen.persona_id}").text
        assert phrase("intake.attachments.legend") in body, chosen.persona_id
        assert phrase("intake.attachments.note") in body, chosen.persona_id
        assert phrase("intake.attachments.no_upload") in body, chosen.persona_id
        for entry in chosen.attachments:
            assert f'name="{entry.field_name}"' in body, entry.attachment_id
            assert f'for="anlage-{entry.attachment_id}"' in body, entry.attachment_id
            assert escape(entry.label) in body, entry.attachment_id
            assert entry.filename in body, entry.attachment_id
        # No upload path, and no dependency that would make one possible.
        assert 'type="file"' not in body
        assert "multipart/form-data" not in body
        # Nothing is ticked until a visitor ticks it.
        assert " checked" not in body


def test_a_ticked_document_follows_the_case_into_the_pipeline_view(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the shape: it is a real part, so it appears as one.

    The pipeline view renders whatever parts the working copy has, so the
    documents arrive there through the same projection the structured payload
    does - no attachment branch was added to that page. What a visitor sees is
    the sealed document beside the sealed form, its own part id, its own
    placeholders, and the sealed-span count risen to match.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    chosen = persona("musterfrau_statusfeststellung")
    ticked = chosen.attachments[0]

    plain = client.get(
        f"/demo/case/{submit(client, form_data(chosen.persona_id))}/pipeline"
    )
    assert "part-text-0" not in plain.text

    case_id = submit(client, {**form_data(chosen.persona_id), ticked.field_name: "1"})
    page = client.get(f"/demo/case/{case_id}/pipeline")
    assert page.status_code == 200
    assert "part-text-0" in page.text
    # A sentence that exists ONLY in the document, next to the placeholders the
    # boundary put in it.
    assert "Beschreibung des Auftragsverhaeltnisses" in page.text
    assert '<mark class="placeholder">' in page.text
    # And the identity in the document is gone from what the page shows.
    #
    # PART 19 RAISED THE BOUND FROM TWO TO THREE AND ADDED THE ASSERTION THAT
    # ACTUALLY MEANS WHAT THIS ONE WAS APPROXIMATING. The two occurrences that
    # were always allowed are the echo pairing ("you typed this") and the
    # "ausgefuellt als" line of stage (a). The third is the simulated Anhoerung
    # letter, which quotes the applicant and the Auftraggeber because a hearing
    # that named neither could not say which relationship it is about. All three
    # are one compartment - the visitor's own value, on the visitor's own page,
    # shown back to the visitor who typed it - and a count was only ever a proxy
    # for the thing this test is about, which is the WORKING COPY. That is now
    # asserted directly, over every `<pre>` on the page.
    for value in identity_strings(chosen.persona_id):
        assert page.text.count(value) <= 3, value
    for block in re.findall(r"<pre[^>]*>(.*?)</pre>", page.text, re.S):
        for value in identity_strings(chosen.persona_id):
            assert value not in block, f"{value!r} is in a working-copy block"

    # The caseworker surface still shows no document content: part 20 adds a
    # part, not a window (ADR-026).
    case_page = client.get(f"/review/case/{case_id}?unit=Referat_340_Clearingstelle")
    assert case_page.status_code == 200
    assert "Beschreibung des Auftragsverhaeltnisses" not in case_page.text
    # It does show that a part arrived, which is metadata and always did.
    assert "part-text-0" in case_page.text


def test_the_sealed_span_table_counts_parts_and_says_so(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A defect part 20's browser walk found, pinned so it cannot come back.

    ``text_sealed_counts`` is keyed by PART - the boundary counts spans per
    part and journals it that way - and the projection that renders it used to
    be called ``sealed_kinds`` and to look every key up in a table of kind
    labels. On the caseworker page that fell through to its own fallback and
    printed the part id twice; on the citizen page, which translates that
    column, it printed the raw key ``kind.part-text-0`` at a reader.

    Until this part it could only be seen on the e-mail tab. Attachments give
    every submission a free-text part, so the table is now on the page a
    visitor lands on - which is how looking at it found this.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    chosen = persona("musterfrau_statusfeststellung")
    case_id = submit(
        client,
        {
            **form_data(chosen.persona_id),
            **{entry.field_name: "1" for entry in chosen.attachments},
        },
    )
    page = client.get(f"/demo/case/{case_id}/pipeline").text
    assert "kind.part-text" not in page, "a raw translation key reached a reader"
    assert phrase("pipeline.b.parts.caption") in page
    assert phrase("pipeline.b.parts.col1") in page
    assert phrase("pipeline.b.parts.col2") in page

    view = demo_view.build_pipeline_view(
        client.app.state.journal,  # type: ignore[attr-defined]
        config=config,
        case_id=case_id,
        outbox=client.app.state.outbox,  # type: ignore[attr-defined]
        store=client.app.state.demo_store,  # type: ignore[attr-defined]
    )
    assert view is not None
    assert [part_id for part_id, _count in view.sealed_text_parts] == [
        f"part-text-{index}" for index in range(len(chosen.attachments))
    ]
    assert all(count > 0 for _part_id, count in view.sealed_text_parts)

    # The caseworker page reads the SAME projection, which is why it was worth
    # fixing once rather than twice.
    case_page = client.get(f"/review/case/{case_id}?unit=Referat_340_Clearingstelle")
    assert "Versiegelte Stellen" in case_page.text
    assert "part-text-0" in case_page.text


def test_the_selection_survives_a_refusal_like_every_other_answer(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-render must not silently untick what somebody chose.

    The refusal path re-renders the page with the visitor's own values; the
    documents are part of those values, so they come back ticked. Without this
    a visitor correcting one field would also, invisibly, drop their
    enclosures.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    chosen = persona("schliebermann_statusfeststellung")
    ticked = chosen.attachments[0]
    with patch.object(
        app_module, "run_pipeline", side_effect=redaction_refusal("egal")
    ):
        refused = client.post(
            "/demo/antrag",
            data={**form_data(chosen.persona_id), ticked.field_name: "1"},
            follow_redirects=False,
        )
    assert refused.status_code == 200
    marked = re.search(
        rf'<input type="checkbox" id="anlage-{ticked.attachment_id}"[^>]*>',
        refused.text,
    )
    assert marked and "checked" in attributes(marked.group(0))
    # And the ones that were not ticked did not become ticked.
    for entry in chosen.attachments[1:]:
        other = re.search(
            rf'<input type="checkbox" id="anlage-{entry.attachment_id}"[^>]*>',
            refused.text,
        )
        assert other and "checked" not in attributes(other.group(0))


def test_the_red_state_is_css_over_a_state_the_browser_maintains() -> None:
    """No script, no `:invalid`, and colour is not the only carrier.

    `:invalid` would paint a form red before anybody had touched it, which is
    the opposite of the user's sentence ("after pressing Antrag absenden");
    `:user-invalid` waits for the interaction. The sentence under the field is
    what keeps 1.4.1: take the colour away and the state still has words.
    """
    css = Path("ui/static/system.css").read_text(encoding="utf-8")
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    assert ":user-invalid" in rules
    assert not re.search(r"(?<![-\w]):invalid", rules), "a form red before it was used"
    # The edge is the element colour, the words are the text colour - the
    # split the palette reserves, and `tests/test_review_accessibility.py`
    # enforces the second half over every stylesheet.
    edge = re.search(r'input\[type="text"\]:user-invalid[^{]*\{([^}]*)\}', rules)
    assert edge and "border-color: var(--alarm)" in edge.group(1)
    # SPECIFICITY AND ORDER, pinned because a browser walk found them wrong.
    # A bare `input:user-invalid` loses to `input[type="text"]:focus`, and the
    # browser focuses the field it stopped at - so the one field the visitor is
    # looking at was the one that stayed blue. The invalid rules match the
    # focus rules' shape and come after them.
    assert rules.index('input[type="text"]:focus') < rules.index(
        'input[type="text"]:user-invalid'
    ), "the focus rule must not out-order the invalid rule"
    for control in ('input[type="date"]', "select", "textarea"):
        assert f"{control}:user-invalid" in rules, control
    sentence = re.search(r"\.field-error\s*\{([^}]*)\}", rules)
    assert sentence and "color: var(--alarm-text)" in sentence.group(1)
    assert "display: none" in sentence.group(1), "the sentence must be pre-rendered"
    assert re.search(r":user-invalid ~ \.field-error\s*\{\s*display: block;", rules)


def test_the_lead_persona_brings_its_own_controls_to_the_first_screen(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason it leads: the richest form of the four, with no clicks.

    Statusfeststellung is the persona whose fields carry the three configured
    selects, so making it the default is what puts the configuration's own
    vocabulary on the screen a visitor lands on rather than one click in.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    body = client.get("/demo/antrag").text
    for field_id in ("antragsart", "antragsteller_rolle", "taetigkeit_bezeichnung"):
        assert f'<select id="feld-{field_id}"' in body, field_id
    assert phrase("intake.hints.heading") in body
    # The arc this persona produces is unchanged; the picker moved, not it.
    assert demo_view.LEAD_PERSONA in ARCS


# ------------------------------------------------------------ 4. the store ---


def envelope_of(config: ConfigBundle, persona_id: str) -> object:
    from engine.demo.personas import build_form_submission
    from engine.pipeline import run_pipeline

    chosen = persona(persona_id)
    return run_pipeline(
        build_form_submission(
            chosen,
            chosen.form_values(),
            submission_id="demo-store-probe",
            submitted_at=BASE_TIME.isoformat(),
        ),
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        now=BASE_TIME,
    ).envelope


def held_submission(config: ConfigBundle, persona_id: str) -> DemoSubmission:
    chosen = persona(persona_id)
    return DemoSubmission.from_envelope(
        envelope_of(config, persona_id),  # type: ignore[arg-type]
        persona_id=persona_id,
        persona_label=chosen.display_name,
        channel=CHANNEL_FORM,
        created_at=BASE_TIME,
        echo=demo_view.echo_values(chosen, chosen.form_values()),
    )


def test_the_working_copy_holds_placeholders_and_never_a_sealed_value(
    config: ConfigBundle,
) -> None:
    """The structural guarantee: it is built from the envelope and nothing else."""
    entry = held_submission(config, "schliebermann_statusfeststellung")
    text = "\n".join(part.text for part in entry.working_copy)
    assert PLACEHOLDER_RE.search(text) is not None
    for value in identity_strings("schliebermann_statusfeststellung"):
        assert value not in text, value
    # The echo is the visitor's own input and is deliberately NOT placeholders.
    assert any(value.value == "Beate Schliebermann" for value in entry.echo)
    # The four address inputs became ONE entry, because sealing groups them.
    address = [value for value in entry.echo if value.kind == "ADDR"]
    assert len(address) == 1
    assert address[0].value == "Prickenweg 4 24939 Musterwarft"


def test_the_working_copy_carries_a_text_part_as_text(config: ConfigBundle) -> None:
    """A free-text part becomes a `text`-shaped entry, not a rendered payload.

    Pinned on its own since part 20. Until this part the branch was reached
    only through the e-mail tab, which no longer renders (work item 1) - and
    the store still has to handle a text part, because a seeded corpus letter
    is one and because a ticked attachment is one (work item 3). A store that
    quietly dropped them would show a visitor an empty stage (b).
    """
    from engine.demo.personas import build_letter_submission
    from engine.pipeline import run_pipeline

    chosen = persona("musterfrau_statusfeststellung")
    envelope = run_pipeline(
        build_letter_submission(
            chosen,
            chosen.letter,
            submission_id="demo-text-part",
            submitted_at=BASE_TIME.isoformat(),
        ),
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        now=BASE_TIME,
        text_detector=text_seal_detector(with_ner=False),
    ).envelope
    entry = DemoSubmission.from_envelope(
        envelope,
        persona_id=chosen.persona_id,
        persona_label=chosen.display_name,
        channel=CHANNEL_EMAIL,
        created_at=BASE_TIME,
    )
    shapes = [part.shape for part in entry.working_copy]
    assert shapes.count("text") == 1
    text = next(part.text for part in entry.working_copy if part.shape == "text")
    assert "Sehr geehrte Damen und Herren" in text
    assert PLACEHOLDER_RE.search(text) is not None
    for value in identity_strings(chosen.persona_id):
        assert value not in text, value


def test_the_store_expires_by_ttl_and_forgets_completely(
    config: ConfigBundle,
) -> None:
    store = DemoStore(ttl=timedelta(minutes=5))
    entry = held_submission(config, "schliebermann_statusfeststellung")
    store.put(entry, now=BASE_TIME)
    assert store.get(entry.case_id, now=BASE_TIME + timedelta(minutes=4)) is entry
    assert store.get(entry.case_id, now=BASE_TIME + timedelta(minutes=5)) is None
    assert len(store) == 0


def test_the_store_evicts_the_oldest_beyond_its_capacity(
    config: ConfigBundle,
) -> None:
    """A demo nobody stops must not become the memory profile of the process."""
    store = DemoStore(capacity=3)
    base = held_submission(config, "schliebermann_statusfeststellung")
    for index in range(5):
        store.put(
            DemoSubmission(
                case_id=f"case-{index}",
                persona_id=base.persona_id,
                persona_label=base.persona_label,
                channel=CHANNEL_FORM,
                created_at=BASE_TIME,
                working_copy=base.working_copy,
            ),
            now=BASE_TIME,
        )
    assert store.case_ids() == ("case-2", "case-3", "case-4")
    assert store.capacity == 3
    assert DemoStore().capacity == DEFAULT_CAPACITY


def test_the_store_reports_the_bounds_it_was_built_with(
    config: ConfigBundle,
) -> None:
    """The TTL and the capacity are readable, because a demo says what it holds."""
    assert DemoStore().ttl == DEFAULT_TTL
    assert DemoStore(ttl=timedelta(minutes=2)).ttl == timedelta(minutes=2)
    # A capacity below one would be a store that cannot hold the submission it
    # was just handed, which is not a bound but a bug.
    assert DemoStore(capacity=0).capacity == 1


def test_a_list_in_the_payload_renders_with_its_index(config: ConfigBundle) -> None:
    """A real FIT-Connect payload carries arrays; the working copy shows them.

    The dotted-path rendering exists because a citizen reads it next to what
    they typed, and ``antrag.anlagen[0] = ...`` is readable in a way that a
    pretty-printed object with six levels of braces is not.
    """
    from engine.pipeline import run_pipeline

    result = run_pipeline(
        {
            "submissionId": "demo-list-probe",
            "destinationId": "drv-bund-eingang-test",
            "channel": CHANNEL_FORM,
            "submittedAt": BASE_TIME.isoformat(),
            "procedureHint": "altersrente",
            "data": {"antrag": {"anlagen": ["Rentenauskunft", "Versicherungsverlauf"]}},
            "attachments": [],
        },
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        now=BASE_TIME,
    )
    entry = DemoSubmission.from_envelope(
        result.envelope,
        persona_id="p",
        persona_label="P",
        channel=CHANNEL_FORM,
        created_at=BASE_TIME,
    )
    text = "\n".join(part.text for part in entry.working_copy)
    assert "antrag.anlagen[0] = Rentenauskunft" in text
    assert "antrag.anlagen[1] = Versicherungsverlauf" in text


def test_reset_wipes_the_store(config: ConfigBundle) -> None:
    """The in-process wipe. A restart does the same thing by construction."""
    store = DemoStore()
    entry = held_submission(config, "schliebermann_statusfeststellung")
    store.put(entry, now=BASE_TIME)
    assert len(store) == 1
    store.reset()
    assert len(store) == 0
    assert store.get(entry.case_id, now=BASE_TIME) is None


def test_the_store_clips_what_it_holds(config: ConfigBundle) -> None:
    """A per-entry size cap, so one large paste cannot become the process."""
    chosen = persona("schliebermann_statusfeststellung")
    entry = DemoSubmission.from_envelope(
        envelope_of(config, chosen.persona_id),  # type: ignore[arg-type]
        persona_id=chosen.persona_id,
        persona_label=chosen.display_name,
        channel=CHANNEL_EMAIL,
        created_at=BASE_TIME,
        echo=(TypedValue(label="x", value="y" * (MAX_CHARS * 2), kind="NAME"),),
        echo_body="z" * (MAX_CHARS * 2),
    )
    assert len(entry.echo_body) == MAX_CHARS
    assert len(entry.echo[0].value) == MAX_CHARS


def test_the_store_module_never_reaches_for_the_vault() -> None:
    """ "Never a vault fetch" as a property of the source, not of a promise.

    Parsed rather than grepped: the module's own docstring explains at length
    why it does not touch the vault, and a substring search would find that
    explanation. What is checked is every NAME the code actually mentions -
    imports, attributes, identifiers - which is the set a vault read would have
    to appear in.
    """
    import ast

    tree = ast.parse(Path("engine/demo/store.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name)
            names.add(node.asname or "")
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    assert names, "the module parsed to nothing"
    for name in names:
        assert "vault" not in name.lower(), name


def test_an_expired_submission_leaves_the_journal_half_readable(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page degrades to what the journal holds, and says which half is gone."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("schliebermann_statusfeststellung"))
    store = client.app.state.demo_store  # type: ignore[attr-defined]
    assert isinstance(store, DemoStore)
    store.reset()
    page = client.get(f"/demo/case/{case_id}/pipeline")
    assert page.status_code == 200
    assert phrase(demo_view.EXPIRED_NOTE) in page.text
    assert "Von Ihnen eingegeben" not in page.text
    # Everything the journal holds is still there.
    assert "Referat_340_Clearingstelle" in page.text
    assert "Tier 3" in page.text


# ------------------------------------------------------- 5. the canary sweep ---


def test_no_page_shows_another_visitors_identity(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one property the echo compartment must not cost.

    A visitor sees the values THEY typed on THEIR pipeline page. Nobody sees
    anybody else's, on any page, ever - not on another case's pipeline view,
    not in a queue, not in the inbox, not on a case view.

    The ONE surface deliberately left out is ``/review/case/{id}?unit=...``,
    because part 08 built it to re-hydrate identity into a prepared letter and
    part 10 put the unit picker in front of it (ADR-023, C-5). Its ungated form
    is swept here; a separate test asserts that the demo store did not turn the
    gated form into a second window onto the working copy.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    first = submit(client, form_data("schliebermann_statusfeststellung"))
    second = submit(client, form_data("musterkind_taetigkeitsbeginn_voraus"))
    first_values = identity_strings("schliebermann_statusfeststellung")

    for path in (
        f"/demo/case/{second}/pipeline",
        "/demo/rundgang",
        # Part 19's counterparty surface reached with no reference and with one
        # that names nothing: neither may show a request that belongs to
        # somebody else's case, which is the whole point of the token.
        "/demo/gegenpartei",
        "/demo/gegenpartei?zeichen=guessed",
        "/hinweise",
        "/review",
        "/review/queue/Referat_340_Clearingstelle",
        f"/review/queue/Referat_340_Clearingstelle?highlight={first}",
        f"/review/case/{first}",
        "/inbox",
        "/metrics",
        "/",
    ):
        body = client.get(path).text
        for value in first_values:
            assert value not in body, f"{value!r} reached {path}"


def test_the_caseworker_pages_never_show_the_working_copy_text(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-026 stands: the demo store is not a back door into the review UI.

    The case view still shows sealed KINDS and span COORDINATES and no content,
    exactly as part 10 shipped it. What part 13 added is visible on the demo
    pages and nowhere else.

    Part 20 swapped the probe from the letter tab's prose to the form's own
    working copy, because the letter tab is gone. The strings below are the
    demo store's dotted-path rendering of the REDACTED structured payload -
    content the caseworker UI has never had a window onto, and the exact thing
    ADR-026 left open.

    Part 23 reads the demo page as TEXT rather than as markup, and the change is
    the point rather than an accommodation. The working copy is now painted like
    code, which means one line of it arrives as a key element, a separator
    element and a value element; a probe over the raw HTML would stop seeing the
    sentence and would have gone green by seeing nothing. What has to be true is
    that a READER sees it, so the assertion is made against what a reader gets.
    The caseworker half is unchanged and is still asserted over the raw bytes,
    where it is strictly the stronger check.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("musterfrau_statusfeststellung"))
    case_page = client.get(f"/review/case/{case_id}?unit=Referat_340_Clearingstelle")
    assert case_page.status_code == 200
    pipeline = client.get(f"/demo/case/{case_id}/pipeline").text
    shown = re.sub(r"<[^>]*>", "", pipeline)
    for sentence in (
        "antrag.taetigkeit_bezeichnung = IT-Beratung und Datenmigration",
        "antrag.antragsart = feststellung_nach_aufnahme",
    ):
        assert sentence not in case_page.text, sentence
        assert sentence not in re.sub(r"<[^>]*>", "", case_page.text), sentence
        # And the demo page does show it, which is the difference part 13 makes.
        assert sentence in shown


#: The two citizen pages that carry the ribbon itself since part 18. The other
#: three carry the ROUTE to it, which is the property that must not regress
#: when the notice stops being repeated on every screen.
RIBBON_PAGES = ("start", "hinweise")


def test_every_demo_page_reaches_the_synthetic_data_notice(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ribbon on two pages, and the way to the notice from all five.

    Until part 17 the ribbon sat on every page and this test said so. The
    user's part-18 direction narrowed it to the landing page and the page it
    links, so the assertion splits in two: the notice RENDERS on those two, and
    it is REACHABLE from every one of them - because a scope change that
    quietly removed the route to the honesty text would be a different change
    from the one that was asked for.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("schliebermann_statusfeststellung"))
    for page in CITIZEN_PAGES:
        path = citizen_path(page, case_id)
        body = client.get(path).text
        carries = page in RIBBON_PAGES
        assert ('id="demo-ribbon"' in body) is carries, path
        assert (phrase("ribbon.text") in body) is carries, path
        assert 'href="/hinweise"' in body, path
    assert demo_personas().note in client.get("/demo/antrag").text


# ------------------------------- 6. the two lines, accessibility and reflow ---


def test_the_highlight_never_reorders_or_hides_anything(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Display only, asserted on the ROWS rather than on the sentence."""
    client = build_client(config, monkeypatch=monkeypatch)
    ids = [
        submit(
            client,
            form_data("schliebermann_statusfeststellung", versicherungsnummer=""),
        )
        for _ in range(3)
    ]
    plain = client.get("/review/queue/Referat_340_Clearingstelle").text
    marked = client.get(
        f"/review/queue/Referat_340_Clearingstelle?highlight={ids[0]}"
    ).text
    assert _rows(plain) == _rows(marked)
    assert len(_rows(plain)) == 3
    assert "Ihr Vorgang" in marked
    assert "Ihr Vorgang" not in plain


def test_a_highlight_for_a_case_not_in_this_queue_says_so(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a confirmation the row is gone, and the page is honest about it."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("schliebermann_statusfeststellung"))
    client.post(
        f"/review/case/{case_id}/confirm",
        data={"unit": "Referat_340_Clearingstelle"},
        follow_redirects=False,
    )
    page = client.get(
        f"/review/queue/Referat_340_Clearingstelle?highlight={case_id}"
    ).text
    assert "steht nicht (mehr) in dieser Warteschlange" in page
    assert 'href="/inbox"' in page


def test_the_demo_adds_no_control_to_the_inbox(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The part-07 line, checked from the part-13 side (ADR-005)."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("schliebermann_statusfeststellung"))
    inbox = client.get("/inbox").text
    assert "<form" not in inbox
    assert "<button" not in inbox
    for path in ("/demo/antrag", f"/demo/case/{case_id}/pipeline"):
        body = client.get(path).text
        assert 'action="/inbox' not in body
        assert "/inbox" in body, "the inbox must be LINKED"
    assert client.post("/inbox", data={}).status_code in (404, 405)


def test_the_inbox_can_be_asked_again_without_gaining_a_control(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part 17: a reload affordance that ADR-005 does not have to bend for.

    Messages arrive here because of actions taken on other screens, so a
    reader needs a way to ask again and a judge looks for one on the page.
    An anchor to the same URL is that, and it is not a control in the sense
    ADR-005 forbids: this page still sends, edits and approves nothing, and
    the assertion above that the document contains no `<form>` and no
    `<button>` stays true rather than being relaxed.

    The timestamp is what makes it observable. The outbox usually holds
    exactly what it held a moment ago, and a reload that changes nothing on
    screen is indistinguishable from a dead link - which is the defect this
    part was called in to fix on the metrics page.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    submit(client, form_data("schliebermann_statusfeststellung"))
    for lang in ("de", "en"):
        client.get(f"/inbox?lang={lang}", follow_redirects=True)
        body = client.get("/inbox").text
        assert (
            f'<a class="cta cta-secondary" href="/inbox">{phrase("inbox.reload", lang)}</a>'
            in body
        ), lang
        assert 'id="inbox-rendered-at"' in body, lang
        # Still no control of any kind, in either language.
        assert "<form" not in body, lang
        assert "<button" not in body, lang
    client.get("/inbox?lang=de", follow_redirects=True)

    # Two renders of an unchanged outbox differ, and differ only there.
    stand = re.compile(r'id="inbox-rendered-at">\s*[^<]*')
    with patch.object(inbox_view, "render_clock", lambda: "2026-08-15T09:00:00+00:00"):
        first = client.get("/inbox").text
    with patch.object(inbox_view, "render_clock", lambda: "2026-08-15T09:01:07+00:00"):
        second = client.get("/inbox").text
    assert first != second, "a reload changes nothing a reader can see"
    assert "2026-08-15T09:01:07+00:00" in second
    assert stand.sub("STAND", first) == stand.sub("STAND", second)


@pytest.mark.parametrize("phase", CITIZEN_PAGES)
def test_the_new_pages_meet_the_mechanical_accessibility_bar(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """The same criteria part 10's suite asserts, on the citizen pages."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("schliebermann_statusfeststellung"))
    body = client.get(citizen_path(phase, case_id)).text

    assert '<html lang="de">' in body
    assert body.count("<h1") == 1
    assert body.index('class="skip-link"') < body.index("<header")
    assert 'href="#inhalt"' in body and 'id="inhalt"' in body
    for landmark in ("<header", "<nav", "<main", "<footer"):
        assert landmark in body, landmark
    # Every table has a caption and scoped headers.
    assert body.count("<table") == body.count("<caption")
    assert "<th>" not in body
    # No script-only interaction, no removed focus, no hover-only content.
    for forbidden in ("onclick", "onmouseover", 'href="#"', "tabindex="):
        assert forbidden not in body, forbidden
    # Every visible control has a label.
    for control_id in re.findall(r'<(?:input|textarea|select)[^>]*id="([^"]+)"', body):
        assert f'for="{control_id}"' in body, control_id
    # Headings do not skip a level.
    levels = [int(level) for level in re.findall(r"<h([1-6])", body)]
    assert all(later - earlier <= 1 for earlier, later in pairwise(levels))


def test_the_landing_page_opens_the_tour_and_reflows_with_it(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The landing page is the first screen a visitor reads, on their phone.

    It is demo-only like the tour pages, so it is in the same class: it links
    into the tour and into phase 1, and it loads the stylesheets that carry the
    reflow rules. The part-11 content is untouched.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    page = client.get("/").text
    # Both routes into the demo are offered above the individual pages: a
    # visitor with ninety seconds should spend them inside the system rather
    # than on the metrics table. Which of the two is PAINTED is part 24's
    # decision and is asserted in its own test, not here.
    assert 'href="/demo/rundgang"' in page
    assert page.index('href="/demo/rundgang"') < page.index('href="/metrics"')
    assert 'href="/demo/antrag"' in page
    assert 'href="/static/system.css"' in page
    assert 'href="/static/demo.css"' in page
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in page
    # Part 11's promises are still on it.
    assert "Einwegventil" in page
    assert review_view.PICKER_NOTE in page


# ---------------------------------------------------------- 7. the tour (P-15) ---


def seed_the_tour_case(client: TestClient, config: ConfigBundle, gold_dir: Path) -> str:
    """Put the tour's gold item into this client's journal, the ingest way.

    The same fold ``engine/demo/seed.py`` runs per item on a real deployment,
    over one item: this suite has no seeded state directory, and a tour that
    was only ever tested against an empty journal would never exercise the
    branch every hosted instance actually renders.
    """
    from engine.pipeline import run_pipeline

    payload = json.loads(
        (gold_dir / f"{demo_view.TOUR_ITEM_ID}.json").read_text(encoding="utf-8")
    )
    result = run_pipeline(
        payload,
        config=config,
        journal=client.app.state.journal,  # type: ignore[attr-defined]
        vault=client.app.state.vault,  # type: ignore[attr-defined]
        now=BASE_TIME,
        text_detector=text_seal_detector(with_ner=False),
    )
    return result.decision.case_id


def test_the_tour_tells_the_whole_story_in_six_steps(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Six steps, in order, each one linking where it actually happens.

    The order is the argument: problem, submission, machine, human, applicant,
    trust. A tour that opened with the metrics would be a tour of a dashboard.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    page = client.get("/demo/rundgang")
    assert page.status_code == 200
    body = page.text
    assert re.findall(r'id="(schritt-\d)"', body) == [
        f"schritt-{index}" for index in range(1, 7)
    ]
    # Part 16: no inline English asides anywhere. The header toggle carries
    # the whole page into English instead, which is asserted in tests/test_i18n.
    assert 'class="aside"' not in body
    assert "In English" not in body
    # The six destinations, and no page of this system left out.
    for href in ('href="/demo/antrag"', 'href="/inbox"', 'href="/metrics"'):
        assert href in body, href
    # The claims a judge is asked to check, in words rather than in a badge.
    for claim in ("Einwegventil", "403", "EUPL-1.2", "synthetisch"):
        assert claim in body, claim
    # And the honesty the rest of the project keeps: the accessibility posture
    # is a self-assessment and the page says so rather than implying an audit.
    assert "Selbsteinschätzung" in body
    assert "BITV 2.0" in body


@pytest.mark.parametrize("open_intake", [True, False])
def test_the_tour_states_the_intake_posture_it_actually_has(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch, open_intake: bool
) -> None:
    """Both states, because the hosted demo has one and a local run the other.

    With a token the tour invites a visitor to run their OWN case through; with
    none it says the ingest is closed, says WHY that is the safe state, and
    still offers the page - phases 2 and 3 walk the seeded corpus either way.
    Neither wording is an apology and neither is a promise the instance cannot
    keep (ADR-027, the part-13 precedent).
    """
    client = build_client(
        config, token=TOKEN if open_intake else None, monkeypatch=monkeypatch
    )
    body = client.get("/demo/rundgang").text
    if open_intake:
        assert phrase(demo_view.TOUR_OPEN_NOTE) in body
        assert 'class="cta" href="/demo/antrag"' in body
        assert "Eingang gesperrt" not in body
    else:
        assert phrase(demo_view.TOUR_CLOSED_NOTE) in body
        assert "Eingang gesperrt" in body
        # The page is still offered, just without the promise of a submission.
        assert 'href="/demo/antrag"' in body
        assert 'class="cta" href="/demo/antrag"' not in body


def test_the_tour_walks_a_seeded_case_before_anybody_submits(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch, gold_v4_dir: Path
) -> None:
    """Step 3 has to be walkable on an instance that accepts nothing.

    So it points at a case from the frozen corpus, and every link it prints
    resolves. The tier and the unit come from ``review_state`` - the same
    projection the caseworker UI and the pipeline view fold - so the tour
    cannot state a routing answer that differs from the one the system gave.
    """
    client = build_client(config, token=None, monkeypatch=monkeypatch)
    case_id = seed_the_tour_case(client, config, gold_v4_dir)
    body = client.get("/demo/rundgang").text

    state = review_view.build_case_view(
        client.app.state.journal,  # type: ignore[attr-defined]
        config=config,
        case_id=case_id,
        unit_id=None,
        now=BASE_TIME,
    )
    assert state is not None
    assert state.tier_label in body
    assert review_view.unit_name(config, state.state.unit_id) in body

    for path in (
        f"/demo/case/{case_id}/pipeline",
        f"/review/queue/{state.state.unit_id}?unit={state.state.unit_id}",
        f"/review/case/{case_id}?unit={state.state.unit_id}",
    ):
        assert f'href="{path}"' in body, path
        assert client.get(path).status_code == 200, path
    # And it says out loud why the working-copy panel is missing over there:
    # that compartment holds what a VISITOR typed, and nobody typed this one.
    assert phrase(demo_view.TOUR_SEEDED_NOTE) in body


def test_the_tour_links_nothing_dead_when_nothing_was_seeded(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A developer's first run has an empty journal, and the page is honest.

    No fabricated case id, no link into a 404, and a sentence saying which
    command fills the state. The alternative - printing the id anyway because
    a seeded deployment would have it - is how a demo greets its first visitor
    with an error page.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    body = client.get("/demo/rundgang").text
    assert phrase(demo_view.TOUR_UNSEEDED_NOTE) in body
    assert "/demo/case/" not in body
    assert 'href="/review"' in body


@pytest.mark.parametrize("phase", CITIZEN_PAGES)
def test_the_new_pages_are_built_to_reflow_at_320_css_pixels(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """1.4.10 on the citizen pages, closed since part 13 and held here.

    A static check cannot measure a viewport, so it checks the three things
    that make reflow possible and whose absence makes it impossible: the
    viewport meta, every wide table inside its own scroll container, and no
    fixed pixel width in the stylesheets these pages load. The caseworker
    pages get the same three in ``tests/test_review_accessibility.py``, which
    is what part 15 closed.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("schliebermann_statusfeststellung"))
    body = client.get(citizen_path(phase, case_id)).text
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in body
    assert body.count("<table") == body.count('<div class="scroll-x">')
    assert "width:" not in body and "style=" not in body

    # Part 15 moved the shared reflow rules into the design system, where every
    # page gets them; demo.css keeps only the ones that stack a layout these
    # pages alone have (the persona grid, the before/after panels).
    system = Path("ui/static/system.css").read_text(encoding="utf-8")
    assert "@media (max-width: 40rem)" in system
    assert "overflow-x: auto" in system
    demo = Path("ui/static/demo.css").read_text(encoding="utf-8")
    assert "@media (max-width: 40rem)" in demo
    for css in (system, demo):
        assert not re.search(r":\s*\d{3,}px", css), "no fixed pixel width"
        assert "outline: none" not in css and "outline: 0" not in css


def test_the_phase_connector_stops_at_the_edge_of_the_circles() -> None:
    """The line between two steps runs edge to edge, not centre to centre.

    A connector from `left: -50%` to `right: 50%` reaches the MIDDLE of the
    circle on either side and crosses both discs. It shipped that way and was
    visible wherever a circle renders as a ring rather than as a fill - and it
    paints on TOP of the previous circle, because the pseudo-element belongs to
    a later sibling than the circle it overlaps.

    This cannot measure a box, and it no longer pins the two literals that made
    it look as though it could. Whether the gap is really there is a browser
    question and was answered in a browser at 320, 768, 1024, 1440 and 1920 px.

    WHAT IT PINS INSTEAD IS THE ARITHMETIC (part 18, when the mark grew from
    2.2rem to 2.6rem). The old version wrote the radius into the assertion by
    hand in two places and then checked separately that the mark was still the
    size those numbers assumed - three literals that have to be kept in step by
    somebody remembering. The radius is now READ OUT of the `.phase-mark` rule
    and the connector is required to pull back by exactly that much plus one
    step of the spacing ladder. Resizing the mark and forgetting the connector
    is a failure; resizing both together is not an edit to this test.
    """
    system = Path("ui/static/system.css").read_text(encoding="utf-8")
    mark = re.search(r"\.phase-mark\s*\{([^}]*)\}", system)
    assert mark, "no mark rule"
    size = re.search(r"width:\s*([\d.]+)rem", mark.group(1))
    assert size, "the mark has no width in rem"
    radius = float(size.group(1)) / 2
    # `2.6 / 2` is `1.3`, and CSS is written without a trailing zero.
    written = f"{radius:g}rem"
    connector = re.search(r"\.phase::before\s*\{([^}]*)\}", system)
    assert connector, "no connector rule"
    body = connector.group(1)
    gap = re.search(r"left: calc\(-50% \+ [\d.]+rem \+ (var\(--space-\d\))\)", body)
    assert gap, body
    assert f"left: calc(-50% + {written} + {gap.group(1)})" in body, body
    assert f"right: calc(50% + {written} + {gap.group(1)})" in body, body
    # The stacked variant below 40rem has no connectors and keeps none.
    narrow = system.split("@media (max-width: 40rem)")[1]
    assert re.search(r"\.phase::before\s*\{\s*content: none;", narrow)


def _rows(page: str) -> list[str]:
    """The case ids in a queue table, in the order they are rendered."""
    return re.findall(r'href="/review/case/([^"?]+)', page)


# ------------------------------------------------- 8. the form controls (P-16) ---


def intake(client: TestClient, persona_id: str) -> str:
    return client.get(f"/demo/antrag?persona={persona_id}").text


def test_the_name_is_two_boxes_that_submit_one_string(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked surname-first, submitted given-name-first, unchanged downstream.

    Two properties in one test because they are one decision. The FORM has two
    labelled inputs in the order a German administrative form asks; the
    SUBMISSION carries the single "Vorname Nachname" string the envelope has
    always carried, so nothing after the boundary can tell that the page
    changed. The arcs are the proof that nothing did (test_demo_personas).
    """
    client = build_client(config, monkeypatch=monkeypatch)
    body = intake(client, "schliebermann_statusfeststellung")
    for field_id in ("nachname", "vorname"):
        assert f'id="feld-{field_id}"' in body, field_id
        assert f'for="feld-{field_id}"' in body, field_id
    assert body.index('id="feld-nachname"') < body.index('id="feld-vorname"')
    # They sit in one row, which is what "visually one group" means here.
    assert '<div class="field-row field-row-2">' in body
    # And the single field the form used to have is gone from the page.
    assert 'id="feld-name"' not in body

    case_id = submit(client, form_data("schliebermann_statusfeststellung"))
    page = client.get(f"/demo/case/{case_id}/pipeline").text
    # The echo shows the visitor the string the machine received, not the
    # order the boxes were in.
    assert "Beate Schliebermann" in page
    assert "Schliebermann Beate" not in page


def test_emptying_one_half_of_the_name_submits_the_other(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank half is dropped, not joined into a string with a stray space."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("schliebermann_statusfeststellung", vorname=""))
    page = client.get(f"/demo/case/{case_id}/pipeline").text
    assert "Schliebermann" in page
    assert " Schliebermann" not in page.split("Von Ihnen eingegeben")[1][:400]


def test_the_two_dates_are_native_pickers_with_a_text_fallback_hint(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`type="date"` submits the ISO value the pipeline already expected.

    The hint is not decoration: a browser without a date picker renders the
    control as a text box, and a visitor then has to be told which of the three
    plausible orderings this field wants.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    body = intake(client, "schliebermann_statusfeststellung")
    for field_id in ("geburtsdatum", "taetigkeit_beginn"):
        assert f'<input type="date" id="feld-{field_id}"' in body, field_id
        assert f'aria-describedby="hilfe-{field_id}"' in body, field_id
    assert phrase("intake.date.hint") in body
    # A date the calendar allows is a date the pipeline still judges: the
    # tampering the hints panel promises keeps working through the new control.
    case_id = submit(
        client, form_data("schliebermann_statusfeststellung", geburtsdatum="1902-01-01")
    )
    assert "geburtsdatum" in client.get(f"/demo/case/{case_id}/pipeline").text


def test_the_selects_offer_the_configuration_s_own_vocabulary(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read from `one_of`, never written here.

    An option this page offers is by construction an option the completeness
    checker accepts, because both read the same requirement. A hand-written
    list would be a second vocabulary, and the first thing a second vocabulary
    does is drift.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    body = intake(client, "musterfrau_statusfeststellung")
    for field_id in ("antragsart", "antragsteller_rolle", "taetigkeit_bezeichnung"):
        assert f'<select id="feld-{field_id}"' in body, field_id
    for option in demo_view.vocabulary(config, "antrag.antragsart"):
        assert f'<option value="{option}"' in body, option
    assert demo_view.vocabulary(config, "antrag.antragsteller_rolle") == (
        "auftragnehmer",
        "auftraggeber",
        "gemeinsam",
    )
    # A requirement with no `one_of` has no vocabulary to read, which is why
    # the activity description carries its options in the persona file.
    assert demo_view.vocabulary(config, "antrag.taetigkeit_bezeichnung") == ()
    assert '<option value="IT-Beratung und Datenmigration" selected>' in body
    # Selecting fills the field exactly as typing did.
    case_id = submit(
        client,
        form_data("musterfrau_statusfeststellung", antragsart="prognose_vor_aufnahme"),
    )
    assert (
        "Referat_340_Clearingstelle"
        in client.get(f"/demo/case/{case_id}/pipeline").text
    )


def test_a_value_outside_the_vocabulary_is_kept_rather_than_silently_changed(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A select must never quietly submit something else than it was given.

    A superseded configuration or a bookmarked URL can hand this page a value
    the current vocabulary does not have. Dropping it would make the form
    submit a DIFFERENT application than the one on the screen, so the value is
    offered as the selected option instead.
    """
    from api.i18n import GERMAN

    persona = demo_personas().get("musterfrau_statusfeststellung")
    assert persona is not None
    rows = demo_view.field_rows(
        persona,
        {"antragsart": "eine_abgeschaffte_antragsart"},
        config=config,
        page=GERMAN,
    )
    field = next(
        entry for row in rows for entry in row if entry.field_id == "antragsart"
    )
    assert field.control == "select"
    assert field.value == "eine_abgeschaffte_antragsart"
    assert field.choices[0] == "eine_abgeschaffte_antragsart"
    assert "feststellung_nach_aufnahme" in field.choices


def test_a_select_with_no_vocabulary_degrades_to_a_text_box(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropdown with nothing in it is a control a visitor cannot use."""
    from api.i18n import GERMAN
    from engine.demo.personas import PersonaField

    persona = demo_personas().first
    orphan = PersonaField(
        field_id="lieblingsfarbe",
        label="Lieblingsfarbe",
        path="antrag.lieblingsfarbe",
        value="blau",
        control="select",
    )
    rows = demo_view.field_rows(
        replace(persona, fields=(orphan,)), {}, config=config, page=GERMAN
    )
    assert rows[0][0].control == "text"
    assert rows[0][0].choices == ()


def test_the_hints_panel_describes_the_controls_the_form_renders(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The panel promises real behaviour; every promise names a real control.

    Part 22 replaced the panel with five suggestions the user wrote. Two are
    made in a calendar, three are DELETIONS - and a deletion the browser would
    block is a promise the page breaks, so the field each one names has to be
    on the page, has to be one a visitor can actually empty
    (``demo_view.HINT_DELETED_FIELDS``), and has to be one every persona
    carries: the panel is the same on all four screens.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    body = intake(client, "schliebermann_statusfeststellung")
    hints = demo_personas().hints
    assert len(hints) == 5
    for label, detail in hints:
        # Escaped, because a hint that quotes a value is not in the HTML
        # verbatim - Jinja escapes everything it renders.
        assert str(escape(label)) in body, label
        assert str(escape(detail[:40])) in body, label
    assert "Kalender" in body, "the date suggestions name the control"
    assert "Textfeld" in body, "the deletion hint names the control it means"
    assert '<input type="text" id="feld-versicherungsnummer"' in body
    assert '<input type="text" id="feld-auftraggeber_name"' in body
    assert '<input type="date" id="feld-taetigkeit_beginn"' in body

    # Every field a hint asks to delete is on every persona's form and none of
    # them asks the browser to block the deletion.
    for chosen in demo_personas().personas:
        page = intake(client, chosen.persona_id)
        for field_id in demo_view.HINT_DELETED_FIELDS:
            entry = chosen.field(field_id)
            assert entry is not None, f"{chosen.persona_id} has no {field_id}"
            control = re.search(
                rf'<(input|select)[^>]*id="feld-{field_id}"[^>]*>', page
            )
            assert control, f"{chosen.persona_id}.{field_id} is not rendered"
            assert "required" not in attributes(control.group(0))


# ------------------------- 12. the homepage says less (part 23) ---


#: The three things the "Fangen Sie hier an" section used to say before it said
#: the same thing a fourth time with five cards. Pinned by their ABSENCE, in
#: both languages, because the reason each one went is that the page already
#: carried it: the tour has a button in the hero, and a grid of five named
#: cards does not need a line announcing that a grid of five named cards
#: follows.
REMOVED_FROM_THE_HOMEPAGE = (
    "Oder direkt an eine Stelle springen",
    "Or jump straight in",
    "erzaehlt das ganze System",
    "erzählt das ganze System",
    "walks the whole system end to end",
)


def test_the_homepage_names_the_procedure_and_invites_one_thing(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hero says which procedure this is and what to do about it.

    The headline is a legal citation and it is the one string in this project
    that carries a Paragraphenzeichen - every other citation on every other page
    is written "par. 7a Abs. 4 SGB IV", which is the house convention and is
    untouched. This asserts both halves of that: the sign is here, and it did
    not spread.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    for lang, headline, lead in (
        (
            "de",
            "Optimiertes Statusfeststellungsverfahren nach § 7a SGB IV",
            "Stellen Sie testweise einen Antrag mit Beispielszenarien",
        ),
        (
            "en",
            "Streamlined status determination under § 7a SGB IV",
            "Submit a test application using one of the example scenarios",
        ),
    ):
        client.get(f"/?lang={lang}", follow_redirects=True)
        page = client.get("/").text
        assert f"<h1>{headline}</h1>" in page, lang
        assert f'<p class="hero-lead">{lead}</p>' in page, lang
        # The sign appears once, in that headline, and nowhere else on the page.
        assert page.count("§") == 1, lang
        for gone in REMOVED_FROM_THE_HOMEPAGE:
            assert gone not in page, (lang, gone)


def test_the_paragraph_sign_stays_on_the_one_string_it_was_written_for(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The house convention, checked where it could most easily erode.

    A sign introduced for one headline is a sign somebody copies into the next
    citation they write. Every other visitor-facing page carries several, so
    this walks them and asserts the ASCII form is still what they use.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    for path in ("/hinweise", "/demo/rundgang", "/demo/antrag", "/demo/gegenpartei"):
        body = client.get(path).text
        assert "§" not in body, path
    assert "par. 7a Abs. 4 SGB IV" in client.get("/demo/rundgang").text


def test_the_tour_is_offered_once_and_the_start_section_is_its_cards(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One action in the hero, one door to the tour, and no skipped heading.

    Part 23 removed the second "Zum Rundgang" button four hundred pixels under
    the first; part 24 made the intake the painted control the lead asks for;
    and the user's decision of 2026-08-18 removed the hero's tour button
    entirely. The hero now offers exactly one action - the intake, which is
    what the lead has asked for since part 23 - and the tour's one remaining
    door is the menu's "Rundgang" item, which is navigation rather than a call
    to action. The button is commented out in `landing.html`, not deleted, so
    restoring it is an uncomment; this test is what makes that restoration a
    deliberate act rather than a drift.

    What is left in the start section is its heading and three fully
    clickable cards (the operations-endpoints and Postfach cards left with
    the same 2026-08-19 direction), the cards a level up with the line that
    used to sit between them - a page that skips from `h2` to `h4` is a page
    a screen reader reports a missing level in.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    page = client.get("/").text
    # The tour route renders exactly once: the menu item. No hero button, and
    # no second offer anywhere on the page.
    assert page.count('href="/demo/rundgang"') == 1
    assert '<a class="cta cta-secondary" href="/demo/rundgang">' not in page
    assert page.count('<a class="cta" href="/demo/antrag">') == 1
    assert phrase("landing.start.heading") in page
    assert '<section aria-labelledby="start-heading">' in page
    assert '<h2 id="start-heading">' in page
    levels = [int(level) for level in re.findall(r"<h([1-6])", page)]
    assert all(later - earlier <= 1 for earlier, later in pairwise(levels))
    # The card grid: THREE cards - the operations-endpoints card left on the
    # user's decision of 2026-08-19 (its /health links answer JSON - the one
    # box that led nowhere a visitor could read) and the Postfach card with
    # it (mid-journey material; the tour, the queue page's closing note and
    # the menu still lead there). Each remaining card is a single destination
    # whose whole box is the click target: the grid carries the stretched-link
    # class and every heading holds the anchor.
    section = page[page.index('<section aria-labelledby="start-heading">') :]
    section = section[: section.index("</section>")]
    assert '<ul class="card-grid card-grid-links">' in section
    assert section.count('<li class="card') == 3
    assert section.count("<h3><a href=") == 3
    assert 'href="/health' not in section
    assert 'href="/inbox"' not in section
    assert section.startswith(
        '<section aria-labelledby="start-heading">\n  <h2 id="start-heading">'
    )
    assert '<li class="card card-cta">' in section
    assert section.index('<li class="card card-cta">') < section.index(
        'href="/review"'
    ), "the call to action is the first card"


def test_the_call_to_action_card_paints_itself_out_of_the_button_tokens(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A standout card is a token re-point, not a second palette.

    The fill and the label are `--grad-cta` and `--cta-ink` - the pair every
    button on the site already uses, so the card inverts with a ground rather
    than needing one rule per ground. The focus ring is overridden to the same
    ink for the reason the closing band's is: the design system's ring measures
    1.37:1 against this fill and a keyboard reader would see nothing.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    assert '<li class="card card-cta">' in client.get("/").text
    system = Path("ui/static/system.css").read_text(encoding="utf-8")
    block = system[system.index(".card-grid > .card-cta {") :]
    block = block[: block.index("}")]
    assert "background: var(--grad-cta);" in block
    assert "color: var(--cta-ink);" in block
    assert ".card-grid > .card-cta::before {\n  background: var(--cta-ink);" in system
    assert ".card-cta :focus-visible {\n  outline-color: var(--cta-ink);" in system


# ------------------- 13. the working copy is painted like code (part 23) ---


#: Inputs `segments()` has to survive without changing a character of them.
#: The last three are the shapes a naive line parser gets wrong: a value that
#: contains the separator, a line that has none, and text that ends on one.
WORKING_COPY_TEXTS = (
    "",
    "antrag.antragsart = feststellung_nach_aufnahme",
    "antragsteller.name = [[PII|NAME|BCDFGHJKMNPQ]]",
    "antrag.anlagen[0] = c0031_auftragsverhaeltnis\nantrag.kanal = fit_connect",
    "a.b = one = two = three",
    "Sehr geehrte Damen und Herren,\n\nAnschrift: [[PII|ADDR|22233344455M]]\n",
    "antrag.freitext = erste Zeile\nzweite Zeile ohne Trenner\n",
)


@pytest.mark.parametrize("text", WORKING_COPY_TEXTS)
@pytest.mark.parametrize("shape", ("structured", "text", "something-else"))
def test_the_spans_wrap_the_working_copy_and_never_alter_it(
    text: str, shape: str
) -> None:
    """THE PROPERTY THE WHOLE TREATMENT RESTS ON.

    A coloured working copy has to be the same working copy. The page is built
    by concatenating segment texts into elements, so if that concatenation is
    the input then nothing was added, dropped, reordered or rewritten - and the
    colouring is presentation in the strict sense.
    """
    assert "".join(run.text for run in demo_view.segments(text, shape)) == text


@pytest.mark.parametrize("shape", ("structured", "text"))
def test_no_span_boundary_ever_falls_inside_a_placeholder(shape: str) -> None:
    """WHY THE CANARY SWEEP IS STILL A SWEEP.

    A substring search over markup cannot see a value a tag fell inside of. The
    placeholders are the strings that sweep is looking for, so every one of them
    has to arrive as exactly one segment: the boundaries are placed at their
    edges and never within them.
    """
    text = (
        "antragsteller.name = [[PII|NAME|BCDFGHJKMNPQ]]\n"
        "antragsteller.anschrift = [[PII|ADDR|22233344455M]], "
        "[[PII|ADDR|RSTVWXZ23456]]\n"
        "antrag.antragsart = feststellung_nach_aufnahme"
    )
    runs = demo_view.segments(text, shape)
    assert [run.text for run in runs if run.placeholder] == [
        "[[PII|NAME|BCDFGHJKMNPQ]]",
        "[[PII|ADDR|22233344455M]]",
        "[[PII|ADDR|RSTVWXZ23456]]",
    ]
    # And a placeholder is never given a role, so it is never wrapped twice.
    assert all(run.role == "" for run in runs if run.placeholder)


def test_a_structured_dump_gets_the_grammar_and_a_letter_gets_none() -> None:
    """The line between machine text and a person's words, drawn on the shape.

    The store already records which of the two a part is, so the distinction is
    read rather than guessed at. A structured dump has a key, a separator and a
    value; a text part is what somebody wrote with the identity values sealed
    out of it, and marking its nouns would say the machine had parsed a
    sentence it never parsed.
    """
    line = "antrag.antragsart = feststellung_nach_aufnahme"
    structured = demo_view.segments(line, "structured")
    assert [(run.role, run.text) for run in structured] == [
        ("key", "antrag.antragsart"),
        ("punct", " = "),
        ("value", "feststellung_nach_aufnahme"),
    ]
    assert all(run.role == "" for run in demo_view.segments(line, "text"))
    # A continuation line inside a structured part has no key and gets no key.
    wrapped = demo_view.segments("a.b = one\nnoch eine Zeile", "structured")
    assert [run.role for run in wrapped] == ["key", "punct", "value", "", ""]


def test_the_pipeline_paints_the_machines_copy_and_not_the_visitors(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two blocks side by side, and only one of them is code.

    The left half is the letter a visitor typed; the right half is what the
    machine holds instead. Colouring both would say the two are the same kind of
    thing, which is the one claim this comparison exists to deny - so the
    grammar appears in the structured dump, the seals appear in both, and the
    visitor's own paragraph carries no span at all.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    # With the C0031 annex ticked, so the working copy has BOTH shapes in it:
    # the structured dump and the prepared document's free text.
    chosen = persona("musterfrau_statusfeststellung")
    case_id = submit(
        client,
        form_data(
            "musterfrau_statusfeststellung",
            **{chosen.attachments[0].field_name: "1"},
        ),
    )
    page = client.get(f"/demo/case/{case_id}/pipeline").text
    assert '<span class="tok-key">antrag.antragsart</span>' in page
    assert '<span class="tok-punct"> = </span>' in page
    assert '<span class="tok-value">feststellung_nach_aufnahme</span>' in page
    assert '<mark class="placeholder">[[PII|NAME|' in page
    # The free-text part is machine text too, but it is somebody's sentences:
    # its placeholders are marked and nothing in it is given a grammar.
    blocks = re.findall(r"<pre>(.*?)</pre>", page, re.S)
    assert blocks, "the pipeline page renders no working copy at all"
    prose = [block for block in blocks if "Clearingstelle" in block]
    assert prose, "the free-text part is not on the page"
    for block in prose:
        assert "tok-key" not in block
        assert "tok-value" not in block
        assert 'class="placeholder"' in block


def test_the_hand_off_to_phase_three_is_the_primary_button(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One forward action per page, rendered as the control it is.

    It used to be a link inside a tinted panel. It is now the same `.cta` the
    intake submits with, whose fill and ink invert together on the machine
    ground - and it is still a link, so it navigates with scripting off and is
    one tab stop. The panels stay on the two-party side roads below it.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("musterfrau_statusfeststellung"))
    page = client.get(f"/demo/case/{case_id}/pipeline").text
    handover = phrase("pipeline.g.handover")
    found = re.search(rf'<a class="cta" href="([^"]+)">{re.escape(handover)}</a>', page)
    assert found, "the phase-3 control is not a primary button"
    assert found.group(1).startswith("/review/queue/")
    assert f'<p class="handover">\n    <a href="{found.group(1)}"' not in page
    # The side roads keep the panel they had.
    assert '<p class="handover">' in page
