"""What the fallback classifier would do, measured and never gated.

Two different questions live in this section, and mixing them would produce a
number that answers neither:

* **Coverage on the items no rule catches.** These are the ones the classifier
  exists for - five in gold v4 - and they are exactly the items whose gold label
  says ``expected_unit_id: null``. There is therefore NO ground truth to be
  right about: the corpus says "a human decides where this goes", which is a
  statement about the world, not a gap in the labels. What can honestly be
  reported is what the classifier proposed and how confidently, so a reader can
  judge the proposals as proposals.
* **Agreement where a rule already fired.** On the other items the corpus does
  name a unit, so the classifier can be scored. It is NOT consulted there by
  the pipeline (rules first), and running it here is a measurement, not a
  behaviour: agreement is the only evidence available for whether the
  similarity means anything at all, and it is also the fit set the calibration
  is learned from.

The whole section is opt-in. ``python -m eval.run`` reports the state of the
classifier and the addressable set without loading a model; ``--classifier``
loads one and fills the rest in. That split is the part-04 precedent: a number
in a gated report may not depend on which wheels a machine happens to have.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from engine.config_loader import ConfigBundle
from engine.evidence import (
    Embedder,
    build_payload_context,
    classifier_from_config,
    render_item_text,
)
from engine.journal.store import InMemoryJournalStore
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore
from eval.calibration import (
    DEFAULT_BIN_COUNT,
    FittedCalibration,
    ScoredOutcome,
    fit_calibration,
)

if TYPE_CHECKING:  # pragma: no cover - the harness imports this module
    from eval.harness import GoldItem


@dataclass(frozen=True)
class ClassifierObservation:
    """What the classifier made of one gold item."""

    item_id: str
    expected_unit_id: str | None
    rule_hit: bool
    suggested_unit_id: str | None
    raw_score: float | None
    confidence: float | None
    margin: float | None

    @property
    def scorable(self) -> bool:
        """Whether the corpus declares a unit this suggestion can be judged by."""
        return self.expected_unit_id is not None

    @property
    def agrees(self) -> bool:
        return self.scorable and self.suggested_unit_id == self.expected_unit_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "expected_unit_id": self.expected_unit_id,
            "rule_hit": self.rule_hit,
            "suggested_unit_id": self.suggested_unit_id,
            "raw_score": None if self.raw_score is None else round(self.raw_score, 4),
            "confidence": self.confidence,
            "margin": None if self.margin is None else round(self.margin, 4),
            "agrees": self.agrees if self.scorable else None,
        }


def observe_corpus(
    items: Sequence[GoldItem], *, config: ConfigBundle, embedder: Embedder
) -> list[ClassifierObservation]:
    """Score every gold item with the classifier, rule hit or not.

    The pipeline is run without the embedder, so the observed ``rule_hit`` is
    the one the shipped system produces; the classifier is then applied
    separately to the same item text. Measuring what a fallback would have said
    about an item it never sees is the only way to find out whether the fallback
    is worth switching on.
    """
    classifier = classifier_from_config(
        config.classifier, config.taxonomy.nodes, embedder
    )
    if classifier is None:
        return []
    observations: list[ClassifierObservation] = []
    for item in items:
        result = run_pipeline(
            item.payload,
            config=config,
            journal=InMemoryJournalStore(),
            vault=InMemoryVaultStore(),
        )
        context = build_payload_context(result.envelope, result.text_layer)
        suggestion = classifier.suggest(render_item_text(context))
        observations.append(
            ClassifierObservation(
                item_id=item.item_id,
                expected_unit_id=item.labels.expected_unit_id,
                rule_hit=bool(result.routing.candidates),
                suggested_unit_id=None if suggestion is None else suggestion.unit_id,
                raw_score=None if suggestion is None else suggestion.raw_score,
                confidence=None if suggestion is None else suggestion.confidence,
                margin=None if suggestion is None else suggestion.margin,
            )
        )
    return observations


def classifier_section(
    *,
    config: ConfigBundle,
    observations: Sequence[ClassifierObservation] | None,
    rule_less_item_ids: Sequence[str],
    gold_dir: str,
    today: date,
    reason: str | None = None,
) -> dict[str, Any]:
    """The report section, with or without a model behind it."""
    settings = config.classifier
    section: dict[str, Any] = {
        "configured": settings is not None,
        "enabled": bool(settings is not None and settings.enabled),
        "calibrated": bool(settings is not None and settings.calibration is not None),
        "model_id": None if settings is None else settings.model_id,
        "min_confidence": None if settings is None else settings.min_confidence,
        "extra_installed": _extra_installed(),
        "ran": bool(observations),
        "addressable_items": len(rule_less_item_ids),
        "addressable_item_ids": list(rule_less_item_ids),
        "admitted_to_decisions": bool(settings is not None and settings.enabled),
        "note": (
            "log-only: suggestions ride the evidence record and the journal and "
            "are excluded from the decision plane (ADR-021)"
        ),
    }
    if not observations:
        section["reason"] = reason or (
            "no embedder was passed; python -m eval.run --classifier loads the "
            "configured model when the [classify] extra is installed"
        )
        return section
    section["reason"] = None
    section["coverage"] = _coverage(observations)
    section["agreement"] = _agreement(observations)
    section["calibration"] = _calibration(
        observations, model_id=section["model_id"], gold_dir=gold_dir, today=today
    )
    section["items"] = [
        observation.to_dict()
        for observation in observations
        if not observation.rule_hit or not observation.agrees
    ]
    return section


def fit_from_observations(
    observations: Sequence[ClassifierObservation],
    *,
    model_id: str,
    gold_dir: str,
    today: date,
    bin_count: int = DEFAULT_BIN_COUNT,
) -> FittedCalibration | None:
    """Fit a calibration on the scorable observations, or None.

    Only items whose gold label names a unit take part. An item the corpus
    says nobody can place has no answer to be right about, and counting it as
    "wrong" would teach the map that high scores are unreliable because of
    items the classifier was never asked about.
    """
    outcomes = [
        ScoredOutcome(raw_score=observation.raw_score, correct=observation.agrees)
        for observation in observations
        if observation.scorable and observation.raw_score is not None
    ]
    return fit_calibration(
        outcomes,
        calibrated_on=gold_dir,
        model_id=model_id,
        fitted_at=today.isoformat(),
        bin_count=bin_count,
    )


def _coverage(observations: Sequence[ClassifierObservation]) -> dict[str, Any]:
    """What was proposed for the items no rule caught."""
    rule_less = [item for item in observations if not item.rule_hit]
    suggested = [item for item in rule_less if item.suggested_unit_id is not None]
    units: dict[str, int] = {}
    for item in suggested:
        key = str(item.suggested_unit_id)
        units[key] = units.get(key, 0) + 1
    return {
        "rule_less_items": len(rule_less),
        "suggested": len(suggested),
        "rate": len(suggested) / len(rule_less) if rule_less else 0.0,
        "units": dict(sorted(units.items())),
        "proposals": [item.to_dict() for item in rule_less],
        "note": (
            "gold declares no expected unit for these items, so none of these "
            "proposals is scored as right or wrong; they are shown so a reader "
            "can judge them as proposals"
        ),
    }


def _agreement(observations: Sequence[ClassifierObservation]) -> dict[str, Any]:
    """How often the classifier lands on the unit the corpus names."""
    scorable = [item for item in observations if item.scorable]
    agreed = [item for item in scorable if item.agrees]
    top_scores = sorted(
        item.raw_score for item in scorable if item.raw_score is not None
    )
    return {
        "scorable_items": len(scorable),
        "agreed": len(agreed),
        "rate": len(agreed) / len(scorable) if scorable else 0.0,
        "mean_score_when_right": _mean(
            [item.raw_score for item in agreed if item.raw_score is not None]
        ),
        "mean_score_when_wrong": _mean(
            [
                item.raw_score
                for item in scorable
                if not item.agrees and item.raw_score is not None
            ]
        ),
        "score_range": (
            [round(top_scores[0], 4), round(top_scores[-1], 4)] if top_scores else []
        ),
        "note": (
            "measured on items a rule already routed, which the pipeline never "
            "asks the classifier about; it is the only ground truth available "
            "and it is the set the calibration is fitted on"
        ),
    }


def _calibration(
    observations: Sequence[ClassifierObservation],
    *,
    model_id: str | None,
    gold_dir: str,
    today: date,
) -> dict[str, Any]:
    """The fit this run would emit, as a curve and two error numbers."""
    fitted = fit_from_observations(
        observations, model_id=model_id or "unknown", gold_dir=gold_dir, today=today
    )
    if fitted is None:
        return {"fitted": False, "reason": "no scorable observation to fit on"}
    return {
        "fitted": True,
        "samples": fitted.sample_count,
        "positives": fitted.positive_count,
        "bin_counts": list(fitted.bin_counts),
        "expected_calibration_error": round(fitted.expected_calibration_error, 4),
        "raw_expected_calibration_error": round(
            fitted.raw_expected_calibration_error, 4
        ),
        "curve": [
            {"upper": round(entry.upper, 4), "confidence": round(entry.confidence, 4)}
            for entry in fitted.bins
        ],
        "note": (
            "raw_expected_calibration_error is the real measurement: the cosine "
            "read as if it were a probability. The fitted one is computed over "
            "the fit's own bins and is 0 unless enforcing monotonicity cost "
            "something - it is not a generalization claim. Paste the block from "
            "python -m eval.calibrate."
        ),
    }


def _extra_installed() -> bool:
    """Whether the ``[classify]`` wheel is present, without importing it.

    ``find_spec`` rather than an import: this runs on every ``eval.run``,
    including the gated one, and importing sentence-transformers would pull
    torch into a process that has no business loading it.
    """
    try:
        return importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):  # a broken or shadowed installation
        return False


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)
