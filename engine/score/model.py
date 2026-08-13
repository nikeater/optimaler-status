"""The model half: two readings of one vector, over a versioned population.

Four decisions live here, and each one is about being able to say what a
number means:

**Two families, two readings, one score.** The forest reads the WHOLE vector
and answers "is this combination unusual". It is measurably bad at a second
question - "is this single number far out" - because an isolation tree splits
uniformly inside a feature's range, so one extreme value in a heavy-tailed
column is isolated no faster than a point in a crowd. That question is answered
directly instead: for each CONSISTENCY feature (``DEVIATION_FEATURES``), how
small a tail of the reference population lies at or beyond this item's value.
The score is the percentile of the larger of the two readings, so an item is
unusual if its combination is - or if one of the numbers the earlier parts
deliberately left to this scorer is far from anything the population shows.
Measured, not assumed: with the forest alone, gold v4's date anomalies scored
mid-field while its Indizienbuendel scored at the top (ADR-024).

**The score is a percentile, not a raw model output.** ``IsolationForest``
produces an unbounded, version-dependent path-length statistic; the contract
wants a number in [0, 1] and a caseworker wants a sentence. So both readings
are mapped through the empirical distribution of the REFERENCE POPULATION, and
the result reads exactly as what it is: "more unusual than 94 percent of the
reference corpus". That also makes the threshold in ``config/scoring/`` a
statement a Fachbereich can argue about ("review the most unusual 7 percent")
rather than a magic constant.

**The reference population is an artifact, not a pickle.** ``config/scoring/
reference_gold_v4.json`` carries the feature matrix as plain rounded numbers
with its provenance, and the forest is re-fitted from it at load time in a few
milliseconds. A committed model binary would be unreadable, unreviewable, and
would silently keep working after the feature set changed under it. The matrix
diffs; a pickle does not.

**Reasons come from the model, by ablation.** For every feature the item's value
is replaced by the reference median and the item is re-scored; the drop in
percentile is that feature's contribution. It is a real measurement on the real
model rather than a plausible-sounding story told next to it, it is
deterministic, and it costs one extra batch of ``d + 1`` rows per item.

Determinism is machine-local and says so: fixed ``random_state``, a fixed
feature order, no clock, no dict-order dependence - and the installed
scikit-learn version recorded in the artifact and in the eval report, because
a tree ensemble is only reproducible against the library that grew it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sklearn.ensemble import IsolationForest

from engine.score.features import DEVIATION_FEATURES, FEATURE_IDS

#: What the reference artifact calls itself. A JSON file in a config directory
#: has to be able to say what it is before anything trusts its numbers.
REFERENCE_ARTIFACT = "anomaly_reference_population"

#: Decimals the reference matrix is written and compared with. Six is far
#: below any feature's meaningful resolution and far above float noise, which
#: is what makes a byte-identical rebuild a fact rather than a hope.
REFERENCE_DECIMALS = 6


class ScoringModelError(RuntimeError):
    """The reference population is unusable (missing, malformed, mismatched)."""


@dataclass(frozen=True)
class ReferencePopulation:
    """The versioned population the scorer calls normal, with its provenance."""

    feature_set_version: str
    reference_id: str
    corpus: str
    sklearn_version: str
    seed: int
    feature_names: tuple[str, ...]
    rows: tuple[tuple[float, ...], ...]
    row_ids: tuple[str, ...]
    expected_scores: tuple[float, ...]

    @property
    def item_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class ForestParams:
    """The model's own settings, all of them versioned in config."""

    n_estimators: int
    max_samples: int | str
    seed: int

    def key(self) -> str:
        return f"{self.n_estimators}|{self.max_samples}|{self.seed}"


@dataclass(frozen=True)
class Attribution:
    """One feature's measured share of an item's score."""

    feature_id: str
    contribution: float
    observed: float
    expected: float


class ScoringModel:
    """A fitted forest plus everything needed to explain what it said."""

    def __init__(self, population: ReferencePopulation, params: ForestParams) -> None:
        self.population = population
        self.params = params
        self.feature_names = population.feature_names
        self._forest = IsolationForest(
            n_estimators=params.n_estimators,
            max_samples=params.max_samples,
            random_state=params.seed,
            bootstrap=False,
            n_jobs=1,
        )
        rows = [list(row) for row in population.rows]
        self._forest.fit(rows)
        self._columns = tuple(
            sorted(row[index] for row in population.rows)
            for index in range(len(population.feature_names))
        )
        self._deviation_indices = tuple(
            index
            for index, name in enumerate(population.feature_names)
            if name in DEVIATION_FEATURES
        )
        self._forest_scores = sorted(self._raw(rows))
        self._medians = tuple(
            _median([row[index] for row in population.rows])
            for index in range(len(population.feature_names))
        )
        self._quartiles = tuple(
            _quartiles([row[index] for row in population.rows])
            for index in range(len(population.feature_names))
        )
        # The combined reading of every reference row, so a new item's score is
        # a percentile of the same distribution the population itself produces.
        self._combined = sorted(self._combine(rows))

    # ------------------------------------------------------------ scoring ---

    def score(self, values: Sequence[float]) -> float:
        """How unusual this item is, as a percentile of the reference set."""
        return _rank(self._combined, self._combine([list(values)])[0])

    def explain(self, values: Sequence[float]) -> tuple[float, list[Attribution]]:
        """The score and every feature's ablation contribution, in one batch.

        Row 0 is the item; row ``j + 1`` is the item with feature ``j`` replaced
        by the reference median. A positive contribution means the observed
        value is part of why the item looks unusual. The delta is taken on the
        FINAL score, so a contribution covers both readings: a feature can earn
        one by being far out on its own or by completing an unusual combination.
        """
        base = list(values)
        batch: list[list[float]] = [base]
        for index in range(len(self.feature_names)):
            ablated = list(base)
            ablated[index] = self._medians[index]
            batch.append(ablated)
        scores = [_rank(self._combined, raw) for raw in self._combine(batch)]
        score = scores[0]
        attributions = [
            Attribution(
                feature_id=name,
                contribution=round(score - scores[index + 1], 6),
                observed=base[index],
                expected=self._medians[index],
            )
            for index, name in enumerate(self.feature_names)
        ]
        return score, attributions

    def readings(self, values: Sequence[float]) -> tuple[float, float]:
        """(combination reading, single-value reading) for one item.

        Reported by the eval so the two halves of the score can be told apart:
        an item flagged by the forest alone and one flagged by a date twelve
        years out are different findings and a caseworker should be able to see
        which one this is.
        """
        row = list(values)
        return (
            _rank(self._forest_scores, self._raw([row])[0]),
            self._deviation(row),
        )

    def _combine(self, rows: list[list[float]]) -> list[float]:
        """The larger of the two readings, per row."""
        forest = self._raw(rows)
        return [
            max(_rank(self._forest_scores, raw), self._deviation(row))
            for raw, row in zip(forest, rows, strict=True)
        ]

    def _deviation(self, row: Sequence[float]) -> float:
        """1 minus the smallest two-sided tail share over the consistency features.

        Tail share rather than a z-score on purpose: several of these columns
        have an interquartile range of zero on a corpus where most items are
        not a Statusfeststellung, and a spread of zero turns every non-zero
        value into an infinite deviation. A tail share is defined for any
        distribution, needs no spread estimate, and says something a reader can
        check by counting rows.
        """
        smallest = 1.0
        for index in self._deviation_indices:
            column = self._columns[index]
            value = row[index]
            at_or_below = _count_le(column, value)
            at_or_above = len(column) - _count_lt(column, value)
            tail = min(at_or_below, at_or_above) / len(column)
            smallest = min(smallest, tail)
        return round(1.0 - smallest, 6)

    def reference_span(self, feature_id: str) -> tuple[float, float, float]:
        """(q1, median, q3) of one feature over the reference population."""
        index = self.feature_names.index(feature_id)
        low, high = self._quartiles[index]
        return low, self._medians[index], high

    def reference_scores(self) -> tuple[float, ...]:
        """Every reference item's score, sorted. Used by the fit check."""
        return tuple(_rank(self._combined, value) for value in self._combined)

    def drift(self) -> float:
        """Largest gap between the recorded and the recomputed reference scores.

        Zero on the machine and library version that wrote the artifact. Any
        other number is the honest answer to "is this the same model", and the
        eval report prints it rather than a claim.
        """
        recorded = sorted(self.population.expected_scores)
        current = list(self.reference_scores())
        if len(recorded) != len(current):
            return 1.0
        return max(
            (abs(a - b) for a, b in zip(recorded, current, strict=True)), default=0.0
        )

    # ------------------------------------------------------------ internals ---

    def _raw(self, rows: list[list[float]]) -> list[float]:
        """Higher means more unusual; sklearn's convention is the other way."""
        return [-float(value) for value in self._forest.score_samples(rows)]


def load_reference(path: Path) -> ReferencePopulation:
    """Read and validate a reference-population artifact."""
    if not path.is_file():
        raise ScoringModelError(f"missing reference population: {path}")
    return parse_reference(path.read_text(encoding="utf-8"), label=str(path))


def parse_reference(text: str, *, label: str) -> ReferencePopulation:
    """Validate the artifact's shape before a single number is trusted."""
    try:
        document: Any = json.loads(text)
    except json.JSONDecodeError as error:
        raise ScoringModelError(f"{label} is not readable JSON: {error}") from error
    if not isinstance(document, dict) or document.get("artifact") != REFERENCE_ARTIFACT:
        raise ScoringModelError(
            f"{label} does not declare itself an {REFERENCE_ARTIFACT!r} artifact"
        )
    names = tuple(document.get("feature_names") or ())
    if names != FEATURE_IDS:
        raise ScoringModelError(
            f"{label} was fitted on features {list(names)}, but this build "
            f"computes {list(FEATURE_IDS)}; re-fit with python -m eval.score_fit"
        )
    rows = tuple(tuple(float(value) for value in row) for row in document["rows"])
    if not rows:
        raise ScoringModelError(f"{label} carries no reference rows")
    widths = {len(row) for row in rows}
    if widths != {len(names)}:
        raise ScoringModelError(
            f"{label} has rows of width {sorted(widths)} for {len(names)} features"
        )
    expected = tuple(float(value) for value in document.get("expected_scores") or ())
    row_ids = tuple(str(value) for value in document.get("row_ids") or ())
    return ReferencePopulation(
        feature_set_version=str(document["feature_set_version"]),
        reference_id=str(document["reference_id"]),
        corpus=str(document.get("corpus", "")),
        sklearn_version=str(document.get("sklearn_version", "")),
        seed=int(document.get("seed", 0)),
        feature_names=names,
        rows=rows,
        row_ids=row_ids,
        expected_scores=expected,
    )


def build_model(path: Path, params: ForestParams) -> ScoringModel:
    """A fitted model for one artifact, cached by content rather than by path.

    Keyed on the file's digest so that editing a reference population in a test
    produces a new model rather than a stale one, and re-reading the same file
    in a hundred pipeline runs fits the forest once.
    """
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return _cached_model(digest, text, params.key(), params)


@lru_cache(maxsize=8)
def _cached_model(
    digest: str, text: str, params_key: str, params: ForestParams
) -> ScoringModel:
    return ScoringModel(parse_reference(text, label=digest[:12]), params)


def sklearn_version() -> str:
    """The installed scikit-learn version, recorded next to every number."""
    import sklearn

    return str(sklearn.__version__)


def reference_document(
    *,
    feature_set_version: str,
    reference_id: str,
    corpus: str,
    seed: int,
    rows: Sequence[tuple[str, Sequence[float]]],
    scores: Mapping[str, float],
) -> dict[str, Any]:
    """The artifact as it is written to disk: sorted, rounded, self-describing."""
    ordered = sorted(rows, key=lambda entry: entry[0])
    return {
        "artifact": REFERENCE_ARTIFACT,
        "note": (
            "GENERATED - do not edit by hand. Rebuild with "
            "python -m eval.score_fit --out config/scoring/"
            "reference_gold_v4.json. The rows are identity-blind feature "
            "vectors of a SYNTHETIC corpus; an agency that re-fits on real "
            "intake produces a derived personal-data set and must cover it in "
            "its DPIA (see docs/vault-dpia-input.md)."
        ),
        "feature_set_version": feature_set_version,
        "reference_id": reference_id,
        "corpus": corpus,
        "seed": seed,
        "sklearn_version": sklearn_version(),
        "item_count": len(ordered),
        "feature_names": list(FEATURE_IDS),
        "row_ids": [item_id for item_id, _ in ordered],
        "rows": [
            [round(value, REFERENCE_DECIMALS) for value in values]
            for _, values in ordered
        ],
        "expected_scores": [
            round(scores[item_id], REFERENCE_DECIMALS) for item_id, _ in ordered
        ],
    }


def _count_le(column: Sequence[float], value: float) -> int:
    """How many sorted reference values are at or below ``value``."""
    low, high = 0, len(column)
    while low < high:
        middle = (low + high) // 2
        if column[middle] <= value:
            low = middle + 1
        else:
            high = middle
    return low


def _count_lt(column: Sequence[float], value: float) -> int:
    """How many sorted reference values are strictly below ``value``."""
    low, high = 0, len(column)
    while low < high:
        middle = (low + high) // 2
        if column[middle] < value:
            low = middle + 1
        else:
            high = middle
    return low


def _rank(sorted_values: Sequence[float], value: float) -> float:
    """Share of a sorted reference distribution at or below ``value``."""
    return round(_count_le(sorted_values, value) / len(sorted_values), 6)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _quartiles(values: Sequence[float]) -> tuple[float, float]:
    ordered = sorted(values)
    half = len(ordered) // 2
    lower = ordered[:half]
    upper = ordered[half + 1 :] if len(ordered) % 2 else ordered[half:]
    return (
        _median(lower) if lower else ordered[0],
        _median(upper) if upper else ordered[-1],
    )
