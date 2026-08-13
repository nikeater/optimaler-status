"""The classifier inside the pipeline: log-only by default, fallback always.

Every test here drives the deterministic stub embedder. What is asserted is the
WIRING - when the classifier runs, where its suggestion lands, and what it is
allowed to move - never a similarity value.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from engine.config_loader import (
    CalibrationBinSpec,
    CalibrationSpec,
    ConfigBundle,
)
from engine.evidence import HashingEmbedder
from engine.evidence.classify import Vector
from engine.journal import InMemoryJournalStore
from engine.pipeline import run_pipeline
from schemas.common import Tier
from schemas.events import EventType
from schemas.evidence import RoutingSource
from tests.factories import FIXED_NOW

#: An item no routing rule catches: a Grundsicherungs-Anfrage that belongs to a
#: different Traeger entirely. Gold v4 expects unit None and tier 3.
UNROUTED_ITEM = "xx-0001-grundsicherung-anfrage.json"

#: A rule-less item that IS a letter: its prose carries sealed identity spans,
#: so its bytes differ on every run.
UNROUTED_LETTER = "xx-0061-email-terminanfrage.json"

#: An item a rule catches on the channel hint.
ROUTED_ITEM = "ar-0001-regelaltersrente-vollstaendig.json"


def _payload(gold_dir: Path, name: str) -> dict[str, Any]:
    return json.loads((gold_dir / name).read_text(encoding="utf-8"))


def _assembled(journal: InMemoryJournalStore) -> dict[str, Any]:
    for case_id in journal.case_ids():
        for event in journal.read(case_id):
            if event.type is EventType.EVIDENCE_ASSEMBLED:
                return dict(event.payload)
    raise AssertionError("no evidence_assembled event")


def _enabled(config: ConfigBundle) -> ConfigBundle:
    """The same config with the classifier switched on and calibrated.

    The calibration is fitted on the STUB, which is the honest way to test the
    enabled path without a model: the loader's own rule is that a calibration
    names the model it was fitted on, and this one does.
    """
    assert config.classifier is not None
    settings = config.classifier.model_copy(
        update={
            "enabled": True,
            "model_id": HashingEmbedder().model_id,
            "min_confidence": 0.0,
            "calibration": CalibrationSpec(
                calibrated_on="stub fixture, not a gold set",
                model_id=HashingEmbedder().model_id,
                fitted_at="2026-08-12",
                bins=[CalibrationBinSpec(upper=1.0, confidence=0.5)],
            ),
        }
    )
    return replace(config, classifier=settings)


# --------------------------------------------------------------------------
# The shipped state
# --------------------------------------------------------------------------


def test_without_an_embedder_nothing_classifies(
    gold_v4_dir: Path, config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    """The gate's state: the model is never auto-loaded, so nothing changes."""
    result = run_pipeline(
        _payload(gold_v4_dir, UNROUTED_ITEM),
        config=config,
        journal=journal,
        now=FIXED_NOW,
    )
    assert result.classifier is None
    assert result.evidence.routing == []
    assert result.decision.routed_unit_id is None
    assert _assembled(journal)["classifier"] is None


def test_an_unrouted_item_gets_a_logged_suggestion_that_moves_nothing(
    gold_v4_dir: Path, config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    result = run_pipeline(
        _payload(gold_v4_dir, UNROUTED_ITEM),
        config=config,
        journal=journal,
        now=FIXED_NOW,
        embedder=HashingEmbedder(),
    )
    assert result.classifier is not None
    # It IS evidence: on the record and in the journal, with its full ranking.
    assert [s.source for s in result.evidence.routing] == [RoutingSource.CLASSIFIER]
    payload = _assembled(journal)["classifier"]
    assert payload["unit_id"] == result.classifier.unit_id
    assert payload["calibrated"] is False
    assert len(payload["ranking"]) == len(config.taxonomy.nodes) - 3  # three excluded
    # And it governs NOTHING: same tier, same (absent) addressee as before.
    assert result.decision.routed_unit_id is None
    assert result.decision.tier is Tier.FULL_HUMAN_REVIEW
    assert result.evidence.conflicts == []


def test_an_uncalibrated_suggestion_claims_no_confidence_on_the_record(
    gold_v4_dir: Path, config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    result = run_pipeline(
        _payload(gold_v4_dir, UNROUTED_ITEM),
        config=config,
        journal=journal,
        now=FIXED_NOW,
        embedder=HashingEmbedder(),
    )
    assert result.evidence.routing[0].confidence == 0.0
    assert result.classifier is not None
    assert result.classifier.raw_score > 0.0


def test_a_rule_hit_is_never_second_guessed(
    gold_v4_dir: Path, config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    """Rules first: the classifier is not consulted at all, not merely ignored."""
    result = run_pipeline(
        _payload(gold_v4_dir, ROUTED_ITEM),
        config=config,
        journal=journal,
        now=FIXED_NOW,
        embedder=HashingEmbedder(),
    )
    assert result.classifier is None
    assert {s.source for s in result.evidence.routing} == {RoutingSource.RULE}
    assert result.decision.routed_unit_id == "Referat_312_Renten"
    assert result.decision.tier is Tier.CLEAR_AND_COMPLETE


def test_a_model_failure_is_no_suggestion_and_not_an_outage(
    gold_v4_dir: Path, config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    class Exploding(HashingEmbedder):
        def embed_query(self, text: str) -> Vector:
            raise RuntimeError("CUDA out of memory")

    result = run_pipeline(
        _payload(gold_v4_dir, UNROUTED_ITEM),
        config=config,
        journal=journal,
        now=FIXED_NOW,
        embedder=Exploding(),
    )
    assert result.classifier is None
    assert result.decision.tier is Tier.FULL_HUMAN_REVIEW
    assert result.decision.routed_unit_id is None


def test_a_model_that_dies_on_the_first_batch_is_also_no_suggestion(
    gold_v4_dir: Path, config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    """The unit texts are embedded in the constructor, before any query.

    A model that fails there would raise out of the pipeline over an optional
    suggestion, which is exactly what the defensive posture forbids.
    """

    class DeadOnArrival(HashingEmbedder):
        def embed_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
            raise RuntimeError("failed to allocate the model")

    result = run_pipeline(
        _payload(gold_v4_dir, UNROUTED_ITEM),
        config=config,
        journal=journal,
        now=FIXED_NOW,
        embedder=DeadOnArrival(),
    )
    assert result.classifier is None
    assert result.decision.tier is Tier.FULL_HUMAN_REVIEW


def test_a_config_without_a_classifier_block_never_classifies(
    gold_v4_dir: Path, config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    result = run_pipeline(
        _payload(gold_v4_dir, UNROUTED_ITEM),
        config=replace(config, classifier=None),
        journal=journal,
        now=FIXED_NOW,
        embedder=HashingEmbedder(),
    )
    assert result.classifier is None


# --------------------------------------------------------------------------
# The enabled state (a future agency decision)
# --------------------------------------------------------------------------


def test_enabling_the_classifier_moves_the_addressee_and_not_the_tier(
    gold_v4_dir: Path, config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    """What an agency buys by enabling it, stated exactly.

    The item leaves nobody's queue and enters a Referat's, marked tier 3. It
    does NOT become cheaper to handle: both table rows require
    ``routing.rule_hit``, and a classifier suggestion is not a rule hit, so a
    fallback-routed item still goes to a human.
    """
    result = run_pipeline(
        _payload(gold_v4_dir, UNROUTED_ITEM),
        config=_enabled(config),
        journal=journal,
        now=FIXED_NOW,
        embedder=HashingEmbedder(),
    )
    assert result.classifier is not None
    assert result.decision.routed_unit_id == result.classifier.unit_id
    assert result.decision.tier is Tier.FULL_HUMAN_REVIEW
    assert result.evidence.routing[0].confidence == 0.5


def test_enabling_it_still_leaves_a_routed_item_alone(
    gold_v4_dir: Path, config: ConfigBundle, journal: InMemoryJournalStore
) -> None:
    result = run_pipeline(
        _payload(gold_v4_dir, ROUTED_ITEM),
        config=_enabled(config),
        journal=journal,
        now=FIXED_NOW,
        embedder=HashingEmbedder(),
    )
    assert result.classifier is None
    assert result.decision.routed_unit_id == "Referat_312_Renten"
    assert result.decision.tier is Tier.CLEAR_AND_COMPLETE


def test_a_letter_ranks_the_same_across_runs_despite_random_seals(
    gold_v4_dir: Path, config: ConfigBundle
) -> None:
    """The determinism defect the first real-model run found, at pipeline level.

    Every run seals identity with freshly drawn placeholder tokens, so a letter's
    prose is literally different bytes each time. The ranking may not be.
    """
    payload = _payload(gold_v4_dir, UNROUTED_LETTER)
    rankings = [
        run_pipeline(
            payload,
            config=config,
            journal=InMemoryJournalStore(),
            now=FIXED_NOW,
            embedder=HashingEmbedder(),
        ).classifier
        for _ in range(3)
    ]
    assert all(entry is not None for entry in rankings)
    assert len({str(entry.ranking) for entry in rankings if entry is not None}) == 1


@pytest.mark.parametrize("item", [UNROUTED_ITEM, UNROUTED_LETTER, ROUTED_ITEM])
def test_the_run_is_reproducible_with_the_same_stub(
    item: str, gold_v4_dir: Path, config: ConfigBundle
) -> None:
    payload = _payload(gold_v4_dir, item)
    runs = [
        run_pipeline(
            payload,
            config=config,
            journal=InMemoryJournalStore(),
            now=FIXED_NOW,
            embedder=HashingEmbedder(),
        )
        for _ in range(2)
    ]
    assert (runs[0].classifier is None) == (runs[1].classifier is None)
    if runs[0].classifier is not None and runs[1].classifier is not None:
        assert runs[0].classifier.ranking == runs[1].classifier.ranking
