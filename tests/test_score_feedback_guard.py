"""P-3: the feedback-loop guard, as a normative suite over BOTH model paths.

The toeslagenaffaire and SyRI lesson, written into ``schemas/anomaly.py`` in
part 01 and into ADR-016 after the research pass: a scoring system that can see
its own earlier output about a person stops measuring the person and starts
measuring itself. An earlier flag raises the next score, the next flag raises
the one after that, and after a while the file says what the system already
believed.

This repository has no per-applicant store at all, so the guard is not about
removing a feature - it is about making sure nobody can add one without
noticing. Three checks, over the two probabilistic components this system has:

1. **The scorer's input TYPE admits nothing forbidden.** Checked by inspecting
   the annotations of :class:`engine.score.ScoringInput`, so a future field
   whose type is a vault, a witness, a journal, a draft store, prior anomaly
   evidence or a sequence of events fails here rather than in review.
2. **Neither component ever sees a sealed value.** The scorer refuses one at
   the guard; the part-06 classifier masks placeholders before embedding
   (the nondeterminism bug it actually had). Both are checked against the same
   part-04 definition, in one place, per the part-06 finding.
3. **The feature set is disjoint from the decision table's own vocabulary.**
   The mirror image of the one-way valve: no feature may restate a qualifying
   field, or the flag stops being independent of the tier.
"""

from __future__ import annotations

import inspect
import typing
from pathlib import Path

import pytest

from engine.config_loader import ConfigBundle
from engine.evidence import render_item_text
from engine.evidence.classify import unit_texts
from engine.journal.store import InMemoryJournalStore, JournalStore
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore, Witness, contains_placeholder
from engine.redact.placeholders import PLACEHOLDER_SHAPED_RE
from engine.redact.vault import VaultStore
from engine.score import FEATURE_IDS, ScoringInput, build_features
from engine.score.features import (
    DEVIATION_FEATURES,
    QUALIFYING_FIELD_ECHOES,
    FeatureGuardError,
    _guarded,
)
from schemas.anomaly import AnomalyEvidence, AnomalyReason
from schemas.config import QUALIFYING_FIELDS
from schemas.events import Event

#: Types a feature builder may never be handed. Named rather than inferred: a
#: list an engineer has to edit is a list an engineer has to think about.
FORBIDDEN_TYPES = (
    VaultStore,
    Witness,
    JournalStore,
    AnomalyEvidence,
    AnomalyReason,
    Event,
)


def _annotations(target: type) -> dict[str, object]:
    return typing.get_type_hints(target)


def _mentions_forbidden(annotation: object) -> list[str]:
    """Every forbidden type reachable from one annotation, including in a list."""
    found: list[str] = []
    for candidate in (annotation, *typing.get_args(annotation)):
        for forbidden in FORBIDDEN_TYPES:
            if candidate is forbidden or (
                inspect.isclass(candidate)
                and inspect.isclass(forbidden)
                and issubclass(candidate, forbidden)
            ):
                found.append(forbidden.__name__)
    return found


def test_the_scoring_input_type_admits_no_history_and_no_store() -> None:
    """(1) P-3 as a property of a signature rather than of a promise."""
    problems = {
        name: _mentions_forbidden(annotation)
        for name, annotation in _annotations(ScoringInput).items()
        if _mentions_forbidden(annotation)
    }
    assert not problems, (
        f"the scorer's input type admits {problems}. A feature over an "
        f"applicant's history or over an earlier flag makes a scorer "
        f"self-confirming (ADR-016)"
    )


def test_build_features_takes_nothing_but_the_scoring_input_and_the_policy() -> None:
    """The only door into the feature builder, and what fits through it."""
    signature = inspect.signature(build_features)
    assert list(signature.parameters) == ["item", "policy"]
    hints = typing.get_type_hints(build_features)
    assert hints["item"] is ScoringInput


def test_no_feature_name_hints_at_a_person_or_a_history() -> None:
    """A blunt but load-bearing check on the vocabulary itself.

    Names are what a future engineer copies. A feature called
    ``vorherige_markierungen`` would fail here before it ever fails a review.
    """
    forbidden = (
        "antragsteller",
        "person",
        "vorherig",
        "historie",
        "history",
        "prior",
        "flag_count",
        "wiederholung",
        "name",
        "geburt",
        "versicherungsnummer",
        "anschrift",
        "adresse",
    )
    hits = {
        feature: [word for word in forbidden if word in feature]
        for feature in FEATURE_IDS
        if any(word in feature for word in forbidden)
    }
    assert not hits, hits


def test_the_feature_set_is_disjoint_from_the_decision_tables_vocabulary() -> None:
    """(3) The mirror image of the one-way valve, pinned.

    ADR-004 keeps anomaly evidence out of qualifying conditions. This keeps
    qualifying fields out of the feature vector, and the reason is measured
    rather than aesthetic: the first fit of part 09 carried all five echoes
    below and spent its whole tail on items the table had already sent to tier
    3, finding none of the nine labelled anomalies.
    """
    assert set(QUALIFYING_FIELD_ECHOES.values()) <= QUALIFYING_FIELDS
    assert not set(FEATURE_IDS) & set(QUALIFYING_FIELD_ECHOES)
    assert set(DEVIATION_FEATURES) <= set(FEATURE_IDS)


@pytest.mark.parametrize(
    "text",
    [
        "[[PII|VSNR|BCDFGHJKMNPQ]]",
        "Rentenbeginn [[PII|GEBDAT|BCDFGHJKMNPQ]] und mehr",
        "[[PII|VSNR|BCDFGHJKMNPQ]",  # right-truncated: part 08 found this shape
        "[[ PII | irgendwas ]]",
    ],
)
def test_the_guard_refuses_every_placeholder_shape(text: str) -> None:
    """(2a) The scorer refuses a sealed value instead of computing over it."""
    with pytest.raises(FeatureGuardError):
        _guarded(text, where="test")


def test_a_clean_value_passes_the_guard_unchanged() -> None:
    """The guard is a refusal, not a filter: ordinary content is untouched."""
    assert _guarded("2026-11-01", where="test") == "2026-11-01"


def test_a_scorer_pointed_at_a_sealed_field_degrades_rather_than_lying(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """The failure mode end to end: a misconfigured feature produces NOTHING.

    Not a wrong number, not a masked number - no anomaly evidence at all, which
    is the state the decision plane was in for eight parts. The degradation is
    journaled so the misconfiguration is visible.
    """
    assert config.scoring is not None
    broken = config.scoring.model_copy(
        update={"umsatz_path": "antragsteller.versicherungsnummer"}
    )
    payload = _payload(gold_v4_dir, "sf-0040-scheinselbststaendigkeit-indizienbuendel")
    journal = InMemoryJournalStore()
    outcome = run_pipeline(
        payload,
        config=config.__class__(**{**config.__dict__, "scoring": broken}),
        journal=journal,
        vault=InMemoryVaultStore(),
    )
    assert outcome.anomaly is None
    assert outcome.scoring is not None and outcome.scoring.degraded
    assert "feature_guard" in (outcome.scoring.degradation or "")
    scored = [
        event
        for event in journal.read(outcome.envelope.case_id)
        if event.type.value == "anomaly_scored"
    ]
    assert len(scored) == 1
    assert scored[0].payload["degraded"] is True
    # The journalled reason names the FEATURE, never the value that tripped it.
    detail = str(scored[0].payload["degradation"])
    assert "versicherungsnummer" in detail
    assert not contains_placeholder(detail)
    assert not PLACEHOLDER_SHAPED_RE.search(detail)


def test_the_classifier_never_embeds_a_placeholder_token(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """(2b) The same normative check for the OTHER probabilistic component.

    Part 06's finding, kept honest here rather than in its own suite: the
    classifier was nondeterministic because random placeholder tokens in a
    sealed letter reached the embedder and tipped a 0.0003 ranking margin.
    ``render_item_text`` masks them now, and this is the assertion that it
    keeps doing so - in the same module as the scorer's guard, because the two
    are one rule.
    """
    payload = _payload(gold_v4_dir, "sf-0062-scan-auftraggeber-und-taetigkeit")
    outcome = run_pipeline(
        payload,
        config=config,
        journal=InMemoryJournalStore(),
        vault=InMemoryVaultStore(),
    )
    from engine.evidence.context import build_context

    context = build_context(
        outcome.envelope,
        outcome.extractions,
        procedure_id=outcome.procedure_id,
        procedure_source=outcome.derivation.source.value,
        layer=outcome.text_layer,
    )
    text = render_item_text(context)
    assert text
    assert not contains_placeholder(text)
    assert not PLACEHOLDER_SHAPED_RE.search(text)


def test_the_classifier_unit_texts_come_from_config_and_carry_no_person(
    config: ConfigBundle,
) -> None:
    """The other half of the classifier's input: taxonomy text, never a case."""
    texts = unit_texts(config.taxonomy.nodes)
    assert texts
    for unit in texts:
        assert not contains_placeholder(unit.text)
        assert not PLACEHOLDER_SHAPED_RE.search(unit.text)


def test_scoring_the_same_item_twice_cannot_differ_because_of_an_earlier_flag(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """The behavioural half of P-3: no state survives between two runs.

    The type check above says the builder cannot SEE a history. This says the
    system does not keep one: the same submission scored twice, on two fresh
    journals, produces the identical score even though the first run wrote an
    ANOMALY_SCORED event that the second run could in principle have read.
    """
    payload = _payload(gold_v4_dir, "ar-0040-rentenbeginn-weit-in-der-zukunft")
    scores = []
    for _ in range(3):
        outcome = run_pipeline(
            payload,
            config=config,
            journal=InMemoryJournalStore(),
            vault=InMemoryVaultStore(),
        )
        assert outcome.anomaly is not None
        scores.append((outcome.anomaly.score, outcome.anomaly.flagged))
    assert len(set(scores)) == 1


def test_scoring_is_stable_when_an_earlier_run_is_replayed_on_one_journal(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """Same item, SAME journal, twice: the second score is not the first plus one."""
    payload = _payload(gold_v4_dir, "sf-0040-scheinselbststaendigkeit-indizienbuendel")
    journal = InMemoryJournalStore()
    vault = InMemoryVaultStore()
    first = run_pipeline(payload, config=config, journal=journal, vault=vault)
    second = run_pipeline(payload, config=config, journal=journal, vault=vault)
    assert first.anomaly is not None and second.anomaly is not None
    assert first.anomaly.score == second.anomaly.score
    assert first.anomaly.flagged == second.anomaly.flagged
    assert [reason.feature for reason in first.anomaly.reasons] == [
        reason.feature for reason in second.anomaly.reasons
    ]


def test_the_feature_builder_reads_no_vault_even_when_one_is_reachable(
    config: ConfigBundle, gold_v4_dir: Path
) -> None:
    """The vault handle rides the envelope; the scorer never dereferences it."""
    payload = _payload(gold_v4_dir, "ar-0001-regelaltersrente-vollstaendig")
    vault = InMemoryVaultStore()
    outcome = run_pipeline(
        payload, config=config, journal=InMemoryJournalStore(), vault=vault
    )
    assert config.scoring is not None
    procedure = config.procedure(outcome.procedure_id)
    vector = build_features(
        ScoringInput(
            envelope=outcome.envelope,
            extractions=outcome.extractions,
            evidence=outcome.evidence,
            procedure_id=outcome.procedure_id,
            field_paths=procedure.field_paths if procedure else {},
        ),
        config.scoring.policy,
    )
    sealed = vault.fetch(outcome.envelope.vault_ref)
    values = {str(entry.value()) for entry in sealed.entries}
    assert values, "the canary needs something sealed to look for"
    for feature in vector.features:
        for value in values:
            assert value.strip() not in feature.display


def _payload(gold_dir: Path, stem: str) -> dict[str, object]:
    import json

    return json.loads((gold_dir / f"{stem}.json").read_text(encoding="utf-8"))
