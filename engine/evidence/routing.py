"""Rules-first routing evidence with explicit arbitration (ADR-014).

Rules are deterministic and auditable, so they run first and, when they fire,
carry confidence 1.0. Part 02 shipped them with an implicit arbitration policy:
every hit had confidence 1.0 and the decision plane picked the first, so *file
order* decided which unit won a disagreement. That is a policy hidden in a
diff-sensitive place, and it is silent - nothing recorded that two units had
been proposed at all.

Arbitration is now explicit:

* Every rule carries an integer ``priority`` (lower wins), so (priority,
  rule_id) is a **total order** over rules. Shuffling the file cannot change
  the winner.
* Suggestions are grouped per unit; a unit inherits the best order key of the
  rules that proposed it. Units are emitted in that order, winner first.
* Two or more units proposed at all is a **recorded conflict**, listed in the
  evidence_assembled event with the rules that produced each candidate.
* A conflict the priorities do **not** resolve - the two best units share the
  same priority - is *unresolved*: the winner keeps its place in the order but
  its confidence drops below the tier-1 and tier-2 thresholds, so a contested
  item ends up in front of a human instead of being routed with a shrug.

Part 03 had only one lever for this, confidence, because ``EvidenceRecord`` had
no field for arbitration and the losing candidates survived in the journal
alone. ADR-016 added ``EvidenceRecord.conflicts``, so :meth:`RoutingOutcome.
as_conflicts` now hands the same facts to the record as first-class evidence.
The journal payload is unchanged: two readers, one source.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from engine.config_loader import rule_order_key
from engine.predicate import Context, PredicateNode, parse_predicate
from schemas.config import RoutingRule
from schemas.evidence import RoutingConflict, RoutingSource, RoutingSuggestion

#: Confidence of an uncontested rule hit.
RULE_CONFIDENCE = 1.0

#: Confidence of the winner when priorities could not resolve the conflict.
#: Deliberately below the tier-1 (0.9) and tier-2 (0.9) qualifying thresholds
#: in ``config/decision/``: an unresolved conflict must cost oversight.
CONTESTED_CONFIDENCE = 0.6

#: Confidence of every unit that lost arbitration. They stay in the record as
#: evidence for the caseworker, and can never win ``max(confidence)``.
ALTERNATIVE_CONFIDENCE = 0.5


@dataclass(frozen=True)
class UnitCandidate:
    """One organizational unit proposed by one or more rules."""

    unit_id: str
    rule_ids: tuple[str, ...]
    priority: int

    @property
    def order_key(self) -> tuple[int, str]:
        return (self.priority, self.rule_ids[0])


@dataclass(frozen=True)
class RoutingOutcome:
    """Everything arbitration decided, and everything it had to discard."""

    suggestions: list[RoutingSuggestion] = field(default_factory=list)
    candidates: tuple[UnitCandidate, ...] = ()
    unresolved: bool = False

    @property
    def winner_unit_id(self) -> str | None:
        """The unit the decision plane will route to, or None."""
        return self.candidates[0].unit_id if self.candidates else None

    @property
    def conflicts(self) -> tuple[UnitCandidate, ...]:
        """Units that were proposed and lost."""
        return self.candidates[1:]

    def as_payload(self) -> dict[str, object]:
        """Journal-shaped view for the evidence_assembled event."""
        return {
            "winner_unit_id": self.winner_unit_id,
            "unresolved": self.unresolved,
            "candidates": [
                {
                    "unit_id": candidate.unit_id,
                    "rule_ids": list(candidate.rule_ids),
                    "priority": candidate.priority,
                }
                for candidate in self.candidates
            ],
        }

    def as_conflicts(self) -> list[RoutingConflict]:
        """Contract-shaped conflicts for ``EvidenceRecord.conflicts`` (ADR-016).

        One entry per contested item, not per losing unit: the fact worth
        recording is that these units were proposed *for the same item* and how
        the tie was settled. A single proposal is agreement, not a conflict, and
        produces an empty list rather than an entry saying so.
        """
        if len(self.candidates) < 2:
            return []
        return [
            RoutingConflict(
                unit_ids=[candidate.unit_id for candidate in self.candidates],
                resolved_by="unresolved" if self.unresolved else "priority",
                detail=(
                    "Prioritaeten loesen den Konflikt nicht auf "
                    f"(gleiche Prioritaet {self.candidates[0].priority}); "
                    "die Confidence des Gewinners sinkt"
                    if self.unresolved
                    else (
                        f"{self.candidates[0].unit_id} gewinnt mit Prioritaet "
                        f"{self.candidates[0].priority} vor "
                        f"{self.candidates[1].unit_id} "
                        f"({self.candidates[1].priority})"
                    )
                ),
            )
        ]


class RoutingEngine:
    """Evaluates routing rules against an evaluation context and arbitrates."""

    def __init__(self, rules: Sequence[RoutingRule]) -> None:
        self._rules: list[tuple[RoutingRule, PredicateNode]] = [
            (rule, parse_predicate(rule.predicate)) for rule in rules
        ]

    def matching_rules(self, context: Context) -> list[RoutingRule]:
        """Rules whose predicate holds, in arbitration order."""
        return sorted(
            (rule for rule, node in self._rules if node.evaluate(context)),
            key=rule_order_key,
        )

    def arbitrate(self, context: Context) -> RoutingOutcome:
        """Full arbitration outcome: suggestions, candidates and conflicts."""
        candidates = _candidates(self.matching_rules(context))
        if not candidates:
            return RoutingOutcome()
        unresolved = (
            len(candidates) > 1 and candidates[0].priority == candidates[1].priority
        )
        winner_confidence = CONTESTED_CONFIDENCE if unresolved else RULE_CONFIDENCE
        suggestions = [
            RoutingSuggestion(
                unit_id=candidate.unit_id,
                source=RoutingSource.RULE,
                rule_ids=list(candidate.rule_ids),
                confidence=winner_confidence if index == 0 else ALTERNATIVE_CONFIDENCE,
                evidence_span=None,  # structured payload, no text passage
            )
            for index, candidate in enumerate(candidates)
        ]
        return RoutingOutcome(
            suggestions=suggestions, candidates=candidates, unresolved=unresolved
        )

    def suggest(self, context: Context) -> list[RoutingSuggestion]:
        """Routing suggestions for one item, at most one per unit."""
        return self.arbitrate(context).suggestions


def _candidates(rules: Sequence[RoutingRule]) -> tuple[UnitCandidate, ...]:
    """Group matching rules per unit and order the units deterministically."""
    by_unit: dict[str, list[RoutingRule]] = {}
    for rule in rules:
        by_unit.setdefault(rule.unit_id, []).append(rule)
    candidates = [
        UnitCandidate(
            unit_id=unit_id,
            rule_ids=tuple(rule.rule_id for rule in sorted(hits, key=rule_order_key)),
            priority=min(rule.priority for rule in hits),
        )
        for unit_id, hits in by_unit.items()
    ]
    return tuple(sorted(candidates, key=lambda candidate: candidate.order_key))
