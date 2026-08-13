"""The S1 HTTP surface: ingest one item, read the case back."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from engine.config_loader import ConfigBundle
from engine.journal import InMemoryJournalStore


@pytest.fixture
def client(config: ConfigBundle) -> Iterator[TestClient]:
    app = create_app(config=config, journal=InMemoryJournalStore())
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def submission(gold_v2_dir: Path) -> dict[str, Any]:
    return json.loads(
        (gold_v2_dir / "ar-0001-regelaltersrente-vollstaendig.json").read_text(
            encoding="utf-8"
        )
    )


def test_health_reports_the_config_versions(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["versions"]["decision_table_version"] == "table_v1"


def test_ingest_runs_the_pipeline_synchronously(
    client: TestClient, submission: dict[str, Any]
) -> None:
    response = client.post("/ingest", json=submission)
    assert response.status_code == 201
    body = response.json()
    assert body["tier"] == 1
    assert body["routed_unit_id"] == "Referat_312_Renten"
    assert body["completeness_verdict"] == "complete"
    assert body["gaps"] == []
    assert body["reasons"][0]["kind"] == "qualified"


def test_case_view_returns_events_and_derived_state(
    client: TestClient, submission: dict[str, Any]
) -> None:
    case_id = client.post("/ingest", json=submission).json()["case_id"]
    response = client.get(f"/cases/{case_id}")
    assert response.status_code == 200
    body = response.json()
    assert [event["type"] for event in body["events"]] == [
        "received",
        "redacted",
        "extracted",
        "evidence_assembled",
        # Part 09: the shadow scorer, log-only. It writes an event for every
        # item it sees, so a missing one means "no scorer configured" and never
        # "the scorer said nothing".
        "anomaly_scored",
        "tier_decided",
        "routed",
        # Part 07: the two ADR-005 projections, written by the worker that runs
        # inline after the pipeline. They are journal events like any other and
        # need no separate view - deliberately, because "what did this applicant
        # receive" has to be answerable from the audit trail.
        "notified",
        "notified",
        # Part 08: this item is a tier-1 Klarfall, so it owes a prepared
        # decision. The event carries ids and counts; the letter itself is in
        # the draft store, because it holds re-hydrated identity data.
        "drafted",
    ]
    assert body["state"]["tier"] == 1
    assert body["state"]["routed_unit_id"] == "Referat_312_Renten"
    assert body["events"][0]["versions"]["schema_version"] == "0.1.0"
    notified = [event for event in body["events"] if event["type"] == "notified"]
    assert [event["template_id"] for event in notified] == [
        "eingangsbestaetigung_v1",
        "zuordnung_v1",
    ]
    assert all(event["informational_only"] is True for event in notified)


def test_unknown_case_is_a_404(client: TestClient) -> None:
    assert client.get("/cases/case-gibtsnicht").status_code == 404


def test_invalid_submission_is_a_422(client: TestClient) -> None:
    response = client.post("/ingest", json={"data": {}})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["detail"] == "invalid submission"
    assert detail["errors"] == [{"loc": ["submissionId"], "type": "missing"}]


def test_a_422_never_echoes_the_submitted_value(client: TestClient) -> None:
    """Part 04: no error path repeats payload content, ever."""
    response = client.post(
        "/ingest",
        json={
            "submissionId": 4711,
            "data": {"antragsteller": {"nachname": "Vollbrecht"}},
        },
    )
    assert response.status_code == 422
    body = response.text
    assert "4711" not in body
    assert "Vollbrecht" not in body


def test_fastapis_own_422_is_sanitized_too(client: TestClient) -> None:
    """A body FastAPI itself rejects would otherwise be echoed back verbatim."""
    response = client.post("/ingest", json=["Vollbrecht", "17170459B012"])
    assert response.status_code == 422
    assert "Vollbrecht" not in response.text
    assert "17170459B012" not in response.text
    assert response.json()["detail"] == "invalid submission"


def test_journal_backend_is_selected_by_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from api.app import default_journal
    from engine.journal import JsonlJournalStore

    monkeypatch.delenv("EINGANGSLOTSE_JOURNAL_DIR", raising=False)
    assert isinstance(default_journal(), InMemoryJournalStore)
    monkeypatch.setenv("EINGANGSLOTSE_JOURNAL_DIR", str(tmp_path / "journal"))
    assert isinstance(default_journal(), JsonlJournalStore)


def test_the_inbox_is_empty_before_anything_arrives(client: TestClient) -> None:
    response = client.get("/inbox")
    assert response.status_code == 200
    assert "Noch keine Nachricht zugestellt" in response.text


def test_the_inbox_shows_what_was_delivered(
    client: TestClient, submission: dict[str, Any]
) -> None:
    """The applicant view (part 07): a page and a JSON twin over the same store."""
    case_id = client.post("/ingest", json=submission).json()["case_id"]

    page = client.get("/inbox")
    assert page.status_code == 200
    assert case_id in page.text
    assert "Eingangsbestaetigung" in page.text
    assert "Zwischenstand" in page.text
    # The page states the legal character of what it shows, every time.
    assert "rein informatorisch" in page.text

    response = client.get(f"/inbox/{case_id}")
    assert response.status_code == 200
    body = response.json()
    assert [item["template_id"] for item in body["notifications"]] == [
        "eingangsbestaetigung_v1",
        "zuordnung_v1",
    ]
    assert [item["delivery"] for item in body["notifications"]] == [
        "status_event",
        "status_event",
    ]


def test_an_unknown_case_has_no_inbox(client: TestClient) -> None:
    assert client.get("/inbox/case-gibtsnicht").status_code == 404


def test_the_ingest_response_names_the_notifications_it_sent(
    client: TestClient, submission: dict[str, Any]
) -> None:
    """Both are marked informational_only, in the response as in the journal."""
    body = client.post("/ingest", json=submission).json()
    assert body["notifications"] == [
        {"template_id": "eingangsbestaetigung_v1", "informational_only": True},
        {"template_id": "zuordnung_v1", "informational_only": True},
    ]


def test_outbox_backend_is_selected_by_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from engine.notify import InMemoryOutbox, JsonlOutbox, default_outbox

    monkeypatch.delenv("EINGANGSLOTSE_OUTBOX_DIR", raising=False)
    assert isinstance(default_outbox(), InMemoryOutbox)
    monkeypatch.setenv("EINGANGSLOTSE_OUTBOX_DIR", str(tmp_path / "outbox"))
    assert isinstance(default_outbox(), JsonlOutbox)
