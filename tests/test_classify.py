"""The zero-shot unit classifier, exercised through the deterministic stub.

Nothing here loads a model. The stub is a hashed-n-gram embedder, so the
numbers are stable on every platform and the assertions are about SHAPE - is
the ranking total, does a failure degrade to None, does an uncalibrated
suggestion refuse to claim a confidence - never about a similarity a real model
would have to reproduce.
"""

from __future__ import annotations

import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine.config_loader import CalibrationBinSpec, CalibrationSpec, ConfigBundle
from engine.evidence import embedding
from engine.evidence.classify import (
    MAX_ITEM_CHARS,
    UNCALIBRATED_CONFIDENCE,
    Calibration,
    CalibrationBin,
    ClassifierSuggestion,
    Embedder,
    HashingEmbedder,
    UnitClassifier,
    UnitText,
    classifier_from_config,
    cosine,
    normalize,
    render_item_text,
    unit_texts,
)
from schemas.config import TaxonomyNode
from schemas.evidence import RoutingSource


def _units() -> tuple[UnitText, ...]:
    return (
        UnitText("Referat_312_Renten", "Referat 312 - Altersrenten. Regelaltersrente"),
        UnitText("Referat_320_Reha", "Referat 320 - Rehabilitation. Teilhabe"),
    )


def _calibration(model_id: str = "hashing-ngram-v1:dim96") -> Calibration:
    return Calibration(
        bins=(
            CalibrationBin(upper=0.2, confidence=0.1),
            CalibrationBin(upper=0.6, confidence=0.5),
            CalibrationBin(upper=1.0, confidence=0.95),
        ),
        calibrated_on="gold v4",
        model_id=model_id,
        fitted_at="2026-08-12",
        expected_calibration_error=0.04,
    )


# --------------------------------------------------------------------------
# The stub embedder
# --------------------------------------------------------------------------


def test_the_stub_is_deterministic_across_instances() -> None:
    """Two embedders, one vector: Python's salted hash() would fail this."""
    first = HashingEmbedder().embed_query("Antrag auf Regelaltersrente")
    second = HashingEmbedder().embed_query("Antrag auf Regelaltersrente")
    assert first == second


def test_the_stub_returns_unit_vectors() -> None:
    vector = HashingEmbedder().embed_query("Rehabilitation")
    assert cosine(vector, vector) == pytest.approx(1.0)


def test_the_stub_carries_enough_signal_to_rank() -> None:
    """Not a quality claim: only that shared wording beats unrelated wording."""
    embedder = HashingEmbedder()
    reha = embedder.embed_query("Antrag auf medizinische Rehabilitation")
    reha_unit = embedder.embed_documents(["Medizinische Rehabilitation und Teilhabe"])[
        0
    ]
    rente_unit = embedder.embed_documents(["Altersrenten, Rentenbeginn, Rentenart"])[0]
    assert cosine(reha, reha_unit) > cosine(reha, rente_unit)


def test_text_shorter_than_the_smallest_ngram_embeds_to_zero() -> None:
    """A degenerate input is a zero vector, and a zero vector scores 0.0."""
    embedder = HashingEmbedder(min_n=3, max_n=3)
    assert cosine(embedder.embed_query("ab"), embedder.embed_query("Rente")) == 0.0


@pytest.mark.parametrize("kwargs", [{"dim": 1}, {"min_n": 0}, {"min_n": 4, "max_n": 2}])
def test_the_stub_refuses_impossible_settings(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        HashingEmbedder(**kwargs)


def test_cosine_refuses_mismatched_dimensions() -> None:
    with pytest.raises(ValueError):
        cosine((1.0, 0.0), (1.0, 0.0, 0.0))


def test_normalize_leaves_the_zero_vector_alone() -> None:
    assert normalize((0.0, 0.0)) == (0.0, 0.0)


# --------------------------------------------------------------------------
# Unit texts from the taxonomy
# --------------------------------------------------------------------------


def test_unit_texts_are_name_plus_responsibilities(config: ConfigBundle) -> None:
    texts = {unit.unit_id: unit.text for unit in unit_texts(config.taxonomy.nodes)}
    assert "Referat 312" in texts["Referat_312_Renten"]
    assert "Regelaltersrente" in texts["Referat_312_Renten"]


def test_unit_texts_leave_the_source_provenance_out(config: ConfigBundle) -> None:
    """Every node's source ends in nearly the same sentence about placeholders.

    Embedding it would pull every unit toward every other one, which is the
    opposite of what a per-unit text is for.
    """
    for unit in unit_texts(config.taxonomy.nodes):
        assert "Platzhalter" not in unit.text


def test_unit_texts_skip_excluded_and_empty_nodes() -> None:
    nodes = [
        TaxonomyNode(unit_id="a", name="A", responsibilities=["Rente"], source="s"),
        TaxonomyNode(unit_id="b", name="B", responsibilities=[], source="s"),
        TaxonomyNode(unit_id="c", name="C", responsibilities=["  "], source="s"),
        TaxonomyNode(unit_id="d", name="D", responsibilities=["Reha"], source="s"),
    ]
    assert [unit.unit_id for unit in unit_texts(nodes, exclude_unit_ids={"d"})] == ["a"]


# --------------------------------------------------------------------------
# Item text rendering
# --------------------------------------------------------------------------


def test_a_letter_is_rendered_as_its_normalized_prose() -> None:
    text = render_item_text(
        {
            "text.normalized": "Sehr geehrte  Damen\nund Herren",
            "payload.antrag.rentenart": "regelaltersrente",
        }
    )
    assert text == "Sehr geehrte Damen und Herren"


def test_a_form_is_rendered_as_sorted_key_facts() -> None:
    text = render_item_text(
        {
            "payload.antrag.rentenart": " regelaltersrente ",
            "payload.antragsteller.geburtsdatum": "1959-04-17",
            "channel": "fit_connect",
        }
    )
    assert text == (
        "antrag.rentenart: regelaltersrente | antragsteller.geburtsdatum: 1959-04-17"
    )


def test_sealed_values_never_reach_the_embedder() -> None:
    """A placeholder is a random token; embedding it would embed the draw."""
    text = render_item_text(
        {
            "payload.antrag.rentenart": "regelaltersrente",
            "payload.antragsteller.versicherungsnummer": "[[PII|VSNR|BCDFGHJKMNPQ]]",
        }
    )
    assert text == "antrag.rentenart: regelaltersrente"


def test_a_placeholder_in_prose_is_masked_out() -> None:
    """The defect the first real-model run found, pinned.

    Two runs of the same corpus disagreed by one item because two Referate
    scored within 0.0003 and a different random token tipped the order. The
    item text may not depend on which token the seal drew.
    """
    first = render_item_text(
        {"text.normalized": "Antrag von [[PII|NAME|BCDFGHJKMNPQ]] auf Reha"}
    )
    second = render_item_text(
        {"text.normalized": "Antrag von [[PII|NAME|ZZZZ22334455]] auf Reha"}
    )
    assert first == second
    assert "PII" not in first
    assert first.startswith("Antrag von auf Reha") or first == "Antrag von auf Reha"


def test_masking_survives_a_letter_with_several_sealed_spans() -> None:
    prose = (
        "Sehr geehrte Damen und Herren, ich, [[PII|NAME|BCDFGHJKMNPQ]], "
        "wohnhaft [[PII|ADDR|MNPQRSTVWXZ2]], beantrage eine medizinische "
        "Rehabilitation. Meine Versicherungsnummer lautet "
        "[[PII|VSNR|3456789BCDFG]]."
    )
    text = render_item_text({"text.normalized": prose})
    assert "[[" not in text
    assert "medizinische Rehabilitation" in text


def test_an_item_with_nothing_readable_renders_empty() -> None:
    assert render_item_text({"channel": "fit_connect"}) == ""


def test_prose_is_truncated_at_a_word_boundary() -> None:
    text = render_item_text({"text.normalized": "Rentenantrag " * 400})
    assert len(text) <= MAX_ITEM_CHARS
    assert not text.endswith("Rentenan")


def test_truncation_without_a_word_boundary_still_cuts() -> None:
    text = render_item_text({"text.normalized": "x" * (MAX_ITEM_CHARS + 50)})
    assert len(text) == MAX_ITEM_CHARS


# --------------------------------------------------------------------------
# Ranking and suggestion
# --------------------------------------------------------------------------


def test_the_ranking_is_total_and_best_first() -> None:
    classifier = UnitClassifier(_units(), HashingEmbedder())
    ranking = classifier.scores("Antrag auf Regelaltersrente")
    assert [unit_id for unit_id, _ in ranking] == [
        "Referat_312_Renten",
        "Referat_320_Reha",
    ]
    assert ranking[0][1] >= ranking[1][1]


def test_ties_break_on_unit_id_so_the_order_never_flips() -> None:
    """Two units with identical text must not swap places between runs."""
    units = (UnitText("zzz", "Rente"), UnitText("aaa", "Rente"))
    ranking = UnitClassifier(units, HashingEmbedder()).scores("Rente")
    assert [unit_id for unit_id, _ in ranking] == ["aaa", "zzz"]


def test_an_empty_item_produces_no_suggestion() -> None:
    classifier = UnitClassifier(_units(), HashingEmbedder())
    assert classifier.scores("   ") == ()
    assert classifier.suggest("   ") is None


def test_an_empty_taxonomy_produces_no_suggestion() -> None:
    classifier = UnitClassifier((), HashingEmbedder())
    assert classifier.unit_ids == ()
    assert classifier.suggest("Antrag auf Regelaltersrente") is None


def test_a_model_that_raises_degrades_to_no_suggestion() -> None:
    """The defensive posture: a classifier failure is today's behaviour."""

    class Exploding(HashingEmbedder):
        def embed_query(self, text: str) -> tuple[float, ...]:
            raise RuntimeError("the model died mid-batch")

    classifier = UnitClassifier(_units(), Exploding())
    assert classifier.suggest("Antrag auf Regelaltersrente") is None


def test_an_uncalibrated_suggestion_claims_no_confidence() -> None:
    classifier = UnitClassifier(_units(), HashingEmbedder(), min_confidence=0.9)
    suggestion = classifier.suggest("Antrag auf Regelaltersrente")
    assert suggestion is not None
    assert suggestion.calibrated is False
    assert suggestion.confidence == UNCALIBRATED_CONFIDENCE
    assert suggestion.raw_score > 0.0
    # The minimum is a statement about calibrated confidence, so it may not be
    # applied to a raw cosine - the suggestion survives and the log says so.
    assert classifier.calibration is None


def test_a_calibrated_suggestion_carries_the_mapped_confidence() -> None:
    classifier = UnitClassifier(
        _units(), HashingEmbedder(), calibration=_calibration(), min_confidence=0.0
    )
    suggestion = classifier.suggest("Antrag auf Regelaltersrente")
    assert suggestion is not None
    assert suggestion.calibrated is True
    assert suggestion.confidence in {0.1, 0.5, 0.95}


def test_a_calibrated_confidence_below_the_minimum_is_refused() -> None:
    classifier = UnitClassifier(
        _units(), HashingEmbedder(), calibration=_calibration(), min_confidence=0.99
    )
    assert classifier.suggest("Antrag auf Regelaltersrente") is None


def test_a_single_unit_taxonomy_has_a_margin_against_nothing() -> None:
    classifier = UnitClassifier((UnitText("only", "Rente"),), HashingEmbedder())
    suggestion = classifier.suggest("Rentenantrag")
    assert suggestion is not None
    assert suggestion.margin == pytest.approx(suggestion.raw_score)


# --------------------------------------------------------------------------
# Calibration application
# --------------------------------------------------------------------------


def test_calibration_is_a_step_function_over_sorted_bins() -> None:
    calibration = _calibration()
    assert calibration.apply(0.0) == 0.1
    assert calibration.apply(0.2) == 0.1
    assert calibration.apply(0.21) == 0.5
    assert calibration.apply(1.0) == 0.95


def test_calibration_above_the_last_bin_keeps_the_last_confidence() -> None:
    assert _calibration().apply(2.0) == 0.95


def test_an_empty_calibration_maps_everything_to_uncalibrated() -> None:
    empty = Calibration(
        bins=(), calibrated_on="none", model_id="m", fitted_at="2026-08-12"
    )
    assert empty.apply(0.9) == UNCALIBRATED_CONFIDENCE


@given(
    raw=st.floats(min_value=-1.0, max_value=1.0),
    step=st.floats(min_value=0.0, max_value=2.0),
)
def test_a_higher_raw_score_never_buys_less_confidence(raw: float, step: float) -> None:
    """Monotonicity, as a property: the map an agency reads cannot invert."""
    calibration = _calibration()
    assert calibration.apply(raw + step) >= calibration.apply(raw)


# --------------------------------------------------------------------------
# The contract shape
# --------------------------------------------------------------------------


def test_a_suggestion_renders_as_a_classifier_routing_suggestion() -> None:
    classifier = UnitClassifier(_units(), HashingEmbedder(), calibration=_calibration())
    suggestion = classifier.suggest("Antrag auf Regelaltersrente")
    assert suggestion is not None
    contract = suggestion.as_routing_suggestion()
    assert contract.source is RoutingSource.CLASSIFIER
    assert contract.rule_ids == []
    assert contract.evidence_span is None
    assert contract.unit_id == suggestion.unit_id


def test_a_suggestion_payload_names_everything_needed_to_disbelieve_it() -> None:
    classifier = UnitClassifier(_units(), HashingEmbedder(), calibration=_calibration())
    suggestion = classifier.suggest("Antrag auf Regelaltersrente")
    assert isinstance(suggestion, ClassifierSuggestion)
    payload = suggestion.as_payload()
    assert set(payload) == {
        "unit_id",
        "raw_score",
        "confidence",
        "margin",
        "model_id",
        "calibrated",
        "ranking",
    }
    assert len(payload["ranking"]) == 2  # type: ignore[arg-type]


def test_the_calibration_payload_carries_its_provenance() -> None:
    payload = _calibration().as_payload()
    assert payload["calibrated_on"] == "gold v4"
    assert payload["model_id"] == "hashing-ngram-v1:dim96"
    assert payload["fitted_at"] == "2026-08-12"


# --------------------------------------------------------------------------
# Building one from the shipped config
# --------------------------------------------------------------------------


def test_the_shipped_config_builds_a_classifier_over_the_leaf_referate(
    config: ConfigBundle,
) -> None:
    classifier = classifier_from_config(
        config.classifier, config.taxonomy.nodes, HashingEmbedder()
    )
    assert classifier is not None
    # The two Geschaeftsbereiche and the catch-all are excluded by config: their
    # texts describe supervision and the absence of a match, not work.
    assert "GB_Rehabilitation" not in classifier.unit_ids
    assert "Referat_390_Sonstiges" not in classifier.unit_ids
    assert "Referat_320_Reha" in classifier.unit_ids
    assert classifier.calibration is None


def test_without_an_embedder_there_is_no_classifier(config: ConfigBundle) -> None:
    """The gate's state: the model is never auto-loaded, so nothing is built."""
    assert (
        classifier_from_config(config.classifier, config.taxonomy.nodes, None) is None
    )


def test_without_a_classifier_config_there_is_no_classifier(
    config: ConfigBundle,
) -> None:
    assert (
        classifier_from_config(None, config.taxonomy.nodes, HashingEmbedder()) is None
    )


def test_a_taxonomy_with_no_usable_node_builds_no_classifier(
    config: ConfigBundle,
) -> None:
    nodes = [TaxonomyNode(unit_id="a", name="A", responsibilities=[], source="s")]
    assert classifier_from_config(config.classifier, nodes, HashingEmbedder()) is None


def test_a_configured_calibration_reaches_the_classifier(config: ConfigBundle) -> None:
    assert config.classifier is not None
    settings = config.classifier.model_copy(
        update={
            "calibration": CalibrationSpec(
                calibrated_on="gold v4",
                model_id="intfloat/multilingual-e5-small",
                fitted_at="2026-08-12",
                expected_calibration_error=0.02,
                bins=[CalibrationBinSpec(upper=1.0, confidence=0.42)],
            )
        }
    )
    classifier = classifier_from_config(
        settings, config.taxonomy.nodes, HashingEmbedder()
    )
    assert classifier is not None
    assert classifier.calibration is not None
    assert classifier.calibration.calibrated_on == "gold v4"
    assert classifier.calibration.apply(0.5) == 0.42


# --------------------------------------------------------------------------
# The optional model-backed member
# --------------------------------------------------------------------------


def test_a_missing_extra_reads_as_unavailable_with_a_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The core install: no wheel, no traceback, an explanation instead."""
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    embedding.reset_cache()
    try:
        assert embedding.available("any-model") is False
        reason = embedding.unavailable_reason("any-model")
        assert reason is not None and "Error" in reason
    finally:
        embedding.reset_cache()


def test_availability_and_reason_never_disagree() -> None:
    """Whatever this machine has installed, the two answers are one answer."""
    embedding.reset_cache()
    try:
        assert embedding.available() is (embedding.unavailable_reason() is None)
    finally:
        embedding.reset_cache()


def test_the_adapter_normalizes_whatever_the_model_returns() -> None:
    """No model needed: the adapter is a shape, and the shape is testable."""

    class FakeModel:
        def __init__(self) -> None:
            self.seen: list[str] = []

        def encode(self, texts: list[str], **_: object) -> list[list[float]]:
            self.seen.extend(texts)
            return [[3.0, 4.0] for _ in texts]

    model = FakeModel()
    adapter = embedding.SentenceTransformerEmbedder(model, "fake/model")
    assert adapter.model_id == "fake/model"
    assert adapter.embed_query("Rente") == (0.6, 0.8)
    assert adapter.embed_documents(["Altersrenten"]) == ((0.6, 0.8),)
    assert model.seen == [
        f"{embedding.QUERY_PREFIX}Rente",
        f"{embedding.PASSAGE_PREFIX}Altersrenten",
    ]


def test_the_adapter_satisfies_the_embedder_protocol() -> None:
    """Static structural typing, asserted where a reader can see it."""
    adapter: Embedder = embedding.SentenceTransformerEmbedder(object(), "fake/model")
    stub: Embedder = HashingEmbedder()
    assert adapter.model_id != stub.model_id
