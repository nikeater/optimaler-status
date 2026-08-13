"""The one-way valve against REAL evidence and the REAL table (part 09 gate).

``tests/test_decide_properties.py`` has proved monotonicity since part 01 over
SYNTHESIZED evidence records. That is the right shape for a property test and
it has one gap: a synthesized record is whatever a Hypothesis strategy happened
to build, and the claim an auditor cares about is about the records this system
actually produces. So this module runs gold v4 through the real pipeline once,
keeps the 101 real ``EvidenceRecord``s and the real ``AnomalyEvidence`` the
real scorer produced for them, and proves the same properties on those:

a) **Monotone.** Raising the anomaly evidence - a higher score, a flag that is
   set - can never produce a LOWER tier, in log-only and in a test-injected
   enforcing config.
b) **One-way.** No anomaly input ever beats the no-anomaly tier, in either mode.
c) **Log-only means log-only.** With the shipped config the tier is identical
   with and without the scorer, item by item.
d) **The audit sample only ever adds review.** For any rate, any salt and any
   item, the sampled tier is at least the unsampled tier, and the deterministic
   ``pre_downgrade_tier`` never moves.

The enforcing config is INJECTED (``model_copy``) rather than edited on disk,
which is the whole point: ``scorer_mode`` lives in the frozen
``config/thresholds.yaml``, so the shipped system cannot enforce without a
config supersession, and the test can still prove what would happen if it did.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.config_loader import ConfigBundle
from engine.decide import AUDIT_SAMPLE_RULE_ID, decide, evaluate_downgrades
from engine.journal.store import InMemoryJournalStore
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore
from eval.harness import load_corpus
from schemas.anomaly import AnomalyEvidence, AnomalyReason
from schemas.common import Tier
from schemas.config import ProcedureFlags
from schemas.decision import DecisionRecord
from schemas.evidence import EvidenceRecord
from tests.factories import FIXED_NOW

SCORER_MODES = ["log_only", "enforcing"]
SALT = "hypothesis-stichprobe-salz-2026"


@dataclass(frozen=True)
class ScoredItem:
    """One gold item as the real pipeline decided it, kept for re-deciding."""

    item_id: str
    evidence: EvidenceRecord
    anomaly: AnomalyEvidence | None
    tier: Tier
    flags: ProcedureFlags | None
    clear_cut: bool


@pytest.fixture(scope="session")
def scored_corpus(config: ConfigBundle, gold_v4_dir: Path) -> list[ScoredItem]:
    """Every gold v4 item, run once, with everything ``decide`` was given.

    The flags and the clear-cut verdict travel with the record on purpose: a
    property that re-decided a real item with DIFFERENT inputs would be proving
    something about a case this system never saw.
    """
    return [
        _scored(item.item_id, item.payload, config) for item in load_corpus(gold_v4_dir)
    ]


def _scored(
    item_id: str, payload: dict[str, object], config: ConfigBundle
) -> ScoredItem:
    outcome = run_pipeline(
        payload,
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
    )
    procedure = config.procedure(outcome.procedure_id)
    return ScoredItem(
        item_id=item_id,
        evidence=outcome.evidence,
        anomaly=outcome.anomaly,
        tier=outcome.decision.tier,
        flags=procedure.flags if procedure else None,
        clear_cut=outcome.clear_cut,
    )


def _decide(
    config: ConfigBundle,
    item: ScoredItem,
    anomaly: AnomalyEvidence | None,
    *,
    mode: str,
    rate: float = 0.0,
    salt: str | None = None,
) -> DecisionRecord:
    """Re-decide one real item with everything the pipeline gave ``decide``."""
    risk = config.risk.model_copy(
        update={"scorer_mode": mode, "audit_sample_rate": rate}
    )
    return decide(
        item.evidence,
        anomaly,
        config.decision_table,
        risk,
        item.flags,
        clear_cut=item.clear_cut,
        now=FIXED_NOW,
        audit_salt=salt,
    )


def _tier(
    config: ConfigBundle,
    item: ScoredItem,
    anomaly: AnomalyEvidence | None,
    *,
    mode: str,
    rate: float = 0.0,
    salt: str | None = None,
) -> Tier:
    return _decide(config, item, anomaly, mode=mode, rate=rate, salt=salt).tier


def _raise(evidence: AnomalyEvidence, score: float) -> AnomalyEvidence:
    """The same evidence with a higher score and the flag set."""
    return evidence.model_copy(
        update={
            "score": max(evidence.score, score),
            "flagged": True,
            "reasons": evidence.reasons
            or [
                AnomalyReason(
                    feature="leitdatum_abstand_jahre",
                    observed="Testwert",
                    expected="Referenzbereich",
                    contribution=0.5,
                )
            ],
        }
    )


@settings(max_examples=60)
@given(
    index=st.integers(min_value=0, max_value=100),
    score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    mode=st.sampled_from(SCORER_MODES),
)
def test_raising_real_anomaly_evidence_never_lowers_a_real_tier(
    config: ConfigBundle,
    scored_corpus: list[ScoredItem],
    index: int,
    score: float,
    mode: str,
) -> None:
    """(a) On the records this system produces, against the shipped table."""
    item = scored_corpus[index % len(scored_corpus)]
    baseline = _tier(config, item, item.anomaly, mode=mode)
    stronger = _tier(
        config,
        item,
        _raise(item.anomaly, score) if item.anomaly is not None else None,
        mode=mode,
    )
    assert stronger.value >= baseline.value


@settings(max_examples=60)
@given(
    index=st.integers(min_value=0, max_value=100),
    score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    mode=st.sampled_from(SCORER_MODES),
)
def test_anomaly_never_beats_the_no_anomaly_tier_on_real_records(
    config: ConfigBundle,
    scored_corpus: list[ScoredItem],
    index: int,
    score: float,
    mode: str,
) -> None:
    """(b) The valve, on real evidence, in both modes."""
    item = scored_corpus[index % len(scored_corpus)]
    without = _tier(config, item, None, mode=mode)
    candidates = [item.anomaly]
    if item.anomaly is not None:
        candidates.append(_raise(item.anomaly, score))
    for candidate in candidates:
        assert _tier(config, item, candidate, mode=mode).value >= without.value


def test_log_only_moves_no_tier_on_the_whole_gold_set(
    config: ConfigBundle, scored_corpus: list[ScoredItem]
) -> None:
    """(c) The hard regression identity, item by item rather than in aggregate."""
    assert config.risk.scorer_mode == "log_only"
    for item in scored_corpus:
        assert item.tier == _tier(config, item, None, mode="log_only"), item.item_id
        assert item.tier == _tier(config, item, item.anomaly, mode="log_only"), (
            item.item_id
        )


def test_enforcing_would_move_only_downgraded_items_and_only_upward(
    config: ConfigBundle, scored_corpus: list[ScoredItem]
) -> None:
    """What the injected enforcing config would actually do, spelled out.

    Not a claim about the shipped system - it is log-only and stays there - but
    the measurement that makes "log-only is a choice" mean something: with the
    switch flipped, exactly the items a downgrade row fired on move, and every
    one of them moves to tier 3.
    """
    moved = []
    for item in scored_corpus:
        log_only = _decide(config, item, item.anomaly, mode="log_only")
        enforcing = _decide(config, item, item.anomaly, mode="enforcing")
        assert enforcing.tier.value >= log_only.tier.value, item.item_id
        assert enforcing.pre_downgrade_tier == log_only.pre_downgrade_tier, item.item_id
        if enforcing.tier != log_only.tier:
            fired = [
                outcome
                for outcome in evaluate_downgrades(
                    item.anomaly, config.decision_table, enforcing=True
                )
                if outcome.fired
            ]
            assert fired, item.item_id
            assert enforcing.tier is Tier.FULL_HUMAN_REVIEW, item.item_id
            moved.append(item.item_id)
    assert moved, "no item would move at all: the enforcing branch is untested"


def test_the_tables_hard_coded_score_row_is_looser_than_the_calibrated_flag(
    config: ConfigBundle, scored_corpus: list[ScoredItem]
) -> None:
    """A finding, pinned so the next table supersession has to face it.

    table_v1's second downgrade row fires on ``anomaly.score >= 0.85``, a
    number written in part 01 when no score scale existed. The calibrated flag
    threshold is 0.86 on a percentile scale, so the two disagree by one notch
    and the TABLE is the looser of the pair: an enforcing run would downgrade
    two items the scorer did not flag.

    Harmless today - log-only applies nothing, and the disagreement can only
    ADD oversight, which is the valve working exactly as designed. But the
    table's number is a bare literal with no calibration behind it, and
    table_v1's version is frozen into the gold manifest, so aligning them costs
    a table supersession. Recorded rather than quietly worked around.
    """
    assert config.scoring is not None
    table_bound = min(
        float(condition.value)
        for rule in config.decision_table.downgrades
        for condition in rule.when_all
        if condition.field == "anomaly.score"
        and isinstance(condition.value, int | float)
    )
    assert table_bound < config.scoring.threshold.value
    between = [
        item.item_id
        for item in scored_corpus
        if item.anomaly is not None
        and not item.anomaly.flagged
        and item.anomaly.score >= table_bound
    ]
    assert between == [
        "ar-0052-mehrere-maengel",
        "sf-0023-beginn-vor-dem-14-lebensjahr",
    ], between
    # Both are already tier 2 for deterministic reasons, so even under the
    # looser row the disagreement adds oversight to items nobody was going to
    # clear. That is the reading that makes this a finding rather than a bug.
    assert all(
        item.tier is not Tier.CLEAR_AND_COMPLETE
        for item in scored_corpus
        if item.item_id in between
    )


@settings(max_examples=60)
@given(
    index=st.integers(min_value=0, max_value=100),
    rate=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    salt=st.sampled_from([SALT, "anderes-salz-fuer-die-stichprobe"]),
    mode=st.sampled_from(SCORER_MODES),
)
def test_audit_sampling_only_ever_adds_review(
    config: ConfigBundle,
    scored_corpus: list[ScoredItem],
    index: int,
    rate: float,
    salt: str,
    mode: str,
) -> None:
    """(d) P-1 is valve-compatible by construction, and here by measurement."""
    item = scored_corpus[index % len(scored_corpus)]
    unsampled = _tier(config, item, item.anomaly, mode=mode)
    sampled = _tier(config, item, item.anomaly, mode=mode, rate=rate, salt=salt)
    assert sampled.value >= unsampled.value


def test_sampling_never_moves_the_pre_downgrade_tier(
    config: ConfigBundle, scored_corpus: list[ScoredItem]
) -> None:
    """The deterministic tier is what the ROWS said, and sampling is not a row."""
    for item in scored_corpus:
        plain = _decide(config, item, item.anomaly, mode="log_only")
        sampled = _decide(
            config, item, item.anomaly, mode="log_only", rate=1.0, salt=SALT
        )
        assert sampled.pre_downgrade_tier == plain.pre_downgrade_tier, item.item_id
        assert sampled.tier.value >= plain.tier.value, item.item_id


def test_sampling_at_rate_one_pulls_every_item_the_rules_had_cleared(
    config: ConfigBundle, scored_corpus: list[ScoredItem]
) -> None:
    """The other end of the range, so 'only adds review' is not vacuously true."""
    pulled = 0
    for item in scored_corpus:
        plain = _decide(config, item, item.anomaly, mode="log_only")
        sampled = _decide(
            config, item, item.anomaly, mode="log_only", rate=1.0, salt=SALT
        )
        assert sampled.tier is Tier.FULL_HUMAN_REVIEW, item.item_id
        drawn = [
            reason
            for reason in sampled.reasons
            if reason.rule_id == AUDIT_SAMPLE_RULE_ID
        ]
        if plain.tier is Tier.FULL_HUMAN_REVIEW:
            # Already in full review: the sample does not pull it, because a
            # reason claiming a move that did not happen is a lie in the trail.
            assert not drawn, item.item_id
        else:
            assert len(drawn) == 1, item.item_id
            pulled += 1
    assert pulled == sum(
        1 for item in scored_corpus if item.tier is not Tier.FULL_HUMAN_REVIEW
    )


def test_a_sampled_item_says_it_was_sampled_and_not_that_it_is_suspicious(
    config: ConfigBundle, scored_corpus: list[ScoredItem]
) -> None:
    """The reason text is the control here: a drawn item is not a flagged one.

    An applicant whose case was pulled at random and who is then treated as
    suspect has been harmed by a measure that exists to protect them, so the
    sentence has to say what happened in words a caseworker reads.
    """
    cleared = [
        item for item in scored_corpus if item.tier is not Tier.FULL_HUMAN_REVIEW
    ]
    record = _decide(
        config, cleared[0], cleared[0].anomaly, mode="log_only", rate=1.0, salt=SALT
    )
    drawn = [
        reason for reason in record.reasons if reason.rule_id == AUDIT_SAMPLE_RULE_ID
    ]
    assert len(drawn) == 1
    assert record.tier is Tier.FULL_HUMAN_REVIEW
    detail = drawn[0].detail
    assert "Zufallsstichprobe" in detail
    assert "KEIN Auffaelligkeitsbefund" in detail
    assert "88 Abs. 5 Nr. 1 AO" in detail


def test_the_shipped_config_cannot_enforce(config: ConfigBundle) -> None:
    """Log-only is structural: it lives in a frozen, manifest-pinned config.

    ``config/thresholds.yaml``'s version is recorded in
    ``corpus/gold/v4/MANIFEST.yaml``, which the corpus check verifies by a
    byte-identical rebuild. Switching to enforcing therefore costs a config
    supersession, which is exactly the amount of friction the decision deserves
    - and this test is where that stops being a comment.
    """
    assert config.risk.scorer_mode == "log_only"
    assert config.risk.audit_sample_rate == 0.0
    assert config.scoring is not None
    assert config.scoring.threshold.threshold_id not in {
        threshold.threshold_id for threshold in config.risk.thresholds
    }


def test_the_real_scorer_produced_evidence_for_every_item(
    scored_corpus: list[ScoredItem],
) -> None:
    """No silent gaps: a property over 'the real anomaly' needs a real anomaly."""
    missing = [item.item_id for item in scored_corpus if item.anomaly is None]
    assert not missing, f"the scorer produced nothing for {missing}"
    flagged = [
        item.item_id
        for item in scored_corpus
        if item.anomaly is not None and item.anomaly.flagged
    ]
    assert flagged, "nothing was flagged; the monotonicity properties would be vacuous"
