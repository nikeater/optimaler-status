"""Turning raw similarities into something a threshold may read.

A cosine of 0.87 between a letter and a Referat's responsibility text is a fact
about two vectors. "This suggestion is right nine times out of ten" is a fact
about the world, and only the second one may be compared against a number in a
decision table. This module is the bridge, and it is deliberately the dullest
piece of statistics that does the job:

* **Equal-frequency bins.** The scored pairs are sorted and cut into bins with
  roughly the same number of samples each, so a bin's accuracy is estimated
  from a comparable amount of evidence rather than from whatever happened to
  fall into a fixed-width interval.
* **Pool adjacent violators.** The per-bin accuracies are made monotone by the
  standard isotonic step (pool a bin with its neighbour whenever the accuracy
  falls, repeat). A map that inverted would say a better match means less,
  which no reader would accept and no threshold could use.
* **ECE, both sides of the fit, and only one of them means what it looks like.**
  The "before" number - the raw cosines read as if they were probabilities - is
  a real measurement and is the number that justifies doing this at all. The
  "after" number is computed over the SAME bins the map was fitted on, so it is
  0 whenever the observed accuracies were already monotone, and it rises only
  by however much enforcing monotonicity cost. Read it as "what the isotonic
  constraint left behind", never as "how well this map generalizes". With about
  a hundred gold items a held-out split would produce two numbers nobody should
  trust; the honest answer is a number with its meaning written next to it.

The output is not code. It is a YAML block with provenance - which gold set,
which model, which day - that a human pastes into ``config/classifier/``. The
loader then refuses to enable the classifier unless that provenance is there,
which is the whole reason the fit emits text rather than writing a file.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import yaml

from engine.evidence.classify import Calibration, CalibrationBin

#: Default bin count. Small on purpose: with ~90 usable gold items, ten bins
#: would put nine samples in each and estimate an accuracy from nine coin
#: flips. Five is already generous.
DEFAULT_BIN_COUNT = 5

#: The last bin always ends here, because the loader requires a total map: a
#: raw score above the highest one ever observed still has to mean something.
MAX_SCORE = 1.0


@dataclass(frozen=True)
class ScoredOutcome:
    """One fit sample: what the classifier scored, and whether it was right."""

    raw_score: float
    correct: bool


@dataclass(frozen=True)
class FittedCalibration:
    """The fitted map with everything needed to judge it."""

    bins: tuple[CalibrationBin, ...]
    calibrated_on: str
    model_id: str
    fitted_at: str
    expected_calibration_error: float
    raw_expected_calibration_error: float
    sample_count: int
    positive_count: int
    bin_counts: tuple[int, ...]

    def as_calibration(self) -> Calibration:
        """The engine-side object, for tests and for an in-process run."""
        return Calibration(
            bins=self.bins,
            calibrated_on=self.calibrated_on,
            model_id=self.model_id,
            fitted_at=self.fitted_at,
            expected_calibration_error=self.expected_calibration_error,
        )

    def as_block(self) -> dict[str, object]:
        """The ``calibration:`` mapping as the loader expects it.

        Rounded for a human to read, and de-duplicated AFTER rounding: two bin
        bounds that differ in the fifteenth decimal are one bound once written
        down, and the loader (rightly) refuses a block whose bounds do not
        strictly rise. Found by the property test that feeds the fit's own
        output back through the config contract.
        """
        rounded = _drop_duplicate_uppers(
            [
                CalibrationBin(
                    upper=round(entry.upper, 4), confidence=round(entry.confidence, 4)
                )
                for entry in self.bins
            ]
        )
        return {
            "calibration": {
                "calibrated_on": self.calibrated_on,
                "model_id": self.model_id,
                "fitted_at": self.fitted_at,
                "expected_calibration_error": round(self.expected_calibration_error, 4),
                "bins": [
                    {"upper": entry.upper, "confidence": entry.confidence}
                    for entry in rounded
                ],
            }
        }

    def as_yaml(self) -> str:
        """Ready to paste into ``config/classifier/classifier_v1.yaml``."""
        header = (
            "# Fitted by `python -m eval.calibrate`. Paste under the classifier's\n"
            "# top level; the loader refuses `enabled: true` without it.\n"
            f"#   samples          {self.sample_count} "
            f"({self.positive_count} correct)\n"
            f"#   per-bin counts   {list(self.bin_counts)}\n"
            f"#   ECE raw          {self.raw_expected_calibration_error:.4f}"
            "  (cosine read as a probability - the reason this fit exists)\n"
            f"#   ECE fitted       {self.expected_calibration_error:.4f}"
            "  (IN-SAMPLE, over the fit's own bins: this is what enforcing\n"
            "#                    monotonicity cost, NOT how well the map "
            "generalizes)\n"
        )
        return header + yaml.safe_dump(
            self.as_block(), sort_keys=False, allow_unicode=True
        )


def fit_calibration(
    outcomes: Sequence[ScoredOutcome],
    *,
    calibrated_on: str,
    model_id: str,
    fitted_at: str,
    bin_count: int = DEFAULT_BIN_COUNT,
) -> FittedCalibration | None:
    """Fit a monotone step map from raw score to observed accuracy.

    Returns None when there is nothing to fit: no samples at all. Everything
    else - one sample, one distinct score, all-right, all-wrong - produces a
    map, because a degenerate answer that says so beats an exception in a
    calibration run somebody scheduled.
    """
    if bin_count < 1:
        raise ValueError("bin_count must be at least 1")
    if not outcomes:
        return None
    ordered = sorted(outcomes, key=lambda outcome: outcome.raw_score)
    groups = _equal_frequency_groups(ordered, bin_count)
    pooled = _pool_adjacent_violators(
        [(len(group), _accuracy(group)) for group in groups]
    )
    bins = tuple(
        CalibrationBin(
            upper=MAX_SCORE if index == len(groups) - 1 else group[-1].raw_score,
            confidence=confidence,
        )
        for index, (group, confidence) in enumerate(zip(groups, pooled, strict=True))
    )
    bins = _drop_duplicate_uppers(bins)
    fitted = Calibration(
        bins=bins,
        calibrated_on=calibrated_on,
        model_id=model_id,
        fitted_at=fitted_at,
    )
    return FittedCalibration(
        bins=bins,
        calibrated_on=calibrated_on,
        model_id=model_id,
        fitted_at=fitted_at,
        expected_calibration_error=expected_calibration_error(
            ordered, fitted.apply, bin_count=bin_count
        ),
        raw_expected_calibration_error=expected_calibration_error(
            ordered, _clamp, bin_count=bin_count
        ),
        sample_count=len(ordered),
        positive_count=sum(1 for outcome in ordered if outcome.correct),
        bin_counts=tuple(len(group) for group in groups),
    )


def expected_calibration_error(
    outcomes: Sequence[ScoredOutcome],
    confidence_of: Callable[[float], float],
    *,
    bin_count: int = DEFAULT_BIN_COUNT,
) -> float:
    """Sample-weighted mean gap between claimed confidence and observed accuracy.

    The textbook ECE: bin the samples, and in each bin compare the average
    confidence the map claims with the share that were actually right. 0.0
    means the claims matched reality on this set; 1.0 means they were exactly
    inverted. An empty sample has no calibration error to report, and 0.0 there
    would read as "perfectly calibrated", so it is reported as 0.0 only because
    the caller has already been told the sample count.
    """
    if not outcomes:
        return 0.0
    ordered = sorted(outcomes, key=lambda outcome: outcome.raw_score)
    total = len(ordered)
    error = 0.0
    for group in _equal_frequency_groups(ordered, bin_count):
        claimed = math.fsum(confidence_of(item.raw_score) for item in group) / len(
            group
        )
        error += len(group) / total * abs(claimed - _accuracy(group))
    return error


def _equal_frequency_groups(
    ordered: Sequence[ScoredOutcome], bin_count: int
) -> list[list[ScoredOutcome]]:
    """Cut sorted samples into near-equal groups, never splitting a tie.

    Two samples with the same raw score must land in the same bin: they are
    indistinguishable to the classifier, so putting them in different bins
    would make the boundary - and therefore the fitted confidence - depend on
    the sort's tie-breaking rather than on the data.
    """
    count = min(bin_count, len(ordered))
    groups: list[list[ScoredOutcome]] = []
    for index in range(count):
        start = index * len(ordered) // count
        end = (index + 1) * len(ordered) // count
        groups.append(list(ordered[start:end]))
    merged: list[list[ScoredOutcome]] = []
    for group in groups:
        if (
            merged
            and group
            and merged[-1]
            and merged[-1][-1].raw_score == group[0].raw_score
        ):
            merged[-1].extend(group)
            continue
        if group:
            merged.append(group)
    return merged


def _pool_adjacent_violators(groups: Sequence[tuple[int, float]]) -> list[float]:
    """Isotonic regression on (weight, value) pairs: the classic PAVA.

    Whenever a bin's accuracy is lower than the one before it, the two are
    pooled into their weighted mean and the check restarts. What comes out is
    the closest non-decreasing sequence in the weighted least-squares sense,
    and - more to the point here - it is the only shape a confidence map is
    allowed to have. One value comes out per INPUT bin, so each block carries
    the number of bins it absorbed alongside its sample weight.
    """
    stack: list[tuple[int, float, int]] = []
    for weight, value in groups:
        block_weight, block_value, block_bins = weight, value, 1
        while stack and stack[-1][1] > block_value:
            previous_weight, previous_value, previous_bins = stack.pop()
            total = previous_weight + block_weight
            block_value = (
                previous_value * previous_weight + block_value * block_weight
            ) / total
            block_weight, block_bins = total, previous_bins + block_bins
        stack.append((block_weight, block_value, block_bins))
    values: list[float] = []
    for _, value, block_bins in stack:
        values.extend([value] * block_bins)
    return values


def _drop_duplicate_uppers(
    bins: Sequence[CalibrationBin],
) -> tuple[CalibrationBin, ...]:
    """Collapse bins that share an upper bound, keeping the last confidence.

    Equal-frequency binning on tied scores can produce two bins ending at the
    same value; the loader requires strictly rising bounds, and the second of
    two identical bounds is unreachable anyway. The FIRST is kept, because that
    is the one :meth:`Calibration.apply` would have used - and after the
    isotonic step it is also the lower of the two, which is the cautious
    direction for a confidence.
    """
    kept: dict[float, CalibrationBin] = {}
    for entry in bins:
        kept.setdefault(entry.upper, entry)
    return tuple(kept[upper] for upper in sorted(kept))


def _accuracy(group: Sequence[ScoredOutcome]) -> float:
    if not group:
        return 0.0
    return sum(1 for item in group if item.correct) / len(group)


def _clamp(score: float) -> float:
    """A raw cosine read as if it were a probability, which is the error."""
    return max(0.0, min(1.0, score))
