"""The metrics panel: the eval report, server-rendered as plain HTML.

This is the seed of the review UI's metrics pane, not a dashboard framework.
Everything is server-rendered Jinja2; htmx swaps one fragment on request, and
the page works with JavaScript switched off because a link is a link.

The panel reads the report ``python -m eval.run`` wrote and never computes a
metric itself. ``eval/reports/`` is a build artifact (gitignored), so "no report
yet" is a normal state with a normal answer: the page prints the command that
produces one.

Read paths, in order: ``$EINGANGSLOTSE_EVAL_REPORT``, then the repo's
``eval/reports/latest.json``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from api.i18n import GERMAN, PageContext
from engine.demo import REPO_URL_PLACEHOLDER, DemoPosture, demo_posture
from eval.harness import DEFAULT_REPORT_PATH

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / "ui" / "templates"
STATIC_DIR = REPO_ROOT / "ui" / "static"
REPORT_ENV = "EINGANGSLOTSE_EVAL_REPORT"
REFRESH_HINT = "python -m eval.run"

#: Headline metrics, in the order the panel shows them.
HEADLINE = (
    ("false_clear_rate", "False-Clear-Rate", "Gate: muss 0.000 sein"),
    ("routing_accuracy", "Routing-Genauigkeit", "Zugeordnete Einheit == Gold"),
    ("tier_accuracy", "Tier-Genauigkeit", "Entschiedenes Tier == Gold"),
    ("false_flag_rate", "False-Flag-Rate", "Effizienz, kein Gate"),
    ("gap_precision", "Vollstaendigkeit Precision", "Gemeldete Luecken, die es gibt"),
    (
        "gap_recall",
        "Vollstaendigkeit Recall",
        "Vorhandene Luecken, die gemeldet werden",
    ),
    ("gap_exact_match_rate", "Lueckenliste exakt", "Gesamte Liste identisch"),
)

#: Metrics that live one level down in the report.
NESTED = (
    (
        ("procedure_derivation", "accuracy"),
        "Verfahrensableitung",
        "Richtiges Verfahren auf dem richtigen Weg",
    ),
    (
        ("redaction", "deterministic_recall"),
        "Redaktions-Recall",
        "Gate: muss 1.000 sein (deterministische Erkenner)",
    ),
    (
        ("span_verification", "verified_rate"),
        "Belegte Fundstellen",
        "Doppelt geprueft (Zitat und Position); berichtet, kein Gate",
    ),
    (
        ("classifier", "coverage", "rate"),
        "Zuordnungsvorschlag",
        "Anteil regelloser Vorgaenge mit Vorschlag; nur Protokoll, kein Gate",
    ),
    (
        ("notifications", "coverage"),
        "Benachrichtigungsquote",
        "Anteil der Vorgaenge mit Eingangsbestaetigung; berichtet, kein Gate",
    ),
    (
        ("drafting", "unresolved_tokens"),
        "Offene Platzhalter",
        "Muss 0 sein: jeder Entwurf ist vollstaendig rueckbefuellt",
    ),
    (
        ("anomaly", "flagged", "rate"),
        "Markierungsquote Scorer",
        "Anteil markierter Vorgaenge; nur Protokoll, bewegt kein Tier",
    ),
    (
        ("anomaly", "anomaly_expected", "recall"),
        "Trefferquote Scorer",
        "Anteil der im Goldsatz als auffaellig gekennzeichneten Vorgaenge",
    ),
    (
        ("anomaly", "false_flags", "rate_on_tier1_eligible"),
        "Fehlmarkierungsquote",
        "Tier-1-Vorgaenge ohne Auffaelligkeitslabel; Budget 0.15",
    ),
)


@dataclass(frozen=True)
class MetricsView:
    """Everything the template needs, already normalized.

    Old reports (written before a metric existed) simply have no value for it;
    the view fills a neutral default rather than letting the template guess.
    """

    available: bool
    report_path: str
    problem: str | None = None
    #: When the REPORT was computed. Baked into the image at build time and
    #: identical on every request until a new report is written.
    generated_at: str = ""
    #: When THIS RENDER happened, off the server clock. Two different facts,
    #: labelled as two different facts on the page: the numbers below cannot
    #: change between two requests, and this line is the only thing on the
    #: panel that can, which is what makes the reload control observable.
    rendered_at: str = ""
    gold_dir: str = ""
    item_count: int = 0
    gate_passed: bool = False
    scorer_mode: str = ""
    versions: dict[str, Any] | None = None
    headline: list[dict[str, Any]] | None = None
    by_procedure: dict[str, dict[str, Any]] | None = None
    anomalous: dict[str, Any] | None = None
    paraphrase_counts: dict[str, int] | None = None
    procedure_derivation: dict[str, Any] | None = None
    redaction: dict[str, Any] | None = None
    span_verification: dict[str, Any] | None = None
    structured_subset: dict[str, Any] | None = None
    thresholds_review: dict[str, Any] | None = None
    classifier: dict[str, Any] | None = None
    notifications: dict[str, Any] | None = None
    drafting: dict[str, Any] | None = None
    anomaly: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    bias: dict[str, Any] | None = None
    items: list[dict[str, Any]] | None = None
    refresh_hint: str = REFRESH_HINT


def report_path() -> Path:
    """Where the panel looks for the eval report."""
    override = os.environ.get(REPORT_ENV)
    return Path(override) if override else REPO_ROOT / DEFAULT_REPORT_PATH


def load_report(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read the report; returns (document, problem)."""
    if not path.is_file():
        return None, "Noch kein Eval-Report vorhanden."
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return None, f"Report nicht lesbar ({type(error).__name__})."
    if not isinstance(document, dict):
        return None, "Report enthaelt kein JSON-Objekt."
    return document, None


def render_clock() -> str:
    """The server clock, at render time, to the second.

    Its own function because two pages print it and one sentence of formatting
    in two places is how the two start disagreeing. Seconds rather than
    microseconds: this is a "you are looking at a fresh render" line for a
    reader, not a measurement.
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def build_view(
    document: dict[str, Any] | None, *, path: Path, problem: str | None
) -> MetricsView:
    """Normalize a report document into the view the template renders."""
    if document is None:
        return MetricsView(
            available=False,
            report_path=str(path),
            problem=problem or "Kein Report.",
            rendered_at=render_clock(),
        )
    return MetricsView(
        available=True,
        report_path=str(path),
        generated_at=str(document.get("generated_at", "")),
        rendered_at=render_clock(),
        gold_dir=str(document.get("gold_dir", "")),
        item_count=int(document.get("item_count", 0)),
        gate_passed=bool(document.get("gate_passed", False)),
        scorer_mode=str(document.get("scorer_mode", "")),
        versions=dict(document.get("versions", {})),
        headline=[
            {
                "key": key,
                "label": label,
                "note": note,
                "value": _format_rate(document.get(key)),
                "is_gate": key == "false_clear_rate",
            }
            for key, label, note in HEADLINE
        ]
        + [
            {
                "key": ".".join(path),
                "label": label,
                "note": note,
                "value": _format_rate(_dig(document, path)),
                # The redaction recall is a gate in its own right (P-7), and the
                # panel has to show it as one or the row reads like trivia.
                "is_gate": path == ("redaction", "deterministic_recall"),
            }
            for path, label, note in NESTED
        ],
        by_procedure=dict(document.get("by_procedure", {})),
        anomalous=dict(document.get("anomalous", {})),
        paraphrase_counts=dict(document.get("paraphrase_counts", {})),
        procedure_derivation=dict(document.get("procedure_derivation", {})),
        redaction=dict(document.get("redaction", {})),
        span_verification=dict(document.get("span_verification", {})),
        structured_subset=dict(document.get("structured_subset", {})),
        thresholds_review=dict(document.get("thresholds_review", {})),
        classifier=dict(document.get("classifier", {})),
        notifications=dict(document.get("notifications", {})),
        drafting=dict(document.get("drafting", {})),
        anomaly=dict(document.get("anomaly", {})),
        review=dict(document.get("review", {})),
        bias=dict(document.get("bias", {})),
        items=list(document.get("items", [])),
    )


def current_view() -> MetricsView:
    """The view for the report on disk right now."""
    path = report_path()
    document, problem = load_report(path)
    return build_view(document, path=path, problem=problem)


@lru_cache(maxsize=1)
def environment() -> Environment:
    """The Jinja environment every page in this project renders through.

    Carries the globals every template may assume exist, whatever view object
    it was handed:

    * ``demo`` - the part-11 posture, so ``_demo_ribbon.html`` and the site
      header's demo-gated menu items can be included from every page without
      six view objects growing the same field. Outside demo mode both render
      exactly zero bytes, which is asserted rather than assumed
      (``tests/test_demo_mode.py``).
    * ``repo_url`` - the CONFIGURED source address, or the empty string. Not
      the placeholder: a menu item pointing at ``github.com/OWNER/...`` would
      be a broken link in the one place a reader looks for the code.
    * ``page``, ``t``, ``m``, ``lang`` - the part-16 language context. Defaulted
      to German here so that a template rendered without one (a test, the htmx
      fragment) still renders rather than raising on an undefined callable.
    """
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    _install(env, demo_posture())
    return env


def _install(env: Environment, posture: DemoPosture) -> None:
    """Write the posture and the default language context into the globals."""
    env.globals["demo"] = posture
    env.globals["repo_url"] = (
        "" if posture.repo_url == REPO_URL_PLACEHOLDER else posture.repo_url
    )
    env.globals.update(page_globals(GERMAN))


def page_globals(page: PageContext) -> dict[str, Any]:
    """What one page's language context looks like to a template."""
    return {"page": page, "t": page.t, "m": page.m, "lang": page.lang}


def set_demo_posture(posture: DemoPosture) -> None:
    """Hand the templates the posture ``create_app`` resolved at startup.

    The environment is process-wide and the app is not, so this exists to keep
    the two in step in a test process that builds several apps. In a deployment
    it is called once, with the same posture ``environment()`` already read.
    """
    _install(environment(), posture)


def render_template(name: str, view: object, page: PageContext | None = None) -> str:
    """Render one page in one language.

    The single place a template is handed a language, so the four view modules
    do not each grow their own copy of the same three keyword arguments. The
    context is passed per render rather than mutated into the globals: the
    environment is process-wide, and a language written into it would be a
    language the next request inherits.
    """
    return (
        environment()
        .get_template(name)
        .render(view=view, **page_globals(page or GERMAN))
    )


def render_page(view: MetricsView, page: PageContext | None = None) -> str:
    """The whole page."""
    return render_template("metrics.html", view, page)


def render_panel(view: MetricsView, page: PageContext | None = None) -> str:
    """Just the panel fragment, for the htmx swap."""
    return render_template("_metrics_panel.html", view, page)


def _dig(document: dict[str, Any], path: tuple[str, ...]) -> object:
    """Follow a key path; anything missing on the way is simply absent."""
    current: object = document
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _format_rate(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "-"
    return f"{float(value):.3f}"
