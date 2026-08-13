"""The opt-in live-model comparison and the P-16 swap harness.

Nothing here opens a socket. The transport is scripted, and the two scripts are
the two interesting models: one that reads the letter correctly and one that
answers confidently and wrongly. The second is the important one - it has to
score badly rather than crash, and none of its values may become evidence.

The reader below reconstructs the letter from the PROMPT, which is what a real
model sees: the normalized, already redacted text in numbered chunks. That makes
the mock faithful in the one way that matters - it can only know what the model
would know, so a passing test says the harness wires the pipeline correctly.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from corpus.generator.letter import FIELD_LABELS
from engine.config_loader import ConfigBundle
from eval.harness import GoldItem, load_corpus
from eval.live import (
    LiveReport,
    ModelResult,
    ModelUnderTest,
    compare_models,
    main,
    text_items,
)

CHUNK = re.compile(r"^\[(\d+)\] (.*)$", re.MULTILINE)


def letters(gold_v4_dir: Path) -> list[GoldItem]:
    """The three shortest letter items; enough to exercise every branch."""
    return text_items(load_corpus(gold_v4_dir))[:3]


def reconstruct(body: dict[str, Any]) -> str:
    """The normalized text, back out of the numbered chunks in the prompt."""
    rendered = str(body["messages"][1]["content"])
    return "".join(chunk for _, chunk in CHUNK.findall(rendered))


def requested_fields(body: dict[str, Any]) -> list[str]:
    rendered = str(body["messages"][1]["content"])
    return re.findall(r"^- ([a-z_]+):", rendered, re.MULTILINE)


def answer(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"choices": [{"message": {"content": json.dumps({"angaben": items})}}]}


def perfect_reader(url: str, body: dict[str, Any] | None, timeout: float) -> Any:
    """A model that reads the letter exactly right, labels and all."""
    if body is None:
        return {"data": [{"id": "scripted"}]}
    text = reconstruct(body)
    labels = {
        field_id: FIELD_LABELS[field_id]
        for field_id in requested_fields(body)
        if field_id in FIELD_LABELS
    }
    positions = sorted(
        (text.find(label), field_id, label)
        for field_id, label in labels.items()
        if text.find(label) >= 0
    )
    proposals: list[dict[str, Any]] = []
    for start, field_id, label in positions:
        value_start = start + len(label) + 1
        # A value runs to the next label a reader would recognise - any label,
        # not only the ones this prompt asked about - or to the closing formula.
        value = _until_next_label(text, value_start)
        proposals.append(
            {
                "field": field_id,
                "value": value,
                "quote": f"{label} {value}",
                "offset": start,
            }
        )
    return answer(proposals)


def _until_next_label(text: str, start: int) -> str:
    """The run of text from ``start`` to whatever a reader would stop at."""
    stops = [
        position
        for label in [*FIELD_LABELS.values(), "Mit freundlichen", "Absender:"]
        if (position := text.find(f" {label}", start)) >= 0
    ]
    return text[start : min(stops)].strip() if stops else text[start:].strip()


def confident_liar(url: str, body: dict[str, Any] | None, timeout: float) -> Any:
    """A model that invents a value and an offset, consistently."""
    if body is None:
        return {"data": []}
    return answer(
        [
            {
                "field": field_id,
                "value": "erfunden",
                "quote": "Rentenart: erfunden",
                "offset": 0,
            }
            for field_id in requested_fields(body)
        ]
    )


def unreachable(url: str, body: dict[str, Any] | None, timeout: float) -> Any:
    raise urllib.error.URLError("nothing is listening")


MODELS = (
    ModelUnderTest(label="scripted", base_url="http://localhost:11434", model="mock"),
)


# ----------------------------------------------------------- the parser ---


def test_a_model_is_configured_as_label_url_model() -> None:
    parsed = ModelUnderTest.parse("ollama=http://localhost:11434,mistral-small")
    assert parsed.label == "ollama"
    assert parsed.settings.extractor_id == "llm:mistral-small"


@pytest.mark.parametrize(
    "specification", ["", "nolabel", "label=onlyurl", "=http://x,y", "label=,model"]
)
def test_a_malformed_model_specification_is_refused(specification: str) -> None:
    with pytest.raises(ValueError, match="label=base_url,model"):
        ModelUnderTest.parse(specification)


# ------------------------------------------------------ the comparison ---


def test_a_model_that_reads_the_letter_scores_well(
    gold_v4_dir: Path, config: ConfigBundle
) -> None:
    report = compare_models(
        letters(gold_v4_dir),
        config=config,
        models=MODELS,
        transport=perfect_reader,
        gold_dir=gold_v4_dir,
    )
    (result,) = report.models
    assert result.reachable is True
    assert result.expected_fields > 0
    assert result.verified == result.proposals > 0, "every span it named held up"
    assert result.discarded == 0

    # It does NOT score 1.000, and the reason is a real limitation rather than a
    # bad mock: the prompt lists a field only if some procedure declares a
    # REQUIREMENT for it, because the requirement wording is the one definition
    # of what a field means (engine.extract.field_descriptions). `auslandsbezug`
    # is mapped but required by nobody, so no model is ever asked for it - and
    # the Altersrente clear-cut criteria read exactly that field. A live run can
    # therefore never reach tier 1 on its own. Documented in
    # docs/KNOWN-ERRORS.md; asserted here so it cannot quietly change.
    requirements = {
        item.requirement_id
        for procedure in config.procedures.values()
        for item in procedure.requirements.requirements
    }
    missed = {outcome.field for outcome in result.outcomes if not outcome.agrees}
    assert missed and missed.isdisjoint(requirements), missed
    assert all(
        outcome.agrees for outcome in result.outcomes if outcome.field in requirements
    )


def test_the_model_is_measured_without_the_corpus_sidecar(
    gold_v4_dir: Path, config: ConfigBundle
) -> None:
    """A live run that still carried the fixture would be measuring the
    generator's notes, not the model."""
    seen: list[dict[str, Any]] = []

    def recording(url: str, body: dict[str, Any] | None, timeout: float) -> Any:
        if body is not None:
            seen.append(body)
        return perfect_reader(url, body, timeout)

    report = compare_models(
        letters(gold_v4_dir),
        config=config,
        models=MODELS,
        transport=recording,
        gold_dir=gold_v4_dir,
    )
    assert seen, "the model was actually asked"
    assert report.models[0].proposals > 0


def test_a_confident_liar_scores_zero_and_produces_no_evidence(
    gold_v4_dir: Path, config: ConfigBundle
) -> None:
    """The whole reason a model may be called: it cannot bypass the verifier."""
    report = compare_models(
        letters(gold_v4_dir),
        config=config,
        models=MODELS,
        transport=confident_liar,
        gold_dir=gold_v4_dir,
    )
    (result,) = report.models
    row = result.to_dict()
    assert row["field_recall"] == 0.0
    assert row["spans_proposed"] > 0
    assert row["spans_verified"] == 0
    assert set(row["failures"]) <= {
        "quote_mismatch",
        "value_not_in_quote",
        "unknown_field",
        "duplicate_field",
        "offset_out_of_range",
    }
    assert row["disagreements"], "every field should be reported as a disagreement"


def test_an_unreachable_endpoint_is_reported_not_ignored(
    gold_v4_dir: Path, config: ConfigBundle
) -> None:
    report = compare_models(
        letters(gold_v4_dir),
        config=config,
        models=MODELS,
        transport=unreachable,
        gold_dir=gold_v4_dir,
    )
    (result,) = report.models
    assert result.reachable is False
    assert result.items == 0
    assert "NO" in report.summary()


def test_two_models_are_two_rows_which_is_the_whole_of_p16(
    gold_v4_dir: Path, config: ConfigBundle
) -> None:
    """Digital sovereignty, measured: the same comparison, pointed twice."""
    report = compare_models(
        letters(gold_v4_dir),
        config=config,
        models=(
            ModelUnderTest("a", "http://localhost:11434", "one"),
            ModelUnderTest("b", "http://localhost:11434", "two"),
        ),
        transport=perfect_reader,
        gold_dir=gold_v4_dir,
    )
    assert [model.label for model in report.models] == ["a", "b"]
    assert [model.model for model in report.models] == ["one", "two"]
    rendered = report.summary()
    assert "  a " in rendered and "  b " in rendered


def test_the_report_writes_json_and_says_it_is_not_a_gate(
    gold_v4_dir: Path, config: ConfigBundle, tmp_path: Path
) -> None:
    report = compare_models(
        letters(gold_v4_dir),
        config=config,
        models=MODELS,
        transport=perfect_reader,
        gold_dir=gold_v4_dir,
    )
    written = report.write(tmp_path / "live.json")
    document = json.loads(written.read_text(encoding="utf-8"))
    assert document["gated"] is False
    assert "never gated" in document["note"]
    assert document["models"][0]["label"] == "scripted"


def test_an_empty_comparison_still_renders() -> None:
    report = LiveReport(gold_dir="x", text_items=0, models=[])
    assert "text items         0" in report.summary()
    row = ModelResult("l", "m", "u", reachable=False).to_dict()
    assert row["field_recall"] == 0.0
    assert row["seconds_per_item"] == 0.0, "no items is not a division"


def test_the_row_carries_the_wall_clock_of_the_live_runs_only(
    gold_v4_dir: Path, config: ConfigBundle
) -> None:
    """Part 12: "usable on this machine" is partly a speed claim, so the harness
    has to produce the seconds rather than leave them to a stopwatch beside it.

    Only the LIVE half of each item is timed. The replay baseline the item is
    compared against runs in the same loop and would otherwise be charged to the
    model, which would make a slow harness look like a slow endpoint."""
    slept: list[float] = []

    def slow_reader(url: str, body: dict[str, Any] | None, timeout: float) -> Any:
        if body is not None:
            # A measurable, deterministic cost. Not a sleep: a test that waits
            # is a test that eventually flakes on a loaded machine.
            slept.append(time.perf_counter())
        return perfect_reader(url, body, timeout)

    report = compare_models(
        letters(gold_v4_dir),
        config=config,
        models=MODELS,
        transport=slow_reader,
        gold_dir=gold_v4_dir,
    )
    (result,) = report.models
    row = result.to_dict()
    assert slept, "the model was actually called"
    assert row["seconds_total"] > 0.0
    assert row["seconds_per_item"] == pytest.approx(
        row["seconds_total"] / result.items, abs=0.001
    )
    assert f"{row['seconds_per_item']:.2f}" in report.summary()


# ------------------------------------------------------------- the CLI ---


def test_the_cli_refuses_to_guess_an_endpoint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ADR-012's rule: no probe unless somebody configured one."""
    assert main([]) == 2
    assert "no endpoint configured" in capsys.readouterr().err


def test_the_cli_rejects_a_malformed_model(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--model", "broken"]) == 2
    assert "live comparison failed" in capsys.readouterr().err


def test_the_cli_reports_an_endpoint_that_is_not_there_and_still_exits_zero(
    gold_v4_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The state every GATE machine is in, and the state this workstation is in
    whenever Ollama is stopped (part 12 installed it; nothing requires it to be
    running). The harness has to ship and run anyway, or P-16 is a promise
    instead of a measurement."""
    exit_code = main(
        [
            "--model",
            "absent=http://127.0.0.1:1,none",
            "--gold",
            str(gold_v4_dir),
            "--report",
            str(tmp_path / "live.json"),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0, "measuring is not gating"
    assert "not reached: absent" in captured.err
    assert (
        "never gated"
        in json.loads((tmp_path / "live.json").read_text(encoding="utf-8"))["note"]
    )
