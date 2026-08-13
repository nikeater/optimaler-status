"""Every governing threshold in one place, with a measured operating point.

Backlog P-5, par. 88 Abs. 5 Nr. 4 AO analog. The question this section answers
is not "what are the thresholds" - anyone can read the YAML - but the three
that a reviewer actually needs and that no config file can answer:

* **Where did this number come from?** A measurement on a named gold set, or an
  uncalibrated placeholder somebody had to pick to make the system run. Both
  are legitimate; confusing them is not, so the provenance is printed next to
  the value and the placeholders say the word.
* **How close is the system to it?** A threshold nothing comes near is not
  governing anything, and a threshold half the corpus sits one thousandth away
  from is a coin flip with a decimal point. The operating point is measured on
  the frozen eval artifacts of this same run.
* **What would change if it moved one step?** The small sweep below is computed
  from the same per-item data, so the answer is "these many spans, these many
  items", not a guess.

Values are read from the files that own them. This module deliberately restates
NONE of them: a second copy in a review register would be a second definition,
and the two would disagree the first time one moved.

The review-date warning is INFORMATIONAL. It reads an injectable ``today`` and
never touches an exit code. The no-wall-clock rule protects gates from becoming
time-dependent; it does not forbid a report from telling a human that a date has
passed, which is the entire point of P-5.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from engine.config_loader import ConfigBundle
from schemas.common import SourceType

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, types only
    from eval.harness import ItemResult

#: How far the sweep looks in each direction. Two steps, because one step
#: answers "is this threshold on a cliff" and a wider sweep would invite
#: reading the table as a tuning aid rather than as a review aid.
SWEEP_STEPS = (-0.05, -0.01, 0.01, 0.05)

#: The confidence bound both decision-table rows use. Read off the table rather
#: than restated, so a config change moves this section with it.
ROUTING_CONFIDENCE_FIELD = "routing.confidence"


@dataclass(frozen=True)
class ThresholdEntry:
    """One governing number with everything a reviewer needs about it."""

    threshold_id: str
    value: float
    source: str
    source_version: str
    governs: str
    provenance: str
    calibrated: bool
    operating_point: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold_id": self.threshold_id,
            "value": self.value,
            "source": self.source,
            "source_version": self.source_version,
            "governs": self.governs,
            "provenance": self.provenance,
            "calibrated": self.calibrated,
            "operating_point": self.operating_point,
        }


def threshold_review(
    results: Sequence[ItemResult],
    *,
    config: ConfigBundle,
    today: date,
) -> dict[str, Any]:
    """The whole section: entries, the review date, and whether it has passed."""
    entries = [
        *_span_match_entries(results, config),
        _routing_confidence_entry(results, config),
        *_risk_entries(results, config),
    ]
    classifier_entry = _classifier_entry(config)
    if classifier_entry is not None:
        entries.append(classifier_entry)
    return {
        "review_due": config.risk.review_due,
        "review_register_version": (
            config.review.version if config.review is not None else None
        ),
        "today": today.isoformat(),
        "days_remaining": _days_remaining(config.risk.review_due, today),
        "overdue": _overdue(config.risk.review_due, today),
        "uncalibrated_count": sum(1 for entry in entries if not entry.calibrated),
        "thresholds": [entry.to_dict() for entry in entries],
    }


def review_warning(section: dict[str, Any]) -> str | None:
    """The one line a human should see, or None when there is nothing to say."""
    due = section.get("review_due")
    if not due:
        return (
            "no review date is set for the governing thresholds "
            "(config/review/ is absent); par. 88(5) Nr. 4 AO analog"
        )
    if section.get("days_remaining") is None:
        return (
            f"the configured review date {due!r} is not a readable ISO date, so "
            f"nothing can say whether the review is due. This is a notice, not "
            f"a gate."
        )
    if section.get("overdue"):
        return (
            f"threshold review was due {due} and has not been recorded "
            f"({abs(int(section['days_remaining']))} days ago). This is a "
            f"notice, not a gate."
        )
    return None


def _span_match_entries(
    results: Sequence[ItemResult], config: ConfigBundle
) -> list[ThresholdEntry]:
    """One entry per source type, because the two are different promises."""
    entries: list[ThresholdEntry] = []
    scores = sorted(score for item in results for score in item.match_scores)
    for source_type in sorted(SourceType, key=lambda item: item.value):
        policy = config.extraction.policy_for(source_type)
        if policy.mode == "exact":
            operating: dict[str, Any] = {
                "note": (
                    "an exact policy has no step to sweep: a span either stands "
                    "at the offset with the quoted characters or it does not"
                ),
                "spans_matched_exactly": sum(
                    item.match_modes.get("exact", 0) for item in results
                ),
            }
        else:
            operating = _fuzzy_operating_point(scores, policy.min_score)
        entries.append(
            ThresholdEntry(
                threshold_id=f"span_match_{source_type.value}",
                value=policy.min_score,
                source="config/extraction/extraction_v1.yaml",
                source_version=config.extraction.version,
                governs=(
                    f"whether a proposed span from {source_type.value} text may "
                    f"become an ExtractionRecord (ADR-020's double lock)"
                ),
                provenance=(
                    "exact by construction; the loader refuses an exact policy "
                    "below 1.0"
                    if policy.mode == "exact"
                    else "measured on gold v4's OCR letters (part 05); at 0.86 a "
                    "twelve-character quote may differ in one character, not two"
                ),
                calibrated=True,
                operating_point=operating,
            )
        )
    return entries


def _fuzzy_operating_point(scores: Sequence[float], threshold: float) -> dict[str, Any]:
    """How the observed fuzzy scores sit around the threshold, and the sweep."""
    if not scores:
        return {"observed_spans": 0, "note": "no fuzzy-matched span in this set"}
    return {
        "observed_spans": len(scores),
        "min": round(scores[0], 4),
        "median": round(scores[len(scores) // 2], 4),
        "below_one": sum(1 for score in scores if score < 1.0),
        "margin_of_closest": round(scores[0] - threshold, 4),
        "sweep": {
            _step_label(step): {
                "value": round(threshold + step, 4),
                "spans_discarded": sum(
                    1 for score in scores if score < threshold + step
                ),
            }
            for step in SWEEP_STEPS
        },
    }


def _routing_confidence_entry(
    results: Sequence[ItemResult], config: ConfigBundle
) -> ThresholdEntry:
    """The 0.9 bound both table rows use, read off the table itself."""
    bounds = [
        float(condition.value)
        for row in config.decision_table.rows
        for condition in row.when_all
        if condition.field == ROUTING_CONFIDENCE_FIELD
        and isinstance(condition.value, int | float)
    ]
    value = max(bounds) if bounds else 0.0
    observed = sorted(item.routing_confidence for item in results)
    return ThresholdEntry(
        threshold_id="routing_confidence",
        value=value,
        source="config/decision/table_v1.yaml",
        source_version=config.decision_table.version,
        governs=(
            "whether an item may qualify for tier 1 or tier 2 at all; both rows "
            "carry the same bound because 'is the routing trustworthy' has one "
            "answer, not one per tier"
        ),
        provenance=(
            "ADR-014: set above the contested-conflict confidence 0.6, so an "
            "item two equal-priority rules disagree about cannot clear it"
        ),
        calibrated=True,
        operating_point={
            "items": len(observed),
            "distribution": _histogram(observed),
            "at_or_above": sum(1 for score in observed if score >= value),
            "below": sum(1 for score in observed if score < value),
            "sweep": {
                _step_label(step): {
                    "value": round(value + step, 4),
                    "items_qualifying": sum(
                        1 for score in observed if score >= value + step
                    ),
                }
                for step in SWEEP_STEPS
            },
            "note": (
                "the distribution is discrete by construction - 1.0 for an "
                "uncontested rule hit, 0.6 for an unresolved conflict, 0.0 for "
                "no admitted suggestion - so a small step moves nothing and the "
                "sweep is expected to be flat"
            ),
        },
    )


def _risk_entries(
    results: Sequence[ItemResult], config: ConfigBundle
) -> list[ThresholdEntry]:
    """The anomaly thresholds and the downgrade budget, now with numbers.

    Two anomaly rows rather than one, and the difference between them is the
    whole point of the second id: ``anomaly_default_v0`` in the frozen
    ``thresholds.yaml`` is the uncalibrated placeholder part 01 had to pick to
    make the system run, and it governs nothing, because every
    ``AnomalyEvidence`` this build produces points its ``threshold_ref`` at the
    calibrated row from ``config/scoring/``. A register that showed one number
    would leave a reader guessing which.
    """
    scored = [item for item in results if item.anomaly_score is not None]
    governing = (
        config.scoring.threshold.threshold_id if config.scoring is not None else None
    )
    entries = [
        ThresholdEntry(
            threshold_id=threshold.threshold_id,
            value=threshold.value,
            source="config/thresholds.yaml",
            source_version=config.risk.version,
            governs=(
                "historical placeholder; superseded as the governing anomaly "
                "threshold by the calibrated row from config/scoring/, which is "
                "what AnomalyEvidence.threshold_ref names"
                if governing is not None
                else "the anomaly score at which the shadow scorer's downgrade "
                "rule fires; applied only in enforcing mode"
            ),
            provenance=threshold.calibrated_on,
            calibrated="uncalibrated" not in threshold.calibrated_on.lower(),
            operating_point={
                "items_scored": 0,
                "scorer_mode": config.risk.scorer_mode,
                "note": (
                    "governs no item in this run: the scorer references "
                    f"{governing!r}. This row is kept because the frozen "
                    "risk config still carries the number and a register that "
                    "hid it would hide a live config value"
                    if governing is not None
                    else "nothing produces anomaly evidence in this run"
                ),
            },
        )
        for threshold in config.risk.thresholds
    ]
    if config.scoring is not None:
        entries.append(_scoring_threshold_entry(scored, config))
    entries.append(_budget_entry(results, scored, config))
    return entries


def _scoring_threshold_entry(
    scored: Sequence[ItemResult], config: ConfigBundle
) -> ThresholdEntry:
    """The calibrated anomaly threshold, with the sweep it was chosen from."""
    scoring = config.scoring
    assert scoring is not None
    values = sorted(item.anomaly_score or 0.0 for item in scored)
    bound = scoring.threshold.value
    return ThresholdEntry(
        threshold_id=scoring.threshold.threshold_id,
        value=bound,
        source=f"config/scoring/{scoring.version}.yaml",
        source_version=scoring.version,
        governs=(
            "whether an item carries AnomalyEvidence.flagged, which is what "
            "the decision table's downgrade rows read; applied only in "
            "enforcing mode, and the frozen risk config says log_only"
        ),
        provenance=scoring.threshold.calibrated_on,
        calibrated=True,
        operating_point={
            "items_scored": len(scored),
            "feature_set_version": scoring.feature_set_version,
            "reference_id": scoring.reference_id,
            "flagged": sum(1 for value in values if value >= bound),
            "closest_below": _closest(values, bound, above=False),
            "closest_above": _closest(values, bound, above=True),
            "sweep": {
                _step_label(step): {
                    "value": round(bound + step, 4),
                    "items_flagged": sum(
                        1 for value in values if value >= bound + step
                    ),
                }
                for step in SWEEP_STEPS
            },
            "note": (
                "the score is a percentile of the reference population, so the "
                "bound reads as a workload decision; the full calibration table "
                "is python -m eval.score_fit --distribution"
            ),
        },
    )


def _budget_entry(
    results: Sequence[ItemResult], scored: Sequence[ItemResult], config: ConfigBundle
) -> ThresholdEntry:
    """The downgrade-rate budget, measured for the first time."""
    tier1 = [item for item in results if item.expected_tier == 1]
    tier1_false = [
        item for item in tier1 if item.anomaly_flagged and not item.anomaly_expected
    ]
    would_move = [
        item for item in scored if item.anomaly_flagged and item.pre_downgrade_tier < 3
    ]
    return ThresholdEntry(
        threshold_id="downgrade_rate_budget",
        value=config.risk.downgrade_rate_budget,
        source="config/thresholds.yaml",
        source_version=config.risk.version,
        governs=(
            "the share of tier-1-eligible items the scorer may downgrade "
            "before it must return to log-only"
        ),
        provenance=(
            "an efficiency budget chosen with the scorer's design (ADR-004), "
            "not a measurement. Measurable since part 09; still not a "
            "measurement, because the number below is bounded by how many "
            "anomalies a curated gold set contains"
        ),
        calibrated=False,
        operating_point={
            "tier1_eligible_items": len(tier1),
            "downgrades_applied": 0,
            "would_downgrade": len(would_move),
            "tier1_false_flags": len(tier1_false),
            "observed_rate": (
                round(len(tier1_false) / len(tier1), 6) if tier1 else 0.0
            ),
            "note": (
                "log-only: no downgrade was applied in this run. The observed "
                "rate counts tier-1 items the scorer marks that the corpus does "
                "NOT call anomalous; gold v4 deliberately over-represents "
                "anomalies, so this is an upper bound on a curated set rather "
                "than an estimate of an intake"
            ),
        },
    )


def _closest(values: Sequence[float], bound: float, *, above: bool) -> float | None:
    """The observed score nearest the bound on one side, or None."""
    side = [float(value) for value in values if (value >= bound) == above]
    if not side:
        return None
    nearest: float = min(side, key=lambda value: abs(value - bound))
    return round(nearest, 6)


def _classifier_entry(config: ConfigBundle) -> ThresholdEntry | None:
    """The classifier minimum, when a classifier exists at all."""
    settings = config.classifier
    if settings is None:
        return None
    calibration = settings.calibration
    return ThresholdEntry(
        threshold_id="classifier_min_confidence",
        value=settings.min_confidence,
        source="config/classifier/classifier_v1.yaml",
        source_version=settings.version,
        governs=(
            "the calibrated confidence a fallback unit suggestion needs before "
            "the classifier proposes it at all"
        ),
        provenance=(
            f"calibrated on {calibration.calibrated_on} with "
            f"{calibration.model_id} on {calibration.fitted_at}"
            if calibration is not None
            else "NO CALIBRATION: this number is not comparable to anything the "
            "classifier currently produces, and the loader refuses to enable "
            "the classifier while that is true"
        ),
        calibrated=calibration is not None,
        operating_point={
            "enabled": settings.enabled,
            "model_id": settings.model_id,
            "note": (
                "see the classifier section for what was measured; with no "
                "calibration a suggestion carries confidence 0.0 and this "
                "minimum is not applied"
                if calibration is None
                else "see the classifier section for the measured operating point"
            ),
        },
    )


def _histogram(values: Sequence[float]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = f"{value:.3f}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _step_label(step: float) -> str:
    return f"{step:+.2f}"


def _days_remaining(review_due: str | None, today: date) -> int | None:
    parsed = _parse(review_due)
    return None if parsed is None else (parsed - today).days


def _overdue(review_due: str | None, today: date) -> bool:
    parsed = _parse(review_due)
    return parsed is not None and parsed < today


def _parse(review_due: str | None) -> date | None:
    if not review_due:
        return None
    try:
        return date.fromisoformat(review_due)
    except ValueError:  # a contract field is a free-form string
        return None
