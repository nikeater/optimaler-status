"""The draft store, the replay CLI and the one API route that returns identity.

The store is parametrised over both backends for the reason ``test_redact_vault``
gives: a second backend that behaved differently would be a second definition of
what a draft is, and the file one is the only one an operator ever inspects.

The CLI is the part-07 replay with a vault in front of it. Its one honest
asymmetry is tested rather than hidden: a Nachforderung replays completely out
of the journal, and a prepared decision does not, because the journal
deliberately carries no extracted values.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from engine.config_loader import ConfigBundle
from engine.draft import (
    DRAFTS_DIR_ENV,
    DraftRecord,
    DraftStore,
    InMemoryDraftStore,
    JsonlDraftStore,
    default_draft_store,
    draft_id_for,
)
from engine.draft import replay as replay_cli
from engine.journal.store import InMemoryJournalStore, JsonlJournalStore
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore, JsonlVaultStore

FIXED = datetime(2026, 8, 6, 7, 21, tzinfo=UTC)

TIER1_ITEM = "ar-0001-regelaltersrente-vollstaendig"
#: Any taxonomy unit id: part 10 gates the drafts route on one.
DRAFT_UNIT = "Referat_312_Renten"

TIER2_ITEM = "ar-0014-ohne-vsnr-und-rentenbeginn"


def a_record(draft_id: str = "draft-1", case_id: str = "case-x") -> DraftRecord:
    return DraftRecord(
        draft_id=draft_id,
        case_id=case_id,
        envelope_id="env-x",
        kind="nachforderung",
        template_id="nachforderung_v1",
        procedure_id="altersrente",
        tier=2,
        requirement_ids=["rentenbeginn"],
        subject="Betreff",
        body="Sehr geehrte Damen und Herren,",
        resolved_tokens=2,
        distinct_tokens=2,
        token_kinds={"ADDR": 1, "GEBDAT": 1},
        response_window_days=30,
        rechtsfolgenhinweis=False,
        source_event_id="event-1",
        drafting_version="drafting_v1",
        created_at=FIXED,
    )


@pytest.fixture(params=["memory", "jsonl"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> DraftStore:
    if request.param == "memory":
        return InMemoryDraftStore()
    return JsonlDraftStore(tmp_path / "drafts")


# --------------------------------------------------------------- the store ---


def test_a_draft_is_stored_once_and_read_back(store: DraftStore) -> None:
    record = a_record()
    assert store.save(record) is True
    assert store.save(record) is False, "a draft id may be stored exactly once"
    assert [item.draft_id for item in store.records("case-x")] == ["draft-1"]
    assert store.case_ids() == ["case-x"]
    assert store.records("case-nothing") == []


def test_a_second_draft_of_the_same_case_is_kept(store: DraftStore) -> None:
    store.save(a_record("draft-1"))
    store.save(a_record("draft-2"))
    assert len(store.records("case-x")) == 2


def test_the_stored_record_round_trips_through_json(store: DraftStore) -> None:
    store.save(a_record())
    read = store.records("case-x")[0]
    assert read.model_dump(mode="json") == a_record().model_dump(mode="json")


def test_the_draft_id_is_a_pure_function_of_the_event_and_the_template() -> None:
    assert draft_id_for("event-9", "nachforderung_v1") == "event-9-nachforderung_v1"


def test_the_file_backend_refuses_an_unsafe_case_id(tmp_path: Path) -> None:
    backend = JsonlDraftStore(tmp_path / "drafts")
    with pytest.raises(ValueError, match="filesystem-safe"):
        backend.save(a_record(case_id="../escape"))


def test_the_default_store_follows_the_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DRAFTS_DIR_ENV, raising=False)
    assert isinstance(default_draft_store(), InMemoryDraftStore)
    monkeypatch.setenv(DRAFTS_DIR_ENV, str(tmp_path / "drafts"))
    assert isinstance(default_draft_store(), JsonlDraftStore)


def test_the_summary_is_value_free() -> None:
    summary = a_record().summary()
    assert "body" not in summary
    assert "subject" not in summary
    assert summary["body_chars"] == len("Sehr geehrte Damen und Herren,")


# ----------------------------------------------------------- the replay CLI ---


def journal_with(
    item_ids: list[str],
    *,
    config: ConfigBundle,
    gold_v4_dir: Path,
    journal_dir: Path,
    vault_dir: Path,
) -> None:
    """Run items through the pipeline into FILE-backed stores, no drafting."""
    journal = JsonlJournalStore(journal_dir)
    vault = JsonlVaultStore(vault_dir)
    for item_id in item_ids:
        payload = json.loads(
            (gold_v4_dir / f"{item_id}.json").read_text(encoding="utf-8")
        )
        run_pipeline(payload, config=config, journal=journal, vault=vault, now=FIXED)


def test_the_cli_drafts_a_nachforderung_out_of_the_journal_alone(
    config: ConfigBundle,
    gold_v4_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    journal_dir, vault_dir = tmp_path / "journal", tmp_path / "vault"
    drafts_dir = tmp_path / "drafts"
    journal_with(
        [TIER2_ITEM],
        config=config,
        gold_v4_dir=gold_v4_dir,
        journal_dir=journal_dir,
        vault_dir=vault_dir,
    )
    code = replay_cli.main(
        [
            "--journal",
            str(journal_dir),
            "--vault",
            str(vault_dir),
            "--drafts",
            str(drafts_dir),
        ]
    )
    assert code == 0
    assert "drafted      nachforderung_v1" in capsys.readouterr().out
    stored = JsonlDraftStore(drafts_dir)
    case_id = stored.case_ids()[0]
    body = stored.records(case_id)[0].body
    assert "Bitte geben Sie an, ab wann Sie Ihre Rente beziehen moechten." in body


def test_replaying_twice_writes_nothing_the_second_time(
    config: ConfigBundle,
    gold_v4_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    journal_dir, vault_dir = tmp_path / "journal", tmp_path / "vault"
    drafts_dir = tmp_path / "drafts"
    journal_with(
        [TIER2_ITEM],
        config=config,
        gold_v4_dir=gold_v4_dir,
        journal_dir=journal_dir,
        vault_dir=vault_dir,
    )
    argv = [
        "--journal",
        str(journal_dir),
        "--vault",
        str(vault_dir),
        "--drafts",
        str(drafts_dir),
    ]
    replay_cli.main(argv)
    capsys.readouterr()
    assert replay_cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "1 drafts already recorded, 0 drafted now" in out
    assert "Nothing owed" in out
    assert len(JsonlDraftStore(drafts_dir).records("case-" + TIER2_ITEM)) == 1


def test_a_prepared_decision_is_reported_as_blocked_on_a_replay(
    config: ConfigBundle,
    gold_v4_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The honest asymmetry: the journal carries no extracted values.

    A letter that stated the applicant's own facts back at them with the facts
    left out would be worse than no letter, so the case is reported instead.
    """
    journal_dir, vault_dir = tmp_path / "journal", tmp_path / "vault"
    journal_with(
        [TIER1_ITEM],
        config=config,
        gold_v4_dir=gold_v4_dir,
        journal_dir=journal_dir,
        vault_dir=vault_dir,
    )
    assert (
        replay_cli.main(
            [
                "--journal",
                str(journal_dir),
                "--vault",
                str(vault_dir),
                "--drafts",
                str(tmp_path / "drafts"),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "blocked      prepared_decision" in out
    assert "pipeline path" in out


def test_the_dry_run_writes_to_neither_store(
    config: ConfigBundle,
    gold_v4_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    journal_dir, vault_dir = tmp_path / "journal", tmp_path / "vault"
    drafts_dir = tmp_path / "drafts"
    journal_with(
        [TIER2_ITEM],
        config=config,
        gold_v4_dir=gold_v4_dir,
        journal_dir=journal_dir,
        vault_dir=vault_dir,
    )
    assert (
        replay_cli.main(
            [
                "--journal",
                str(journal_dir),
                "--vault",
                str(vault_dir),
                "--drafts",
                str(drafts_dir),
                "--dry-run",
            ]
        )
        == 0
    )
    assert "would draft  nachforderung" in capsys.readouterr().out
    assert JsonlDraftStore(drafts_dir).case_ids() == []
    events = JsonlJournalStore(journal_dir).read("case-" + TIER2_ITEM)
    assert not any(event.type.value == "drafted" for event in events)


def test_the_cli_refuses_a_config_without_drafting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    target = tmp_path / "config"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", target)
    shutil.rmtree(target / "drafting")
    code = replay_cli.main(
        [
            "--journal",
            str(tmp_path / "journal"),
            "--vault",
            str(tmp_path / "vault"),
            "--config",
            str(target),
        ]
    )
    assert code == 1
    assert "prepares no drafts" in capsys.readouterr().err


def test_the_optional_par_66_flag_reaches_the_letter(
    config: ConfigBundle, gold_v4_dir: Path, tmp_path: Path
) -> None:
    journal_dir, vault_dir = tmp_path / "journal", tmp_path / "vault"
    drafts_dir = tmp_path / "drafts"
    journal_with(
        [TIER2_ITEM],
        config=config,
        gold_v4_dir=gold_v4_dir,
        journal_dir=journal_dir,
        vault_dir=vault_dir,
    )
    replay_cli.main(
        [
            "--journal",
            str(journal_dir),
            "--vault",
            str(vault_dir),
            "--drafts",
            str(drafts_dir),
            "--rechtsfolgenhinweis",
        ]
    )
    record = JsonlDraftStore(drafts_dir).records("case-" + TIER2_ITEM)[0]
    assert record.rechtsfolgenhinweis is True
    assert "par. 66 Abs. 3 SGB I" in record.body


# ------------------------------------------------------------- the API ---


@pytest.fixture
def client(config: ConfigBundle, tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            config=config,
            journal=InMemoryJournalStore(),
            vault=InMemoryVaultStore(),
            drafts=JsonlDraftStore(tmp_path / "drafts"),
        )
    )


def test_ingest_prepares_a_draft_and_reports_which_one(
    client: TestClient, gold_v4_dir: Path
) -> None:
    payload: dict[str, Any] = json.loads(
        (gold_v4_dir / f"{TIER2_ITEM}.json").read_text(encoding="utf-8")
    )
    created = client.post("/ingest", json=payload)
    assert created.status_code == 201
    drafts = created.json()["drafts"]
    assert [draft["kind"] for draft in drafts] == ["nachforderung"]
    assert drafts[0]["resolved_tokens"] == 2
    # The response says which draft exists; the letter is behind its own route.
    assert "Sehr geehrte" not in created.text


def test_the_drafts_route_returns_the_letter_and_a_404_when_there_is_none(
    client: TestClient, gold_v4_dir: Path
) -> None:
    payload: dict[str, Any] = json.loads(
        (gold_v4_dir / f"{TIER2_ITEM}.json").read_text(encoding="utf-8")
    )
    case_id = client.post("/ingest", json=payload).json()["case_id"]
    # Part 10 put this route behind the demo role model: a letter carries the
    # applicant's re-hydrated identity, so a caller must name a unit.
    assert client.get(f"/drafts/{case_id}").status_code == 403
    response = client.get(f"/drafts/{case_id}?unit={DRAFT_UNIT}")
    assert response.status_code == 200
    body = response.json()
    assert body["drafts"][0]["kind"] == "nachforderung"
    assert "Sehr geehrte Damen und Herren" in body["drafts"][0]["body"]
    assert "nothing here is a Verwaltungsakt" in body["note"]
    assert client.get(f"/drafts/case-gibtsnicht?unit={DRAFT_UNIT}").status_code == 404


def test_a_tier_three_item_gets_no_draft_through_the_api(
    client: TestClient, gold_v4_dir: Path
) -> None:
    payload: dict[str, Any] = json.loads(
        (gold_v4_dir / "sf-0001-it-beratung-vollstaendig.json").read_text(
            encoding="utf-8"
        )
    )
    created = client.post("/ingest", json=payload)
    assert created.json()["tier"] == 3
    assert created.json()["drafts"] == []
    unknown = f"/drafts/{created.json()['case_id']}?unit={DRAFT_UNIT}"
    assert client.get(unknown).status_code == 404
