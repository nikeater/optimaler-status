"""``config/scoring/scoring_v1.yaml``: the scorer's own versioned subsystem.

Its own file with its own version, for the reason part 06 established and part
07 and 08 followed without being asked twice: ``config/thresholds.yaml``'s
version string is frozen into ``corpus/gold/v4/MANIFEST.yaml``, and gold v4 is
verified by a byte-identical rebuild. Bumping ``risk_v0`` to add a calibrated
anomaly threshold would fail that check, and shipping changed content under an
unchanged version would make a version string stop identifying its content.
So the calibrated threshold lives here, ``anomaly_default_v0`` stays in
thresholds.yaml as the historical uncalibrated placeholder it always was, and
``AnomalyEvidence.threshold_ref`` names which of the two actually governed.

Everything here is validated at load time, because a scoring config that is
wrong in a quiet way produces numbers rather than errors. Three checks earn
their place:

* the feature ids the config describes must be EXACTLY the ids the engine
  computes - an unknown name is a typo that would silently lose its wording,
  and a missing name is a feature that would reach a caseworker unworded;
* the audit-sampling salt must be present and non-trivial, because a sampling
  draw nobody can recompute is not an audit measure;
* every leading-date field must be a field the named procedure actually maps,
  checked in ``engine/config_loader.py`` where the procedures are known.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import Field, model_validator

from engine.score.features import FEATURE_IDS, FeaturePolicy, FeatureSpec, Indiz
from engine.score.model import ForestParams
from schemas.common import StrictModel
from schemas.config import AnomalyThreshold

#: Shortest salt the loader accepts. Not a security parameter - the draw is
#: meant to be recomputable, not secret - but a one-character salt is a
#: forgotten field rather than a decision.
MIN_SALT_LENGTH = 16

#: Longest salt the loader accepts, in BYTES, because that is blake2b's own
#: limit for a keyed hash. A longer one would have to be hashed down first, and
#: then the one-line recomputation the config file promises a caseworker would
#: stop being the arithmetic the engine actually does. Found by a Hypothesis
#: property, not by reading the docs.
MAX_SALT_BYTES = 64


class FeatureWording(StrictModel):
    """How one feature is named and framed in front of a caseworker."""

    label: str = Field(min_length=1)
    unit: str = ""
    expected_note: str = ""


class IndizSpec(StrictModel):
    """One Indiz of the par. 7a SGB IV Gesamtabwaegung, and which way it points.

    Which VALUES point at Beschaeftigung is agency knowledge and lives here
    rather than in code, so a Fachbereich can correct the weighting of a
    Gesamtwuerdigung without a release. No threshold is expressed: the feature
    is a share of the Indizien the form actually states.
    """

    path: str = Field(min_length=1, description="Dotted path into the payload")
    label: str = Field(min_length=1)
    beschaeftigung_values: list[str] = Field(min_length=1)


class ReasonPolicy(StrictModel):
    """How many reasons a flag carries, and how small a reason may be."""

    max_reasons: int = Field(default=4, ge=1, le=10)
    min_contribution: float = Field(default=0.005, ge=0.0, le=1.0)


class ForestSettings(StrictModel):
    """IsolationForest settings. Fixed seed, no clock, no auto-tuning."""

    n_estimators: int = Field(default=200, ge=10, le=2000)
    max_samples: int | Literal["auto"] = "auto"
    seed: int = Field(default=42, ge=0)


class AuditSamplingConfig(StrictModel):
    """The salt half of P-1; the RATE lives in ``AgencyRiskConfig``.

    Deliberately split. The rate is a policy an agency sets and an auditor
    reads off the risk config that every DecisionRecord already names by
    version; the salt is an operational value that must be stable and must not
    force a risk-config supersession when it is rotated.
    """

    salt: str = Field(min_length=MIN_SALT_LENGTH)
    note: str | None = None

    @model_validator(mode="after")
    def _salt_fits_a_keyed_hash(self) -> AuditSamplingConfig:
        size = len(self.salt.encode("utf-8"))
        if size > MAX_SALT_BYTES:
            raise ValueError(
                f"the audit-sampling salt is {size} bytes; blake2b takes at "
                f"most {MAX_SALT_BYTES} as a key. A longer salt would have to "
                f"be hashed down first, and the draw would stop being the "
                f"arithmetic this config tells a caseworker to redo"
            )
        return self


class BiasMonitoringConfig(StrictModel):
    """P-2's advisory bounds. Reported, never gated - deliberately.

    An alarm that failed a build would teach people to tune the alarm. What
    this number does is put a line in the report that a human has to look at
    and either explain or act on.
    """

    max_flag_rate_ratio: float = Field(default=3.0, ge=1.0)
    min_group_items: int = Field(default=5, ge=1)


class ScoringConfig(StrictModel):
    """The whole of ``config/scoring/scoring_v1.yaml``."""

    version: str = Field(min_length=1)
    feature_set_version: str = Field(min_length=1)
    reference_id: str = Field(
        min_length=1,
        description="Name of the population the scorer calls normal; it is "
        "what 'more unusual than 94 percent of the reference' refers to",
    )
    reference_population: str = Field(
        min_length=1,
        description="File name of the reference-population artifact, next to "
        "this config; re-fitted with python -m eval.score_fit",
    )
    threshold: AnomalyThreshold
    forest: ForestSettings = Field(default_factory=ForestSettings)
    reasons: ReasonPolicy = Field(default_factory=ReasonPolicy)
    audit_sampling: AuditSamplingConfig
    bias_monitoring: BiasMonitoringConfig = Field(default_factory=BiasMonitoringConfig)
    leading_date_fields: dict[str, str] = Field(default_factory=dict)
    indizien: list[IndizSpec] = Field(default_factory=list)
    umsatz_path: str = Field(min_length=1)
    features: dict[str, FeatureWording]

    @model_validator(mode="after")
    def _features_match_the_engine(self) -> ScoringConfig:
        described = set(self.features)
        known = set(FEATURE_IDS)
        unknown = sorted(described - known)
        if unknown:
            raise ValueError(
                f"scoring config describes unknown feature(s) {unknown}; this "
                f"build computes {sorted(known)}. A wording for a feature that "
                f"does not exist would never be rendered"
            )
        missing = sorted(known - described)
        if missing:
            raise ValueError(
                f"scoring config has no wording for {missing}; a feature "
                f"without wording would reach a caseworker as a bare id"
            )
        paths = [indiz.path for indiz in self.indizien]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate Indiz path in the scoring config")
        return self

    @property
    def max_reasons(self) -> int:
        return self.reasons.max_reasons

    @property
    def min_contribution(self) -> float:
        return self.reasons.min_contribution

    @property
    def forest_params(self) -> ForestParams:
        return ForestParams(
            n_estimators=self.forest.n_estimators,
            max_samples=self.forest.max_samples,
            seed=self.forest.seed,
        )

    @property
    def policy(self) -> FeaturePolicy:
        """The config-owned half of the feature set, in the engine's shapes."""
        return FeaturePolicy(
            feature_set_version=self.feature_set_version,
            leading_date_fields=dict(self.leading_date_fields),
            indizien=tuple(
                Indiz(
                    path=entry.path,
                    label=entry.label,
                    beschaeftigung_values=tuple(
                        value.lower() for value in entry.beschaeftigung_values
                    ),
                )
                for entry in self.indizien
            ),
            umsatz_path=self.umsatz_path,
            specs=_specs(self.features),
        )


def _specs(wording: Mapping[str, FeatureWording]) -> dict[str, FeatureSpec]:
    return {
        feature_id: FeatureSpec(
            feature_id=feature_id,
            label=entry.label,
            unit=entry.unit,
            expected_note=entry.expected_note,
        )
        for feature_id, entry in sorted(wording.items())
    }
