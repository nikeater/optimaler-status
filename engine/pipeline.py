"""One item, both planes, everything journaled.

    submission JSON
      -> ingest    Envelope                      (RECEIVED)
      -> textlayer TextLayer over redacted text  (REDACTED)
      -> derive    procedure id from hint/content/text
      -> extract   ExtractionSet                 (EXTRACTED)
      -> evidence  routing + completeness + gaps (EVIDENCE_ASSEMBLED)
      -> decide    DecisionRecord                (TIER_DECIDED, ROUTED)

The text layer sits between ingest and derivation because both of the stages
after it need it and neither may build its own: derivation reads the normalized
prose through the ``text.*`` namespace (ADR-020), and extraction verifies every
span against exactly the same string. Two layers built from the same text would
be the same string until the day they were not.

Derivation runs before extraction and decides which ``field_map`` the mapper
gets, which is why it cannot use extracted values: it reads the payload and the
text (ADR-013, extended in ADR-020). Everything downstream sees one procedure id
and does not care where it came from - except the journal, which records exactly
that.

"The payload" is the REDACTED working copy from part 04 on: ingest seals
identity-classed paths before the envelope exists, so every stage below reads
placeholders where identity used to be. The one thing that does not travel on
the envelope is the transient witness, which goes straight from ingest to the
completeness checker so sealed values can still be validated for real (ADR-017).
It is deliberately absent from :class:`PipelineResult`: what a run reports about
the seal is counts and a verified flag, never values and never the mapping.

The shadow scorer (part 09, ADR-024) runs between evidence and decision, and it
is the thing that has been missing from that sentence since part 01: anomaly
evidence is still accepted as an input - tests and callers that want to drive
the valve directly still can - but when nothing passes one and the agency has a
``config/scoring/``, this pipeline now produces it. It can only add oversight,
it may not stop the pipeline, and every failure of it is a journaled
degradation that leaves the decision plane exactly where it was for eight
parts: deciding on the deterministic evidence alone.

The zero-shot unit classifier (part 06, ADR-021) is wired here as a FALLBACK: it
runs only when routing arbitration produced nothing, and only when a caller
passed an embedder. It is never auto-loaded - a gate's numbers may not depend on
which wheels a machine has - and while ``config/classifier/`` says
``enabled: false`` its suggestion is evidence and nothing else: the decision
plane is handed the admitted routing sources, which are rules alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime
from typing import Any

from engine.config_loader import ConfigBundle, ProcedureConfig
from engine.decide import (
    ADMITTED_ROUTING_SOURCES,
    decide,
    evaluate_audit_sample,
    evaluate_downgrades,
)
from engine.evidence import (
    ClassifierSuggestion,
    Embedder,
    GapRendering,
    ProcedureDerivation,
    RoutingEngine,
    RoutingOutcome,
    UnitClassifier,
    assemble_evidence,
    build_context,
    classifier_from_config,
    derive_procedure,
    evaluate_clear_cut,
    evaluate_completeness,
    render_gaps,
    render_item_text,
)
from engine.extract import (
    ExtractionOutcome,
    TextExtractor,
    extract_all,
    fixture_from_payload,
)
from engine.ingest import IngestResult, ingest_submission
from engine.journal.store import JournalStore, emit
from engine.redact import Detector, InMemoryVaultStore, TokenSource, VaultStore
from engine.score import Scorer, ScoringOutcome, scorer_from_config
from engine.textlayer import build_text_layer, layer_stats
from schemas.anomaly import AnomalyEvidence, ScorerMode
from schemas.common import VersionStamp
from schemas.config import ProcedureFlags
from schemas.decision import DecisionRecord
from schemas.envelope import Envelope
from schemas.events import Event, EventType
from schemas.evidence import EvidenceRecord, RoutingSource
from schemas.extraction import ExtractionSet
from schemas.textlayer import TextLayer

ENFORCING = "enforcing"

#: What the decision plane may build on when the classifier IS enabled. Named
#: here, next to the wiring, so the whole of "enabling the classifier" is one
#: readable line rather than a flag threaded through three modules.
CLASSIFIER_ADMITTED_SOURCES = frozenset({RoutingSource.RULE, RoutingSource.CLASSIFIER})


@dataclass(frozen=True)
class RedactionSummary:
    """What the privacy boundary did with this item. Values never appear here."""

    vault_ref: str
    sealed_count: int
    verified: bool
    auto_sealed_paths: tuple[str, ...] = ()
    text_sealed_counts: dict[str, int] = dc_field(default_factory=dict)

    @property
    def text_sealed_count(self) -> int:
        """How many identity spans were sealed out of prose."""
        return sum(self.text_sealed_counts.values())


@dataclass(frozen=True)
class PipelineResult:
    """Everything one run produced, in pipeline order."""

    envelope: Envelope
    extractions: ExtractionSet
    evidence: EvidenceRecord
    decision: DecisionRecord
    clear_cut: bool
    procedure_id: str | None
    derivation: ProcedureDerivation
    routing: RoutingOutcome
    gap_renderings: tuple[GapRendering, ...] = ()
    redaction: RedactionSummary | None = None
    text_layer: TextLayer | None = None
    extraction: ExtractionOutcome | None = None
    classifier: ClassifierSuggestion | None = None
    #: What the shadow scorer made of this item, INCLUDING the case where it
    #: made nothing. ``None`` means no scorer exists for this agency at all;
    #: an outcome with ``degraded`` set means one exists and could not run,
    #: which is a different fact and is journaled as one.
    scoring: ScoringOutcome | None = None
    anomaly: AnomalyEvidence | None = None


def run_pipeline(
    payload: Mapping[str, Any],
    *,
    config: ConfigBundle,
    journal: JournalStore,
    vault: VaultStore | None = None,
    anomaly: AnomalyEvidence | None = None,
    now: datetime | None = None,
    token_source: TokenSource | None = None,
    text_detector: Detector | None = None,
    live_extractor: TextExtractor | None = None,
    embedder: Embedder | None = None,
) -> PipelineResult:
    """Run one submission through both planes and journal every step.

    Args:
        vault: where the sealed identity record goes. Defaults to a
            run-scoped in-memory store, which is what tests and the eval
            harness want; ``api/app.py`` always passes an explicit one.
        token_source: placeholder token source; a ``SeededTokenSource`` makes a
            run reproducible. No decision ever depends on it - verdicts come
            from real values through the witness, and predicates only ever test
            a placeholder for presence.
        text_detector: union that seals free text. Defaults to the
            deterministic one, which is what every gate path uses.
        live_extractor: optional LLM reader of prose. Defaults to None, and
            every gate leaves it there: its proposals go through the same
            verifier as everything else, but a metric that moved because a
            model was warm would not be a metric.
        embedder: optional embedding model for the fallback unit classifier.
            Defaults to None, which is the shipped state and produces exactly
            the behaviour of every part before 06. Passing one turns the
            classifier on as EVIDENCE; whether that evidence may govern a
            decision is a separate switch, and it lives in config.
    """
    # Provenance for THIS run: the replay id when nothing but deterministic
    # readers ran, the model id as soon as a live one did. Never blank, and
    # never the wrong one - a report that said "replay:v4" over numbers a model
    # produced would be the one lie the version stamp exists to prevent.
    versions = config.version_stamp(
        model_id=(
            config.extraction.replay.extractor_id
            if live_extractor is None
            else live_extractor.extractor_id
        )
    )
    ingest = ingest_submission(
        payload,
        journal=journal,
        versions=versions,
        vault=vault if vault is not None else InMemoryVaultStore(),
        policy=config.redaction,
        token_source=token_source,
        now=now,
        text_detector=text_detector,
    )
    envelope = ingest.envelope
    layer = build_text_layer(envelope, versions=versions, now=now)
    _journal_text_layer(ingest, layer, journal=journal, versions=versions, now=now)

    derivation = derive_procedure(envelope, config.procedures, layer=layer)
    procedure_id = derivation.procedure_id
    procedure = config.procedure(procedure_id)

    extraction = extract_all(
        envelope,
        layer,
        procedure,
        config=config.extraction,
        journal=journal,
        versions=versions,
        fixture=fixture_from_payload(payload),
        live=live_extractor,
        procedure_id=procedure_id,
        now=now,
    )
    extractions = extraction.extractions

    context = build_context(
        envelope,
        extractions,
        procedure_id=procedure_id,
        procedure_source=derivation.source.value,
        layer=layer,
    )
    routing = RoutingEngine(config.routing.rules).arbitrate(context)
    suggestion = _classify_fallback(routing, context, config=config, embedder=embedder)
    completeness = evaluate_completeness(
        extractions,
        procedure.requirements if procedure else None,
        procedure_id=procedure_id,
        field_paths=procedure.field_paths if procedure else None,
        # The witness never leaves this call chain: ingest built it, the
        # checker consumes it, and it is not on the result below.
        witness=ingest.witness,
        sealed_fields=config.sealed_field_ids(procedure_id),
    )
    renderings = render_gaps(completeness, procedure)
    clear_cut = evaluate_clear_cut(procedure.clear_cut if procedure else None, context)
    evidence = assemble_evidence(
        envelope,
        extractions,
        routing.suggestions,
        completeness,
        journal=journal,
        versions=versions,
        clear_cut=clear_cut,
        derivation=derivation,
        outcome=routing,
        renderings=renderings,
        classifier=suggestion,
        now=now,
    )

    scoring = (
        _score(
            envelope,
            extractions,
            evidence,
            config=config,
            procedure=procedure,
            procedure_id=procedure_id,
            versions=versions,
            now=now,
        )
        if anomaly is None
        else None
    )
    if scoring is not None and scoring.evidence is not None:
        anomaly = scoring.evidence
    if anomaly is not None:
        _journal_anomaly(anomaly, envelope, journal=journal, config=config, now=now)
    elif scoring is not None:
        _journal_degradation(scoring, envelope, journal=journal, config=config, now=now)

    decision = decide(
        evidence,
        anomaly,
        config.decision_table,
        config.risk,
        _flags(procedure),
        clear_cut=clear_cut,
        versions=versions,
        now=now,
        routing_sources=_admitted_sources(config),
        audit_salt=_audit_salt(config),
    )
    _journal_decision(
        decision, anomaly, config=config, journal=journal, clear_cut=clear_cut, now=now
    )
    return PipelineResult(
        envelope=envelope,
        extractions=extractions,
        evidence=evidence,
        decision=decision,
        clear_cut=clear_cut,
        procedure_id=procedure_id,
        derivation=derivation,
        routing=routing,
        gap_renderings=tuple(renderings),
        redaction=RedactionSummary(
            vault_ref=ingest.vault_ref,
            sealed_count=ingest.sealed_count,
            verified=ingest.redaction_verified,
            auto_sealed_paths=ingest.auto_sealed_paths,
            text_sealed_counts=dict(ingest.text_sealed_counts),
        ),
        text_layer=layer,
        extraction=extraction,
        classifier=suggestion,
        scoring=scoring,
        anomaly=anomaly,
    )


def _score(
    envelope: Envelope,
    extractions: ExtractionSet,
    evidence: EvidenceRecord,
    *,
    config: ConfigBundle,
    procedure: ProcedureConfig | None,
    procedure_id: str | None,
    versions: VersionStamp,
    now: datetime | None,
) -> ScoringOutcome | None:
    """Anomaly evidence for this item, or None when no scorer exists.

    Wrapped exactly like the part-06 classifier and for the same reason, one
    step stronger: ``Scorer.score`` already turns every internal failure into a
    degradation, and this wrapper covers the ones that happen BEFORE there is a
    scorer to ask - a missing reference population, a matrix fitted on a
    different feature set, a file that will not parse. All of them produce "no
    anomaly evidence", which is what the decision plane did for eight parts.
    """
    if config.scoring is None:
        return None
    try:
        scorer: Scorer | None = scorer_from_config(config.scoring, config.scoring_dir)
    except Exception as error:  # a scorer may never take the pipeline down
        return ScoringOutcome(
            envelope_id=envelope.envelope_id,
            case_id=envelope.case_id,
            evidence=None,
            degraded=True,
            degradation=f"scorer_unavailable: {type(error).__name__}: {error}",
        )
    if scorer is None:
        return None
    return scorer.score(
        envelope,
        extractions,
        evidence,
        procedure_id=procedure_id,
        field_paths=procedure.field_paths if procedure else {},
        mode=ScorerMode(config.risk.scorer_mode),
        versions=versions,
        now=now,
    )


def _audit_salt(config: ConfigBundle) -> str | None:
    """The P-1 sampling salt, or None when no scoring config exists.

    The salt and the RATE live apart on purpose (see ``config/scoring/``): the
    rate is the agency's policy and rides the risk config every DecisionRecord
    already names, the salt is operational and must be rotatable without
    superseding a frozen config version.
    """
    if config.scoring is None:
        return None
    return config.scoring.audit_sampling.salt


def _classify_fallback(
    routing: RoutingOutcome,
    context: Mapping[str, object],
    *,
    config: ConfigBundle,
    embedder: Embedder | None,
) -> ClassifierSuggestion | None:
    """The fallback suggestion for an item no rule caught, or None.

    Rules first: a rule that fired ends this function before a vector is
    computed. That is not an optimization, it is the policy - a similarity may
    not be consulted about an item an agency's own sentence already answered,
    because a reader comparing the two would eventually start weighing them.

    The whole thing is wrapped, not only the query. ``UnitClassifier`` embeds the
    unit texts in its constructor, so a model that dies on the FIRST batch would
    otherwise raise out of here and take the pipeline down over an optional
    suggestion. Every classifier failure is "no suggestion", which is what this
    pipeline did before part 06.
    """
    if routing.candidates or embedder is None:
        return None
    try:
        classifier: UnitClassifier | None = classifier_from_config(
            config.classifier, config.taxonomy.nodes, embedder
        )
        if classifier is None:
            return None
        return classifier.suggest(render_item_text(context))
    except Exception:  # an optional suggestion may not raise into the pipeline
        return None


def _admitted_sources(config: ConfigBundle) -> frozenset[RoutingSource]:
    """Which routing sources may govern a decision for this config.

    The shipped answer is "rules alone". It becomes "rules and the classifier"
    only when an agency sets ``enabled: true``, which the loader refuses
    without a calibration and its provenance.
    """
    if config.classifier is not None and config.classifier.enabled:
        return CLASSIFIER_ADMITTED_SOURCES
    return ADMITTED_ROUTING_SOURCES


def _journal_text_layer(
    ingest: IngestResult,
    layer: TextLayer | None,
    *,
    journal: JournalStore,
    versions: VersionStamp,
    now: datetime | None,
) -> None:
    """Record what the seal did and what the layer became, under REDACTED.

    One event for both halves, because they are one step: the text that was
    sealed IS the text that was normalized, and splitting them would invite a
    reader to believe the layer might have been built over something else. The
    payload is counts and part ids - no text, no placeholder tokens, no kinds
    per span. Emitted for a structured-only item too, so "this case had no
    prose" is a recorded fact rather than a missing event (ADR-019).
    """
    emit(
        journal,
        case_id=ingest.envelope.case_id,
        event_type=EventType.REDACTED,
        versions=versions,
        occurred_at=now,
        payload={
            "envelope_id": ingest.envelope.envelope_id,
            "sealed_count": ingest.sealed_count,
            "text_sealed_counts": dict(sorted(ingest.text_sealed_counts.items())),
            "auto_sealed_paths": list(ingest.auto_sealed_paths),
            "redaction_verified": ingest.redaction_verified,
            "text_layer": layer_stats(layer),
        },
    )


def _flags(procedure: ProcedureConfig | None) -> ProcedureFlags | None:
    return procedure.flags if procedure else None


def _journal_anomaly(
    anomaly: AnomalyEvidence,
    envelope: Envelope,
    *,
    journal: JournalStore,
    config: ConfigBundle,
    now: datetime | None,
) -> Event:
    """The scored event, stamped with the feature set that produced the number.

    The stamp comes off the evidence rather than off the config bundle, because
    ``feature_set_version`` is the one part of the provenance the config
    directory does not know: it is a property of the artifact, and a score
    whose event said "fsv_v1" while the artifact said something else would be a
    lie in exactly the field that exists to prevent one.
    """
    return emit(
        journal,
        case_id=envelope.case_id,
        event_type=EventType.ANOMALY_SCORED,
        versions=anomaly.versions,
        occurred_at=now,
        payload={
            "envelope_id": anomaly.envelope_id,
            "score": anomaly.score,
            "threshold_ref": anomaly.threshold_ref,
            "flagged": anomaly.flagged,
            "mode": anomaly.mode.value,
            "degraded": False,
            "reasons": [
                {
                    "feature": reason.feature,
                    "observed": reason.observed,
                    "expected": reason.expected,
                    "contribution": reason.contribution,
                }
                for reason in anomaly.reasons
            ],
        },
    )


def _journal_degradation(
    scoring: ScoringOutcome,
    envelope: Envelope,
    *,
    journal: JournalStore,
    config: ConfigBundle,
    now: datetime | None,
) -> Event | None:
    """Record that the scorer ran and produced nothing, and why.

    Without this event, "no ANOMALY_SCORED for this case" would mean two very
    different things - this agency runs no scorer, and this agency's scorer
    fell over - and an audit trail may not leave those two indistinguishable.
    The payload names the failure class, never a value: a guard that fired
    because a sealed value reached a feature must not put that value in the
    journal while saying so.
    """
    if not scoring.degraded:
        return None
    return emit(
        journal,
        case_id=envelope.case_id,
        event_type=EventType.ANOMALY_SCORED,
        versions=config.version_stamp().model_copy(
            update={
                "feature_set_version": (
                    config.scoring.feature_set_version
                    if config.scoring is not None
                    else None
                )
            }
        ),
        occurred_at=now,
        payload={
            "envelope_id": envelope.envelope_id,
            "degraded": True,
            "degradation": scoring.degradation,
            "flagged": False,
        },
    )


def _journal_decision(
    decision: DecisionRecord,
    anomaly: AnomalyEvidence | None,
    *,
    config: ConfigBundle,
    journal: JournalStore,
    clear_cut: bool,
    now: datetime | None,
) -> None:
    outcomes = evaluate_downgrades(
        anomaly,
        config.decision_table,
        enforcing=config.risk.scorer_mode == ENFORCING,
    )
    # Recomputed rather than threaded out of ``decide``: the draw is a pure
    # function of (case id, salt), so recomputing it here cannot disagree with
    # what the decision did, and the decision path keeps returning a contract
    # object rather than a tuple of extras.
    sample = evaluate_audit_sample(
        decision.case_id,
        rate=config.risk.audit_sample_rate,
        salt=_audit_salt(config),
    )
    emit(
        journal,
        case_id=decision.case_id,
        event_type=EventType.TIER_DECIDED,
        versions=decision.versions,
        occurred_at=now,
        payload={
            "envelope_id": decision.envelope_id,
            "tier": int(decision.tier),
            "pre_downgrade_tier": int(decision.pre_downgrade_tier),
            "routed_unit_id": decision.routed_unit_id,
            "clear_cut": clear_cut,
            "scorer_mode": config.risk.scorer_mode,
            "decision_table_version": decision.decision_table_version,
            "risk_config_version": decision.risk_config_version,
            "reasons": [
                {
                    "kind": reason.kind.value,
                    "rule_id": reason.rule_id,
                    "detail": reason.detail,
                }
                for reason in decision.reasons
            ],
            # Log-only downgrades are recorded here and nowhere else: the
            # decision record must not claim a downgrade that was not applied.
            "downgrades": [
                {
                    "rule_id": outcome.rule_id,
                    "to_tier": outcome.to_tier,
                    "fired": outcome.fired,
                    "applied": outcome.applied,
                    "detail": outcome.detail,
                }
                for outcome in outcomes
            ],
            # P-1: present whenever sampling is switched on at all, so "not
            # sampled" is a recorded fact rather than a missing key. Absent
            # when the rate is 0.0, which is the shipped state.
            **(
                {
                    "audit_sample": {
                        "sampled": sample.sampled,
                        "rate": sample.rate,
                        "draw": round(sample.draw, 9),
                    }
                }
                if sample is not None
                else {}
            ),
        },
    )
    if decision.routed_unit_id is not None:
        emit(
            journal,
            case_id=decision.case_id,
            event_type=EventType.ROUTED,
            versions=decision.versions,
            occurred_at=now,
            payload={
                "envelope_id": decision.envelope_id,
                "unit_id": decision.routed_unit_id,
                "tier": int(decision.tier),
            },
        )
