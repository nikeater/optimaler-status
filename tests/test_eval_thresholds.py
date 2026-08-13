"""The threshold-review section (P-5) and the classifier section.

Both are reported and neither is gated. What is asserted here is that every
governing number appears with its provenance, that an uncalibrated one says so,
that the operating point is computed from the same per-item data the decision
table saw, and that the review notice reads an injectable clock and touches no
exit code.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from engine.config_loader import ConfigBundle
from engine.evidence import HashingEmbedder
from eval.classifier import (
    ClassifierObservation,
    classifier_section,
    fit_from_observations,
    observe_corpus,
)
from eval.harness import EvalReport, ItemResult, evaluate_corpus, load_corpus
from eval.run import main as run_main
from eval.thresholds import review_warning, threshold_review

TODAY = date(2026, 8, 12)
AFTER_REVIEW = date(2027, 1, 15)


@pytest.fixture(scope="module")
def report(gold_v4_dir: Path) -> EvalReport:
    """One full eval run, shared: it is the expensive fixture in this file."""
    from engine.config_loader import load_config

    config = load_config()
    return evaluate_corpus(
        load_corpus(gold_v4_dir), config=config, gold_dir=gold_v4_dir, today=TODAY
    )


def _entries(section: dict[str, Any]) -> list[dict[str, Any]]:
    return list(section["thresholds"])


def _ids(section: dict[str, Any]) -> list[str]:
    return [str(entry["threshold_id"]) for entry in _entries(section)]


def _entry(section: dict[str, Any], threshold_id: str) -> dict[str, Any]:
    for entry in _entries(section):
        if entry["threshold_id"] == threshold_id:
            return dict(entry)
    raise AssertionError(f"no threshold entry {threshold_id}")


def _operating(section: dict[str, Any], threshold_id: str) -> dict[str, Any]:
    return dict(_entry(section, threshold_id)["operating_point"])


# --------------------------------------------------------------------------
# Coverage: every governing number is listed
# --------------------------------------------------------------------------


def test_every_governing_threshold_is_listed(report: EvalReport) -> None:
    assert _ids(report.thresholds_review) == [
        "span_match_born_digital",
        "span_match_ocr",
        "routing_confidence",
        # Two anomaly rows since part 09, and the pair is the point: the frozen
        # risk config still carries the uncalibrated placeholder, and the
        # calibrated number that actually governs lives in config/scoring/.
        "anomaly_default_v0",
        "anomaly_gold_v4_v1",
        "downgrade_rate_budget",
        "classifier_min_confidence",
    ]


def test_values_are_read_from_the_files_that_own_them(
    report: EvalReport, config: ConfigBundle
) -> None:
    """No number is restated here; a config change moves the section with it."""
    section = report.thresholds_review
    assert (
        _entry(section, "span_match_ocr")["value"]
        == config.extraction.match["ocr"].min_score
    )
    assert _entry(section, "routing_confidence")["value"] == 0.9
    assert (
        _entry(section, "downgrade_rate_budget")["value"]
        == config.risk.downgrade_rate_budget
    )
    assert config.classifier is not None
    assert (
        _entry(section, "classifier_min_confidence")["value"]
        == config.classifier.min_confidence
    )


def test_the_uncalibrated_ones_say_so(report: EvalReport) -> None:
    """Placeholders and measurements are both legitimate; confusing them is not."""
    section = report.thresholds_review
    uncalibrated = {
        str(entry["threshold_id"])
        for entry in section["thresholds"]
        if not entry["calibrated"]
    }
    assert uncalibrated == {
        "anomaly_default_v0",
        "downgrade_rate_budget",
        "classifier_min_confidence",
    }
    assert section["uncalibrated_count"] == 3
    assert "NO CALIBRATION" in str(
        _entry(section, "classifier_min_confidence")["provenance"]
    )


# --------------------------------------------------------------------------
# The measured operating point
# --------------------------------------------------------------------------


def test_the_ocr_operating_point_is_measured_on_this_run(report: EvalReport) -> None:
    operating = _operating(report.thresholds_review, "span_match_ocr")
    assert operating["observed_spans"] == 31
    assert operating["below_one"] == 6
    # The closest span sits well above the threshold, which is the finding: the
    # 0.86 bound is not doing work on this corpus.
    assert operating["margin_of_closest"] > 0.0
    assert all(step["spans_discarded"] == 0 for step in operating["sweep"].values())


def test_the_routing_operating_point_matches_what_the_table_saw(
    report: EvalReport,
) -> None:
    operating = _operating(report.thresholds_review, "routing_confidence")
    assert operating["items"] == len(report.items)
    assert operating["distribution"] == {"0.000": 5, "0.600": 2, "1.000": 94}
    assert operating["at_or_above"] == 94


def test_an_exact_policy_has_no_sweep(report: EvalReport) -> None:
    operating = _operating(report.thresholds_review, "span_match_born_digital")
    assert "sweep" not in operating
    assert operating["spans_matched_exactly"] == 57


def test_a_set_without_fuzzy_spans_says_so(config: ConfigBundle) -> None:
    section = threshold_review([], config=config, today=TODAY)
    assert _operating(section, "span_match_ocr")["observed_spans"] == 0


def test_a_config_without_a_classifier_lists_one_threshold_fewer(
    config: ConfigBundle,
) -> None:
    section = threshold_review([], config=replace(config, classifier=None), today=TODAY)
    assert "classifier_min_confidence" not in _ids(section)


# --------------------------------------------------------------------------
# The review date: informational, injectable, never a gate
# --------------------------------------------------------------------------


def test_the_review_date_comes_from_the_register(
    report: EvalReport, config: ConfigBundle
) -> None:
    section = report.thresholds_review
    assert section["review_due"] == "2026-11-30"
    assert section["review_register_version"] == "threshold_review_v1"
    assert section["overdue"] is False
    assert section["days_remaining"] == 110
    assert review_warning(section) is None


def test_an_overdue_review_produces_a_notice_and_nothing_else(
    config: ConfigBundle,
) -> None:
    section = threshold_review([], config=config, today=AFTER_REVIEW)
    assert section["overdue"] is True
    warning = review_warning(section)
    assert warning is not None
    assert "not a gate" in warning


def test_a_config_with_no_review_register_says_no_date_is_set(
    config: ConfigBundle,
) -> None:
    risk = config.risk.model_copy(update={"review_due": None})
    section = threshold_review(
        [], config=replace(config, risk=risk, review=None), today=TODAY
    )
    assert section["review_due"] is None
    assert section["days_remaining"] is None
    assert "no review date" in str(review_warning(section))


def test_an_unparseable_review_date_degrades_to_no_date(
    config: ConfigBundle,
) -> None:
    """``review_due`` is a free-form contract string; the report must survive it."""
    risk = config.risk.model_copy(update={"review_due": "irgendwann"})
    section = threshold_review([], config=replace(config, risk=risk), today=TODAY)
    assert section["overdue"] is False
    assert section["days_remaining"] is None


def test_an_overdue_review_does_not_fail_the_eval(
    tmp_path: Path, gold_v4_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The no-wall-clock rule protects gates; this notice is report prose."""
    exit_code = run_main(
        [
            "--gold",
            str(gold_v4_dir),
            "--report",
            str(tmp_path / "report.json"),
            "--today",
            AFTER_REVIEW.isoformat(),
        ]
    )
    assert exit_code == 0
    assert "NOTICE:" in capsys.readouterr().out


# --------------------------------------------------------------------------
# The classifier section
# --------------------------------------------------------------------------


def test_the_classifier_section_is_present_without_a_model(
    report: EvalReport,
) -> None:
    section = report.classifier
    assert section["configured"] is True
    assert section["enabled"] is False
    assert section["calibrated"] is False
    assert section["ran"] is False
    assert section["admitted_to_decisions"] is False
    assert "--classifier" in str(section["reason"])


def test_the_addressable_set_is_the_items_no_rule_catches(
    report: EvalReport,
) -> None:
    """Computed from the run, not from a naming convention in the item ids."""
    assert report.classifier["addressable_items"] == 5
    assert report.classifier["addressable_item_ids"] == [
        item.item_id for item in report.items if not item.rule_hit
    ]


def test_a_measured_section_separates_coverage_from_agreement(
    gold_v4_dir: Path, config: ConfigBundle
) -> None:
    """With the stub, so this runs everywhere; the numbers are not the point."""
    items = load_corpus(gold_v4_dir)[:12]
    observations = observe_corpus(items, config=config, embedder=HashingEmbedder())
    section = classifier_section(
        config=config,
        observations=observations,
        rule_less_item_ids=[],
        gold_dir=str(gold_v4_dir),
        today=TODAY,
    )
    assert section["ran"] is True
    assert section["coverage"]["note"].startswith("gold declares no expected unit")
    assert section["agreement"]["scorable_items"] > 0
    assert section["calibration"]["fitted"] is True


def test_items_the_corpus_cannot_place_are_never_scored_as_wrong() -> None:
    """An item gold says nobody can place has no answer to be right about."""
    observations = [
        ClassifierObservation(
            item_id="xx-0001",
            expected_unit_id=None,
            rule_hit=False,
            suggested_unit_id="Referat_312_Renten",
            raw_score=0.87,
            confidence=0.0,
            margin=0.01,
        )
    ]
    assert observations[0].scorable is False
    assert observations[0].to_dict()["agrees"] is None
    assert (
        fit_from_observations(
            observations, model_id="stub", gold_dir="corpus/gold/v4", today=TODAY
        )
        is None
    )


def test_a_section_with_no_classifier_configured_says_so(
    config: ConfigBundle,
) -> None:
    section = classifier_section(
        config=replace(config, classifier=None),
        observations=None,
        rule_less_item_ids=["xx-0001"],
        gold_dir="corpus/gold/v4",
        today=TODAY,
        reason="no config/classifier/",
    )
    assert section["configured"] is False
    assert section["model_id"] is None
    assert section["reason"] == "no config/classifier/"


def test_observing_without_a_classifier_config_observes_nothing(
    gold_v4_dir: Path, config: ConfigBundle
) -> None:
    observations = observe_corpus(
        load_corpus(gold_v4_dir)[:2],
        config=replace(config, classifier=None),
        embedder=HashingEmbedder(),
    )
    assert observations == []


# --------------------------------------------------------------------------
# The regression identity
# --------------------------------------------------------------------------


def test_the_new_sections_moved_no_gated_number(report: EvalReport) -> None:
    """Part 05's numbers, exactly, with the classifier disabled."""
    assert report.item_count == 101
    assert report.routing_accuracy == 1.0
    assert report.tier_accuracy == 1.0
    assert report.false_clear_rate == 0.0
    assert report.false_flag_rate == 0.0
    assert report.procedure_derivation["accuracy"] == 1.0
    assert report.span_verification["verified"] == 88
    assert report.structured_subset["invariant_held"] is True
    assert report.gate_passed
    assert report.redaction_gate_passed
    assert report.structured_subset_gate_passed


def test_item_results_carry_what_the_review_section_reads(
    report: EvalReport,
) -> None:
    """The section may not recompute evidence from a different reading."""
    for item in report.items:
        assert isinstance(item, ItemResult)
        assert item.rule_hit is (item.routing_confidence > 0.0)
        assert all(0.0 <= score <= 1.0 for score in item.match_scores)
