"""ADR-025 migration: an audit draw carries its own reason kind.

Part 09 had to express "the dice picked this item" as ``ReasonKind.DOWNGRADED``
with a dedicated rule id, because the contract enum had no better member. That
worked mechanically and misled semantically, and part 10 is where it stops:
``decide`` emits ``ReasonKind.SAMPLED``, the rule id stays ``audit_sample`` as
the stable identifier of the draw rule, and every READER in the repository
accepts both shapes because a journal is append-only and last month's entries
still say ``DOWNGRADED``.

The distinction is worth a test file rather than an assertion: a caseworker who
reads a random quality-assurance draw as a machine suspicion starts the review
biased, and the whole of P-1 is that the draw says nothing about the item.
"""

from __future__ import annotations

import pytest

from engine.config_loader import ConfigBundle, load_config
from engine.decide import (
    AUDIT_SAMPLE_RULE_ID,
    decide,
    is_audit_sample_reason,
)
from schemas.anomaly import AnomalyEvidence
from schemas.common import Tier
from schemas.config import ProcedureFlags
from schemas.decision import DecisionRecord, ReasonKind
from tests.factories import make_anomaly, make_evidence

SALT = "test-salt-2026"

#: A procedure whose tier-1 row can qualify at all - the audit sample only ever
#: pulls items the rules cleared, so a fixture that always lands on tier 3
#: would test nothing.
TIER1_FLAGS = ProcedureFlags(
    procedure_id="altersrente", tier1_enabled=True, fully_automated=False
)


@pytest.fixture(scope="module")
def config() -> ConfigBundle:
    return load_config()


def _decide(
    config: ConfigBundle, *, rate: float, anomaly: AnomalyEvidence | None
) -> DecisionRecord:
    risk = config.risk.model_copy(update={"audit_sample_rate": rate})
    return decide(
        make_evidence(),
        anomaly,
        config.decision_table,
        risk,
        TIER1_FLAGS,
        clear_cut=True,
        audit_salt=SALT,
    )


def test_a_drawn_item_carries_the_sampled_kind_not_the_downgraded_one(
    config: ConfigBundle,
) -> None:
    record = _decide(config, rate=1.0, anomaly=None)
    drawn = [
        reason for reason in record.reasons if reason.rule_id == AUDIT_SAMPLE_RULE_ID
    ]
    assert len(drawn) == 1
    assert drawn[0].kind is ReasonKind.SAMPLED
    assert record.tier is Tier.FULL_HUMAN_REVIEW
    # And no reason on the record claims a downgrade, because none happened.
    assert not [
        reason for reason in record.reasons if reason.kind is ReasonKind.DOWNGRADED
    ]


def test_an_anomaly_downgrade_keeps_the_downgraded_kind(config: ConfigBundle) -> None:
    """The other half of the split: SAMPLED did not swallow the valve's kind."""
    risk = config.risk.model_copy(
        update={"scorer_mode": "enforcing", "audit_sample_rate": 0.0}
    )
    record = decide(
        make_evidence(),
        make_anomaly(score=0.99, flagged=True),
        config.decision_table,
        risk,
        TIER1_FLAGS,
        clear_cut=True,
    )
    kinds = {reason.kind for reason in record.reasons}
    assert ReasonKind.DOWNGRADED in kinds
    assert ReasonKind.SAMPLED not in kinds


def test_the_shipped_rate_draws_nobody(config: ConfigBundle) -> None:
    """0.0 is the shipped value, so the migration moves nothing on gold."""
    assert config.risk.audit_sample_rate == 0.0
    record = _decide(config, rate=0.0, anomaly=None)
    assert not [
        reason for reason in record.reasons if reason.rule_id == AUDIT_SAMPLE_RULE_ID
    ]


@pytest.mark.parametrize(
    ("kind", "rule_id", "expected"),
    [
        # The new shape, as an enum and as the string a journal payload holds.
        (ReasonKind.SAMPLED, AUDIT_SAMPLE_RULE_ID, True),
        ("sampled", AUDIT_SAMPLE_RULE_ID, True),
        # A SAMPLED kind is an audit draw whatever rule id rides with it: the
        # kind is the contract, the rule id identifies which draw rule.
        ("sampled", "some_future_sample_rule", True),
        # The OLD shape, which every journal written before part 10 carries.
        (ReasonKind.DOWNGRADED, AUDIT_SAMPLE_RULE_ID, True),
        ("downgraded", AUDIT_SAMPLE_RULE_ID, True),
        # A real anomaly downgrade, in either era, is not an audit draw.
        ("downgraded", "dg_anomaly_flagged", False),
        (ReasonKind.QUALIFIED, "row_tier1", False),
        # Malformed payloads degrade, they do not raise (part-01 discipline).
        (None, None, False),
        (17, AUDIT_SAMPLE_RULE_ID, False),
        ("downgraded", 17, False),
    ],
)
def test_readers_accept_both_shapes(
    kind: object, rule_id: object, expected: bool
) -> None:
    assert is_audit_sample_reason(kind, rule_id) is expected
