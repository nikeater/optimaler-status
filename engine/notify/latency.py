"""Notification latency, derived from journal deltas (P-10, 07 slice).

ADR-005 accepted a cost in one line: "notification latency is journal-projection
latency (acceptable: seconds)". This module is what turns that from an
expectation into a measured number, and it measures it the only way that is
defensible here - as the difference between two timestamps THE JOURNAL ALREADY
HOLDS:

    RECEIVED -> its NOTIFIED   the instant receipt
    ROUTED   -> its NOTIFIED   the status update

No stopwatch runs anywhere. The function is deterministic over an event list: the
same journal produces the same numbers on any machine, at any later date, which
is what lets the metric sit in a report next to gated ones without being one.
The pairing is by source event id, the same key the fold dedupes on, so a
notification can only ever be timed against the event that actually owed it.

What this is NOT: the exception-path latency P-10 also asks for (age of the
oldest tier-3 item, a latency budget per tier). That needs the queue and the
review UI, and it stays open for part 10.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from schemas.events import Event, EventType


@dataclass(frozen=True)
class LatencySample:
    """One measured delta: a trigger event and the notification it produced."""

    case_id: str
    trigger: str
    template_id: str
    milliseconds: float


def case_latencies(events: Sequence[Event]) -> tuple[LatencySample, ...]:
    """Every RECEIVED/ROUTED -> NOTIFIED delta in one case's event list.

    A NOTIFIED whose source event is not in this list (a partial read, a
    truncated file) is skipped rather than timed against nothing.
    """
    by_id = {event.event_id: event for event in events}
    samples: list[LatencySample] = []
    for event in sorted(events, key=lambda item: item.sequence):
        if event.type is not EventType.NOTIFIED:
            continue
        source_id = event.payload.get("source_event_id")
        source = by_id.get(source_id) if isinstance(source_id, str) else None
        if source is None or event.template_id is None:
            continue
        delta = (event.occurred_at - source.occurred_at).total_seconds()
        samples.append(
            LatencySample(
                case_id=event.case_id,
                trigger=source.type.value,
                template_id=event.template_id,
                milliseconds=delta * 1000.0,
            )
        )
    return tuple(samples)


def latency_section(samples: Iterable[LatencySample]) -> dict[str, Any]:
    """The report section: counts and the distribution, per trigger.

    Percentiles use nearest-rank on the sorted samples - no interpolation, no
    numpy - because a p95 over 101 items is an order statistic and pretending
    otherwise would invent precision the measurement does not have.
    """
    collected = list(samples)
    by_trigger: dict[str, list[float]] = {}
    by_template: dict[str, int] = {}
    for sample in collected:
        by_trigger.setdefault(sample.trigger, []).append(sample.milliseconds)
        by_template[sample.template_id] = by_template.get(sample.template_id, 0) + 1
    return {
        "notification_count": len(collected),
        "by_template": dict(sorted(by_template.items())),
        "by_trigger": {
            trigger: _distribution(values)
            for trigger, values in sorted(by_trigger.items())
        },
        "overall": _distribution([sample.milliseconds for sample in collected]),
    }


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "min_ms": None,
            "median_ms": None,
            "p95_ms": None,
            "max_ms": None,
        }
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min_ms": round(ordered[0], 3),
        "median_ms": round(_percentile(ordered, 50), 3),
        "p95_ms": round(_percentile(ordered, 95), 3),
        "max_ms": round(ordered[-1], 3),
    }


def _percentile(ordered: Sequence[float], percent: float) -> float:
    """Nearest-rank percentile over an already-sorted sequence."""
    if not ordered:  # pragma: no cover - guarded by the caller
        return 0.0
    rank = max(1, min(len(ordered), _ceil(percent / 100.0 * len(ordered))))
    return ordered[rank - 1]


def _ceil(value: float) -> int:
    whole = int(value)
    return whole if value == whole else whole + 1
