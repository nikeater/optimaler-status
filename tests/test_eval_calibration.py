"""The calibration fit: synthetic pairs, no model, arithmetic that must hold.

The machinery ships tested whether or not a model ever runs on this machine.
What is asserted here is the maths and the refusals - monotonicity as a
property, the ECE arithmetic against a hand-computed value, and every
degenerate shape a real gold set can hand it.
"""

from __future__ import annotations

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from engine.config_loader import CalibrationSpec
from eval.calibration import (
    DEFAULT_BIN_COUNT,
    FittedCalibration,
    ScoredOutcome,
    expected_calibration_error,
    fit_calibration,
)

CALIBRATED_ON = "gold v4"
MODEL_ID = "intfloat/multilingual-e5-small"
FITTED_AT = "2026-08-12"


def _fit(
    outcomes: list[ScoredOutcome], bin_count: int = DEFAULT_BIN_COUNT
) -> FittedCalibration:
    fitted = fit_calibration(
        outcomes,
        calibrated_on=CALIBRATED_ON,
        model_id=MODEL_ID,
        fitted_at=FITTED_AT,
        bin_count=bin_count,
    )
    assert fitted is not None
    return fitted


def _args(
    outcomes: list[ScoredOutcome], bin_count: int = DEFAULT_BIN_COUNT
) -> FittedCalibration | None:
    """fit_calibration with the provenance filled in, for the None cases."""
    return fit_calibration(
        outcomes,
        calibrated_on=CALIBRATED_ON,
        model_id=MODEL_ID,
        fitted_at=FITTED_AT,
        bin_count=bin_count,
    )


def _separable(count: int = 40) -> list[ScoredOutcome]:
    """A clean signal: high scores right, low scores wrong."""
    return [
        ScoredOutcome(raw_score=index / count, correct=index >= count // 2)
        for index in range(count)
    ]


# --------------------------------------------------------------------------
# Shape and refusals
# --------------------------------------------------------------------------


def test_no_samples_is_no_calibration() -> None:
    """Nothing to fit is None, not a map of zeros somebody could paste."""
    assert _args([]) is None


def test_a_negative_bin_count_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        _args(_separable(), bin_count=0)


def test_one_sample_still_produces_a_usable_map() -> None:
    fitted = _fit([ScoredOutcome(raw_score=0.7, correct=True)])
    assert fitted.sample_count == 1
    assert fitted.bins[-1].upper == 1.0
    assert fitted.as_calibration().apply(0.7) == 1.0


def test_one_distinct_score_collapses_to_one_bin() -> None:
    """Indistinguishable samples may not be split by the sort's tie-breaking."""
    fitted = _fit([ScoredOutcome(0.5, index % 2 == 0) for index in range(20)])
    assert len(fitted.bins) == 1
    assert fitted.bins[0].confidence == pytest.approx(0.5)


def test_all_correct_maps_everything_to_one() -> None:
    fitted = _fit([ScoredOutcome(index / 10, True) for index in range(10)])
    assert {entry.confidence for entry in fitted.bins} == {1.0}
    assert fitted.expected_calibration_error == pytest.approx(0.0)


def test_all_wrong_maps_everything_to_zero() -> None:
    fitted = _fit([ScoredOutcome(index / 10, False) for index in range(10)])
    assert {entry.confidence for entry in fitted.bins} == {0.0}


def test_fewer_samples_than_bins_produces_fewer_bins() -> None:
    fitted = _fit([ScoredOutcome(0.1, False), ScoredOutcome(0.9, True)], bin_count=8)
    assert len(fitted.bins) == 2


def test_the_last_bin_always_reaches_one() -> None:
    """A total map: a score above anything ever observed still means something."""
    fitted = _fit([ScoredOutcome(index / 100, index > 10) for index in range(20)])
    assert fitted.bins[-1].upper == 1.0
    assert fitted.as_calibration().apply(0.999) == fitted.bins[-1].confidence


# --------------------------------------------------------------------------
# Monotonicity
# --------------------------------------------------------------------------


def test_a_falling_bin_is_pooled_with_its_neighbour() -> None:
    """The signal inverts in the middle; the fit refuses to say so."""
    outcomes = (
        [ScoredOutcome(0.10 + i / 100, False) for i in range(10)]
        + [ScoredOutcome(0.30 + i / 100, True) for i in range(10)]
        + [ScoredOutcome(0.50 + i / 100, False) for i in range(10)]
        + [ScoredOutcome(0.70 + i / 100, True) for i in range(10)]
    )
    fitted = _fit(outcomes, bin_count=4)
    confidences = [entry.confidence for entry in fitted.bins]
    assert confidences == sorted(confidences)
    # The two middle bins disagreed and were pooled into their shared mean.
    assert confidences[1] == pytest.approx(confidences[2])


@given(
    outcomes=st.lists(
        st.tuples(
            st.floats(min_value=-1.0, max_value=1.0, allow_nan=False),
            st.booleans(),
        ),
        min_size=1,
        max_size=60,
    ),
    bin_count=st.integers(min_value=1, max_value=8),
)
def test_the_fitted_map_is_always_a_monotone_total_map(
    outcomes: list[tuple[float, bool]], bin_count: int
) -> None:
    """The property the loader will later re-check on the pasted block."""
    fitted = _fit(
        [ScoredOutcome(score, correct) for score, correct in outcomes],
        bin_count=bin_count,
    )
    uppers = [entry.upper for entry in fitted.bins]
    confidences = [entry.confidence for entry in fitted.bins]
    assert uppers == sorted(uppers)
    assert len(set(uppers)) == len(uppers)
    assert confidences == sorted(confidences)
    assert uppers[-1] == 1.0
    assert all(0.0 <= value <= 1.0 for value in confidences)


@given(
    outcomes=st.lists(
        st.tuples(
            st.floats(min_value=-1.0, max_value=1.0, allow_nan=False),
            st.booleans(),
        ),
        min_size=1,
        max_size=40,
    )
)
def test_whatever_is_fitted_is_accepted_by_the_loader(
    outcomes: list[tuple[float, bool]],
) -> None:
    """The fit and the config contract cannot drift: one is checked by the other."""
    fitted = _fit([ScoredOutcome(score, correct) for score, correct in outcomes])
    block = fitted.as_block()["calibration"]
    CalibrationSpec.model_validate(block)


# --------------------------------------------------------------------------
# Expected calibration error
# --------------------------------------------------------------------------


def test_ece_is_zero_when_the_claim_matches_reality() -> None:
    """Four samples, half right; a map that claims exactly 0.5 is calibrated."""
    outcomes = [
        ScoredOutcome(0.1, True),
        ScoredOutcome(0.2, False),
        ScoredOutcome(0.3, True),
        ScoredOutcome(0.4, False),
    ]
    assert expected_calibration_error(outcomes, lambda _: 0.5, bin_count=1) == 0.0


def test_ece_is_one_when_the_claim_is_exactly_inverted() -> None:
    outcomes = [ScoredOutcome(0.1, False), ScoredOutcome(0.2, False)]
    assert expected_calibration_error(outcomes, lambda _: 1.0, bin_count=1) == 1.0


def test_ece_is_sample_weighted_across_bins() -> None:
    """Hand-computed: bin one claims 0.0 and is 0/2; bin two claims 1.0 and is 1/2."""
    outcomes = [
        ScoredOutcome(0.1, False),
        ScoredOutcome(0.2, False),
        ScoredOutcome(0.8, True),
        ScoredOutcome(0.9, False),
    ]
    error = expected_calibration_error(
        outcomes, lambda score: 0.0 if score < 0.5 else 1.0, bin_count=2
    )
    assert error == pytest.approx(0.5 * 0.0 + 0.5 * 0.5)


def test_ece_of_an_empty_sample_is_zero() -> None:
    assert expected_calibration_error([], lambda _: 0.5) == 0.0


def test_the_fit_reports_the_error_it_removed() -> None:
    """The 'before' number is the reason the fit exists, so it is reported."""
    fitted = _fit(_separable())
    assert fitted.raw_expected_calibration_error > fitted.expected_calibration_error


# --------------------------------------------------------------------------
# The emitted block
# --------------------------------------------------------------------------


def test_the_emitted_block_is_pasteable_yaml_with_its_provenance() -> None:
    fitted = _fit(_separable())
    text = fitted.as_yaml()
    document = yaml.safe_load(text)
    assert set(document) == {"calibration"}
    block = document["calibration"]
    assert block["calibrated_on"] == "gold v4"
    assert block["model_id"] == "intfloat/multilingual-e5-small"
    assert block["fitted_at"] == "2026-08-12"
    assert len(block["bins"]) == DEFAULT_BIN_COUNT
    CalibrationSpec.model_validate(block)


def test_the_emitted_header_says_the_error_is_in_sample() -> None:
    """A number that could be mistaken for a generalization claim says it is not."""
    text = _fit(_separable()).as_yaml()
    assert "IN-SAMPLE" in text
    assert "samples" in text


def test_the_fitted_calibration_converts_to_the_engine_object() -> None:
    fitted = _fit(_separable())
    calibration = fitted.as_calibration()
    assert calibration.calibrated_on == "gold v4"
    assert calibration.expected_calibration_error == fitted.expected_calibration_error
    assert calibration.apply(1.0) == fitted.bins[-1].confidence
