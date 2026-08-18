"""The two-party loop: the Auftraggeber is heard, and answers (part 19).

Six groups, and the first two are the ones that would be prose in a worse
repository.

1. **The loop, end to end, through the REAL app.** Submit as Sabine, read the
   request letter off her pipeline page, follow the reference, answer as the
   Auftraggeber, and watch the answer arrive as its own sealed case that both
   pages then link. Nothing in this group inspects an internal: it posts forms
   and reads pages, because that is what a judge does.
2. **The correlation.** The token is drawn rather than derived, it is not a
   function of any case id, an unknown one is a page and never a 404 or somebody
   else's request, it expires with the request it belongs to, and a request can
   be answered once - a second answer would be a second case, not an edit.
3. **What the statement carries, and what it deliberately does not.** The
   counterparty's own name, company, Betriebsnummer and address are sealed at
   the boundary exactly like the applicant's; the applicant's Versicherungsnummer
   and Geburtsdatum are absent from the surface AND from the submission, and the
   completeness check's honest answer to that absence is two gaps and tier 2.
4. **Display only.** The cross-link on the caseworker case view re-derives
   nothing, reorders no queue and writes no journal event - the same four rules
   the tour's `highlight` has followed since part 13 - and with the flag off the
   page is byte-identical.
5. **The fixtures.** Every invented value is Mustermann-class, collides with
   nothing in either frozen set, and carries the SHAPE the deterministic
   detector union needs. Every select option this module offers actually occurs
   in the corpus scenario file it says it was read from.
6. **The states of the page**: no reference, an unknown reference, a live
   hearing, an answered one - four pages, no error, and each one says which it
   is.

Every client here injects the DETERMINISTIC union, which is the part-10
precedent and the shipped demo's posture (the container installs no `[redact]`
extra): a test that ran with the optional model would be testing a
configuration the demo never runs.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader
from markupsafe import escape

from api import demo as demo_view
from api import review as review_view
from api.app import create_app
from api.i18n import TABLE, PageContext, phrase
from api.metrics import TEMPLATE_DIR, environment, set_demo_posture
from engine.config_loader import ConfigBundle
from engine.demo import DEMO_MODE_ENV, INGEST_TOKEN_ENV, DemoPosture, gegenpartei
from engine.demo import demo_posture as posture_cache
from engine.demo.personas import Persona, demo_personas
from engine.demo.store import (
    DEFAULT_CAPACITY,
    DemoStore,
    StatementAnswer,
    StatementLink,
    new_token,
)
from engine.draft import InMemoryDraftStore
from engine.journal import InMemoryJournalStore
from engine.notify import InMemoryOutbox
from engine.redact import InMemoryVaultStore, text_seal_detector
from engine.redact.placeholders import PLACEHOLDER_RE
from engine.review import build_index, build_queue
from schemas.events import EventType

TOKEN = "gegenpartei-token"
SABINE = "musterfrau_statusfeststellung"
CLEARING = "Referat_340_Clearingstelle"
BASE_TIME = datetime(2026, 8, 18, 9, 0, tzinfo=UTC)

#: The scenario file the Indizien vocabularies say they were read from.
SCENARIOS = Path("corpus/generator/scenarios/statusfeststellung.yaml")


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
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> TestClient:
    """The real app, on in-memory stores, with the deterministic union."""
    if monkeypatch is not None:
        if demo:
            monkeypatch.setenv(DEMO_MODE_ENV, "1")
            monkeypatch.setenv(INGEST_TOKEN_ENV, TOKEN)
        else:
            monkeypatch.delenv(DEMO_MODE_ENV, raising=False)
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


def shown(key: str, lang: str = "de") -> str:
    """One phrase as it appears in the rendered page.

    Jinja escapes what ``t()`` returns, so a sentence carrying a quotation mark
    or an apostrophe is not in the HTML verbatim. The same helper
    ``tests/test_i18n.py`` uses, for the same reason.
    """
    return str(escape(phrase(key, lang)))


def reads(body: str) -> str:
    """One rendered page, as a READER receives it rather than as markup.

    Part 23 paints the working-copy dumps like code, so a line of one arrives
    as three elements and no longer occurs in the HTML as a single string.
    What has to hold is that the line is on the page, so the assertions that
    care about the dump are made against this.
    """
    return re.sub(r"<[^>]*>", "", body)


def persona(persona_id: str = SABINE) -> Persona:
    found = demo_personas().get(persona_id)
    assert found is not None
    return found


def submit(client: TestClient, persona_id: str = SABINE, **edits: str) -> str:
    """Post the intake form and return the case id it redirected to."""
    chosen = persona(persona_id)
    data = {"persona": persona_id, "kanal": "fit_connect", **chosen.form_values()}
    data.update(edits)
    posted = client.post("/demo/antrag", data=data, follow_redirects=False)
    assert posted.status_code == 303, posted.text[:2000]
    return posted.headers["location"].split("/")[3]


def reference(body: str) -> str:
    """The correlation token, read off a rendered page the way a visitor does."""
    found = re.search(r'href="/demo/gegenpartei\?zeichen=([0-9a-f]+)"', body)
    assert found is not None, "no reference on the page"
    return found.group(1)


def statement_form_data(body: str, token: str) -> dict[str, str]:
    """What the counterparty form posts, read off the page it rendered.

    Built by PARSING the page rather than by calling the module that produced
    it: a form whose controls a browser could not submit would still pass a test
    that constructed its own payload, and this suite exists to walk what a
    visitor walks.
    """
    data: dict[str, str] = {"zeichen": token}
    for name, value in re.findall(
        r'<input type="(?:text|date|hidden)" [^>]*name="([^"]+)"[^>]*value="([^"]*)"',
        body,
    ):
        data[name] = value
    for name, value in re.findall(
        r'<input type="hidden" name="([^"]+)" value="([^"]*)"', body
    ):
        data[name] = value
    for name, options in re.findall(
        r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', body, re.S
    ):
        selected = re.search(r'value="([^"]*)" selected', options)
        assert selected is not None, name
        data[name] = selected.group(1)
    prose = re.search(r"<textarea[^>]*>(.*?)</textarea>", body, re.S)
    assert prose is not None
    data["body"] = prose.group(1)
    return data


def answer(client: TestClient, token: str, **edits: str) -> str:
    """Play the Auftraggeber; return the statement's own case id."""
    page = client.get(f"/demo/gegenpartei?zeichen={token}")
    assert page.status_code == 200
    data = statement_form_data(page.text, token)
    data.update(edits)
    posted = client.post("/demo/gegenpartei", data=data, follow_redirects=False)
    assert posted.status_code == 303, posted.text[:2000]
    location = posted.headers["location"]
    assert location.startswith("/demo/case/"), location
    return location.split("/")[3]


def store_of(client: TestClient) -> DemoStore:
    held = client.app.state.demo_store  # type: ignore[attr-defined]
    assert isinstance(held, DemoStore)
    return held


def link(*, token: str = "zeichen-1", **fields: object) -> StatementLink:
    """A bare link, for the unit-level tests that need no HTTP."""
    values: dict[str, object] = {
        "token": token,
        "case_id": "case-demo-0001",
        "created_at": BASE_TIME,
        "auftraggeber": "Seezeichen Beispielwerk GmbH",
        "applicant": "Sabine Musterfrau",
        "taetigkeit": "IT-Beratung und Datenmigration",
        "beginn": "2026-01-08",
        "antragsart": "feststellung_nach_aufnahme",
    }
    values.update(fields)
    return StatementLink(**values)  # type: ignore[arg-type]


# ------------------------------------------------------- 1. the loop, walked ---


def test_the_whole_two_party_loop_from_one_submission(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Submit, read the letter, answer as the Auftraggeber, see both sides.

    The single test that walks what a judge walks, with nothing mocked and
    nothing inspected that a page does not show.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)

    # The request is on the applicant's own page, as a letter, with the
    # reference the counterparty will present.
    page = client.get(f"/demo/case/{case_id}/pipeline")
    assert page.status_code == 200
    assert 'id="gegenpartei"' in page.text
    assert phrase("pipeline.statement.heading") in page.text
    assert phrase("pipeline.statement.waiting") in page.text
    assert '<div class="letter" lang="de">' in page.text
    token = reference(page.text)
    assert f"Zeichen: {token}" in page.text
    assert case_id in page.text

    # The counterparty sees the same letter and a form.
    surface = client.get(f"/demo/gegenpartei?zeichen={token}")
    assert surface.status_code == 200
    assert f"Zeichen: {token}" in surface.text
    assert phrase("gegenpartei.form.heading") in surface.text
    assert 'name="weisungsgebunden"' in surface.text

    # And answering produces a case of its own, through the real pipeline.
    statement_id = answer(client, token)
    assert statement_id != case_id
    statement_page = client.get(f"/demo/case/{statement_id}/pipeline")
    assert statement_page.status_code == 200
    assert phrase("pipeline.statement.origin.heading") in statement_page.text
    assert f'href="/demo/case/{case_id}/pipeline"' in statement_page.text
    assert CLEARING in statement_page.text
    # It is a REAL case: sealed, with a free-text part the boundary walked.
    assert "part-text-0" in statement_page.text
    assert '<mark class="placeholder">' in statement_page.text

    # The applicant's page now says the statement arrived and links it.
    answered = client.get(f"/demo/case/{case_id}/pipeline").text
    assert phrase("pipeline.statement.arrived.heading") in answered
    assert f'href="/demo/case/{statement_id}/pipeline"' in answered
    assert phrase("pipeline.statement.waiting") not in answered
    # ... with what the Auftraggeber said, verbatim.
    assert phrase("pipeline.statement.answers.caption") in answered
    assert "<code>ja</code>" in answered
    assert "<code>beim_auftraggeber</code>" in answered


def test_the_contradiction_the_loop_exists_for(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two sealed statements about one relationship, saying opposite things.

    Sabine's own C0031 says the working time is hers to divide and that there
    is no Weisungsbindung; the counterparty form arrives claiming the reverse,
    and both are readable, side by side, off two working copies. That is the
    Gesamtwuerdigung par. 7a Abs. 2 S. 1 SGB IV reserves for a human, and it is
    why this procedure ships `tier1_enabled: false`.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    chosen = persona()
    case_id = submit(client, **{chosen.attachments[0].field_name: "1"})
    applicant_page = client.get(f"/demo/case/{case_id}/pipeline").text
    assert "keine Weisungsbindung im Einzelnen" in applicant_page

    statement_id = answer(client, reference(applicant_page))
    statement_page = reads(client.get(f"/demo/case/{statement_id}/pipeline").text)
    assert "antrag.weisungsgebunden = ja" in statement_page
    assert "antrag.eingliederung_arbeitsorganisation = ja" in statement_page
    assert "antrag.arbeitsort = beim_auftraggeber" in statement_page
    # Neither case was "resolved" by the other: the contradiction is evidence,
    # and this procedure has no tier-1 row for a checklist to reach.
    review = client.get(f"/review/case/{case_id}?unit={CLEARING}").text
    assert "Tier 3" in review
    assert "tier1_enabled" in client.get(f"/demo/case/{case_id}/pipeline").text


def test_a_different_start_date_is_a_real_contradiction(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one date the counterparty may restate, and both answers survive."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    page = client.get(f"/demo/case/{case_id}/pipeline").text
    statement_id = answer(client, reference(page), taetigkeit_beginn="2025-11-03")
    statement_page = reads(client.get(f"/demo/case/{statement_id}/pipeline").text)
    assert "antrag.taetigkeit_beginn = 2025-11-03" in statement_page
    # The applicant's own answer is untouched on the applicant's own case.
    assert "antrag.taetigkeit_beginn = 2026-01-08" in reads(
        client.get(f"/demo/case/{case_id}/pipeline").text
    )
    # And the summary on the applicant's page repeats what the OTHER side said.
    assert (
        "<code>2025-11-03</code>" in client.get(f"/demo/case/{case_id}/pipeline").text
    )


def test_the_statement_travels_the_one_ingest_path(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One `run_ingest`, one journal, and the raw endpoint keeps its 403.

    The counterparty page is the authorized SERVER-SIDE caller of exactly the
    machinery the intake page calls (ADR-029 ruling 2), which is why the
    statement is journaled, routed and notified like everything else - and why a
    stranger POSTing to `/ingest` still gets refused before their body is read.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    statement_id = answer(
        client, reference(client.get(f"/demo/case/{case_id}/pipeline").text)
    )

    assert client.post("/ingest", json={"submissionId": "x"}).status_code == 403

    journal = client.app.state.journal  # type: ignore[attr-defined]
    events = journal.read(statement_id)
    kinds = [event.type.value for event in events]
    for kind in ("received", "redacted", "tier_decided", "routed"):
        assert kind in kinds, kind
    # NO EVENT TYPE FOR THE LOOP WAS INVENTED. Every event on the statement is
    # one the contract already declared, and the correlation token - the only
    # thing this part added - reaches no journal payload at all. That is the
    # whole of "demo-scoped": the production shape is new event types, and
    # those are a contract change (ADR-036).
    declared = {member.value for member in EventType}
    assert set(kinds) <= declared
    token = store_of(client).link_for_case(case_id)
    assert token is not None
    for event in (*events, *journal.read(case_id)):
        assert token.token not in event.model_dump_json()
    # And the notifications the projection owes were produced for it too.
    assert client.get(f"/inbox/{statement_id}").json()["notifications"]


# --------------------------------------------------------- 2. the correlation ---


def test_the_token_is_drawn_and_derived_from_nothing() -> None:
    """96 bits out of `secrets`, and not a function of any case id."""
    tokens = {new_token() for _ in range(200)}
    assert len(tokens) == 200
    for token in tokens:
        assert len(token) == 24
        assert re.fullmatch(r"[0-9a-f]{24}", token)


def test_the_token_is_not_a_function_of_the_case(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two submissions of the same persona get two unrelated references."""
    client = build_client(config, monkeypatch=monkeypatch)
    first = reference(client.get(f"/demo/case/{submit(client)}/pipeline").text)
    second_case = submit(client)
    second = reference(client.get(f"/demo/case/{second_case}/pipeline").text)
    assert first != second
    # And neither is derived from the case id it belongs to.
    assert first not in second_case and second not in second_case
    assert second_case.replace("case-demo-", "") not in second


def test_an_unknown_reference_is_a_page_and_never_somebody_elses_request(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The house rule for every unknown value here, applied to the token."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    real = reference(client.get(f"/demo/case/{case_id}/pipeline").text)
    for guess in ("", "not-a-token", real[:-1], real.upper(), real + "0"):
        page = client.get(f"/demo/gegenpartei?zeichen={guess}")
        assert page.status_code == 200, guess
        assert phrase("gegenpartei.unknown.heading") in page.text, guess
        assert "<form" not in page.text, guess
        assert "Seezeichen Beispielwerk GmbH" not in page.text, guess
    # The real one still works, which is what makes the five above meaningful.
    assert (
        phrase("gegenpartei.form.heading")
        in client.get(f"/demo/gegenpartei?zeichen={real}").text
    )


def test_a_request_can_be_answered_once(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second statement would be a second case, not an edit of the first."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    page = client.get(f"/demo/case/{case_id}/pipeline").text
    token = reference(page)
    data = statement_form_data(
        client.get(f"/demo/gegenpartei?zeichen={token}").text, token
    )
    first = client.post("/demo/gegenpartei", data=data, follow_redirects=False)
    assert first.status_code == 303

    again = client.post("/demo/gegenpartei", data=data, follow_redirects=False)
    assert again.status_code == 303
    assert again.headers["location"] == demo_view.gegenpartei_href(token)
    surface = client.get(f"/demo/gegenpartei?zeichen={token}")
    assert phrase("gegenpartei.answered.heading") in surface.text
    assert "<form" not in surface.text
    # Exactly two cases exist for this visitor, not three.
    assert len(store_of(client).case_ids()) == 2


def test_the_link_expires_with_the_request_it_belongs_to() -> None:
    """One TTL for both compartments: a letter about a case nobody holds."""
    store = DemoStore(ttl=timedelta(minutes=30))
    store.request_statement(link(), now=BASE_TIME)
    assert store.link_by_token("zeichen-1", now=BASE_TIME) is not None
    later = BASE_TIME + timedelta(minutes=29)
    assert store.link_by_token("zeichen-1", now=later) is not None
    gone = BASE_TIME + timedelta(minutes=30)
    assert store.link_by_token("zeichen-1", now=gone) is None
    assert store.link_for_case("case-demo-0001", now=gone) is None
    assert store.tokens() == ()


def test_answering_does_not_extend_the_lifetime_of_anything() -> None:
    """The correlation still expires when the request would have."""
    store = DemoStore(ttl=timedelta(minutes=30))
    store.request_statement(link(), now=BASE_TIME)
    answered = store.record_statement(
        "zeichen-1",
        statement_case_id="case-demo-answer",
        answers=(StatementAnswer(field_id="weisungsgebunden", value="ja"),),
        now=BASE_TIME + timedelta(minutes=10),
    )
    assert answered is not None
    assert answered.created_at == BASE_TIME
    assert answered.answered
    assert (
        store.link_by_token("zeichen-1", now=BASE_TIME + timedelta(minutes=31)) is None
    )


def test_recording_a_statement_for_a_gone_request_is_none() -> None:
    """Nothing is resurrected and nothing raises: the request simply is not."""
    store = DemoStore()
    assert store.record_statement("never-existed", statement_case_id="c") is None


def test_the_links_are_capacity_bounded_and_reset_wipes_them() -> None:
    """The same three bounds the submission compartment has, asserted."""
    store = DemoStore(capacity=4)
    for index in range(6):
        store.request_statement(
            link(token=f"zeichen-{index}", case_id=f"case-{index}"), now=BASE_TIME
        )
    assert len(store.tokens()) == 4
    assert store.link_by_token("zeichen-0", now=BASE_TIME) is None
    assert store.link_by_token("zeichen-5", now=BASE_TIME) is not None
    store.reset()
    assert store.tokens() == ()
    assert store.link_by_token("zeichen-5", now=BASE_TIME) is None


def test_one_relation_answers_from_both_ends() -> None:
    """`link_for_case` is one finder because there is one relation."""
    store = DemoStore()
    store.request_statement(link(), now=BASE_TIME)
    store.record_statement(
        "zeichen-1", statement_case_id="case-demo-answer", now=BASE_TIME
    )
    asked = store.link_for_case("case-demo-0001", now=BASE_TIME)
    answered = store.link_for_case("case-demo-answer", now=BASE_TIME)
    assert asked == answered
    assert store.link_for_case("case-demo-unrelated", now=BASE_TIME) is None
    assert store.link_for_case("", now=BASE_TIME) is None


def test_a_case_with_no_second_party_gets_no_request(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The emptied-Auftraggeber hint: nobody to hear, so no letter and no section.

    One of the intake hints tells the visitor to empty exactly that field. A
    hearing addressed to nobody would be worse than no hearing, so the request
    is not recorded at all - and the pipeline page then renders no two-party
    section rather than announcing an absence.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, auftraggeber_name="")
    page = client.get(f"/demo/case/{case_id}/pipeline")
    assert page.status_code == 200
    assert 'id="gegenpartei"' not in page.text
    assert phrase("pipeline.statement.heading") not in page.text
    assert store_of(client).link_for_case(case_id) is None


def test_a_seeded_case_has_no_two_party_section(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody submitted it, so nobody was heard - and the page says nothing."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    store_of(client).reset()
    page = client.get(f"/demo/case/{case_id}/pipeline")
    assert page.status_code == 200
    assert 'id="gegenpartei"' not in page.text
    # The journal half of the page is still there, which is the part-13 rule.
    assert CLEARING in page.text


def test_the_request_is_only_recorded_for_the_procedure_that_has_a_second_party(
    config: ConfigBundle,
) -> None:
    """Read at the unit, over the two facts the rule is made of."""
    chosen = persona()
    values = chosen.form_values()
    made = gegenpartei.statement_request(
        chosen,
        values,
        token="zeichen-1",
        case_id="case-1",
        procedure_id="statusfeststellung",
        now=BASE_TIME,
    )
    assert made is not None
    assert made.auftraggeber == "Seezeichen Beispielwerk GmbH"
    assert made.applicant == "Sabine Musterfrau"
    assert made.taetigkeit == "IT-Beratung und Datenmigration"
    assert made.beginn == "2026-01-08"
    assert made.antragsart == "feststellung_nach_aufnahme"
    assert not made.answered

    for procedure in ("altersrente", None, ""):
        assert (
            gegenpartei.statement_request(
                chosen,
                values,
                token="zeichen-1",
                case_id="case-1",
                procedure_id=procedure,
                now=BASE_TIME,
            )
            is None
        ), procedure
    assert (
        gegenpartei.statement_request(
            chosen,
            {**values, "auftraggeber_name": "   "},
            token="zeichen-1",
            case_id="case-1",
            procedure_id="statusfeststellung",
            now=BASE_TIME,
        )
        is None
    )


def test_the_letter_reads_the_name_the_way_ingest_joins_it(
    config: ConfigBundle,
) -> None:
    """Surname-first on the screen, given-name-first in the value (part 16)."""
    chosen = persona()
    made = gegenpartei.statement_request(
        chosen,
        {**chosen.form_values(), "vorname": ""},
        token="zeichen-1",
        case_id="case-1",
        procedure_id="statusfeststellung",
        now=BASE_TIME,
    )
    assert made is not None
    assert made.applicant == "Musterfrau", "a blank half is dropped, not joined"


def test_a_letter_for_a_case_that_named_little_still_reads(
    config: ConfigBundle,
) -> None:
    """No hole in the letter, whatever the visitor left empty."""
    sparse = link(applicant="", taetigkeit="", beginn="", antragsart="")
    letter = "\n".join(gegenpartei.request_letter(sparse))
    assert gegenpartei.UNNAMED_APPLICANT in letter
    assert letter.count(gegenpartei.UNSTATED) == 3
    assert "{" not in letter and "}" not in letter
    # And the prose falls back the same way rather than printing an empty name.
    assert gegenpartei.UNSTATED in gegenpartei.statement_prose(link(auftraggeber=""))


# ---------------------------- 3. what the statement carries, and what it does not ---


def test_the_counterpartys_own_identity_is_sealed_like_everybody_elses(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The demonstration: four kinds, both parties, one boundary.

    The Auftraggeber's contact person (NAME), company (ORG), Betriebsnummer
    (BNR) and address (ADDR) all leave the raw plane at the same boundary the
    applicant's data left it at - and BNR is a kind no other demo surface has
    ever shown.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    statement_id = answer(
        client, reference(client.get(f"/demo/case/{case_id}/pipeline").text)
    )
    page = client.get(f"/demo/case/{statement_id}/pipeline").text
    kinds = {match.group("kind") for match in PLACEHOLDER_RE.finditer(page)}
    assert {"NAME", "ORG", "BNR", "ADDR"} <= kinds
    # The typed values are in the echo (the visitor's own page) and NEVER in a
    # working-copy block.
    for value in (
        gegenpartei.CONTACT_SURNAME,
        gegenpartei.COMPANY_BETRIEBSNUMMER,
        gegenpartei.COMPANY_TOWN,
    ):
        assert value in page, value
    for block in re.findall(r"<pre[^>]*>(.*?)</pre>", page, re.S):
        for value in (
            gegenpartei.CONTACT_SURNAME,
            gegenpartei.COMPANY_BETRIEBSNUMMER,
            gegenpartei.COMPANY_TOWN,
            gegenpartei.COMPANY_STREET,
        ):
            assert value not in block, f"{value!r} survived into the working copy"


def test_the_statement_never_carries_the_applicants_number_or_birth_date(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Data minimisation, asserted on the SURFACE and on the SUBMISSION.

    A Stellungnahme does not need them, so the counterparty page never renders
    them and the second submission never contains them. The honest consequence
    is the next test.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    chosen = persona()
    case_id = submit(client)
    token = reference(client.get(f"/demo/case/{case_id}/pipeline").text)
    surface = client.get(f"/demo/gegenpartei?zeichen={token}").text
    vsnr = chosen.form_values()["versicherungsnummer"]
    geburtsdatum = chosen.form_values()["geburtsdatum"]
    assert vsnr not in surface
    assert geburtsdatum not in surface
    assert 'name="versicherungsnummer"' not in surface
    assert 'name="geburtsdatum"' not in surface

    statement_id = answer(client, token)
    statement = client.get(f"/demo/case/{statement_id}/pipeline").text
    assert vsnr not in statement
    assert "antragsteller.versicherungsnummer" not in statement
    assert "antragsteller.geburtsdatum" not in statement


def test_the_missing_two_are_two_gaps_and_tier_two(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The machine treats the second party exactly like the first.

    It reports what is missing, in the PROCEDURE'S own Nachforderung wording,
    and routes the case to the unit that can decide it. Nobody wrote a branch
    for "this one came from the counterparty", which is the point.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    statement_id = answer(
        client, reference(client.get(f"/demo/case/{case_id}/pipeline").text)
    )
    page = client.get(f"/demo/case/{statement_id}/pipeline").text
    assert "<code>versicherungsnummer</code>" in page
    assert "<code>geburtsdatum</code>" in page
    assert "Sozialversicherungsausweis" in page, "the config's own wording"
    assert phrase("pipeline.statement.minimal") in page
    review = client.get(f"/review/case/{statement_id}?unit={CLEARING}").text
    assert "Tier 2" in review
    assert CLEARING in review


def test_the_statement_is_a_status_application_by_the_other_party() -> None:
    """`antragsteller_rolle: auftraggeber` - par. 7a Abs. 1 S. 1 SGB IV.

    Asserted on the payload the builder produces, because that is the shape the
    gold corpus already has an item for (`sf-0004`).
    """
    form = gegenpartei.statement_form(link())
    payload = gegenpartei.build_statement_submission(
        form,
        form.form_values(),
        submission_id="demo-stellungnahme-0001",
        submitted_at=BASE_TIME.isoformat(),
        body="Eine Schilderung.",
    )
    assert payload["procedureHint"] == "statusfeststellung"
    assert payload["channel"] == "fit_connect"
    assert payload["bodyText"] == "Eine Schilderung."
    assert payload["attachments"] == []
    data = payload["data"]
    assert data["antrag"]["antragsteller_rolle"] == "auftraggeber"
    assert data["antrag"]["taetigkeit_bezeichnung"] == "IT-Beratung und Datenmigration"
    assert data["auftraggeber"]["firmenname"] == "Seezeichen Beispielwerk GmbH"
    assert data["auftraggeber"]["betriebsnummer"] == gegenpartei.COMPANY_BETRIEBSNUMMER
    assert data["antragsteller"]["name"] == "Ole Musterhold", "joined given-name first"
    assert "versicherungsnummer" not in data["antragsteller"]
    assert "geburtsdatum" not in data["antragsteller"]


def test_an_emptied_statement_simply_has_no_covering_text() -> None:
    """A submission with nothing written on it is a submission, not an error."""
    form = gegenpartei.statement_form(link())
    payload = gegenpartei.build_statement_submission(
        form,
        form.form_values(),
        submission_id="demo-stellungnahme-0002",
        submitted_at=BASE_TIME.isoformat(),
        body="   \n  ",
    )
    assert "bodyText" not in payload


def test_the_answers_are_what_the_form_asked_in_the_order_it_asked(
    config: ConfigBundle,
) -> None:
    """One list drives the form and the summary, so they cannot drift."""
    form = gegenpartei.statement_form(link())
    assert (
        tuple(entry.field_id for entry in gegenpartei.question_fields(form))
        == gegenpartei.ANSWER_FIELD_IDS
    )
    answers = gegenpartei.statement_answers(form.form_values())
    assert tuple(entry.field_id for entry in answers) == gegenpartei.ANSWER_FIELD_IDS
    # An answer that came back empty is absent rather than recorded as "".
    thinner = gegenpartei.statement_answers(
        {**form.form_values(), "arbeitsort": "", "honorar_modell": "  "}
    )
    assert "arbeitsort" not in {entry.field_id for entry in thinner}
    assert len(thinner) == len(answers) - 2
    # A key this form does not have is not an answer.
    assert gegenpartei.statement_answers({"erfundenes_feld": "ja"}) == ()


def test_the_prose_says_the_opposite_and_keeps_the_transliterated_spelling() -> None:
    """Submission DATA, so ADR-031's rule applies: no umlauts, German, sealed shapes."""
    prose = gegenpartei.statement_prose(link())
    assert "Weisung" not in prose or "vor" in prose
    assert "eingegliedert" in prose
    for forbidden in "äöüßÄÖÜ":
        assert forbidden not in prose, forbidden
    # The shapes the DETERMINISTIC union needs, without the optional model.
    assert (
        f"Herr {gegenpartei.CONTACT_GIVEN_NAME} {gegenpartei.CONTACT_SURNAME}" in prose
    )
    assert (
        f"{gegenpartei.COMPANY_STREET} {gegenpartei.COMPANY_NUMBER}, "
        f"{gegenpartei.COMPANY_POSTCODE} {gegenpartei.COMPANY_TOWN}" in prose
    )
    # The derivation rule of `config/procedures/statusfeststellung_v1.yaml`:
    # this procedure's signals, and never another procedure's.
    assert "Erwerbsstatus" in prose and "par. 7a" in prose
    lowered = prose.lower()
    assert "altersrente" not in lowered and "rentenbeginn" not in lowered


# ----------------------------------------------------------- 4. display only ---


def test_the_caseworker_cross_link_shows_both_directions(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One relation, read from either end, and nothing else on the page moved."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    statement_id = answer(
        client, reference(client.get(f"/demo/case/{case_id}/pipeline").text)
    )
    application = client.get(f"/review/case/{case_id}?unit={CLEARING}").text
    assert "Zweite Partei" in application
    assert f'href="/review/case/{statement_id}?unit={CLEARING}"' in application
    assert "Stellungnahme des\n    Auftraggebers" in application or (
        "Stellungnahme des" in application
    )

    statement = client.get(f"/review/case/{statement_id}?unit={CLEARING}").text
    assert "Zweite Partei" in statement
    assert f'href="/review/case/{case_id}?unit={CLEARING}"' in statement
    assert "ist</strong> die Stellungnahme" in statement


def test_the_cross_link_appears_only_once_the_answer_exists(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A waiting hearing has no second case to link, so it links nothing.

    The caseworker surface says nothing at all rather than announcing an
    absence it does not act on either - which is the same discipline the
    citizen page follows in the other direction, where the absence IS the
    point and is stated in words.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    waiting = client.get(f"/review/case/{case_id}?unit={CLEARING}").text
    assert "Zweite Partei" not in waiting
    answer(client, reference(client.get(f"/demo/case/{case_id}/pipeline").text))
    assert "Zweite Partei" in client.get(f"/review/case/{case_id}?unit={CLEARING}").text


def test_the_cross_link_changes_no_queue_and_writes_no_event(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The four rules the tour's highlight has followed since part 13.

    Both cases sit in the same queue, oldest first, in the order `build_queue`
    produced them; rendering the case view a second time appends nothing; and
    the queue page is byte-identical to what it was before the second case was
    linked to the first.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    journal = client.app.state.journal  # type: ignore[attr-defined]
    before = len(journal.read(case_id))
    statement_id = answer(
        client, reference(client.get(f"/demo/case/{case_id}/pipeline").text)
    )
    # Rendering both case views changes nothing about either journal.
    client.get(f"/review/case/{case_id}?unit={CLEARING}")
    client.get(f"/review/case/{statement_id}?unit={CLEARING}")
    assert len(journal.read(case_id)) == before

    index = build_index(journal)
    rows = build_queue(
        index, unit_id=CLEARING, now=datetime.now(UTC), config=config.queues
    ).rows
    ordered = [row.case_id for row in rows]
    assert set(ordered) == {case_id, statement_id}
    assert ordered == sorted(ordered, key=lambda item: _received(journal, item))


def _received(journal: object, case_id: str) -> datetime:
    events = journal.read(case_id)  # type: ignore[attr-defined]
    return min(event.occurred_at for event in events)


def test_with_the_flag_off_the_case_view_is_byte_identical(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The part-11 control group, applied to the one caseworker page that grew.

    Rendered through an environment where the demo include is neutralised and
    through the real one with the posture off: identical bytes, or the
    cross-link costs something on an instance that has no demo.
    """
    monkeypatch.delenv(DEMO_MODE_ENV, raising=False)
    set_demo_posture(DemoPosture())
    client = build_client(config, demo=False, monkeypatch=monkeypatch)
    created = client.post(
        "/ingest",
        json={
            "submissionId": "flag-off-0001",
            "destinationId": "drv-bund-eingang-test",
            "procedureHint": "statusfeststellung",
            "channel": "fit_connect",
            "submittedAt": "2026-08-18T09:00:00+00:00",
            "data": {
                "antragsteller": {
                    "geburtsdatum": "1957-03-19",
                    "versicherungsnummer": "42190357M724",
                },
                "antrag": {
                    "antragsart": "feststellung_nach_aufnahme",
                    "antragsteller_rolle": "auftragnehmer",
                    "taetigkeit_bezeichnung": "IT-Beratung",
                    "taetigkeit_beginn": "2026-01-08",
                },
                "auftraggeber": {"firmenname": "Seezeichen Beispielwerk GmbH"},
            },
            "attachments": [],
        },
    )
    assert created.status_code == 201
    case_id = created.json()["case_id"]
    view = review_view.build_case_view(
        client.app.state.journal,  # type: ignore[attr-defined]
        config=config,
        case_id=case_id,
        unit_id=CLEARING,
        now=BASE_TIME,
    )
    assert view is not None
    assert view.statement is None
    stripped = Environment(
        loader=ChoiceLoader(
            [DictLoader({"_demo_ribbon.html": ""}), FileSystemLoader(TEMPLATE_DIR)]
        ),
        autoescape=environment().autoescape,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    stripped.globals.update(environment().globals)
    live = environment().get_template("review_case.html").render(view=view)
    control = stripped.get_template("review_case.html").render(view=view)
    assert live == control
    assert "Zweite Partei" not in live


def test_with_the_flag_off_there_is_no_counterparty_route(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Route table AND OpenAPI document, like every other demo surface."""
    monkeypatch.delenv(DEMO_MODE_ENV, raising=False)
    app = create_app(
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
        outbox=InMemoryOutbox(),
        drafts=InMemoryDraftStore(),
    )
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/demo/gegenpartei" not in paths
    assert "/demo/gegenpartei" not in app.openapi()["paths"]
    client = TestClient(app)
    assert client.get("/demo/gegenpartei").status_code == 404
    assert client.post("/demo/gegenpartei", data={}).status_code == 404


def test_a_closed_instance_offers_no_statement_button(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The safe state is closed for everybody, the demo app included.

    ADR-029 ruling 2, applied to the second surface that holds the deployment's
    token: with no token configured the page renders and says so in words, and
    a POST is refused rather than half-executed.
    """
    monkeypatch.setenv(DEMO_MODE_ENV, "1")
    monkeypatch.delenv(INGEST_TOKEN_ENV, raising=False)
    client = TestClient(
        create_app(
            config=config,
            journal=InMemoryJournalStore(),
            vault=InMemoryVaultStore(),
            text_detector=text_seal_detector(with_ner=False),
            outbox=InMemoryOutbox(),
            drafts=InMemoryDraftStore(),
        )
    )
    store = store_of(client)
    store.request_statement(link(token="closed-token"), now=datetime.now(UTC))
    page = client.get("/demo/gegenpartei?zeichen=closed-token")
    assert page.status_code == 200
    assert phrase("intake.closed_button") in page.text
    assert "<button" not in page.text
    refused = client.post(
        "/demo/gegenpartei",
        data={"zeichen": "closed-token", **{"body": "x"}},
        follow_redirects=False,
    )
    assert refused.status_code == 200
    assert "ingest is disabled" in refused.text
    assert store.link_by_token("closed-token") is not None
    assert not store.link_by_token("closed-token").answered  # type: ignore[union-attr]


# --------------------------------------------------------------- 5. fixtures ---


def fixture_values() -> tuple[tuple[str, str], ...]:
    """Every invented identity string this module ships."""
    return (
        ("contact given name", gegenpartei.CONTACT_GIVEN_NAME),
        ("contact surname", gegenpartei.CONTACT_SURNAME),
        ("street", gegenpartei.COMPANY_STREET),
        ("postcode", gegenpartei.COMPANY_POSTCODE),
        ("town", gegenpartei.COMPANY_TOWN),
        ("betriebsnummer", gegenpartei.COMPANY_BETRIEBSNUMMER),
    )


def test_no_counterparty_value_collides_with_a_frozen_set(gold_v4_dir: Path) -> None:
    """The persona file's collision rule, over this module's own values.

    A demo string that also occurs in `corpus/pii_golden/` or `corpus/gold/`
    would make the canary sweep over these pages unable to tell a leak from a
    fixture, and it would make a recall measurement gameable by memorisation.
    """
    chunks = [
        path.read_text(encoding="utf-8")
        for path in sorted(Path("corpus/pii_golden").glob("*.yaml"))
    ]
    for directory in sorted(Path("corpus/gold").iterdir()):
        if directory.is_dir():
            chunks.extend(
                path.read_text(encoding="utf-8")
                for path in sorted(directory.glob("*.json"))
            )
    haystack = "\n".join(chunks)
    for where, value in fixture_values():
        if len(value) < 4:
            continue
        assert value not in haystack, (
            f"counterparty fixture {value!r} ({where}) also occurs in a frozen "
            "set; the canary sweep could then not tell a leak from a fixture"
        )
    assert "11040650L949" in haystack, "the pii_golden canaries must be in scope"
    assert "17170459B012" in haystack, "the gold corpus must be in scope"


def test_the_counterparty_name_is_mustermann_class() -> None:
    """Unmistakably fictional to a German reader - the rule, not a preference."""
    full = f"{gegenpartei.CONTACT_GIVEN_NAME} {gegenpartei.CONTACT_SURNAME}".lower()
    assert any(marker in full for marker in ("muster", "beispiel", "demo"))
    assert "muster" in gegenpartei.COMPANY_TOWN.lower()


def test_every_offered_option_occurs_in_the_scenario_file_it_was_read_from() -> None:
    """The copy is CHECKED rather than promised (the persona file's own method).

    The procedure config deliberately constrains none of these - they are
    Abwaegungsmaterial rather than requirements - so there is no `one_of` to
    feed them from and the values are a copy. A copy nobody checks is a copy
    that drifts, so this reads the source.
    """
    source = SCENARIOS.read_text(encoding="utf-8")
    offered = (
        *gegenpartei.JA_NEIN,
        *gegenpartei.ARBEITSORT_OPTIONS,
        *gegenpartei.HONORAR_OPTIONS,
    )
    for option in offered:
        assert option in source, option
    # And the paths those values are written to are the paths the scenarios use.
    form = gegenpartei.statement_form(link())
    for field_id in ("weisungsgebunden", "arbeitsort", "honorar_modell"):
        entry = form.field(field_id)
        assert entry is not None
        assert entry.path == f"antrag.{field_id}"
        assert f"{field_id}:" in source


def test_the_role_select_is_fed_from_the_procedure_configuration(
    config: ConfigBundle,
) -> None:
    """A vocabulary that EXISTS is read, never copied (the part-16 rule)."""
    allowed = demo_view.vocabulary(config, "antrag.antragsteller_rolle")
    assert allowed == ("auftragnehmer", "auftraggeber", "gemeinsam")
    view = demo_view.build_gegenpartei_view(
        DemoPosture(enabled=True, ingest_token=TOKEN),
        link(),
        token="zeichen-1",
        config=config,
    )
    rolle = next(
        field for field in view.fields if field.field_id == "antragsteller_rolle"
    )
    assert rolle.control == "select"
    assert rolle.choices == allowed
    assert rolle.value == "auftraggeber"


def test_the_counterparty_form_asks_no_field_a_hint_tells_a_visitor_to_delete() -> None:
    """`HINT_DELETED_FIELDS` names intake fields; none of them is on this form.

    Worth asserting rather than assuming: the two forms share the required-field
    rule (`api/demo.py::required_for`), so a counterparty field that happened to
    be called `vorname` would silently lose its `required` attribute.
    """
    form = gegenpartei.statement_form(link())
    ids = {entry.field_id for entry in form.fields}
    assert not (ids & demo_view.HINT_DELETED_FIELDS)
    view = demo_view.build_gegenpartei_view(
        DemoPosture(enabled=True, ingest_token=TOKEN), link(), token="zeichen-1"
    )
    assert all(field.required for field in view.fields)


# ------------------------------------------------------- 6. the four states ---


@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_page_says_which_of_its_states_it_is_in(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    """No reference, a live hearing, an answered one - in both languages."""
    client = build_client(config, monkeypatch=monkeypatch)
    client.get(f"/demo/gegenpartei?lang={lang}", follow_redirects=True)

    empty = client.get("/demo/gegenpartei").text
    assert shown("gegenpartei.unknown.heading", lang) in empty
    assert 'href="/demo/antrag"' in empty

    case_id = submit(client)
    token = reference(client.get(f"/demo/case/{case_id}/pipeline").text)
    live = client.get(f"/demo/gegenpartei?zeichen={token}").text
    assert shown("gegenpartei.form.heading", lang) in live
    assert shown("gegenpartei.sealed_note", lang) in live
    assert shown("gegenpartei.minimal", lang) in live
    assert shown("gegenpartei.contradiction", lang) in live
    # The LETTER stays German on both, and says so on the English page.
    assert '<div class="letter" lang="de">' in live
    assert "Deutsche Rentenversicherung Bund - Clearingstelle" in live
    assert shown("gegenpartei.letter.note", lang) in live

    answer(client, token)
    done = client.get(f"/demo/gegenpartei?zeichen={token}").text
    assert shown("gegenpartei.answered.heading", lang) in done
    assert "<form" not in done
    client.get("/demo/gegenpartei?lang=de", follow_redirects=True)


def test_the_tour_and_the_menu_both_lead_to_the_second_party(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A judge with nobody presenting has to be able to FIND the loop."""
    client = build_client(config, monkeypatch=monkeypatch)
    tour = client.get("/demo/rundgang").text
    assert phrase("tour.s3.gegenpartei") in tour
    assert 'href="/demo/gegenpartei"' in tour
    # And from the menu, on every page of the instance.
    for path in ("/", "/demo/antrag", "/demo/rundgang", "/review"):
        body = client.get(path).text
        assert f"<span>{phrase('chrome.nav.gegenpartei')}</span>" in body, path


def test_the_pipeline_page_offers_the_loop_at_the_top_and_in_place(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discoverability, asserted as an ordering rather than as a sentence.

    The section itself sits where the step belongs - after the message, before
    the hand-off to phase 3 - which is a long way down a page with seven stages
    on it. The notice at the top is what makes it findable, and the hand-off
    stays the last thing on the page.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    page = client.get(f"/demo/case/{case_id}/pipeline").text
    notice = page.index('href="#gegenpartei"')
    overview = page.index('id="a-heading"')
    section = page.index('id="gegenpartei-heading"')
    message = page.index('id="f-heading"')
    queue = page.index('id="g-heading"')
    assert notice < overview, "the notice is above the first stage"
    assert message < section < queue, "the section is between (f) and (g)"
    # It is NOT lettered, because it is not one of the machine's seven stages.
    assert "h) " not in page
    assert shown("pipeline.statement.demo_note") in page


def test_the_two_party_section_is_translated_and_the_letter_is_not(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The house rule for letters, applied to the one this part invents."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    client.get("/demo/antrag?lang=en", follow_redirects=True)
    page = client.get(f"/demo/case/{case_id}/pipeline").text
    assert phrase("pipeline.statement.heading", "en") in page
    assert phrase("pipeline.statement.heading") not in page
    assert "Deutsche Rentenversicherung Bund - Clearingstelle" in page
    assert phrase("pipeline.statement.letter.note", "en") in page
    client.get("/demo/antrag?lang=de", follow_redirects=True)


def test_every_key_this_part_added_is_a_pair() -> None:
    """The table's shape, over the families this part introduced."""
    families = ("gegenpartei.", "pipeline.statement.", "tour.s3.gegenpartei")
    keys = [key for key in TABLE if key.startswith(families)]
    assert len(keys) >= 30, keys
    for key in keys:
        german, english = TABLE[key]
        assert german.strip() and english.strip(), key
        assert german != english, key
    # Every question on the form has a label for the summary table.
    for field_id in gegenpartei.ANSWER_FIELD_IDS:
        assert f"gegenpartei.field.{field_id}" in TABLE, field_id


def test_the_answer_lines_translate_the_label_and_never_the_value() -> None:
    """A prettier rendering of `ja` would be a second vocabulary for it."""
    answered = link(
        answers=(
            StatementAnswer(field_id="weisungsgebunden", value="ja"),
            StatementAnswer(field_id="arbeitsort", value="beim_auftraggeber"),
        )
    )
    lines = demo_view.answer_lines(answered, PageContext(lang="en"))
    assert [entry.label for entry in lines] == [
        phrase("gegenpartei.field.weisungsgebunden", "en"),
        phrase("gegenpartei.field.arbeitsort", "en"),
    ]
    assert [entry.value for entry in lines] == ["ja", "beim_auftraggeber"]
    assert demo_view.answer_lines(answered)[0].label == phrase(
        "gegenpartei.field.weisungsgebunden"
    )


def test_the_section_is_none_when_there_is_nothing_to_say() -> None:
    """Most cases have no second party, and the page then has no section."""
    assert demo_view.build_statement_section(None, case_id="case-1") is None
    section = demo_view.build_statement_section(link(), case_id="case-demo-0001")
    assert section is not None
    assert section.asked and not section.answered
    assert section.answer_href == "/demo/gegenpartei?zeichen=zeichen-1"
    other = demo_view.build_statement_section(
        link(statement_case_id="case-demo-answer"), case_id="case-demo-answer"
    )
    assert other is not None
    assert not other.asked
    assert other.origin_href == "/demo/case/case-demo-0001/pipeline"
    assert other.statement_href == "/demo/case/case-demo-answer/pipeline"


def test_the_view_of_an_unknown_reference_holds_nothing() -> None:
    """Not an empty form: no form, no letter, no answers, no case id."""
    view = demo_view.build_gegenpartei_view(
        DemoPosture(enabled=True, ingest_token=TOKEN), None, token="gone"
    )
    assert not view.known
    assert not view.answered
    assert view.letter == ()
    assert view.fields == ()
    assert view.carried == ()
    assert view.answers == ()
    assert view.body == ""
    assert view.origin_href == "" and view.statement_href == ""
    assert view.phase_index == 1


def test_a_capacity_eviction_leaves_the_page_honest(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store is bounded, so a reference CAN go away while a browser waits.

    What must not happen is a page that half-renders the request it no longer
    holds. It falls back to the same "no hearing on file" page an unknown
    reference gets, because the store cannot tell those two apart.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    token = reference(client.get(f"/demo/case/{case_id}/pipeline").text)
    store = store_of(client)
    now = datetime.now(UTC)
    for index in range(DEFAULT_CAPACITY + 1):
        store.request_statement(
            link(token=f"filler-{index}", case_id=f"case-filler-{index}"), now=now
        )
    page = client.get(f"/demo/gegenpartei?zeichen={token}")
    assert page.status_code == 200
    assert phrase("gegenpartei.unknown.heading") in page.text


def test_the_form_a_visitor_edits_is_the_submission_that_arrives(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is injected server-side out of the store.

    The counterparty page RENDERS the carried values into controls and the
    visitor's own browser posts them back, so what reaches ingest is a posted
    form exactly like the intake's. Rewriting the company name proves it: the
    working copy of the statement is sealed from what was POSTED, not from what
    was stored.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    token = reference(client.get(f"/demo/case/{case_id}/pipeline").text)
    statement_id = answer(client, token, firmenname="Musterhafener Werftbetrieb GmbH")
    page = client.get(f"/demo/case/{statement_id}/pipeline").text
    # The echo - "you typed this" - carries the rewritten name, because that is
    # what was posted and what the boundary sealed.
    assert "Musterhafener Werftbetrieb GmbH" in page
    echo = re.search(r"<caption>[^<]*</caption>(.*?)</table>", page, re.S)
    assert echo is not None
    assert "Musterhafener Werftbetrieb GmbH" in echo.group(1)
    assert "Seezeichen Beispielwerk GmbH" not in echo.group(1)
    # ... and the stored link still says what the APPLICANT wrote, because that
    # is what the letter was about.
    held = store_of(client).link_for_case(case_id)
    assert held is not None
    assert held.auftraggeber == "Seezeichen Beispielwerk GmbH"


def test_the_statement_form_is_grouped_the_way_the_module_declares(
    config: ConfigBundle,
) -> None:
    """Three fieldsets over one field list, with the name and address as rows."""
    view = demo_view.build_gegenpartei_view(
        DemoPosture(enabled=True, ingest_token=TOKEN),
        link(),
        token="zeichen-1",
        config=config,
    )
    assert [len(row) for row in view.party_rows] == [2, 1, 1, 4]
    assert [len(row) for row in view.question_rows] == [1] * len(
        gegenpartei.ANSWER_FIELD_IDS
    )
    assert [field.field_id for field in view.carried] == list(
        gegenpartei.CARRIED_FIELD_IDS
    )
    assert view.body.startswith("Stellungnahme des Auftraggebers")


def test_the_required_note_is_this_forms_own(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A defect the part-19 browser walk found, pinned so it cannot come back.

    The intake's note names the three fields its HINTS tell a visitor to delete
    - Versicherungsnummer, Auftraggeber, Vorname. This form has no hints and
    none of those three fields, so borrowing that sentence would explain an
    exemption that does not exist on the page it is printed on.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    token = reference(client.get(f"/demo/case/{case_id}/pipeline").text)
    body = client.get(f"/demo/gegenpartei?zeichen={token}").text
    assert shown("gegenpartei.required.note") in body
    assert shown("intake.required.note") not in body
    # ... and the intake keeps its own, which is the half that must not move.
    assert shown("intake.required.note") in client.get("/demo/antrag").text


def test_the_select_sentence_is_said_once_and_not_under_every_control(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second defect the same walk found.

    "Die Auswahl kommt aus der Verfahrenskonfiguration" is worth reading once.
    The counterparty form asks six questions in a column and printed it under
    every one of them, which stops being an explanation and becomes wallpaper.
    The fieldset says where the values come from once, above the six; and the
    ONE select whose vocabulary really is a procedure requirement says so in its
    own, more specific words (V0027 Ziffer 9.1) rather than in the generic ones.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    token = reference(client.get(f"/demo/case/{case_id}/pipeline").text)
    body = client.get(f"/demo/gegenpartei?zeichen={token}").text
    assert shown("intake.select.hint") not in body
    assert shown("gegenpartei.questions.note") in body
    assert "V0027, Ziffer 9.1" in body
    # The intake page, which has three selects spread over a long form, is
    # untouched: the hint still rides every one of them there.
    intake = client.get("/demo/antrag").text
    assert intake.count(shown("intake.select.hint")) == 3


def test_a_timestamp_read_in_a_sentence_is_trimmed_to_the_second(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prose gets a time; a definition list keeps the machine's precision."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client)
    token = reference(client.get(f"/demo/case/{case_id}/pipeline").text)
    statement_id = answer(client, token)
    held = store_of(client).link_for_case(case_id)
    assert held is not None and held.answered_at is not None
    exact = held.answered_at.isoformat()
    trimmed = held.answered_at.replace(microsecond=0).isoformat()
    assert exact != trimmed, "the clock has microseconds, or this proves nothing"

    applicant = client.get(f"/demo/case/{case_id}/pipeline").text
    assert trimmed in applicant and exact not in applicant
    surface = client.get(f"/demo/gegenpartei?zeichen={token}").text
    assert trimmed in surface and exact not in surface
    # The caseworker page lists it as data and keeps every digit.
    review = client.get(f"/review/case/{statement_id}?unit={CLEARING}").text
    assert exact in review


def test_a_submitted_form_survives_a_refusal_re_render(
    config: ConfigBundle,
) -> None:
    """What the visitor typed comes back, exactly as the intake page does it."""
    typed: Mapping[str, str] = {
        "ansprechpartner_vorname": "Mona",
        "weisungsgebunden": "nein",
    }
    view = demo_view.build_gegenpartei_view(
        DemoPosture(enabled=True, ingest_token=TOKEN),
        link(),
        token="zeichen-1",
        values=typed,
        body="Eigener Text.",
        error_key="intake.refused.redaction",
        error_details=("NAME at antragsteller.name",),
        config=config,
    )
    values = {field.field_id: field.value for field in view.fields}
    assert values["ansprechpartner_vorname"] == "Mona"
    assert values["weisungsgebunden"] == "nein"
    assert view.body == "Eigener Text."
    assert view.error_key == "intake.refused.redaction"
    assert view.error_details == ("NAME at antragsteller.name",)
