"""The two eval sections the text path added, and the gate one of them carries.

``span_verification`` is reported and never gated: it is a quality number about
extraction, and a gate on it would create pressure to lower the match threshold
until the number looked good.

``structured_subset`` IS gated, and it is the regression identity of this whole
part: the items with no prose in them are the frozen set the previous part gated
on, byte-identical, and anything the text path changed about them would be a
change to items that have no text in them. The required values are exact.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from engine.config_loader import ConfigBundle
from eval.harness import (
    STRUCTURED_INVARIANT,
    EvalReport,
    ItemResult,
    evaluate_corpus,
    load_corpus,
    span_verification_metrics,
    structured_subset_metrics,
)
from eval.run import main


@pytest.fixture(scope="module")
def report(gold_v4_dir: Path, config: ConfigBundle) -> EvalReport:
    return evaluate_corpus(
        load_corpus(gold_v4_dir), config=config, gold_dir=gold_v4_dir
    )


def result(
    item_id: str = "item",
    *,
    source_types: tuple[str, ...] = (),
    proposals: int = 0,
    verified: int = 0,
    discarded: int = 0,
    tier: int = 1,
    expected_tier: int = 1,
    unit: str | None = "u",
) -> ItemResult:
    return ItemResult(
        item_id=item_id,
        expected_unit_id="u",
        actual_unit_id=unit,
        expected_tier=expected_tier,
        actual_tier=tier,
        expected_gaps=[],
        actual_gaps=[],
        reason_kinds=[],
        expected_derivation_source="content",
        actual_derivation_source="content",
        expected_derived_procedure_id="altersrente",
        actual_derived_procedure_id="altersrente",
        source_types=source_types,
        proposals=proposals,
        verified=verified,
        discarded=discarded,
    )


# ------------------------------------------------- span verification ---


def test_the_section_counts_what_the_double_lock_did(report: EvalReport) -> None:
    section = report.span_verification
    assert section["text_items"] == 24
    assert section["structured_items"] == 77
    assert section["proposals"] == section["verified"] > 0
    assert section["discarded"] == 0
    assert section["verified_rate"] == 1.0
    assert section["failures"] == {}


def test_the_section_splits_by_source_type_because_the_rules_differ(
    report: EvalReport,
) -> None:
    """A discard rate that rose only on the scan channel is a scanner problem;
    one that rose on both is an extractor problem."""
    by_source = report.span_verification["by_source_type"]
    assert set(by_source) == {"born_digital", "ocr"}
    assert by_source["born_digital"]["items"] == 16
    assert by_source["ocr"]["items"] == 8
    for counts in by_source.values():
        assert counts["verified_rate"] == 1.0


def test_the_section_also_splits_by_procedure(report: EvalReport) -> None:
    by_procedure = report.span_verification["by_procedure"]
    assert set(by_procedure) == {
        "altersrente",
        "erwerbsminderungsrente",
        "statusfeststellung",
        "unknown",
    }
    assert by_procedure["altersrente"]["items"] == 8


def test_ocr_items_really_are_matched_fuzzily(report: EvalReport) -> None:
    """Otherwise the bounded-fuzzy half of the verifier would be untested by the
    corpus and only unit tests would stand behind it."""
    modes = report.span_verification["match_modes"]
    assert modes["fuzzy"] > 0
    assert modes["exact"] > 0
    assert modes["structured"] > 0


def test_a_corpus_without_prose_reports_no_span_section(
    gold_v3_dir: Path, config: ConfigBundle
) -> None:
    older = evaluate_corpus(
        load_corpus(gold_v3_dir), config=config, gold_dir=gold_v3_dir
    )
    assert older.span_verification["text_items"] == 0
    assert "n/a" in older.summary()


def test_discards_are_counted_and_rated() -> None:
    section = span_verification_metrics(
        [
            result(
                "a",
                source_types=("born_digital",),
                proposals=4,
                verified=3,
                discarded=1,
            ),
            result("b", source_types=("ocr",), proposals=2, verified=1, discarded=1),
            result("c"),
        ]
    )
    assert section["text_items"] == 2
    assert section["proposals"] == 6
    assert section["verified_rate"] == 4 / 6
    assert section["discard_rate"] == 1 / 3
    assert section["by_source_type"]["ocr"]["discard_rate"] == 0.5


# --------------------------------------------------- structured subset ---


def test_the_seventy_seven_form_items_score_exactly_what_they_always_did(
    report: EvalReport,
) -> None:
    subset = report.structured_subset
    assert subset["item_count"] == 77
    for name, required in STRUCTURED_INVARIANT.items():
        assert subset[name] == required, f"{name} moved"
    assert subset["invariant_held"] is True
    assert subset["broken"] == []
    assert subset["moved_items"] == []
    assert report.structured_subset_gate_passed is True


def test_the_subset_is_the_items_with_no_prose_not_a_naming_convention(
    report: EvalReport,
) -> None:
    """Computed from the envelope, so a v5 that renames its items cannot make
    the invariant quietly measure a different set."""
    text_ids = {item.item_id for item in report.items if item.is_text_item}
    assert len(text_ids) == 24
    assert all("email" in item_id or "scan" in item_id for item_id in text_ids)


def test_one_moved_form_item_breaks_the_gate_and_says_which() -> None:
    subset = structured_subset_metrics(
        [result("good"), result("moved", tier=2, expected_tier=1)]
    )
    assert subset["invariant_held"] is False
    assert subset["moved_items"] == ["moved"]
    assert any("tier_accuracy" in problem for problem in subset["broken"])
    assert any("false_flag_rate" in problem for problem in subset["broken"])


def test_a_corpus_of_nothing_but_letters_makes_no_such_claim() -> None:
    assert structured_subset_metrics([result(source_types=("ocr",))]) == {}
    report = EvalReport(
        generated_at=None,  # type: ignore[arg-type]
        gold_dir="x",
        item_count=0,
        routing_accuracy=1.0,
        tier_accuracy=1.0,
        false_clear_rate=0.0,
        false_flag_rate=0.0,
        gap_exact_match_rate=1.0,
        schema_version="0.1.0",
        decision_table_version="t",
        rules_version="r",
        taxonomy_version="x",
        thresholds_version="s",
        scorer_mode="log_only",
        items=[],
    )
    assert report.structured_subset_gate_passed is True


def test_the_broken_gate_is_visible_in_the_summary(report: EvalReport) -> None:
    assert "STRUCTURED SUBSET UNCHANGED" in report.summary()
    broken = replace(
        report,
        structured_subset={**report.structured_subset, "invariant_held": False},
    )
    assert broken.structured_subset_gate_passed is False
    assert "STRUCTURED SUBSET GATE FAILED" in broken.summary()


# ------------------------------------------------------------ the CLI ---


def test_the_cli_exits_zero_on_the_current_corpus(
    gold_v4_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["--gold", str(gold_v4_dir), "--report", str(tmp_path / "r.json")])
    assert exit_code == 0
    printed = capsys.readouterr().out
    assert "span verification" in printed
    assert "structured subset  HELD" in printed


def test_derivation_is_split_into_forms_and_letters(report: EvalReport) -> None:
    """'read off a form' and 'read out of a sentence' are different
    achievements, and part 05 is the first release where both exist."""
    shapes = report.procedure_derivation["by_shape"]
    assert shapes["form"]["labelled_items"] == 77
    assert shapes["letter"]["labelled_items"] == 24
    assert shapes["form"]["accuracy"] == shapes["letter"]["accuracy"] == 1.0
    # Sixteen of the 24 letters have their procedure ONLY in the prose.
    assert shapes["letter"]["by_source"]["content"] == 16
    assert "by shape {form 77/1.000, letter 24/1.000}" in report.summary()


def test_a_corpus_of_only_forms_has_only_the_form_row(
    gold_v3_dir: Path, config: ConfigBundle
) -> None:
    older = evaluate_corpus(
        load_corpus(gold_v3_dir), config=config, gold_dir=gold_v3_dir
    )
    assert set(older.procedure_derivation["by_shape"]) == {"form"}
