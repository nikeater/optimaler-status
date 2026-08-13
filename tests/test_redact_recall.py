"""P-7: the redaction recall gate over the seeded German-PII golden set."""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pytest

from corpus.pii_golden.build import (
    DEFAULT_SEED,
    BuildError,
    build_items,
    render_items,
    self_check,
    write_set,
)
from engine.redact import Kind, Profile
from engine.redact.detector import Detector
from engine.redact.ner import available as ner_available
from engine.redact.recall import (
    DETERMINISTIC_GATE_KINDS,
    NER_GATE_KINDS,
    Label,
    LabelledText,
    load_labelled_texts,
    measure,
    redaction_metrics,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PII_GOLDEN = REPO_ROOT / "corpus" / "pii_golden" / "items.yaml"


@pytest.fixture(scope="module")
def golden() -> tuple[LabelledText, ...]:
    return load_labelled_texts(PII_GOLDEN)


@pytest.fixture(scope="module")
def deterministic(golden: tuple[LabelledText, ...]) -> object:
    return measure(golden, detector=Detector(Profile.REDACT))


# ------------------------------------------------------------------- gates ---


def test_the_deterministic_recall_gate_is_green(
    golden: tuple[LabelledText, ...],
) -> None:
    """The gate P-7 asks for: full recall on every kind a regex can carry."""
    report = measure(golden, detector=Detector(Profile.REDACT))
    assert report.deterministic_gate_passed is True
    assert report.deterministic_recall == 1.0
    for kind in DETERMINISTIC_GATE_KINDS:
        metrics = report.by_kind[kind]
        assert metrics.label_count > 0, f"{kind.value} has no labelled example"
        assert metrics.recall == 1.0, f"{kind.value} recall {metrics.recall}"


def test_every_gated_kind_is_represented_in_the_set(
    golden: tuple[LabelledText, ...],
) -> None:
    labelled = {label.kind for item in golden for label in item.labels}
    assert labelled >= DETERMINISTIC_GATE_KINDS
    assert labelled >= NER_GATE_KINDS


def test_precision_is_reported_and_the_misses_are_inventoried(
    golden: tuple[LabelledText, ...],
) -> None:
    """Over-redaction costs utility, under-redaction costs a person's data."""
    report = measure(golden, detector=Detector(Profile.REDACT))
    document = report.to_dict()
    for kind, metrics in document["by_kind"].items():
        assert 0.0 <= metrics["precision"] <= 1.0, kind
    # Whatever is missed or over-detected is reported as kind and length only.
    for entry in document["misses"] + document["false_positives"]:
        assert set(entry) <= {"item_id", "kind", "length", "recognizer_id"}
    blob = json.dumps(document, ensure_ascii=False)
    for item in golden:
        for label in item.labels:
            assert item.text[label.start : label.end] not in blob


@pytest.mark.skipif(
    not ner_available(), reason="the optional [redact] extra is not installed"
)
def test_the_ner_gate_is_green_when_the_extra_is_installed(
    golden: tuple[LabelledText, ...],
) -> None:
    from engine.redact.detector import redact_detector

    report = measure(golden, detector=redact_detector())
    assert report.ner_installed is True
    assert report.ner_gate_passed is True
    for kind in NER_GATE_KINDS:
        assert report.by_kind[kind].recall == 1.0


def test_names_without_a_salutation_are_the_documented_deterministic_gap(
    golden: tuple[LabelledText, ...],
) -> None:
    """Exactly the finding P-7 records: a union, not one more regular expression."""
    report = measure(golden, detector=Detector(Profile.REDACT))
    assert report.by_kind[Kind.NAME].recall < 1.0
    assert all(item_id for item_id, label in report.misses)
    assert {label.kind for _, label in report.misses} == {Kind.NAME}


# ---------------------------------------------------------------- the set ---


def test_the_committed_set_matches_a_fresh_build() -> None:
    """A hand edit to items.yaml is a test failure, not a silent metric change."""
    assert PII_GOLDEN.read_text(encoding="utf-8") == render_items(
        build_items(DEFAULT_SEED), DEFAULT_SEED
    )


def test_the_builder_is_deterministic() -> None:
    assert [item.to_dict() for item in build_items(DEFAULT_SEED)] == [
        item.to_dict() for item in build_items(DEFAULT_SEED)
    ]


def test_a_different_seed_produces_a_different_set() -> None:
    assert build_items(DEFAULT_SEED) != build_items(DEFAULT_SEED + 1)


def test_labels_never_overlap_and_always_sit_inside_their_text(
    golden: tuple[LabelledText, ...],
) -> None:
    for item in golden:
        for label in item.labels:
            assert 0 <= label.start < label.end <= len(item.text)
        spans = sorted((label.start, label.end) for label in item.labels)
        for (_, end), (start, _) in pairwise(spans):
            assert end <= start, f"{item.item_id} has overlapping labels"


def test_the_hard_negatives_produce_nothing(golden: tuple[LabelledText, ...]) -> None:
    detector = Detector(Profile.REDACT)
    negatives = [item for item in golden if item.scenario == "hard_negative"]
    assert len(negatives) >= 10
    for item in negatives:
        assert item.labels == ()
        assert detector.scan(item.text) == (), item.text


def test_the_mistyped_versicherungsnummer_is_still_a_positive(
    golden: tuple[LabelledText, ...],
) -> None:
    """A typo does not make a number less identifying (recall-first profile)."""
    item = next(item for item in golden if item.scenario == "vsnr_mistyped")
    assert item.labels[0].kind is Kind.VSNR
    assert Detector(Profile.REDACT).scan(item.text)


def test_the_build_self_check_refuses_a_broken_set() -> None:
    """The generator is a pipeline of refusals, like the triage corpus builder."""
    items = [
        item for item in build_items(DEFAULT_SEED) if item.scenario != "hard_negative"
    ]
    broken = [
        *items,
        LabelledText(
            item_id="pii-9999",
            scenario="hard_negative",
            text="Versicherungsnummer 65170839J003 im Bestand.",
            labels=(),
        ),
    ]
    with pytest.raises(BuildError, match="hard negative produced"):
        self_check(broken)


def test_a_missing_gated_kind_fails_the_self_check() -> None:
    items = [
        item
        for item in build_items(DEFAULT_SEED)
        if not any(label.kind is Kind.IBAN for label in item.labels)
    ]
    with pytest.raises(BuildError, match="IBAN: no labelled example"):
        self_check(items)


def test_an_uncovered_label_fails_the_self_check() -> None:
    broken = [
        LabelledText(
            item_id="pii-0001",
            scenario="iban_de",
            text="Konto DE02120300000000202051.",
            labels=(Label(start=0, end=28, kind=Kind.IBAN),),
        ),
        *[
            item
            for item in build_items(DEFAULT_SEED)
            if not any(label.kind is Kind.IBAN for label in item.labels)
            and item.scenario != "hard_negative"
        ],
    ]
    with pytest.raises(BuildError, match=r"duplicate item ids|not covered"):
        self_check(broken)


def test_the_set_can_be_written_and_read_back(tmp_path: Path) -> None:
    items = build_items(DEFAULT_SEED)
    written = write_set(items, tmp_path, DEFAULT_SEED)
    assert [path.name for path in written] == ["items.yaml", "MANIFEST.yaml"]
    assert load_labelled_texts(tmp_path / "items.yaml") == tuple(items)
    manifest = (tmp_path / "MANIFEST.yaml").read_text(encoding="utf-8")
    assert "items_sha256" in manifest
    assert "labels_by_kind" in manifest


# ------------------------------------------------------- the report section ---


def test_the_eval_section_is_value_free_and_names_its_gates() -> None:
    section = redaction_metrics()
    assert section is not None
    assert section["deterministic_gate_passed"] is True
    assert section["ner_installed"] is ner_available()
    assert section["golden_set"] == "corpus/pii_golden/items.yaml"
    assert set(section["gated_kinds"]["ner_only"]) == {"NAME"}
    assert section["detector"]["profile"] == "redact"


def test_a_missing_golden_set_is_reported_as_absent_not_as_a_failure(
    tmp_path: Path,
) -> None:
    """The triage gates must not start failing because a corpus directory moved."""
    assert redaction_metrics(tmp_path / "gibtsnicht.yaml") is None


def test_an_empty_measurement_is_perfect_rather_than_zero() -> None:
    report = measure([], detector=Detector(Profile.REDACT))
    assert report.deterministic_recall == 1.0
    assert report.overall_recall == 1.0
    assert report.deterministic_gate_passed is True
    assert "redaction recall" in report.summary()


def test_a_malformed_golden_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "items.yaml"
    path.write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must contain a YAML mapping"):
        load_labelled_texts(path)


def test_a_kind_with_no_labels_and_no_detections_is_perfect_not_zero() -> None:
    """An empty denominator is agreement, not failure; the panel would lie otherwise."""
    report = measure(
        [LabelledText(item_id="x", scenario="hard_negative", text="Kein Inhalt.")],
        detector=Detector(Profile.REDACT),
    )
    assert report.by_kind == {}
    assert report.overall_recall == 1.0
