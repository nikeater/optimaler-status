"""The valve as a property of the config format (ADR-004, schema half).

Fuzzes field names and operators against the two condition types. The point is
not that the current table is fine - it is that no table an agency could write
can reference anomaly evidence in a qualifying condition, or test it with an
operator that is not monotone in the anomaly direction.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from schemas.config import (
    ANOMALY_FIELD_PREFIX,
    DOWNGRADE_ANOMALY_FIELDS,
    QUALIFYING_FIELDS,
    DowngradeCondition,
    DowngradeRule,
    Op,
    QualifyingCondition,
)

ALL_OPS = [op.value for op in Op]
NON_MONOTONE_SCORE_OPS = [
    op for op in ALL_OPS if op not in DOWNGRADE_ANOMALY_FIELDS["anomaly.score"]
]
NON_EQ_OPS = [op for op in ALL_OPS if op != "eq"]

field_names = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=12
)


@given(suffix=field_names, op=st.sampled_from(ALL_OPS), value=st.booleans())
def test_qualifying_conditions_reject_any_anomaly_field(
    suffix: str, op: str, value: bool
) -> None:
    """No anomaly.* field can ever qualify an item for a better tier."""
    with pytest.raises(ValidationError):
        QualifyingCondition(
            field=f"{ANOMALY_FIELD_PREFIX}{suffix}", op=Op(op), value=value
        )


@given(field=field_names, op=st.sampled_from(ALL_OPS))
def test_qualifying_conditions_reject_unknown_fields(field: str, op: str) -> None:
    """Typos fail loudly at load time instead of silently never matching."""
    if field in QUALIFYING_FIELDS:  # pragma: no cover - generator cannot hit these
        return
    with pytest.raises(ValidationError):
        QualifyingCondition(field=field, op=Op(op), value=1)


@pytest.mark.parametrize("field", sorted(QUALIFYING_FIELDS))
def test_known_qualifying_fields_are_accepted(field: str) -> None:
    assert QualifyingCondition(field=field, op=Op.EQ, value=True).field == field


@given(field=field_names, op=st.sampled_from(ALL_OPS))
def test_downgrade_conditions_reject_non_anomaly_fields(field: str, op: str) -> None:
    """Downgrades read anomaly evidence and nothing else."""
    with pytest.raises(ValidationError):
        DowngradeCondition(field=field, op=Op(op), value=0.5)


@given(op=st.sampled_from(NON_MONOTONE_SCORE_OPS))
def test_downgrade_score_rejects_non_monotone_operators(op: str) -> None:
    """Only gt/ge: an operator like `lt` would let a higher score deactivate a
    downgrade, which is exactly how ML could raise a tier."""
    with pytest.raises(ValidationError):
        DowngradeCondition(field="anomaly.score", op=Op(op), value=0.5)


@given(op=st.sampled_from(NON_EQ_OPS))
def test_downgrade_flagged_rejects_non_eq_operators(op: str) -> None:
    with pytest.raises(ValidationError):
        DowngradeCondition(field="anomaly.flagged", op=Op(op), value=True)


def test_downgrade_flagged_rejects_false() -> None:
    """`flagged eq false` would fire on quiet items and downgrade the calm."""
    with pytest.raises(ValidationError):
        DowngradeCondition(field="anomaly.flagged", op=Op.EQ, value=False)


@pytest.mark.parametrize("op", ["gt", "ge"])
def test_downgrade_score_accepts_monotone_operators(op: str) -> None:
    condition = DowngradeCondition(field="anomaly.score", op=Op(op), value=0.85)
    assert condition.field == "anomaly.score"


def test_downgrade_rule_target_tier_is_fixed_at_three() -> None:
    """The schema pins the target: a downgrade cannot aim at tier 1 or 2."""
    with pytest.raises(ValidationError):
        DowngradeRule(
            row_id="bad",
            when_all=[
                DowngradeCondition(field="anomaly.flagged", op=Op.EQ, value=True)
            ],
            to_tier=1,  # type: ignore[arg-type]
            reason_template="never",
        )


def test_shipped_table_uses_only_valve_conforming_conditions(config: object) -> None:
    """Belt and braces: the table that ships parses under the same rules."""
    from engine.config_loader import ConfigBundle

    assert isinstance(config, ConfigBundle)
    for row in config.decision_table.rows:
        for condition in row.when_all:
            assert not condition.field.startswith(ANOMALY_FIELD_PREFIX)
            assert condition.field in QUALIFYING_FIELDS
    for downgrade in config.decision_table.downgrades:
        assert downgrade.to_tier == 3
        for anomaly_condition in downgrade.when_all:
            field = anomaly_condition.field
            assert field in DOWNGRADE_ANOMALY_FIELDS
            assert anomaly_condition.op.value in DOWNGRADE_ANOMALY_FIELDS[field]
