"""Clear-cut evaluation (ADR-007).

"Is this the simple, unambiguous variant of the procedure?" is a fachliche
question the agency answers in config, not a property the engine may infer. The
criteria are a predicate over the same evaluation context the routing rules use;
the boolean result travels into the decision table as ``procedure.clear_cut``.

Unknown procedure, or a procedure without criteria, is False: absent criteria
must never qualify an item for tier 1.
"""

from __future__ import annotations

from engine.config_loader import ClearCutCriteria
from engine.predicate import Context, parse_predicate


def evaluate_clear_cut(criteria: ClearCutCriteria | None, context: Context) -> bool:
    """Evaluate a procedure's clear-cut criteria; missing criteria are False."""
    if criteria is None:
        return False
    return parse_predicate(criteria.predicate).evaluate(context)
