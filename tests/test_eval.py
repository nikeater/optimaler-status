"""Eval harness: the numbers, the breakdowns and the false-clear gate."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from engine.config_loader import ConfigBundle
from eval.harness import (
    DEFAULT_GOLD_DIR,
    GoldItem,
    GoldLabels,
    ItemResult,
    breakdown_by_procedure,
    derivation_metrics,
    evaluate_corpus,
    gap_precision_recall,
    load_corpus,
)
from eval.run import main


def _result(
    item_id: str = "x",
    *,
    expected_gaps: list[str] | None = None,
    actual_gaps: list[str] | None = None,
    expected_tier: int = 2,
    actual_tier: int = 2,
    expected_unit_id: str | None = "Referat_312_Renten",
    actual_unit_id: str | None = "Referat_312_Renten",
    procedure_id: str = "altersrente",
    anomaly_expected: bool = False,
) -> ItemResult:
    return ItemResult(
        item_id=item_id,
        expected_unit_id=expected_unit_id,
        actual_unit_id=actual_unit_id,
        expected_tier=expected_tier,
        actual_tier=actual_tier,
        expected_gaps=expected_gaps or [],
        actual_gaps=actual_gaps or [],
        reason_kinds=["qualified"],
        procedure_id=procedure_id,
        anomaly_expected=anomaly_expected,
    )


def test_corpus_loads_with_its_labels(gold_dir: Path) -> None:
    items = load_corpus(gold_dir)
    assert [item.item_id for item in items] == [
        "s1-0001-altersrente-complete",
        "s1-0002-altersrente-missing-vsnr",
    ]
    assert items[1].labels.expected_tier == 2
    assert items[1].labels.expected_gaps[0].requirement_id == "versicherungsnummer"


def test_missing_labels_sidecar_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "item.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="missing labels sidecar"):
        load_corpus(tmp_path)


def test_empty_and_unknown_directories_are_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no corpus items"):
        load_corpus(tmp_path)
    with pytest.raises(FileNotFoundError, match="gold directory not found"):
        load_corpus(tmp_path / "nope")


def test_report_is_written_as_json(
    gold_v3_dir: Path, config: ConfigBundle, tmp_path: Path
) -> None:
    report = evaluate_corpus(
        load_corpus(gold_v3_dir), config=config, gold_dir=gold_v3_dir
    )
    path = report.write(tmp_path / "reports" / "latest.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["false_clear_rate"] == 0.0
    assert document["versions"]["decision_table"] == "table_v1"
    assert len(document["items"]) == report.item_count
    assert document["procedure_derivation"]["accuracy"] == 1.0
    assert "GATE PASSED" in report.summary()


def test_false_clear_is_detected_and_fails_the_gate() -> None:
    """The metric that governs everything: cleared what needed oversight."""
    result = ItemResult(
        item_id="x",
        expected_unit_id="Referat_312_Renten",
        actual_unit_id="Referat_312_Renten",
        expected_tier=2,
        actual_tier=1,
        expected_gaps=["versicherungsnummer"],
        actual_gaps=[],
        reason_kinds=["qualified"],
    )
    assert result.false_clear is True
    assert result.false_flag is False
    assert result.tier_correct is False
    assert result.gaps_correct is False


def test_false_flag_is_an_efficiency_number_not_a_gate() -> None:
    result = ItemResult(
        item_id="x",
        expected_unit_id="Referat_312_Renten",
        actual_unit_id="Referat_390_Sonstiges",
        expected_tier=1,
        actual_tier=3,
        expected_gaps=[],
        actual_gaps=[],
        reason_kinds=["defaulted"],
    )
    assert result.false_flag is True
    assert result.false_clear is False
    assert result.routing_correct is False


def test_cli_writes_a_report_and_exits_zero(
    tmp_path: Path, gold_v3_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report_path = tmp_path / "latest.json"
    exit_code = main(["--gold", str(gold_v3_dir), "--report", str(report_path)])
    assert exit_code == 0
    assert report_path.is_file()
    output = capsys.readouterr().out
    assert "tier accuracy      1.000" in output
    assert "derivation acc     1.000" in output
    assert "GATE PASSED" in output


# ------------------------------------------------- completeness precision/recall ---


def test_precision_and_recall_on_hand_built_confusion_cases() -> None:
    """Two gaps found, one invented, one missed: P = 2/3, R = 2/3."""
    results = [
        _result("a", expected_gaps=["rentenart"], actual_gaps=["rentenart"]),
        _result(
            "b",
            expected_gaps=["geburtsdatum", "rentenbeginn"],
            actual_gaps=["geburtsdatum", "versicherungsnummer"],
        ),
    ]
    precision, recall, f1 = gap_precision_recall(results)
    assert precision == pytest.approx(2 / 3)
    assert recall == pytest.approx(2 / 3)
    assert f1 == pytest.approx(2 / 3)


def test_a_missed_gap_costs_recall_a_spurious_one_costs_precision() -> None:
    missed = gap_precision_recall([_result(expected_gaps=["rentenart"])])
    assert missed == (1.0, 0.0, 0.0)
    spurious = gap_precision_recall([_result(actual_gaps=["rentenart"])])
    assert spurious == (0.0, 1.0, 0.0)


def test_no_gaps_anywhere_is_perfect_agreement_not_zero() -> None:
    assert gap_precision_recall([_result()]) == (1.0, 1.0, 1.0)
    assert gap_precision_recall([]) == (1.0, 1.0, 1.0)


# ------------------------------------------------------------- breakdowns ---


def test_per_procedure_breakdown_separates_the_procedures() -> None:
    results = [
        _result("a", procedure_id="altersrente", expected_tier=1, actual_tier=1),
        _result(
            "b",
            procedure_id="erwerbsminderungsrente",
            expected_tier=3,
            actual_tier=2,
            actual_unit_id="Referat_390_Sonstiges",
        ),
        _result("c", procedure_id="erwerbsminderungsrente"),
    ]
    breakdown = breakdown_by_procedure(results)
    assert set(breakdown) == {"altersrente", "erwerbsminderungsrente"}
    assert breakdown["altersrente"]["item_count"] == 1
    assert breakdown["altersrente"]["tier_accuracy"] == 1.0
    assert breakdown["erwerbsminderungsrente"]["item_count"] == 2
    assert breakdown["erwerbsminderungsrente"]["tier_accuracy"] == 0.5
    assert breakdown["erwerbsminderungsrente"]["routing_accuracy"] == 0.5
    assert breakdown["erwerbsminderungsrente"]["false_clear_rate"] == 0.0


def test_v3_corpus_metrics_and_gate(gold_v3_dir: Path, config: ConfigBundle) -> None:
    report = evaluate_corpus(
        load_corpus(gold_v3_dir), config=config, gold_dir=gold_v3_dir
    )
    assert 70 <= report.item_count <= 90
    assert report.false_clear_rate == 0.0, "the one gate: never clear what needs review"
    assert report.gate_passed is True
    assert report.gap_precision == 1.0
    assert report.gap_recall == 1.0
    assert set(report.by_procedure) == {
        "altersrente",
        "erwerbsminderungsrente",
        "statusfeststellung",
        "unknown",
    }
    assert report.paraphrase_counts == {"deterministic": report.item_count}


def test_v3_anomalous_subset_is_reported_separately(
    gold_v3_dir: Path, config: ConfigBundle
) -> None:
    """Anomalous items carry today's rule-based tier; that is the baseline."""
    report = evaluate_corpus(
        load_corpus(gold_v3_dir), config=config, gold_dir=gold_v3_dir
    )
    assert report.anomalous["item_count"] >= 5
    assert report.anomalous["tier_agreement"] == 1.0
    assert report.anomalous["false_clear_rate"] == 0.0
    assert report.anomalous["item_count"] < report.item_count


def test_declared_divergences_are_carried_but_never_excluded() -> None:
    """A documented mismatch is documented, not excused (ADR-011).

    v3 declares none - the Widerspruchs-Routing-Regel closed the last one - so
    the property is asserted on a hand-built result rather than on the corpus.
    A test that needed a live divergence would quietly stop testing anything the
    day the corpus got them all right, which is exactly the day it matters.
    """
    diverging = _result(
        item_id="declared",
        expected_unit_id="Widerspruchsstelle_360",
        actual_unit_id=None,
        expected_tier=3,
        actual_tier=3,
    )
    diverging = ItemResult(**{**diverging.__dict__, "known_divergence": ["unit"]})
    report_items = [diverging, _result(item_id="clean")]
    assert diverging.routing_correct is False
    # Counted, not filtered: the accuracy of a two-item set with one declared
    # unit divergence is 0.5, never 1.0.
    correct = sum(1 for item in report_items if item.routing_correct)
    assert correct / len(report_items) == 0.5


def test_the_current_corpus_declares_no_divergence(
    gold_v3_dir: Path, config: ConfigBundle
) -> None:
    """v3 is the first set that gets every item right, and says so."""
    report = evaluate_corpus(
        load_corpus(gold_v3_dir), config=config, gold_dir=gold_v3_dir
    )
    assert [item.item_id for item in report.items if item.known_divergence] == []
    assert report.routing_accuracy == 1.0
    assert report.tier_accuracy == 1.0


def test_the_default_gold_dir_is_the_current_frozen_set() -> None:
    assert DEFAULT_GOLD_DIR.as_posix() == "corpus/gold/v4"


def test_old_s1_sidecars_still_load(gold_dir: Path) -> None:
    """The part-01 sidecars have none of the new fields; they get defaults."""
    items = load_corpus(gold_dir)
    assert items[0].labels.anomaly_expected is False
    assert items[0].labels.paraphrase == "none"
    assert items[0].labels.procedure_id is None
    assert items[0].labels.derivation_source is None


# ------------------------------------------------- procedure derivation ---


def _derivation(
    item_id: str,
    *,
    expected_source: str | None,
    actual_source: str | None,
    expected_procedure: str | None = None,
    actual_procedure: str | None = None,
) -> ItemResult:
    return ItemResult(
        item_id=item_id,
        expected_unit_id=None,
        actual_unit_id=None,
        expected_tier=3,
        actual_tier=3,
        expected_gaps=[],
        actual_gaps=[],
        reason_kinds=["defaulted"],
        expected_derivation_source=expected_source,
        actual_derivation_source=actual_source,
        expected_derived_procedure_id=expected_procedure,
        actual_derived_procedure_id=actual_procedure,
    )


def test_derivation_needs_both_the_procedure_and_the_route() -> None:
    """Right answer by the wrong route is not the same achievement."""
    right = _derivation(
        "a",
        expected_source="content",
        actual_source="content",
        expected_procedure="altersrente",
        actual_procedure="altersrente",
    )
    wrong_route = _derivation(
        "b",
        expected_source="content",
        actual_source="hint",
        expected_procedure="altersrente",
        actual_procedure="altersrente",
    )
    wrong_procedure = _derivation(
        "c",
        expected_source="content",
        actual_source="content",
        expected_procedure="altersrente",
        actual_procedure="erwerbsminderungsrente",
    )
    assert right.derivation_correct is True
    assert wrong_route.derivation_correct is False
    assert wrong_procedure.derivation_correct is False


def test_unlabelled_items_are_skipped_not_scored_as_wrong() -> None:
    """Otherwise the metric would measure corpus age, not the engine."""
    metrics = derivation_metrics(
        [
            _derivation("a", expected_source="none", actual_source="none"),
            _derivation("b", expected_source=None, actual_source="hint"),
        ]
    )
    assert metrics["labelled_items"] == 1
    assert metrics["unlabelled_items"] == 1
    assert metrics["accuracy"] == 1.0


def test_derivation_metrics_break_down_by_source_and_list_mismatches() -> None:
    metrics = derivation_metrics(
        [
            _derivation(
                "a",
                expected_source="hint",
                actual_source="hint",
                expected_procedure="altersrente",
                actual_procedure="altersrente",
            ),
            _derivation("b", expected_source="content", actual_source="none"),
            _derivation("c", expected_source="none", actual_source="none"),
        ]
    )
    assert metrics["accuracy"] == pytest.approx(2 / 3)
    assert metrics["accuracy_by_source"] == {"content": 0.0, "hint": 1.0, "none": 1.0}
    assert metrics["items_by_source"] == {"content": 1, "hint": 1, "none": 1}
    assert metrics["confusion"]["content"] == {"none": 1}
    assert [entry["item_id"] for entry in metrics["mismatches"]] == ["b"]


def test_v3_reports_derivation_for_every_item(
    gold_v3_dir: Path, config: ConfigBundle
) -> None:
    report = evaluate_corpus(
        load_corpus(gold_v3_dir), config=config, gold_dir=gold_v3_dir
    )
    metrics = report.procedure_derivation
    assert metrics["labelled_items"] == report.item_count
    assert metrics["unlabelled_items"] == 0
    assert set(metrics["items_by_source"]) == {"hint", "content", "none"}
    assert metrics["accuracy"] == 1.0, metrics["mismatches"]


def test_the_unknown_procedure_subset_routes_better_than_the_part_02_baseline(
    gold_v3_dir: Path, config: ConfigBundle
) -> None:
    """Part 02 left this at 0.600; content-based derivation is what moves it."""
    report = evaluate_corpus(
        load_corpus(gold_v3_dir), config=config, gold_dir=gold_v3_dir
    )
    assert report.by_procedure["unknown"]["routing_accuracy"] > 0.600


def test_the_report_counts_the_notifications_the_corpus_produced(
    config: ConfigBundle, gold_v1_dir: Path
) -> None:
    """Part 07: every item gets a receipt, and the routed ones get a status too."""
    report = evaluate_corpus(
        load_corpus(gold_v1_dir), config=config, gold_dir=gold_v1_dir
    )
    section = report.notifications
    assert section["configured"] is True
    assert section["version"] == "notifications_v1"
    assert section["items_notified"] == report.item_count
    assert section["coverage"] == 1.0
    # One receipt per item, one status update per item that was routed anywhere.
    routed = sum(1 for item in report.items if item.actual_unit_id is not None)
    assert section["by_template"] == {
        "eingangsbestaetigung_v1": report.item_count,
        "zuordnung_v1": routed,
    }
    assert section["by_trigger"]["received"]["count"] == report.item_count
    assert section["notification_count"] == report.item_count + routed
    assert "notifications" in report.to_dict()
    assert "notifications" in report.summary()


def test_the_notification_section_moves_no_gated_number(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """The worker reads the journal and appends to it; it decides nothing.

    On the CURRENT gold set, because that is the set the gates are quoted from:
    the whole claim of part 07 is that it added sections and moved nothing, and
    a superseded corpus could not carry that claim.
    """
    report = evaluate_corpus(
        load_corpus(gold_v4_dir), config=config, gold_dir=gold_v4_dir
    )
    assert report.notifications["configured"] is True
    assert report.false_clear_rate == 0.0
    assert report.routing_accuracy == 1.0
    assert report.tier_accuracy == 1.0
    assert report.gate_passed is True
    assert report.redaction_gate_passed is True
    assert report.structured_subset_gate_passed is True


def test_an_agency_without_notifications_reports_that_rather_than_zero(
    config: ConfigBundle, gold_v1_dir: Path
) -> None:
    from dataclasses import replace as dc_replace

    from eval.harness import notification_metrics

    silent = dc_replace(config, notifications=None)
    assert notification_metrics([], config=silent, item_count=3, notified=0) == {
        "configured": False
    }


def test_the_drafting_section_counts_letters_and_asserts_zero_unresolved(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """Part 08: the drafting section, on three items with three outcomes.

    Reported and never gated - the round-trip property in
    ``tests/test_draft_rehydrate.py`` is the gate - but the corpus saying the
    same thing over every letter it produced is worth having next to it.
    """
    items = [
        _gold_item(gold_v4_dir, "ar-0001-regelaltersrente-vollstaendig"),
        _gold_item(gold_v4_dir, "ar-0014-ohne-vsnr-und-rentenbeginn"),
        _gold_item(gold_v4_dir, "sf-0001-it-beratung-vollstaendig"),
    ]
    report = evaluate_corpus(items, config=config, gold_dir=gold_v4_dir)
    section = report.drafting
    assert section["configured"] is True
    assert section["version"] == "drafting_v1"
    assert section["draft_count"] == 2
    assert section["by_kind"] == {"nachforderung": 1, "prepared_decision": 1}
    assert section["no_draft_items"] == 1  # the tier-3 item, by design
    assert section["blocked"] == 0
    assert section["unresolved_tokens"] == 0
    assert section["dispatched"] == 0
    assert section["tokens"]["resolved"] == 7
    assert section["amtsermittlung_softened"] == 1
    assert report.to_dict()["drafting"] == section
    assert "drafting" in report.summary()


def test_an_agency_without_drafting_config_reports_it(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    items = [_gold_item(gold_v4_dir, "ar-0014-ohne-vsnr-und-rentenbeginn")]
    report = evaluate_corpus(
        items, config=replace(config, drafting=None), gold_dir=gold_v4_dir
    )
    assert report.drafting == {"configured": False}
    assert "prepares no drafts" in report.summary()


def _gold_item(gold_dir: Path, item_id: str) -> GoldItem:
    path = gold_dir / f"{item_id}.json"
    labels = GoldLabels.model_validate(
        yaml.safe_load(
            (gold_dir / f"{item_id}.labels.yaml").read_text(encoding="utf-8")
        )
    )
    return GoldItem(
        item_id=item_id,
        payload=json.loads(path.read_text(encoding="utf-8")),
        labels=labels,
        path=path,
    )
