"""Feature-level reasons, in German, or the flag does not ship.

ADR-004 has said since part 01 that "every flag carries feature-level reasons;
a flag without readable reasons never ships", and ``schemas/anomaly.py``
refuses to construct a flagged AnomalyEvidence without them. This module is
where that stops being a refusal and becomes a sentence: which feature, what
this item shows, what the reference population shows, and how much of the score
that difference accounts for.

Two things it deliberately does not do. It does not invent a THRESHOLD to
describe ("liegt ueber der zulaessigen Grenze" would be a rule, and the rules
are in ``config/procedures/``); it states an observation against a reference
range. And it does not claim additivity: the contributions are ablation deltas
on a tree ensemble and do not sum to the score, so the wording says what they
are - what the score loses when this value is replaced by the usual one.
"""

from __future__ import annotations

from collections.abc import Sequence

from engine.score.features import FeaturePolicy, FeatureVector
from engine.score.model import Attribution, ScoringModel
from schemas.anomaly import AnomalyReason

#: What a reason says when a feature has no configured wording. Never silently
#: blank: an unworded feature must be visible as a gap in the config, not as an
#: empty half-sentence in front of a caseworker.
UNKNOWN_LABEL = "unbeschriebenes Merkmal"

#: Shortest observation or expectation that can still be a reason. See
#: :func:`reason_is_readable`.
MIN_PART_CHARS = 8


def build_reasons(
    vector: FeatureVector,
    attributions: Sequence[Attribution],
    *,
    model: ScoringModel,
    policy: FeaturePolicy,
    max_reasons: int,
    min_contribution: float,
) -> list[AnomalyReason]:
    """The reasons for one item, strongest contribution first.

    Always returns at least one reason. If no feature contributed above the
    configured minimum - which happens when an item is unusual as a COMBINATION
    rather than through any single value - the feature furthest from the
    reference median is reported with its measured contribution, whatever that
    is. A flagged item with an empty reason list is the one output this system
    may not produce, so the fallback is here rather than in the caller.
    """
    ranked = sorted(
        attributions, key=lambda item: (-item.contribution, item.feature_id)
    )
    chosen = [item for item in ranked if item.contribution >= min_contribution]
    if not chosen:
        chosen = [_most_deviant(attributions, model=model)]
    return [
        _reason(attribution, vector=vector, model=model, policy=policy)
        for attribution in chosen[:max_reasons]
    ]


def render_reason(reason: AnomalyReason) -> str:
    """One reason as the German sentence a caseworker reads.

    The same shape the decision table's downgrade template renders (see
    ``engine/decide/interpreter.py``), spelled out once here so the eval gate
    checks the string a human would actually see rather than a reconstruction
    of it.
    """
    return (
        f"Merkmal {reason.feature}: beobachtet {reason.observed}; "
        f"erwartet {reason.expected}; "
        f"Beitrag zum Anomaliescore {reason.contribution:+.3f}."
    )


def reason_is_readable(reason: AnomalyReason) -> bool:
    """Whether a reason carries all four parts a caseworker needs.

    The gate behind ``eval.run``'s exit code. A reason that names no feature,
    shows no observation, states no expectation or renders to a fragment is not
    a reason, and an item flagged with one has been flagged without a reason.

    ``MIN_PART_CHARS`` is the deliberately blunt part: "x" is technically an
    observation and is not one a person can act on. It is set low enough that
    no sentence this system produces can trip it by accident and high enough
    that a stub - a bare feature id, a single number, an empty label - cannot
    pass as a reason.
    """
    if not reason.feature.strip():
        return False
    observed, expected = reason.observed.strip(), reason.expected.strip()
    if len(observed) < MIN_PART_CHARS or len(expected) < MIN_PART_CHARS:
        return False
    if UNKNOWN_LABEL in reason.observed or UNKNOWN_LABEL in reason.expected:
        return False
    return render_reason(reason).endswith(".")


def _reason(
    attribution: Attribution,
    *,
    vector: FeatureVector,
    model: ScoringModel,
    policy: FeaturePolicy,
) -> AnomalyReason:
    spec = policy.specs.get(attribution.feature_id)
    label = spec.label if spec is not None else UNKNOWN_LABEL
    unit = spec.unit if spec is not None else ""
    note = spec.expected_note if spec is not None else ""
    low, median, high = model.reference_span(attribution.feature_id)
    return AnomalyReason(
        feature=attribution.feature_id,
        observed=f"{label}: {vector.display(attribution.feature_id)}",
        expected=_expected(low, median, high, unit=unit, note=note),
        contribution=attribution.contribution,
    )


def _expected(low: float, median: float, high: float, *, unit: str, note: str) -> str:
    """The reference range, said plainly and without a threshold in sight.

    Rounded to two decimals because this is a sentence, not a matrix: a
    quartile printed to six places invites a reader to believe the sixth means
    something, and the artifact next door carries the exact numbers.
    """
    suffix = f" {unit}".rstrip()
    span = (
        f"Referenzbestand: Median {round(median, 2):g}{suffix}, "
        f"mittlere Haelfte {round(low, 2):g} bis {round(high, 2):g}{suffix}"
    )
    return f"{span}. {note}".strip()


def _most_deviant(
    attributions: Sequence[Attribution], *, model: ScoringModel
) -> Attribution:
    """The feature furthest from the reference median, scaled by its spread.

    Scaled, because comparing "0.4 away" on a share with "0.4 away" on a count
    of years would always pick the same column. The spread is the interquartile
    distance of the reference population, floored so a constant feature cannot
    divide by zero and win every time.
    """

    def deviation(attribution: Attribution) -> float:
        low, _, high = model.reference_span(attribution.feature_id)
        spread = max(abs(high - low), 1e-6)
        return abs(attribution.observed - attribution.expected) / spread

    ordered = sorted(attributions, key=lambda item: (-deviation(item), item.feature_id))
    return ordered[0]
