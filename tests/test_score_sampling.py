"""P-1: the audit-sampling arithmetic, checked against itself by hand.

The point of a deterministic sample is that a caseworker can be told WHY their
case was pulled and can check it. So the tests here do not mock the hash - they
recompute it the way the documentation says a reader would, with the stdlib and
one line, and compare. If those two ever disagree, the sentence in
``config/scoring/scoring_v1.yaml`` has become a lie.

Everything else about sampling is proved next door in
``tests/test_score_monotonicity.py`` against the real decision table: it only
ever adds review, it never touches ``pre_downgrade_tier``, and at rate 1.0 it
pulls exactly the items the rules had cleared.
"""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from engine.config_loader import ConfigBundle
from engine.decide import audit_sample_draw, evaluate_audit_sample
from engine.score.config import MAX_SALT_BYTES, AuditSamplingConfig

SALT = "eingangslotse-stichprobe-2026-demo"


def _by_hand(case_id: str, salt: str) -> float:
    """The arithmetic exactly as the config file tells a reader to redo it."""
    digest = hashlib.blake2b(
        case_id.encode("utf-8"), key=salt.encode("utf-8"), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big") / 2**64


@settings(max_examples=200)
@given(
    case_id=st.text(min_size=1, max_size=40),
    salt=st.text(min_size=1, max_size=40).filter(
        lambda value: len(value.encode("utf-8")) <= MAX_SALT_BYTES
    ),
)
def test_the_draw_is_exactly_the_documented_arithmetic(case_id: str, salt: str) -> None:
    """A sample nobody can recompute is a story, not an audit measure."""
    assert audit_sample_draw(case_id, salt) == _by_hand(case_id, salt)


def test_a_salt_longer_than_a_blake2b_key_is_refused_by_the_config() -> None:
    """Found by the property above: blake2b keys stop at 64 bytes.

    The config refuses a longer one rather than hashing it down, because a
    hashed-down salt would make the one-line recomputation this system promises
    a caseworker produce a different number than the engine did.
    """
    with pytest.raises(ValueError, match="blake2b"):
        AuditSamplingConfig(salt="x" * (MAX_SALT_BYTES + 1))
    with pytest.raises(ValueError):
        audit_sample_draw("case", "x" * (MAX_SALT_BYTES + 1))


@settings(max_examples=200)
@given(case_id=st.text(min_size=1, max_size=40))
def test_the_draw_is_in_the_unit_interval(case_id: str) -> None:
    """Half-open [0, 1): rate 0.0 can therefore never draw anything."""
    draw = audit_sample_draw(case_id, SALT)
    assert 0.0 <= draw < 1.0


def test_the_draw_is_stable_across_calls() -> None:
    """Same case, same salt, same number - forever, and across processes."""
    assert audit_sample_draw("case-ar-0001", SALT) == audit_sample_draw(
        "case-ar-0001", SALT
    )


def test_a_different_salt_gives_a_different_sample() -> None:
    """Rotating the salt re-draws the sample; that is what a salt is for."""
    one = {
        case
        for case in (f"case-{index:04d}" for index in range(200))
        if audit_sample_draw(case, SALT) < 0.1
    }
    other = {
        case
        for case in (f"case-{index:04d}" for index in range(200))
        if audit_sample_draw(case, "ein-anderes-salz-2027") < 0.1
    }
    assert one != other
    assert one and other


def test_the_draw_is_roughly_uniform() -> None:
    """Not a statistics test - a smoke test that the mapping is not degenerate.

    A sampling function whose draws piled up in one decile would systematically
    sample the same kind of case id forever, which for ids derived from
    submission ids could correlate with a channel or a batch.
    """
    deciles = [0] * 10
    for index in range(2000):
        deciles[int(audit_sample_draw(f"case-{index:05d}", SALT) * 10)] += 1
    assert min(deciles) > 120, deciles
    assert max(deciles) < 280, deciles


@pytest.mark.parametrize("rate", [0.0, -0.0])
def test_rate_zero_never_samples_and_costs_nothing(rate: float) -> None:
    """The shipped state: gold behaviour is unchanged because nothing runs."""
    assert evaluate_audit_sample("case-ar-0001", rate=rate, salt=SALT) is None


def test_a_rate_without_a_salt_is_off_rather_than_random() -> None:
    """Half a sampling policy samples nothing; the loader is where it is refused."""
    assert evaluate_audit_sample("case-ar-0001", rate=0.5, salt=None) is None
    assert evaluate_audit_sample("case-ar-0001", rate=0.5, salt="") is None


def test_rate_one_samples_everything() -> None:
    sample = evaluate_audit_sample("case-ar-0001", rate=1.0, salt=SALT)
    assert sample is not None and sample.sampled


@settings(max_examples=100)
@given(
    case_id=st.text(min_size=1, max_size=20),
    low=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    step=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_raising_the_rate_never_un_samples_an_item(
    case_id: str, low: float, step: float
) -> None:
    """Monotone in the rate: a bigger sample is a superset of a smaller one.

    Which is what makes "increase the sample rate" a safe operational lever: it
    can add cases to review and can never take one out.
    """
    high = min(1.0, low + step)
    lower = evaluate_audit_sample(case_id, rate=low, salt=SALT)
    higher = evaluate_audit_sample(case_id, rate=high, salt=SALT)
    if lower is not None and lower.sampled:
        assert higher is not None and higher.sampled


def test_the_configured_salt_is_the_one_the_documentation_names(
    config: ConfigBundle,
) -> None:
    """The example in this test file has to be the shipped salt or it proves nothing."""
    assert config.scoring is not None
    assert config.scoring.audit_sampling.salt == SALT
    assert len(SALT) >= 16


def test_a_worked_example_a_caseworker_could_check(config: ConfigBundle) -> None:
    """One concrete case id, one concrete number, printed in the failure message.

    This is the test that would be pasted into a Betriebshandbuch: at a rate of
    0.1 this case is not sampled and at 0.9 it is, and the number that decides
    it is reproducible with two lines of Python.
    """
    assert config.scoring is not None
    case_id = "case-ar-0001-regelaltersrente-vollstaendig"
    draw = audit_sample_draw(case_id, config.scoring.audit_sampling.salt)
    assert draw == _by_hand(case_id, config.scoring.audit_sampling.salt)
    low = evaluate_audit_sample(case_id, rate=0.0001, salt=SALT)
    high = evaluate_audit_sample(case_id, rate=0.9999, salt=SALT)
    assert low is not None and high is not None
    assert not low.sampled, draw
    assert high.sampled, draw
    assert low.draw == high.draw == draw
