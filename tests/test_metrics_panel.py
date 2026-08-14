"""The metrics panel: a page that shows the gate, and says so when it cannot.

The panel must never invent a number. Its two jobs are to show what the eval
report says and to be honest when there is no report - a blank dashboard that
looks fine is worse than one that says "run the eval".
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api import metrics as metrics_view
from api.app import create_app
from api.metrics import (
    REPORT_ENV,
    build_view,
    current_view,
    load_report,
    render_page,
    report_path,
)
from engine.config_loader import ConfigBundle
from engine.journal import InMemoryJournalStore
from eval.harness import evaluate_corpus, load_corpus


@pytest.fixture
def report_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gold_v1_dir: Path,
    config: ConfigBundle,
) -> Path:
    """A real eval report over the frozen gold set, in a temp location."""
    report = evaluate_corpus(
        load_corpus(gold_v1_dir), config=config, gold_dir=gold_v1_dir
    )
    path = report.write(tmp_path / "latest.json")
    monkeypatch.setenv(REPORT_ENV, str(path))
    return path


@pytest.fixture
def client(config: ConfigBundle) -> Iterator[TestClient]:
    app = create_app(config=config, journal=InMemoryJournalStore())
    with TestClient(app) as test_client:
        yield test_client


def test_panel_shows_the_gate_metrics(client: TestClient, report_file: Path) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert '<html lang="de">' in body
    assert "False-Clear-Rate" in body
    assert "Routing-Genauigkeit" in body
    assert "Tier-Genauigkeit" in body
    assert "Vollstaendigkeit Precision" in body
    assert "Gate bestanden" in body
    assert "corpus" in body and "v1" in body


def test_panel_shows_the_breakdowns(client: TestClient, report_file: Path) -> None:
    body = client.get("/metrics").text
    assert "Nach Verfahren" in body
    assert "erwerbsminderungsrente" in body
    assert "Auffällige Teilmenge" in body
    assert "Shadow-Scorer aus Teil 06" in body


#: The one line in the panel that is allowed to differ between two renders.
RENDERED_AT = re.compile(r"Stand dieser Anzeige: [^<]*")


@contextmanager
def freeze_render(when: str) -> Iterator[None]:
    """Hold the render clock still, so two renders are comparable.

    Patched at the one function that reads the clock rather than at
    `datetime.now`, which the pipeline under this app also calls.
    """
    with patch.object(metrics_view, "render_clock", lambda: when):
        yield


def test_panel_fragment_is_the_same_section(
    client: TestClient, report_file: Path
) -> None:
    """The fragment htmx swaps in IS the section the page already carries.

    Compared with the render clock masked out on both sides. That line is by
    construction different in two responses - it is the server's clock at
    render time, and it is the only thing on the panel a reload can visibly
    change, since the numbers come from a report that does not move while the
    process runs. Masking it is the point of the test, not a concession: what
    is being asserted is that the two renders are otherwise the same markup.
    """
    fragment = client.get("/metrics/panel")
    assert fragment.status_code == 200
    assert fragment.text.strip().startswith('<section id="metrics-panel"')
    assert "<html" not in fragment.text
    masked = RENDERED_AT.sub("STAND", fragment.text.strip())
    assert masked in RENDERED_AT.sub("STAND", client.get("/metrics").text)
    # ... and both really do carry one, or the mask would hide an omission.
    assert RENDERED_AT.search(fragment.text)
    assert RENDERED_AT.search(client.get("/metrics").text)


def test_the_reload_control_changes_something_a_reader_can_see(
    client: TestClient, report_file: Path
) -> None:
    """Part 17: "Neu laden" looked dead, and the reason was not a broken chain.

    Traced in a real browser: htmx loads, the click issues `GET
    /metrics/panel`, and the swap lands. The panel then contained exactly the
    markup it already had, because the eval report is written at build time and
    never changes while the process runs - so a working control produced no
    visible effect at all.

    The render clock is the fix and this is what pins it: two renders of the
    same unchanged report differ, and they differ ONLY there.
    """
    first = client.get("/metrics/panel").text
    assert "Stand dieser Anzeige" in first
    # The report's own timestamp is a different fact and says so.
    assert "Zeitpunkt des Eval-Laufs" in first
    assert RENDERED_AT.sub("STAND", first) != first

    with freeze_render("2026-08-15T09:00:00+00:00"):
        frozen_a = client.get("/metrics/panel").text
    with freeze_render("2026-08-15T09:00:41+00:00"):
        frozen_b = client.get("/metrics/panel").text
    assert frozen_a != frozen_b, "the reload changes nothing a reader can see"
    assert "2026-08-15T09:00:41+00:00" in frozen_b
    assert RENDERED_AT.sub("STAND", frozen_a) == RENDERED_AT.sub("STAND", frozen_b)

    # With scripting off the anchor is still a link to the whole page, so the
    # same proof of life arrives by navigation.
    page = client.get("/metrics").text
    assert '<a class="cta cta-secondary" href="/metrics"' in page
    assert "Stand dieser Anzeige" in page


def test_missing_report_is_a_200_with_the_refresh_hint(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(REPORT_ENV, str(tmp_path / "nicht-da.json"))
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "python -m eval.run" in response.text
    assert "Noch kein Eval-Report" in response.text
    assert "Gate bestanden" not in response.text


def test_unreadable_report_is_reported_not_swallowed(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "latest.json"
    broken.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv(REPORT_ENV, str(broken))
    body = client.get("/metrics").text
    assert "nicht lesbar" in body
    assert "python -m eval.run" in body


def test_a_failed_gate_is_stated_in_words_not_only_in_colour(tmp_path: Path) -> None:
    document: dict[str, Any] = {
        "item_count": 1,
        "gate_passed": False,
        "false_clear_rate": 0.5,
        "gold_dir": "corpus/gold/v1",
        "versions": {},
        "by_procedure": {},
        "anomalous": {"item_count": 0, "tier_agreement": 0.0},
        "paraphrase_counts": {},
        "items": [],
    }
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    from api.metrics import render_page

    body = render_page(build_view(document, path=path, problem=None))
    assert "Gate gerissen" in body
    assert "0.500" in body


def test_report_path_prefers_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REPORT_ENV, raising=False)
    assert report_path().as_posix().endswith("eval/reports/latest.json")
    monkeypatch.setenv(REPORT_ENV, "C:/woanders/report.json")
    assert report_path() == Path("C:/woanders/report.json")


def test_a_non_object_report_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "latest.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    document, problem = load_report(path)
    assert document is None
    assert problem is not None
    assert build_view(document, path=path, problem=problem).available is False


def test_current_view_reads_the_configured_path(
    report_file: Path,
) -> None:
    view = current_view()
    assert view.available is True
    assert view.report_path == str(report_file)
    assert view.item_count >= 30


def test_the_panel_shows_the_derivation_metric(tmp_path: Path) -> None:
    """A metric nobody can see is a metric nobody acts on."""
    from api.metrics import build_view, render_panel

    view = build_view(
        {
            "procedure_derivation": {
                "accuracy": 0.875,
                "labelled_items": 8,
                "unlabelled_items": 2,
                "accuracy_by_source": {"content": 0.5, "hint": 1.0},
            }
        },
        path=tmp_path / "latest.json",
        problem=None,
    )
    assert any(
        metric["label"] == "Verfahrensableitung" and metric["value"] == "0.875"
        for metric in view.headline or []
    )
    html = render_panel(view)
    assert "Verfahrensableitung" in html
    assert "content 0.500" in html
    assert "8 Vorgänge tragen" in html


def test_a_report_without_the_derivation_block_degrades_quietly(
    tmp_path: Path,
) -> None:
    """Old reports predate the metric; the panel must not invent a value."""
    from api.metrics import build_view

    view = build_view({}, path=tmp_path / "latest.json", problem=None)
    assert any(
        metric["label"] == "Verfahrensableitung" and metric["value"] == "-"
        for metric in view.headline or []
    )


def test_the_panel_shows_the_span_verification_and_subset_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two sections part 05 added, and the one number that is a status
    rather than a rate."""
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "gold_dir": "corpus/gold/v4",
                "item_count": 101,
                "gate_passed": True,
                "span_verification": {
                    "text_items": 24,
                    "proposals": 88,
                    "verified": 87,
                    "discarded": 1,
                    "verified_rate": 0.9886,
                    "failures": {"quote_mismatch": 1},
                    "by_source_type": {
                        "ocr": {
                            "items": 8,
                            "proposals": 31,
                            "verified_rate": 0.968,
                            "discard_rate": 0.032,
                        }
                    },
                },
                "structured_subset": {
                    "item_count": 77,
                    "routing_accuracy": 1.0,
                    "tier_accuracy": 1.0,
                    "false_clear_rate": 0.0,
                    "derivation_accuracy": 1.0,
                    "invariant_held": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(REPORT_ENV, str(report))
    page = render_page(current_view())
    assert "Spanprüfung" in page
    assert "Belegte Fundstellen" in page
    assert "quote_mismatch 1" in page
    assert "<strong>unverändert</strong>" in page
    assert "0.989" in page, "the headline row reads the nested rate"


def test_the_panel_says_so_when_the_subset_invariant_is_violated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "structured_subset": {"item_count": 77, "invariant_held": False},
                "span_verification": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(REPORT_ENV, str(report))
    page = render_page(current_view())
    assert "<strong>VERLETZT</strong>" in page
    assert "Kein Vorgang in diesem Satz trägt Freitext." in page


def test_the_panel_shows_the_threshold_review_and_the_classifier(
    tmp_path: Path,
) -> None:
    """P-5's whole point: the numbers that govern, visible with their provenance."""
    from api.metrics import build_view, render_panel

    view = build_view(
        {
            "thresholds_review": {
                "review_due": "2026-11-30",
                "overdue": False,
                "uncalibrated_count": 1,
                "thresholds": [
                    {
                        "threshold_id": "span_match_ocr",
                        "value": 0.86,
                        "source_version": "extraction_v1",
                        "provenance": "measured on gold v4's OCR letters",
                        "calibrated": True,
                    },
                    {
                        "threshold_id": "anomaly_default_v0",
                        "value": 0.85,
                        "source_version": "risk_v0",
                        "provenance": "uncalibrated placeholder",
                        "calibrated": False,
                    },
                ],
            },
            "classifier": {
                "configured": True,
                "enabled": False,
                "ran": False,
                "extra_installed": True,
                "model_id": "intfloat/multilingual-e5-small",
                "addressable_items": 5,
            },
        },
        path=tmp_path / "latest.json",
        problem=None,
    )
    html = render_panel(view)
    assert "Schwellenwert-Review" in html
    assert "2026-11-30" in html
    assert "span_match_ocr" in html
    assert "unkalibriert:" in html
    assert "nur Protokoll" in html
    assert "intfloat/multilingual-e5-small" in html


def test_an_overdue_review_is_visible_on_the_panel(tmp_path: Path) -> None:
    from api.metrics import build_view, render_panel

    view = build_view(
        {"thresholds_review": {"review_due": "2026-01-01", "overdue": True}},
        path=tmp_path / "latest.json",
        problem=None,
    )
    assert "UEBERFAELLIG" in render_panel(view)


def test_a_report_without_the_new_sections_degrades_quietly(tmp_path: Path) -> None:
    """Reports written before part 06 have neither section; nothing is invented."""
    from api.metrics import build_view, render_panel

    view = build_view({}, path=tmp_path / "latest.json", problem=None)
    html = render_panel(view)
    assert "nicht gesetzt" in html
    assert "Kein Klassifikator konfiguriert" in html
    assert any(
        metric["label"] == "Zuordnungsvorschlag" and metric["value"] == "-"
        for metric in view.headline or []
    )


def test_the_panel_shows_the_notification_section(report_file: Path) -> None:
    """Part 07: the applicant-notification numbers and what they are not."""
    page = render_page(current_view())
    assert "Benachrichtigungen" in page
    assert "notifications_v1" in page
    assert "Realakt, kein Verwaltungsakt" in page
    assert "kein Sprachmodell" in page
    # The latency is honest about being a measurement of the run, not a gate.
    assert "Differenz zweier Journal-Zeitstempel" in page
    assert "berichtet, nicht gegated" in page
    assert "Benachrichtigungsquote" in page
    assert '<a href="/inbox">' in page


def test_the_panel_survives_a_report_written_before_notifications_existed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report_file: Path
) -> None:
    """An old report simply has no notification section; the page still renders."""
    document = json.loads(report_file.read_text(encoding="utf-8"))
    document.pop("notifications")
    older = tmp_path / "older.json"
    older.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv(REPORT_ENV, str(older))
    page = render_page(current_view())
    assert "keine Benachrichtigungen hinterlegt" in page
