"""The one-way valve, property-tested end to end (non-negotiable gate).

ADR-004 enforces "ML may only add oversight" three times over: in the config
schema, in the DecisionRecord, and here - against the real interpreter and the
real table_v0, on every commit. If this file goes red, the scorer can lower a
tier and nothing else in the system is trustworthy.

Three properties:

a) monotone in the anomaly evidence: with the scorer enforcing, a higher score
   and/or a flag that is set can only produce a tier greater than or equal to
   the one a weaker anomaly produced;
b) one-way: anomaly evidence never produces a tier below the tier the same
   evidence gets with no anomaly at all, in either scorer mode;
c) deterministic: same inputs, same record (timestamps excluded).
"""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from engine.config_loader import ConfigBundle
from engine.decide import decide
from schemas.anomaly import AnomalyEvidence, AnomalyReason, ScorerMode
from schemas.common import Tier
from schemas.config import ProcedureFlags
from schemas.evidence import (
    CompletenessEvidence,
    CompletenessVerdict,
    EvidenceRecord,
    RoutingSource,
)
from tests.factories import (
    FIXED_NOW,
    TEST_VERSIONS,
    make_completeness,
    make_evidence,
    make_suggestion,
)

UNITS = ["Referat_312_Renten", "Referat_320_Reha", "Referat_390_Sonstiges"]
SCORER_MODES = ["log_only", "enforcing"]

confidences = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


@st.composite
def evidence_records(draw: st.DrawFn) -> EvidenceRecord:
    """Any valid evidence record the decision plane might be handed."""
    suggestions = [
        make_suggestion(
            draw(st.sampled_from(UNITS)),
            source=draw(st.sampled_from(list(RoutingSource))),
            confidence=draw(confidences),
            rule_ids=["rule_altersrente_hint"],
        )
        for _ in range(draw(st.integers(min_value=0, max_value=3)))
    ]
    verdict = draw(st.sampled_from(list(CompletenessVerdict)))
    gap_ids = draw(
        st.lists(
            st.sampled_from(["geburtsdatum", "rentenart"]), max_size=2, unique=True
        )
    )
    completeness: CompletenessEvidence = make_completeness(verdict, gap_ids=gap_ids)
    return make_evidence(
        routing=suggestions,
        completeness=completeness,
        min_confidence=draw(st.one_of(st.none(), confidences)),
        discarded_count=draw(st.integers(min_value=0, max_value=5)),
    )


def _anomaly(score: float, flagged: bool) -> AnomalyEvidence:
    return AnomalyEvidence(
        envelope_id="env-test",
        case_id="case-test",
        score=score,
        threshold_ref="anomaly_default_v0",
        flagged=flagged,
        reasons=[
            AnomalyReason(
                feature="konsistenz_rentenbeginn",
                observed="abweichend",
                expected="plausibel",
                contribution=0.5,
            )
        ]
        if flagged
        else [],
        mode=ScorerMode.ENFORCING,
        created_at=FIXED_NOW,
        versions=TEST_VERSIONS,
    )


@st.composite
def anomaly_pairs(draw: st.DrawFn) -> tuple[AnomalyEvidence, AnomalyEvidence]:
    """A weaker and a stronger anomaly: score and flag both monotone."""
    low_score = draw(confidences)
    high_score = draw(st.floats(min_value=low_score, max_value=1.0, allow_nan=False))
    low_flagged = draw(st.booleans())
    high_flagged = draw(st.booleans()) or low_flagged
    return _anomaly(low_score, low_flagged), _anomaly(high_score, high_flagged)


@st.composite
def procedure_flags(draw: st.DrawFn) -> ProcedureFlags | None:
    if draw(st.booleans()):
        return None
    return ProcedureFlags(
        procedure_id="altersrente",
        tier1_enabled=draw(st.booleans()),
        fully_automated=False,
    )


def _tier(
    config: ConfigBundle,
    evidence: EvidenceRecord,
    anomaly: AnomalyEvidence | None,
    flags: ProcedureFlags | None,
    clear_cut: bool,
    mode: str,
) -> Tier:
    risk = config.risk.model_copy(update={"scorer_mode": mode})
    return decide(
        evidence,
        anomaly,
        config.decision_table,
        risk,
        flags,
        clear_cut=clear_cut,
        now=FIXED_NOW,
    ).tier


@given(
    evidence=evidence_records(),
    pair=anomaly_pairs(),
    flags=procedure_flags(),
    clear_cut=st.booleans(),
)
def test_tier_is_monotone_in_anomaly_evidence(
    config: ConfigBundle,
    evidence: EvidenceRecord,
    pair: tuple[AnomalyEvidence, AnomalyEvidence],
    flags: ProcedureFlags | None,
    clear_cut: bool,
) -> None:
    """(a) Stronger anomaly evidence can only raise the tier, never lower it."""
    low, high = pair
    low_tier = _tier(config, evidence, low, flags, clear_cut, "enforcing")
    high_tier = _tier(config, evidence, high, flags, clear_cut, "enforcing")
    assert high_tier.value >= low_tier.value


@given(
    evidence=evidence_records(),
    pair=anomaly_pairs(),
    flags=procedure_flags(),
    clear_cut=st.booleans(),
    mode=st.sampled_from(SCORER_MODES),
)
def test_anomaly_never_lowers_the_deterministic_tier(
    config: ConfigBundle,
    evidence: EvidenceRecord,
    pair: tuple[AnomalyEvidence, AnomalyEvidence],
    flags: ProcedureFlags | None,
    clear_cut: bool,
    mode: str,
) -> None:
    """(b) No anomaly input can beat the no-anomaly tier, in any mode."""
    baseline = _tier(config, evidence, None, flags, clear_cut, mode)
    for anomaly in pair:
        with_anomaly = _tier(config, evidence, anomaly, flags, clear_cut, mode)
        assert with_anomaly.value >= baseline.value


@given(
    evidence=evidence_records(),
    pair=anomaly_pairs(),
    flags=procedure_flags(),
    clear_cut=st.booleans(),
    mode=st.sampled_from(SCORER_MODES),
)
def test_log_only_mode_never_changes_the_tier(
    config: ConfigBundle,
    evidence: EvidenceRecord,
    pair: tuple[AnomalyEvidence, AnomalyEvidence],
    flags: ProcedureFlags | None,
    clear_cut: bool,
    mode: str,
) -> None:
    """Log-only means log-only: identical tiers with and without the scorer."""
    baseline = _tier(config, evidence, None, flags, clear_cut, "log_only")
    for anomaly in pair:
        assert (
            _tier(config, evidence, anomaly, flags, clear_cut, "log_only") == baseline
        )


@given(
    evidence=evidence_records(),
    pair=anomaly_pairs(),
    flags=procedure_flags(),
    clear_cut=st.booleans(),
    mode=st.sampled_from(SCORER_MODES),
)
def test_pre_downgrade_tier_is_the_deterministic_tier(
    config: ConfigBundle,
    evidence: EvidenceRecord,
    pair: tuple[AnomalyEvidence, AnomalyEvidence],
    flags: ProcedureFlags | None,
    clear_cut: bool,
    mode: str,
) -> None:
    """pre_downgrade_tier must equal the tier the rows produce on their own."""
    risk = config.risk.model_copy(update={"scorer_mode": mode})
    baseline = _tier(config, evidence, None, flags, clear_cut, mode)
    for anomaly in pair:
        record = decide(
            evidence,
            anomaly,
            config.decision_table,
            risk,
            flags,
            clear_cut=clear_cut,
            now=FIXED_NOW,
        )
        assert record.pre_downgrade_tier == baseline
        assert record.tier.value >= record.pre_downgrade_tier.value


@given(
    evidence=evidence_records(),
    pair=anomaly_pairs(),
    flags=procedure_flags(),
    clear_cut=st.booleans(),
    mode=st.sampled_from(SCORER_MODES),
)
def test_decide_is_deterministic(
    config: ConfigBundle,
    evidence: EvidenceRecord,
    pair: tuple[AnomalyEvidence, AnomalyEvidence],
    flags: ProcedureFlags | None,
    clear_cut: bool,
    mode: str,
) -> None:
    """(c) Same inputs twice produce the identical record, timestamps aside."""
    risk = config.risk.model_copy(update={"scorer_mode": mode})
    anomaly = pair[1]

    def run() -> dict[str, Any]:
        record = decide(
            evidence,
            anomaly,
            config.decision_table,
            risk,
            flags,
            clear_cut=clear_cut,
        )
        dumped = record.model_dump(mode="json")
        dumped.pop("created_at")
        return dumped

    assert run() == run()
