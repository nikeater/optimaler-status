"""What the shadow scorer did on a corpus, and what it did NOT move.

Two sections and one gate.

The **anomaly section** answers the four questions a reviewer has about a
scorer that has never seen an outcome: where do the scores sit, which items did
it mark, did it find the ones the corpus says are there, and what would it cost
if it were ever switched on. The last one is the reading the downgrade-rate
budget bounds, and it is deliberately narrow: an item already at tier 3 costs
nothing to flag, so the efficiency question is only ever about the items the
rules would have cleared.

The **bias section** (P-2) is flag rate and mean contribution per procedure,
per channel and per item shape, with a skew line. Reported, never gated: an
alarm that failed a build would teach people to tune the alarm. It exists
because the feature set contains an item-shape feature, and "the scorer flags
everybody who sends paper" is the failure this project's prior-art research
spent a chapter on.

The **reasons gate** is the one thing here that touches an exit code. Every
flagged item must carry at least one feature-level reason that renders to a
German sentence naming the feature, the observation, the expectation and the
contribution. ADR-004 has promised since part 01 that a flag without readable
reasons never ships; this is where the promise is measured over a whole corpus
rather than argued about.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from engine.config_loader import ConfigBundle
from engine.score import reason_is_readable, render_reason, sklearn_version

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, types only
    from eval.harness import ItemResult

#: Score buckets the distribution is reported in. Ten equal steps, because the
#: score IS a percentile and a reader should be able to see immediately whether
#: the population is spread out or piled up against the threshold.
HISTOGRAM_STEPS = 10

#: How many flagged item ids the section lists in full. Above this the list is
#: truncated with a count, because a report nobody scrolls to the end of is a
#: report nobody reads.
MAX_LISTED = 40


def anomaly_section(
    results: Sequence[ItemResult], *, config: ConfigBundle
) -> dict[str, Any]:
    """Everything the scorer did on this corpus, including nothing."""
    scoring = config.scoring
    if scoring is None:
        return {"configured": False}
    scored = [item for item in results if item.anomaly_score is not None]
    degraded = [item for item in results if item.anomaly_degraded]
    flagged = [item for item in scored if item.anomaly_flagged]
    expected = [item for item in results if item.anomaly_expected]
    hits = [item for item in expected if item.anomaly_flagged]
    misses = [item for item in expected if not item.anomaly_flagged]
    false_flags = [item for item in flagged if not item.anomaly_expected]
    tier1 = [item for item in results if item.expected_tier == 1]
    tier1_false = [item for item in false_flags if item.expected_tier == 1]
    # The sf-0040 class, named in the corpus and reported separately since part
    # 03b asked for it: an item already at tier 3 that the scorer marks. A
    # downgrade would move nothing, so a budget read off tier movements alone
    # would report zero while three items carried a flag and a reason.
    no_movement = [item for item in flagged if item.pre_downgrade_tier == 3]
    return {
        "configured": True,
        "version": scoring.version,
        "feature_set_version": scoring.feature_set_version,
        "reference_id": scoring.reference_id,
        "reference_population": scoring.reference_population,
        "sklearn_version": sklearn_version(),
        "scorer_mode": config.risk.scorer_mode,
        "threshold": {
            "threshold_id": scoring.threshold.threshold_id,
            "value": scoring.threshold.value,
            "calibrated_on": scoring.threshold.calibrated_on,
            "in_sample": True,
        },
        "items": len(results),
        "items_scored": len(scored),
        "degraded": {
            "count": len(degraded),
            "item_ids": sorted(item.item_id for item in degraded)[:MAX_LISTED],
            "reasons": sorted(
                {item.anomaly_degradation or "" for item in degraded} - {""}
            ),
        },
        "score_distribution": _distribution(scored),
        "flagged": {
            "count": len(flagged),
            "rate": _ratio(len(flagged), len(scored)),
            "item_ids": sorted(item.item_id for item in flagged)[:MAX_LISTED],
        },
        "anomaly_expected": {
            "count": len(expected),
            "recall": _ratio(len(hits), len(expected)),
            "found": sorted(item.item_id for item in hits),
            "missed": sorted(item.item_id for item in misses),
        },
        "false_flags": {
            "count": len(false_flags),
            "tier1_eligible_items": len(tier1),
            "tier1_false_flags": sorted(item.item_id for item in tier1_false),
            # The reading the budget bounds: tier-1 items the scorer marks that
            # the corpus does NOT call anomalous, over all tier-1 items. Items
            # at tier 2 and 3 are excluded because a downgrade there costs no
            # automation - it is oversight added to oversight.
            "rate_on_tier1_eligible": _ratio(len(tier1_false), len(tier1)),
            "budget": config.risk.downgrade_rate_budget,
            "within_budget": _ratio(len(tier1_false), len(tier1))
            <= config.risk.downgrade_rate_budget,
        },
        "tier_movement": {
            "would_downgrade": sum(
                1 for item in flagged if item.pre_downgrade_tier < 3
            ),
            "flag_without_tier_movement": len(no_movement),
            "flag_without_tier_movement_ids": sorted(
                item.item_id for item in no_movement
            ),
            "by_pre_downgrade_tier": _counter(
                str(item.pre_downgrade_tier) for item in flagged
            ),
            "note": (
                "an item already at tier 3 is flagged without anything moving; "
                "the value there is the reason in the journal, and a budget "
                "read off tier movements alone would report zero for it "
                "(gold v4: the three sf items)"
            ),
        },
        "log_only": {
            "moved_nothing": all(
                item.actual_tier == item.pre_downgrade_tier for item in results
            ),
            "items_where_tier_differs_from_pre_downgrade": sorted(
                item.item_id
                for item in results
                if item.actual_tier != item.pre_downgrade_tier
            ),
        },
        "reasons": _reason_report(flagged),
    }


def bias_section(
    results: Sequence[ItemResult], *, config: ConfigBundle
) -> dict[str, Any]:
    """Flag rate and mean contribution per procedure, channel and item shape.

    Report-only, and the note in the output says so. The number to read is not
    any single flag rate but the skew: one group flagged three times as often
    as another is either a real difference in the cases or a proxy the feature
    set picked up, and only a human looking at the two groups can tell which.
    """
    scoring = config.scoring
    if scoring is None:
        return {"configured": False}
    scored = [item for item in results if item.anomaly_score is not None]
    advisory = scoring.bias_monitoring
    dimensions = {
        "procedure": _dimension(scored, lambda item: item.procedure_id),
        "channel": _dimension(scored, lambda item: item.channel or "unbekannt"),
        "shape": _dimension(scored, lambda item: item.item_shape),
    }
    return {
        "configured": True,
        "advisory": {
            "max_flag_rate_ratio": advisory.max_flag_rate_ratio,
            "min_group_items": advisory.min_group_items,
        },
        "items_scored": len(scored),
        "dimensions": dimensions,
        "skew": {
            name: _skew(groups, advisory.max_flag_rate_ratio, advisory.min_group_items)
            for name, groups in dimensions.items()
        },
        "note": (
            "reported, never gated. The feature set carries an item-shape "
            "feature and no channel feature, because on every configured intake "
            "path the channel is a function of the shape; both are dimensions "
            "here so a systematic skew against people who send paper is visible "
            "rather than inferred."
        ),
    }


def reasons_gate_passed(section: dict[str, Any]) -> bool:
    """Whether every flag in this run carried a readable reason.

    True when no scorer is configured, for the reason every optional subsystem
    gets: an agency that runs no scorer cannot fail a check about its flags.
    """
    if not section.get("configured"):
        return True
    reasons = section.get("reasons", {})
    return bool(reasons.get("gate_passed", True))


def _reason_report(flagged: Sequence[ItemResult]) -> dict[str, Any]:
    """Per-flag reason accounting, and the sample a reader should look at."""
    bare = [item.item_id for item in flagged if not item.anomaly_reasons]
    unreadable = sorted(
        f"{item.item_id}:{reason.feature}"
        for item in flagged
        for reason in item.anomaly_reasons
        if not reason_is_readable(reason)
    )
    rendered = sorted(
        render_reason(reason) for item in flagged for reason in item.anomaly_reasons[:1]
    )
    return {
        "flagged_items": len(flagged),
        "items_with_reasons": sum(1 for item in flagged if item.anomaly_reasons),
        "reason_count": sum(len(item.anomaly_reasons) for item in flagged),
        "flags_without_reasons": sorted(bare),
        "unreadable_reasons": unreadable,
        "gate_passed": not bare and not unreadable,
        "example": rendered[0] if rendered else None,
        "features_used": _counter(
            reason.feature for item in flagged for reason in item.anomaly_reasons
        ),
    }


def _distribution(scored: Sequence[ItemResult]) -> dict[str, Any]:
    """Where the scores sit, as order statistics and a fixed-width histogram."""
    values = sorted(item.anomaly_score or 0.0 for item in scored)
    if not values:
        return {"items": 0}
    histogram = {
        f"{step / HISTOGRAM_STEPS:.1f}-{(step + 1) / HISTOGRAM_STEPS:.1f}": sum(
            1
            for value in values
            if step / HISTOGRAM_STEPS <= value < (step + 1) / HISTOGRAM_STEPS
            or (step == HISTOGRAM_STEPS - 1 and value == 1.0)
        )
        for step in range(HISTOGRAM_STEPS)
    }
    return {
        "items": len(values),
        "min": round(values[0], 6),
        "median": round(values[len(values) // 2], 6),
        "p90": round(values[min(len(values) - 1, (len(values) * 9) // 10)], 6),
        "max": round(values[-1], 6),
        "histogram": histogram,
    }


def _dimension(scored: Sequence[ItemResult], key: Any) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[ItemResult]] = {}
    for item in scored:
        groups.setdefault(str(key(item)), []).append(item)
    return {
        name: {
            "items": len(members),
            "flagged": sum(1 for item in members if item.anomaly_flagged),
            "flag_rate": _ratio(
                sum(1 for item in members if item.anomaly_flagged), len(members)
            ),
            "mean_abs_contribution": round(
                sum(item.anomaly_mean_abs_contribution for item in members)
                / len(members),
                6,
            ),
            "mean_score": round(
                sum(item.anomaly_score or 0.0 for item in members) / len(members), 6
            ),
        }
        for name, members in sorted(groups.items())
    }


def _skew(
    groups: dict[str, dict[str, Any]], advisory: float, min_items: int
) -> dict[str, Any]:
    """max/min flag rate across the groups big enough to mean anything.

    A group whose minimum is zero has no finite ratio, and that is reported as
    exceeding the advisory rather than as a missing number: "one group is never
    flagged and another is" is exactly the observation this line exists for.
    """
    eligible = {
        name: entry for name, entry in groups.items() if entry["items"] >= min_items
    }
    if len(eligible) < 2:
        return {
            "ratio": None,
            "above_advisory": False,
            "note": f"fewer than two groups with at least {min_items} items",
        }
    rates = {name: float(entry["flag_rate"]) for name, entry in eligible.items()}
    highest = max(rates, key=lambda name: (rates[name], name))
    lowest = min(rates, key=lambda name: (rates[name], name))
    if rates[lowest] == 0.0:
        return {
            "ratio": None,
            "max_group": highest,
            "max_rate": rates[highest],
            "min_group": lowest,
            "min_rate": 0.0,
            "above_advisory": rates[highest] > 0.0,
            "note": (
                "no finite ratio: one group carries no flag at all while another does"
            ),
        }
    ratio = round(rates[highest] / rates[lowest], 4)
    return {
        "ratio": ratio,
        "max_group": highest,
        "max_rate": rates[highest],
        "min_group": lowest,
        "min_rate": rates[lowest],
        "above_advisory": ratio > advisory,
        "note": None,
    }


def _counter(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 6) if whole else 0.0
