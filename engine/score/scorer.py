"""The shadow scorer: evidence in, AnomalyEvidence out, and nothing else.

It runs after ``assemble_evidence`` and before ``decide``, and its whole output
is one artifact the decision table may reference in exactly one place. What it
cannot do is structural rather than promised: ``QualifyingCondition`` refuses
``anomaly.*`` fields, ``DowngradeCondition`` accepts only monotone operators
with a fixed tier-3 target, and the engine applies ``max(tier, to_tier)``
(ADR-004). This module adds no rail; it feeds the ones part 01 built.

**It may never block the pipeline.** Every failure - a missing reference
population, a malformed config, a sealed value reaching a feature, a model that
raises - produces NO anomaly evidence and a journaled degradation. That is
precisely the state the decision plane has been in since part 01, so a broken
scorer costs the system its extra oversight and nothing else. It can never
produce tier 1 (it produces no tier at all) and it can never silence an item
(the degradation is an event, not a hole in the journal).

**Log-only is structural, not polite.** ``scorer_mode`` lives in
``config/thresholds.yaml``, whose version string is frozen into the gold set's
manifest, so switching to enforcing means superseding a frozen config version -
exactly as much friction as the decision deserves.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from engine.score.config import ScoringConfig
from engine.score.features import (
    FeatureGuardError,
    FeatureVector,
    ScoringInput,
    build_features,
)
from engine.score.model import Attribution, ScoringModel, build_model
from engine.score.reasons import build_reasons
from schemas.anomaly import AnomalyEvidence, ScorerMode
from schemas.common import VersionStamp
from schemas.envelope import Envelope
from schemas.evidence import EvidenceRecord
from schemas.extraction import ExtractionSet


@dataclass(frozen=True)
class ScoringOutcome:
    """What one scoring run produced, including the case where it produced none.

    ``evidence`` is what the decision plane sees. Everything else is for the
    eval harness and the journal: contributions for every feature (the bias
    section needs them for unflagged items too), the rendered feature displays,
    and the honest record of a degradation.
    """

    envelope_id: str
    case_id: str
    evidence: AnomalyEvidence | None
    vector: FeatureVector | None = None
    attributions: tuple[Attribution, ...] = ()
    degraded: bool = False
    degradation: str | None = None

    @property
    def score(self) -> float | None:
        return None if self.evidence is None else self.evidence.score

    @property
    def flagged(self) -> bool:
        return self.evidence is not None and self.evidence.flagged

    def contribution(self, feature_id: str) -> float:
        for attribution in self.attributions:
            if attribution.feature_id == feature_id:
                return attribution.contribution
        return 0.0

    @property
    def mean_abs_contribution(self) -> float:
        """Average magnitude of the feature contributions for this item.

        The number P-2 watches per procedure, channel and item shape: a group
        whose items are consistently explained by large contributions is a
        group the model treats as a different population, whether or not it is
        flagged more often.
        """
        if not self.attributions:
            return 0.0
        total = sum(abs(item.contribution) for item in self.attributions)
        return round(total / len(self.attributions), 6)


class Scorer:
    """A loaded scoring config plus its fitted model. Reusable and stateless."""

    def __init__(self, config: ScoringConfig, model: ScoringModel) -> None:
        self.config = config
        self.model = model

    @property
    def feature_set_version(self) -> str:
        return self.config.feature_set_version

    def score(
        self,
        envelope: Envelope,
        extractions: ExtractionSet,
        evidence: EvidenceRecord,
        *,
        procedure_id: str | None,
        field_paths: Mapping[str, str],
        mode: ScorerMode = ScorerMode.LOG_ONLY,
        versions: VersionStamp | None = None,
        now: datetime | None = None,
    ) -> ScoringOutcome:
        """Score one item. Never raises; a failure is a degradation."""
        item = ScoringInput(
            envelope=envelope,
            extractions=extractions,
            evidence=evidence,
            procedure_id=procedure_id,
            field_paths=field_paths,
        )
        try:
            return self._score(item, mode=mode, versions=versions, now=now)
        except FeatureGuardError as error:
            return self._degraded(envelope, f"feature_guard: {error}")
        except Exception as error:  # a scorer may never take the pipeline down
            return self._degraded(
                envelope, f"scoring_failed: {type(error).__name__}: {error}"
            )

    def _score(
        self,
        item: ScoringInput,
        *,
        mode: ScorerMode,
        versions: VersionStamp | None,
        now: datetime | None,
    ) -> ScoringOutcome:
        vector = build_features(item, self.config.policy)
        score, attributions = self.model.explain(vector.values)
        flagged = score >= self.config.threshold.value
        reasons = (
            build_reasons(
                vector,
                attributions,
                model=self.model,
                policy=self.config.policy,
                max_reasons=self.config.max_reasons,
                min_contribution=self.config.min_contribution,
            )
            if flagged
            else []
        )
        stamp = (versions or VersionStamp(schema_version="0.1.0")).model_copy(
            update={"feature_set_version": self.config.feature_set_version}
        )
        evidence = AnomalyEvidence(
            envelope_id=item.envelope.envelope_id,
            case_id=item.envelope.case_id,
            score=score,
            threshold_ref=self.config.threshold.threshold_id,
            flagged=flagged,
            reasons=reasons,
            mode=mode,
            created_at=now or datetime.now(UTC),
            versions=stamp,
        )
        return ScoringOutcome(
            envelope_id=item.envelope.envelope_id,
            case_id=item.envelope.case_id,
            evidence=evidence,
            vector=vector,
            attributions=tuple(attributions),
        )

    @staticmethod
    def _degraded(envelope: Envelope, reason: str) -> ScoringOutcome:
        return ScoringOutcome(
            envelope_id=envelope.envelope_id,
            case_id=envelope.case_id,
            evidence=None,
            degraded=True,
            degradation=reason,
        )


def scorer_from_config(config: ScoringConfig | None, directory: Path) -> Scorer | None:
    """Build a scorer, or None when this agency has no usable scoring config.

    Absent is a legitimate state with a defined meaning, exactly as it is for
    the classifier and the notification templates: no ``config/scoring/`` means
    this agency runs no shadow scorer, and every item decides on the
    deterministic evidence alone. A PRESENT but unusable reference population
    is a different thing and must not be quiet, so it raises here and the
    pipeline turns it into a journaled degradation per item.
    """
    if config is None:
        return None
    model = build_model(directory / config.reference_population, config.forest_params)
    return Scorer(config, model)
