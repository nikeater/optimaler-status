"""The public-demo posture, and the promise that it is invisible when off.

Two halves, and the first one matters more than the second. With
``EINGANGSLOTSE_DEMO_MODE`` unset, this part must be undetectable: the route
table is the part-10 route table, ``GET /`` is still a 404, ``POST /ingest``
still ingests, and every page renders BYTE for byte what it rendered before the
demo include existed. That last one is checked against the same templates
loaded through an environment where ``_demo_banner.html`` is the empty string -
so "the include adds nothing" is measured rather than eyeballed.

With the flag on: ingest closes (entirely, or behind a token), every rendered
page carries the synthetic-data banner, ``GET /`` becomes the landing page, and
the review actions deliberately keep working, because they are the product and
the reset makes them harmless.

The seed is tested for the property the reset rests on: two seedings of the
same corpus with the same base clock produce the same state, and neither of
them writes a byte under ``corpus/gold/``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from api import landing as landing_view
from api import review as review_view
from api.app import create_app
from api.inbox import build_view as build_inbox_view
from api.metrics import TEMPLATE_DIR, MetricsView, environment, set_demo_posture
from engine.config_loader import ConfigBundle
from engine.demo import (
    BANNER_CLOSED,
    BANNER_TOKEN_GATED,
    DEMO_MODE_ENV,
    INGEST_CLOSED_DETAIL,
    INGEST_HEADER,
    INGEST_TOKEN_DETAIL,
    INGEST_TOKEN_ENV,
    REPO_URL_ENV,
    REPO_URL_PLACEHOLDER,
    DemoPosture,
    demo_posture,
)
from engine.demo.seed import StatePaths, reset_state, seed_state, state_digest
from engine.demo.seed import main as seed_main
from engine.draft import JsonlDraftStore
from engine.journal.store import JsonlJournalStore
from engine.notify import JsonlOutbox
from engine.redact import JsonlVaultStore

TOKEN = "demo-token-for-tests"
REVIEW_UNIT = "Referat_312_Renten"
BASE_TIME = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def submission(submission_id: str = "demo-0001") -> dict[str, Any]:
    """A plain Altersrente submission: routes, tier 2, owes a Nachforderung."""
    return {
        "submissionId": submission_id,
        "destinationId": "drv-bund-eingang-test",
        "procedureHint": "altersrente",
        "channel": "fit_connect",
        "submittedAt": "2026-08-11T09:00:00+00:00",
        "data": {
            "antragsteller": {
                "geburtsdatum": "1959-04-12",
                "versicherungsnummer": "12120459M012",
                "name": "Dora Demo",
                "anschrift": {
                    "strasse": "Demoweg",
                    "hausnummer": "3",
                    "plz": "10115",
                    "ort": "Berlin",
                },
            },
            "antrag": {"rentenart": "regelaltersrente", "auslandsbezug": "nein"},
        },
        "attachments": [],
    }


@pytest.fixture(autouse=True)
def restore_posture() -> Iterator[None]:
    """Leave the process the way it was found.

    ``create_app`` writes the posture into the process-wide Jinja environment,
    so a demo test that did not clean up would banner the pages of every test
    that ran after it.
    """
    yield
    demo_posture.cache_clear()
    set_demo_posture(DemoPosture())


def build_client(config: ConfigBundle, tmp_path: Path) -> TestClient:
    """The real app on file-backed stores under ``tmp_path``."""
    return TestClient(
        create_app(
            config=config,
            journal=JsonlJournalStore(tmp_path / "journal"),
            vault=JsonlVaultStore(tmp_path / "vault"),
            outbox=JsonlOutbox(tmp_path / "outbox"),
            drafts=JsonlDraftStore(tmp_path / "drafts"),
        )
    )


# ------------------------------------------------- the flag-off identity ---


def stripped_environment() -> Environment:
    """The same templates, with the demo include neutralised to nothing.

    This is the control group. Rendering a page through it and through the real
    environment with the posture off must produce identical bytes; if it does
    not, the demo mechanism costs something even when switched off, and the
    ruling that "nothing observable changes" is false.
    """
    return Environment(
        loader=ChoiceLoader(
            [
                DictLoader({"_demo_banner.html": ""}),
                FileSystemLoader(TEMPLATE_DIR),
            ]
        ),
        autoescape=environment().autoescape,
        trim_blocks=True,
        lstrip_blocks=True,
    )


@pytest.mark.parametrize(
    "template",
    ["metrics.html", "inbox.html", "review_overview.html"],
)
def test_with_the_flag_off_every_page_renders_byte_identically(
    config: ConfigBundle, tmp_path: Path, template: str
) -> None:
    """The demo include adds exactly zero bytes when the posture is off."""
    set_demo_posture(DemoPosture())
    views: dict[str, object] = {
        "metrics.html": MetricsView(available=False, report_path="/nowhere"),
        "inbox.html": build_inbox_view(JsonlOutbox(tmp_path / "outbox")),
        "review_overview.html": review_view.build_overview(
            JsonlJournalStore(tmp_path / "journal"),
            config=config,
            unit_id=REVIEW_UNIT,
            now=BASE_TIME,
        ),
    }
    view = views[template]
    live = environment().get_template(template).render(view=view)
    control = stripped_environment().get_template(template).render(view=view)
    assert live == control


def test_with_the_flag_off_the_route_table_has_no_landing_page(
    config: ConfigBundle, tmp_path: Path
) -> None:
    """``GET /`` is the 404 it has been since part 01.

    Checked on the route table and not only on the status code: "nothing
    observable changes" has to hold for the OpenAPI document too, which is the
    thing an integrator reads.
    """
    app = create_app(
        config=config,
        journal=JsonlJournalStore(tmp_path / "journal"),
        vault=JsonlVaultStore(tmp_path / "vault"),
        outbox=JsonlOutbox(tmp_path / "outbox"),
        drafts=JsonlDraftStore(tmp_path / "drafts"),
    )
    assert TestClient(app).get("/").status_code == 404
    assert "/" not in {getattr(route, "path", "") for route in app.routes}


def test_with_the_flag_off_ingest_still_ingests(
    config: ConfigBundle, tmp_path: Path
) -> None:
    """The gate is a demo posture and nothing else: no flag, no gate."""
    client = build_client(config, tmp_path)
    created = client.post("/ingest", json=submission())
    assert created.status_code == 201
    assert created.json()["tier"] == 2


def test_with_the_flag_off_no_page_carries_a_demo_marker(
    config: ConfigBundle, tmp_path: Path
) -> None:
    """Belt and braces over the byte-identity check, at the HTTP level."""
    client = build_client(config, tmp_path)
    case_id = client.post("/ingest", json=submission()).json()["case_id"]
    for path in (
        "/review",
        f"/review/queue/{REVIEW_UNIT}",
        f"/review/case/{case_id}",
        "/metrics",
        "/inbox",
    ):
        body = client.get(path).text
        assert "demo-banner" not in body, path
        assert "Demo-Instanz" not in body, path


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "on", "2", "11"])
def test_only_the_exact_string_one_arms_the_posture(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A near-miss in a deployment's environment must not half-open the demo."""
    monkeypatch.setenv(DEMO_MODE_ENV, value)
    assert DemoPosture.from_env().enabled is False


def test_a_padded_one_still_arms_the_posture() -> None:
    """Whitespace from a YAML env block is not a decision the operator made."""
    assert DemoPosture.from_env({DEMO_MODE_ENV: " 1 "}).enabled is True


# ------------------------------------------------------------ the ingest ---


def test_demo_mode_without_a_token_disables_ingest_entirely(
    config: ConfigBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No token configured is the safe state, and the body says so."""
    monkeypatch.setenv(DEMO_MODE_ENV, "1")
    monkeypatch.delenv(INGEST_TOKEN_ENV, raising=False)
    client = build_client(config, tmp_path)
    refused = client.post("/ingest", json=submission())
    assert refused.status_code == 403
    assert refused.json()["detail"] == INGEST_CLOSED_DETAIL
    # A token cannot be guessed into existence either.
    assert (
        client.post(
            "/ingest", json=submission(), headers={INGEST_HEADER: "anything"}
        ).status_code
        == 403
    )
    assert JsonlJournalStore(tmp_path / "journal").case_ids() == []


def test_a_refused_submission_is_never_read(
    config: ConfigBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """403 comes from middleware, so the body is never read or decoded.

    The parametrisation is not padding. A dependency-based gate passes the
    first three of these and FAILS the fourth: a malformed body with a JSON
    content type is decoded by FastAPI before any dependency runs, so the
    refusal arrives as a 422 - after this process has read a stranger's
    submission. A demo whose refusal path reads submissions is not a closed
    demo, and this case is the one that proves which of the two was built.
    """
    monkeypatch.setenv(DEMO_MODE_ENV, "1")
    monkeypatch.delenv(INGEST_TOKEN_ENV, raising=False)
    client = build_client(config, tmp_path)
    cases: tuple[tuple[bytes, dict[str, str]], ...] = (
        (b"not json at all", {}),
        (b"[1, 2, 3]", {}),
        (b"", {}),
        (b"{ this is not json", {"Content-Type": "application/json"}),
        (b"<xml/>", {"Content-Type": "application/xml"}),
    )
    for body, headers in cases:
        response = client.post("/ingest", content=body, headers=headers)
        assert response.status_code == 403, (body, headers)
        assert response.json()["detail"] == INGEST_CLOSED_DETAIL


def test_demo_mode_with_a_token_gates_ingest(
    config: ConfigBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token is the whole authorization, and it must match exactly."""
    monkeypatch.setenv(DEMO_MODE_ENV, "1")
    monkeypatch.setenv(INGEST_TOKEN_ENV, TOKEN)
    client = build_client(config, tmp_path)
    assert client.post("/ingest", json=submission()).status_code == 403
    assert (
        client.post("/ingest", json=submission()).json()["detail"]
        == INGEST_TOKEN_DETAIL
    )
    for wrong in (TOKEN[:-1], TOKEN + "x", TOKEN.upper(), ""):
        assert (
            client.post(
                "/ingest", json=submission(), headers={INGEST_HEADER: wrong}
            ).status_code
            == 403
        ), wrong
    accepted = client.post("/ingest", json=submission(), headers={INGEST_HEADER: TOKEN})
    assert accepted.status_code == 201


def test_the_posture_object_answers_the_same_question_as_the_route() -> None:
    """The unit-level truth table behind the two tests above."""
    off = DemoPosture()
    assert off.check_ingest(None).allowed is True
    assert off.ingest_open is True
    assert off.banner == ""

    closed = DemoPosture(enabled=True)
    assert closed.check_ingest(None).allowed is False
    assert closed.check_ingest("anything").allowed is False
    assert closed.ingest_open is False
    assert closed.banner == BANNER_CLOSED

    gated = DemoPosture(enabled=True, ingest_token=TOKEN)
    assert gated.check_ingest(TOKEN).allowed is True
    assert gated.check_ingest(None).allowed is False
    assert gated.ingest_open is True
    assert gated.banner == BANNER_TOKEN_GATED


# ------------------------------------------------------------ the banner ---


def test_the_banner_is_on_every_rendered_page(
    config: ConfigBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every page. The one that is missed is the one somebody screenshots."""
    monkeypatch.setenv(DEMO_MODE_ENV, "1")
    monkeypatch.setenv(INGEST_TOKEN_ENV, TOKEN)
    client = build_client(config, tmp_path)
    case_id = client.post(
        "/ingest", json=submission(), headers={INGEST_HEADER: TOKEN}
    ).json()["case_id"]
    for path in (
        "/",
        "/review",
        f"/review/queue/{REVIEW_UNIT}",
        "/review/queue/__clearing__",
        f"/review/case/{case_id}",
        f"/review/case/{case_id}?unit={REVIEW_UNIT}",
        "/metrics",
        "/inbox",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert 'id="demo-banner"' in response.text, path
        assert "SYNTHETISCHEN Daten" in response.text, path
        assert "all data is synthetic" in response.text, path


def test_the_banner_states_the_ingest_posture_it_actually_has(
    config: ConfigBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A banner that said "no submissions" on a token-gated box would be false."""
    monkeypatch.setenv(DEMO_MODE_ENV, "1")
    monkeypatch.delenv(INGEST_TOKEN_ENV, raising=False)
    closed = build_client(config, tmp_path).get("/review").text
    assert "Der Eingang ist gesperrt" in closed

    monkeypatch.setenv(INGEST_TOKEN_ENV, TOKEN)
    gated = build_client(config, tmp_path / "second").get("/review").text
    assert "nur mit Token erreichbar" in gated


# ------------------------------------------------------ the landing page ---


def test_the_landing_page_introduces_the_product_and_the_instance(
    config: ConfigBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What it is, the two-plane guarantee, no model text, and the links."""
    monkeypatch.setenv(DEMO_MODE_ENV, "1")
    monkeypatch.setenv(REPO_URL_ENV, "https://example.invalid/repo")
    page = build_client(config, tmp_path).get("/")
    assert page.status_code == 200
    assert 'lang="de"' in page.text
    assert "Einwegventil" in page.text
    assert "Kein Modelltext" in page.text
    for href in ('href="/review"', 'href="/metrics"', 'href="/inbox"'):
        assert href in page.text, href
    assert "https://example.invalid/repo" in page.text
    # C-5: the demo role model is stated here too, not only inside the UI.
    assert review_view.PICKER_NOTE in page.text
    assert "EUPL-1.2" in page.text


def test_the_landing_page_falls_back_to_the_repo_placeholder() -> None:
    """An unset repo URL must render a visible placeholder, not an empty link."""
    view = landing_view.build_view(DemoPosture(enabled=True), gold_dir="corpus/gold/v4")
    assert view.repo_url == REPO_URL_PLACEHOLDER
    assert landing_view.INGEST_CLOSED_NOTE in landing_view.render_page(view)
    gated = landing_view.build_view(
        DemoPosture(enabled=True, ingest_token=TOKEN), gold_dir="corpus/gold/v4"
    )
    assert landing_view.INGEST_TOKEN_NOTE in landing_view.render_page(gated)


def test_healthz_is_a_constant(config: ConfigBundle, tmp_path: Path) -> None:
    """The container healthcheck answers without touching config or a store."""
    response = build_client(config, tmp_path).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ------------------------------- the actions that stay open in demo mode ---


def test_the_review_actions_stay_enabled_in_demo_mode(
    config: ConfigBundle, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirm, override and escalate are the product; the reset is the safety."""
    monkeypatch.setenv(DEMO_MODE_ENV, "1")
    monkeypatch.setenv(INGEST_TOKEN_ENV, TOKEN)
    client = build_client(config, tmp_path)
    case_id = client.post(
        "/ingest", json=submission(), headers={INGEST_HEADER: TOKEN}
    ).json()["case_id"]

    assert (
        client.post(
            f"/review/case/{case_id}/override",
            data={
                "unit": REVIEW_UNIT,
                "field": "unit",
                "to": "Referat_318_Auslandsrenten",
                "reason": "Auslandsbezug nachgereicht",
            },
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert (
        client.post(
            f"/review/case/{case_id}/confirm",
            data={"unit": "Referat_318_Auslandsrenten"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    events = JsonlJournalStore(tmp_path / "journal").read(case_id)
    kinds = [event.type.value for event in events]
    assert "overridden" in kinds
    assert "confirmed" in kinds


# --------------------------------------------------- the seed and the reset ---


def test_reset_is_idempotent_on_an_empty_and_on_a_full_state(
    tmp_path: Path,
) -> None:
    """Wiping twice leaves the same five empty directories."""
    paths = StatePaths.under(tmp_path / "state")
    reset_state(paths)
    assert all(directory.is_dir() for directory in paths.all())
    (paths.journal / "leftover.jsonl").write_text("{}\n", encoding="utf-8")
    reset_state(paths)
    assert [sorted(directory.iterdir()) for directory in paths.all()] == [[]] * 5


def test_two_seedings_produce_the_same_state(
    config: ConfigBundle, tmp_path: Path, gold_v4_dir: Path
) -> None:
    """The property the reset rests on, over the whole frozen corpus.

    Digested with the minted ids removed - see ``engine/demo/seed.py`` - because
    a journal event id is a uuid4 and always will be. Everything a reader of the
    demo can see is inside the digest: the cases, the events in order, their
    payloads, the clock, the notifications and the letters.
    """
    first = StatePaths.under(tmp_path / "first")
    second = StatePaths.under(tmp_path / "second")
    summary = seed_state(first, gold_dir=gold_v4_dir, config=config, now=BASE_TIME)
    seed_state(second, gold_dir=gold_v4_dir, config=config, now=BASE_TIME)
    assert state_digest(first) == state_digest(second)
    # And the same numbers the eval measures on the same corpus.
    assert (summary.items, summary.cases) == (101, 101)
    assert summary.notifications == 197
    assert (summary.drafts, summary.unresolved_tokens) == (60, 0)


@pytest.fixture
def small_gold_dir(tmp_path: Path, gold_v4_dir: Path) -> Path:
    """Four COPIED gold items, for the tests that are about the CLI not the set.

    Copied rather than pointed at, so a test that turned out to write something
    would write it here and the frozen set would stay frozen either way.
    """
    target = tmp_path / "small-gold"
    target.mkdir()
    for path in sorted(gold_v4_dir.glob("ar-000*.json"))[:4]:
        shutil.copy(path, target / path.name)
        sidecar = path.parent / (path.stem + ".labels.yaml")
        shutil.copy(sidecar, target / sidecar.name)
    return target


def test_re_seeding_over_an_existing_state_replaces_it(
    config: ConfigBundle, tmp_path: Path, small_gold_dir: Path
) -> None:
    """A reset is a wipe, not an append: a stale case must not survive it."""
    paths = StatePaths.under(tmp_path / "state")
    reset_state(paths)
    (paths.journal / "stale-case.jsonl").write_text("{}\n", encoding="utf-8")
    seed_state(paths, gold_dir=small_gold_dir, config=config, now=BASE_TIME)
    case_ids = JsonlJournalStore(paths.journal).case_ids()
    assert "stale-case" not in case_ids
    assert len(case_ids) == 4


def test_the_cli_seeds_prints_the_environment_and_digests(
    tmp_path: Path, small_gold_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``python -m engine.demo.seed`` end to end, on a copied four-item set."""
    paths = StatePaths.under(tmp_path / "state")
    argv = [
        "--state-dir",
        str(tmp_path / "state"),
        "--gold-dir",
        str(small_gold_dir),
        "--now",
        "2026-08-12T12:00:00+00:00",
        "--digest",
    ]
    assert seed_main(argv) == 0
    printed = capsys.readouterr().out
    assert "items seeded      4" in printed
    assert f"EINGANGSLOTSE_VAULT_DIR         {paths.vault}" in printed
    assert state_digest(paths) in printed

    # And again, quietly: the digest line survives --quiet and matches.
    assert seed_main([*argv, "--quiet"]) == 0
    quiet = capsys.readouterr().out.strip().splitlines()
    assert len(quiet) == 1
    assert quiet[0].split()[-1] == state_digest(paths)


def test_the_cli_reads_the_state_directories_out_of_the_environment(
    tmp_path: Path, small_gold_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the container entrypoint does: no --state-dir, five env vars."""
    paths = StatePaths.under(tmp_path / "state")
    for key, value in paths.as_env().items():
        monkeypatch.setenv(key, value)
    assert seed_main(["--gold-dir", str(small_gold_dir), "--quiet"]) == 0
    assert len(JsonlJournalStore(paths.journal).case_ids()) == 4


def test_a_naive_base_timestamp_is_read_as_utc(
    tmp_path: Path, small_gold_dir: Path
) -> None:
    """An operator who omits the offset gets UTC, not a local-time surprise."""
    paths = StatePaths.under(tmp_path / "state")
    assert (
        seed_main(
            [
                "--state-dir",
                str(tmp_path / "state"),
                "--gold-dir",
                str(small_gold_dir),
                "--now",
                "2026-08-12T12:00:00",
                "--quiet",
            ]
        )
        == 0
    )
    aware = StatePaths.under(tmp_path / "aware")
    seed_state(
        aware,
        gold_dir=small_gold_dir,
        now=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
    )
    assert state_digest(paths) == state_digest(aware)


def test_the_seed_never_writes_into_the_frozen_corpus(
    config: ConfigBundle, tmp_path: Path, gold_v4_dir: Path
) -> None:
    """The gold set is read-only, and this is the assertion of it."""
    before = _tree_digest(gold_v4_dir)
    seed_state(
        StatePaths.under(tmp_path / "state"),
        gold_dir=gold_v4_dir,
        config=config,
        now=BASE_TIME,
    )
    assert _tree_digest(gold_v4_dir) == before


def test_the_seeded_state_serves_the_review_ui(
    config: ConfigBundle,
    tmp_path: Path,
    gold_v4_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the container does on boot, without the container."""
    monkeypatch.setenv(DEMO_MODE_ENV, "1")
    paths = StatePaths.under(tmp_path / "state")
    seed_state(paths, gold_dir=gold_v4_dir, config=config, now=BASE_TIME)
    client = TestClient(
        create_app(
            config=config,
            journal=JsonlJournalStore(paths.journal),
            vault=JsonlVaultStore(paths.vault),
            outbox=JsonlOutbox(paths.outbox),
            drafts=JsonlDraftStore(paths.drafts),
        )
    )
    overview = client.get("/review")
    assert overview.status_code == 200
    assert "101 offene(r) Vorgang" in overview.text
    assert 'id="demo-banner"' in overview.text
    assert client.get("/inbox").status_code == 200


def test_state_paths_refuse_a_half_configured_environment() -> None:
    """All five env vars or none: a partial state is a demo that loses half."""
    with pytest.raises(SystemExit) as raised:
        StatePaths.from_env({"EINGANGSLOTSE_JOURNAL_DIR": "/tmp/journal"})
    message = str(raised.value)
    assert "EINGANGSLOTSE_VAULT_DIR" in message
    assert "EINGANGSLOTSE_JOURNAL_DIR" not in message


def test_state_paths_round_trip_through_the_environment(tmp_path: Path) -> None:
    """``as_env`` is what an entrypoint exports; ``from_env`` must read it back."""
    paths = StatePaths.under(tmp_path)
    assert StatePaths.from_env(paths.as_env()) == paths


def _tree_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(directory).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_the_digest_covers_the_dispatch_exports_too(tmp_path: Path) -> None:
    """A confirmation writes an XML stub, and a reset has to have removed it.

    The dispatch directory is the one state store that holds something other
    than JSONL, so the digest reads those files verbatim. It also walks
    recursively, which is why a directory in the way must not derail it.
    """
    paths = StatePaths.under(tmp_path / "state")
    reset_state(paths)
    empty = state_digest(paths)
    (paths.dispatch / "nested").mkdir()
    assert state_digest(paths) == empty, "a directory is not state"
    (paths.dispatch / "nested" / "stub.xml").write_text(
        "<handover/>\n", encoding="utf-8"
    )
    assert state_digest(paths) != empty
    reset_state(paths)
    assert state_digest(paths) == empty


def test_the_digest_ignores_only_the_minted_ids(tmp_path: Path) -> None:
    """The digest must not be a hash of nothing: change a fact, change it."""
    paths = StatePaths.under(tmp_path / "state")
    reset_state(paths)
    line = {"event_id": "aaa", "case_id": "c-1", "payload": {"tier": 1}}
    (paths.journal / "c-1.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    first = state_digest(paths)
    (paths.journal / "c-1.jsonl").write_text(
        json.dumps({**line, "event_id": "bbb"}) + "\n", encoding="utf-8"
    )
    assert state_digest(paths) == first
    (paths.journal / "c-1.jsonl").write_text(
        json.dumps({**line, "payload": {"tier": 3}}) + "\n", encoding="utf-8"
    )
    assert state_digest(paths) != first
