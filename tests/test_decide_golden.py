"""Golden-file tests: evidence in, tier and reasons out, against table_v0.

Every file in ``tests/golden/`` is one documented case. Adding a row to the
decision table that changes any of these outcomes should break here loudly,
which is the point: the table is the product's behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from engine.config_loader import ConfigBundle
from engine.decide import decide
from schemas.config import ProcedureFlags
from schemas.evidence import CompletenessVerdict, RoutingSource
from tests.factories import (
    FIXED_NOW,
    make_anomaly,
    make_completeness,
    make_evidence,
    make_suggestion,
)

GOLDEN_DIR = Path(__file__).parent / "golden"
GOLDEN_FILES = sorted(GOLDEN_DIR.glob("*.yaml"))


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _build_evidence(spec: dict[str, Any]) -> Any:
    completeness_spec = spec["completeness"]
    completeness = make_completeness(
        CompletenessVerdict(completeness_spec["verdict"]),
        procedure_id=completeness_spec.get("procedure_id", "altersrente"),
        gap_ids=completeness_spec.get("gap_ids", []),
        requirements_version=completeness_spec.get(
            "requirements_version", "altersrente_requirements_v0"
        ),
    )
    routing = [
        make_suggestion(
            item["unit_id"],
            source=RoutingSource(item["source"]),
            confidence=item["confidence"],
            rule_ids=item.get("rule_ids", []),
        )
        for item in spec["routing"]
    ]
    return make_evidence(
        routing=routing,
        completeness=completeness,
        min_confidence=spec.get("min_confidence"),
        discarded_count=spec.get("discarded_count", 0),
    )


def test_golden_dir_is_not_empty() -> None:
    assert GOLDEN_FILES, "no golden decision fixtures found"


@pytest.mark.parametrize("path", GOLDEN_FILES, ids=lambda path: path.stem)
def test_golden_decision(path: Path, config: ConfigBundle) -> None:
    case = _load(path)
    evidence = _build_evidence(case["evidence"])
    anomaly_spec = case.get("anomaly")
    anomaly = (
        make_anomaly(score=anomaly_spec["score"], flagged=anomaly_spec["flagged"])
        if anomaly_spec
        else None
    )
    flags = ProcedureFlags.model_validate(case["flags"]) if case["flags"] else None
    risk = config.risk.model_copy(update={"scorer_mode": case["scorer_mode"]})

    record = decide(
        evidence,
        anomaly,
        config.decision_table,
        risk,
        flags,
        clear_cut=case["clear_cut"],
        now=FIXED_NOW,
    )

    expected = case["expect"]
    assert int(record.tier) == expected["tier"], case["description"]
    assert int(record.pre_downgrade_tier) == expected["pre_downgrade_tier"]
    assert record.routed_unit_id == expected["routed_unit_id"]
    assert [reason.kind.value for reason in record.reasons] == expected["reason_kinds"]
    assert [reason.rule_id for reason in record.reasons] == expected["reason_rule_ids"]
    assert record.decision_table_version == config.decision_table.version
    assert record.risk_config_version == risk.version
    # Structural half of the one-way valve, on every golden case.
    assert int(record.tier) >= int(record.pre_downgrade_tier)


def test_log_only_downgrade_records_no_downgrade_reason(
    config: ConfigBundle,
) -> None:
    """The record must not claim a downgrade the engine did not apply."""
    record = decide(
        make_evidence(),
        make_anomaly(score=0.99, flagged=True),
        config.decision_table,
        config.risk,  # ships as log_only
        ProcedureFlags(
            procedure_id="altersrente", tier1_enabled=True, fully_automated=False
        ),
        clear_cut=True,
        now=FIXED_NOW,
    )
    assert int(record.tier) == 1
    assert all(reason.kind.value != "downgraded" for reason in record.reasons)
