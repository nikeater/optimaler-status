"""The three-phase guided showcase, end to end, and the promises around it.

Six groups, and the first two are the ones that would be prose in a worse
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
6. **Accessibility and reflow** for the two new citizen-facing pages, plus the
   two lines that must stay true forever: the queue is never reordered and the
   inbox never grows a control.

Every client here injects the DETERMINISTIC detector union. That is the part-10
precedent (``tests/test_review_no_person.py``) and it is also the shipped demo's
posture: the container installs no ``[redact]`` extra (ADR-027 ruling 8), so a
test that ran with the optional model would be testing a configuration the demo
never runs. See KE-6 for what the model member does to a demo letter.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jinja2 import Environment

from api import demo as demo_view
from api import review as review_view
from api.app import REFUSED_ENVELOPE, REFUSED_REDACTION, create_app
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
from engine.redact import InMemoryVaultStore, text_seal_detector
from engine.redact.placeholders import PLACEHOLDER_RE

TOKEN = "demo-journey-token"
BASE_TIME = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)

#: The arcs, as the showcase promises them: which queue the visitor is handed
#: to, and what phase 3 has waiting there.
ARCS = {
    "mustermann_regelaltersrente": ("Referat_312_Renten", "Tier 1"),
    "beispielmann_ohne_rentenbeginn": ("Referat_312_Renten", "Tier 2"),
    "musterfrau_statusfeststellung": ("Referat_340_Clearingstelle", "Tier 3"),
    "musterkind_rentenbeginn_2048": ("Referat_312_Renten", "Tier 1"),
}


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
    """The part-11 control group: the demo include neutralised to nothing."""
    from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

    return Environment(
        loader=ChoiceLoader(
            [DictLoader({"_demo_banner.html": ""}), FileSystemLoader(TEMPLATE_DIR)]
        ),
        autoescape=environment().autoescape,
        trim_blocks=True,
        lstrip_blocks=True,
    )


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
    case_id = submit(client, form_data("mustermann_regelaltersrente"))
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
    assert demo_view.CLOSED_NOTE in page.text
    assert "Antrag absenden" not in page.text
    refused = client.post(
        "/demo/antrag",
        data=form_data("mustermann_regelaltersrente"),
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
    assert "Was Sie ausprobieren koennen" in intake.text
    assert 'id="demo-banner"' in intake.text
    assert "Phase 1: Antrag" in intake.text

    case_id = submit(client, form_data(persona_id))

    # Phase 2: seven stages, in order, over this case.
    page = client.get(f"/demo/case/{case_id}/pipeline")
    assert page.status_code == 200
    headings = re.findall(r'id="([a-g])-heading"', page.text)
    assert headings == ["a", "b", "c", "d", "e", "f", "g"]
    assert case_id in page.text
    assert demo_view.SEAL_SENTENCE in page.text
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


def test_the_letter_tab_goes_through_the_same_pipeline(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One pipeline, two tabs. The e-mail adapter is simulated and says so."""
    client = build_client(config, monkeypatch=monkeypatch)
    page = client.get("/demo/antrag?persona=musterfrau_statusfeststellung&kanal=email")
    assert "simulierter Adapter" in page.text
    assert "SIMULIERTER Adapter" in page.text
    assert "P-14" in page.text
    assert "<textarea" in page.text

    case_id = submit(
        client, form_data("musterfrau_statusfeststellung", channel=CHANNEL_EMAIL)
    )
    pipeline = client.get(f"/demo/case/{case_id}/pipeline")
    assert pipeline.status_code == 200
    # The prose letter routes to the Clearingstelle, derived from CONTENT.
    assert "Referat_340_Clearingstelle" in pipeline.text
    assert "Ihr Anschreiben, vorher und nachher" in pipeline.text
    # And the honest sentence about why nothing was extracted (ADR-028).
    assert demo_view.NO_EXTRACTION_NOTE in pipeline.text


def test_a_tampered_submission_fires_the_gap_and_the_flag(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hints panel promises real behaviour; this is the behaviour."""
    client = build_client(config, monkeypatch=monkeypatch)

    # Delete the Versicherungsnummer: a gap, a Nachforderung sentence, tier 2.
    gap_case = submit(
        client, form_data("mustermann_regelaltersrente", versicherungsnummer="")
    )
    gap_page = client.get(f"/demo/case/{gap_case}/pipeline").text
    assert "Tier 2" in gap_page
    assert "versicherungsnummer" in gap_page
    assert "Sozialversicherungsausweis" in gap_page

    # Push the Rentenbeginn out: the shadow scorer flags it and moves nothing.
    flag_case = submit(
        client, form_data("mustermann_regelaltersrente", rentenbeginn="2048-01-01")
    )
    flag_page = client.get(f"/demo/case/{flag_case}/pipeline").text
    assert "Merkmal leitdatum_abstand_jahre" in flag_page
    assert demo_view.LOG_ONLY_NOTE in flag_page

    # Auslandsbezug: priority 10 wins and the case changes unit.
    abroad_case = submit(
        client, form_data("mustermann_regelaltersrente", auslandsbezug="ja")
    )
    abroad_page = client.get(f"/demo/case/{abroad_case}/pipeline").text
    assert "Referat_318_Auslandsrenten" in abroad_page
    assert "rule_auslandsbezug" in abroad_page


def test_a_refused_submission_renders_the_refusal_on_the_page(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boundary refusing its own output is a real behaviour worth showing.

    Forged placeholder syntax is the reliable way to produce one without an
    optional model: the sweep treats placeholder-SHAPED text that is not a
    valid placeholder as residue, because something is imitating the reserved
    syntax (ADR-019, ruling 4).
    """
    client = build_client(config, monkeypatch=monkeypatch)
    refused = client.post(
        "/demo/antrag",
        data=form_data(
            "mustermann_regelaltersrente",
            channel=CHANNEL_EMAIL,
            body="[[PII|VSNR|nope]]",
        ),
        follow_redirects=False,
    )
    assert refused.status_code == 200
    assert REFUSED_REDACTION in refused.text
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
    """
    client = build_client(config, monkeypatch=monkeypatch)
    canary = "KANARIENVOGEL-4711"
    refused = client.post(
        "/demo/antrag",
        data=form_data(
            "mustermann_regelaltersrente",
            channel=CHANNEL_EMAIL,
            body=f"[[PII|VSNR|nope]] {canary}",
        ),
        follow_redirects=False,
    )
    assert refused.status_code == 200
    assert REFUSED_REDACTION in refused.text
    block = refused.text.split('id="refusal"')[1].split("</div>")[0]
    assert canary not in block
    assert "nope" not in block
    # The envelope refusal has its own wording, and it is not this one.
    assert REFUSED_ENVELOPE not in refused.text


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


def test_an_unknown_persona_or_channel_falls_back_instead_of_failing(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale bookmark shows the picker, never a stack trace."""
    client = build_client(config, monkeypatch=monkeypatch)
    page = client.get("/demo/antrag?persona=ghost&kanal=telepathie")
    assert page.status_code == 200
    assert demo_personas().first.display_name in page.text
    assert "Formular (FIT-Connect)" in page.text
    assert demo_view.resolve_channel("telepathie") == CHANNEL_FORM
    assert demo_view.resolve_channel(None) == CHANNEL_FORM
    assert demo_view.resolve_channel(CHANNEL_EMAIL) == CHANNEL_EMAIL


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
    entry = held_submission(config, "mustermann_regelaltersrente")
    text = "\n".join(part.text for part in entry.working_copy)
    assert PLACEHOLDER_RE.search(text) is not None
    for value in identity_strings("mustermann_regelaltersrente"):
        assert value not in text, value
    # The echo is the visitor's own input and is deliberately NOT placeholders.
    assert any(value.value == "Renate Mustermann" for value in entry.echo)
    # The four address inputs became ONE entry, because sealing groups them.
    address = [value for value in entry.echo if value.kind == "ADDR"]
    assert len(address) == 1
    assert address[0].value == "Lotsenweg 7 21029 Musterhafen"


def test_the_store_expires_by_ttl_and_forgets_completely(
    config: ConfigBundle,
) -> None:
    store = DemoStore(ttl=timedelta(minutes=5))
    entry = held_submission(config, "mustermann_regelaltersrente")
    store.put(entry, now=BASE_TIME)
    assert store.get(entry.case_id, now=BASE_TIME + timedelta(minutes=4)) is entry
    assert store.get(entry.case_id, now=BASE_TIME + timedelta(minutes=5)) is None
    assert len(store) == 0


def test_the_store_evicts_the_oldest_beyond_its_capacity(
    config: ConfigBundle,
) -> None:
    """A demo nobody stops must not become the memory profile of the process."""
    store = DemoStore(capacity=3)
    base = held_submission(config, "mustermann_regelaltersrente")
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
    entry = held_submission(config, "mustermann_regelaltersrente")
    store.put(entry, now=BASE_TIME)
    assert len(store) == 1
    store.reset()
    assert len(store) == 0
    assert store.get(entry.case_id, now=BASE_TIME) is None


def test_the_store_clips_what_it_holds(config: ConfigBundle) -> None:
    """A per-entry size cap, so one large paste cannot become the process."""
    chosen = persona("mustermann_regelaltersrente")
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
    case_id = submit(client, form_data("mustermann_regelaltersrente"))
    store = client.app.state.demo_store  # type: ignore[attr-defined]
    assert isinstance(store, DemoStore)
    store.reset()
    page = client.get(f"/demo/case/{case_id}/pipeline")
    assert page.status_code == 200
    assert demo_view.EXPIRED_NOTE in page.text
    assert "Von Ihnen eingegeben" not in page.text
    # Everything the journal holds is still there.
    assert "Referat_312_Renten" in page.text
    assert "Tier 1" in page.text


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
    first = submit(client, form_data("mustermann_regelaltersrente"))
    second = submit(client, form_data("musterkind_rentenbeginn_2048"))
    first_values = identity_strings("mustermann_regelaltersrente")

    for path in (
        f"/demo/case/{second}/pipeline",
        "/review",
        "/review/queue/Referat_312_Renten",
        f"/review/queue/Referat_312_Renten?highlight={first}",
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
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(
        client, form_data("musterfrau_statusfeststellung", channel=CHANNEL_EMAIL)
    )
    case_page = client.get(f"/review/case/{case_id}?unit=Referat_340_Clearingstelle")
    assert case_page.status_code == 200
    # Sentences that exist ONLY in the letter this visitor wrote. The prepared
    # Nachforderung on the same page has its own greeting from config, which is
    # why the assertion is over the submission's wording and not over a
    # salutation any German letter carries.
    for sentence in (
        "Bitte um Klaerung meines Erwerbsstatus",
        "seit Anfang des Jahres arbeite ich als freie Beraterin",
    ):
        assert sentence not in case_page.text, sentence
        # And the demo page does show it, which is the difference part 13 makes.
        assert sentence in client.get(f"/demo/case/{case_id}/pipeline").text


def test_the_demo_pages_carry_the_synthetic_data_banner(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every page. The one that is missed is the one somebody screenshots."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("mustermann_regelaltersrente"))
    for path in ("/demo/antrag", f"/demo/case/{case_id}/pipeline"):
        body = client.get(path).text
        assert 'id="demo-banner"' in body, path
        assert "SYNTHETISCHEN Daten" in body, path
    assert demo_personas().note in client.get("/demo/antrag").text


# ------------------------------- 6. the two lines, accessibility and reflow ---


def test_the_highlight_never_reorders_or_hides_anything(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Display only, asserted on the ROWS rather than on the sentence."""
    client = build_client(config, monkeypatch=monkeypatch)
    ids = [
        submit(client, form_data("mustermann_regelaltersrente", versicherungsnummer=""))
        for _ in range(3)
    ]
    plain = client.get("/review/queue/Referat_312_Renten").text
    marked = client.get(f"/review/queue/Referat_312_Renten?highlight={ids[0]}").text
    assert _rows(plain) == _rows(marked)
    assert len(_rows(plain)) == 3
    assert "Ihr Vorgang" in marked
    assert "Ihr Vorgang" not in plain


def test_a_highlight_for_a_case_not_in_this_queue_says_so(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a confirmation the row is gone, and the page is honest about it."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("mustermann_regelaltersrente"))
    client.post(
        f"/review/case/{case_id}/confirm",
        data={"unit": "Referat_312_Renten"},
        follow_redirects=False,
    )
    page = client.get(f"/review/queue/Referat_312_Renten?highlight={case_id}").text
    assert "steht nicht (mehr) in dieser Warteschlange" in page
    assert 'href="/inbox"' in page


def test_the_demo_adds_no_control_to_the_inbox(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The part-07 line, checked from the part-13 side (ADR-005)."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("mustermann_regelaltersrente"))
    inbox = client.get("/inbox").text
    assert "<form" not in inbox
    assert "<button" not in inbox
    for path in ("/demo/antrag", f"/demo/case/{case_id}/pipeline"):
        body = client.get(path).text
        assert 'action="/inbox' not in body
        assert "/inbox" in body, "the inbox must be LINKED"
    assert client.post("/inbox", data={}).status_code in (404, 405)


@pytest.mark.parametrize("phase", ["antrag", "pipeline"])
def test_the_new_pages_meet_the_mechanical_accessibility_bar(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """The same criteria part 10's suite asserts, on the two citizen pages."""
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("mustermann_regelaltersrente"))
    body = client.get(
        "/demo/antrag" if phase == "antrag" else f"/demo/case/{case_id}/pipeline"
    ).text

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

    It is demo-only like the two tour pages, so it is in the same class: it
    links into phase 1 and it loads the stylesheet that carries the reflow
    rules. The part-11 content is untouched.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    page = client.get("/").text
    assert 'href="/demo/antrag"' in page
    assert 'href="/static/demo.css"' in page
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in page
    # Part 11's promises are still on it.
    assert "Einwegventil" in page
    assert review_view.PICKER_NOTE in page


@pytest.mark.parametrize("phase", ["antrag", "pipeline"])
def test_the_new_pages_are_built_to_reflow_at_320_css_pixels(
    config: ConfigBundle, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """1.4.10, which the caseworker UI still has open and these pages do not.

    A static check cannot measure a viewport, so it checks the three things
    that make reflow possible and whose absence makes it impossible: the
    viewport meta, every wide table inside its own scroll container, and no
    fixed pixel width anywhere in the stylesheet these pages add.
    """
    client = build_client(config, monkeypatch=monkeypatch)
    case_id = submit(client, form_data("mustermann_regelaltersrente"))
    body = client.get(
        "/demo/antrag" if phase == "antrag" else f"/demo/case/{case_id}/pipeline"
    ).text
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in body
    assert body.count("<table") == body.count('<div class="scroll-x">')
    assert "width:" not in body and "style=" not in body

    css = Path("ui/static/demo.css").read_text(encoding="utf-8")
    assert "@media (max-width: 40rem)" in css
    assert "overflow-x: auto" in css
    assert not re.search(r":\s*\d{3,}px", css), "no fixed pixel width"
    assert "outline: none" not in css and "outline: 0" not in css


def _rows(page: str) -> list[str]:
    """The case ids in a queue table, in the order they are rendered."""
    return re.findall(r'href="/review/case/([^"?]+)', page)
