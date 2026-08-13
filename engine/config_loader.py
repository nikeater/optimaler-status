"""Load and validate the agency-editable config in ``config/``.

Config is the product: everything an agency may tune (taxonomy, routing rules,
per-procedure requirements and flags, the decision table, thresholds) is YAML
that is validated through the contracts in ``schemas/`` at load time. Anything
that is not expressible in a contract model gets a thin engine-local wrapper
model here - never a change to ``schemas/``.

Three composition decisions live in this module:

* ``ProcedureFlags`` are edited in ``config/procedures/<id>_v1.yaml`` and the
  loader assembles them into ``AgencyRiskConfig.procedures``, so a flag has
  exactly one editable home and cannot drift between two files.
* Routing rule fixtures are stored next to the rules they exercise; the loader
  checks that every id a rule references exists, and ``tests/test_routing.py``
  runs them.
* Routing rules are loaded straight into the contract model
  ``schemas.config.RoutingRule``, ``priority`` included. Part 03 could not do
  that - the contract had no such field - so the loader carried a local
  ``RoutingRuleSpec`` shim and handed the contract subset out through
  ``.rule``. ADR-016 legalized the field, and part 03b removed the shim: there
  is now exactly one model for a routing rule, and arbitration precedence is
  part of the published contract rather than an engine-local convention
  (ADR-014 for the policy, ADR-016 for the contract).

Predicates are parsed (not just shape-checked) during loading, so a typo in a
rule fails at startup instead of silently evaluating to False forever.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import jinja2
import yaml
from jinja2 import meta as jinja_meta
from pydantic import Field, model_validator

from engine.namespaces import (
    EXTRACTION_PREFIX,
    PAYLOAD_PREFIX,
    TEXT_FIELDS,
    TEXT_PREFIX,
)
from engine.predicate import (
    AllOf,
    AnyOf,
    Comparison,
    PredicateNode,
    TextOp,
    parse_predicate,
)
from engine.redact.detector import redact_detector
from engine.redact.policy import (
    IdentityFieldsPolicy,
    check_witnessless_seals,
    load_policy,
)
from engine.score.config import ScoringConfig
from schemas import SCHEMA_VERSION
from schemas.common import Channel, SourceType, StrictModel, Tier, VersionStamp
from schemas.config import (
    AgencyRiskConfig,
    DecisionTable,
    Op,
    ProcedureFlags,
    Requirement,
    RequirementList,
    RoutingRule,
    TaxonomyNode,
)

CONFIG_DIR_ENV = "EINGANGSLOTSE_CONFIG_DIR"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"

#: Validation constraints understood by engine.evidence.completeness.
SUPPORTED_VALIDATION_KEYS = frozenset(
    {"pattern", "one_of", "min_length", "max_length", "date", "cross_field"}
)

#: Keys of the ``date`` constraint block (all optional; ``iso`` parseability is
#: always checked once the block exists).
SUPPORTED_DATE_KEYS = frozenset({"min", "max"})

#: Cross-field check kinds understood by engine.evidence.completeness.
SUPPORTED_CROSS_FIELD_KINDS = frozenset(
    {
        "not_before",
        "not_after",
        "min_years_after",
        "max_years_after",
        "birthdate_in_vsnr",
    }
)

#: Cross-field kinds that need a ``years`` argument.
CROSS_FIELD_KINDS_WITH_YEARS = frozenset({"min_years_after", "max_years_after"})

#: Keys of one cross-field entry.
SUPPORTED_CROSS_FIELD_KEYS = frozenset({"kind", "field", "years", "detail"})

#: Default routing priority when a rule does not state one. Deliberately in the
#: middle of the range so a new rule neither outranks nor loses to everything.
#: Read off the contract field rather than restated, so the two cannot drift.
DEFAULT_RULE_PRIORITY: int = RoutingRule.model_fields["priority"].default

#: The only comparisons an identity-classed field may appear in: presence and
#: absence. Both are ``value: null`` tests, so neither reads what the field
#: holds - which is what makes them survive sealing unchanged (ADR-017).
PRESENCE_OPS = frozenset({Op.EQ, Op.NE})


class ConfigError(ValueError):
    """Raised when the config on disk is unusable."""


class FieldMapEntry(StrictModel):
    """One payload path mapped onto a procedure-schema field id."""

    path: str = Field(description="Dotted path into the structured payload")
    field: str = Field(description="Procedure-schema field id")


class ClearCutCriteria(StrictModel):
    """Declarative clear-cut criteria for a procedure (ADR-007).

    Evaluated by deterministic code in the evidence plane; the boolean result
    enters the decision table as the qualifying field ``procedure.clear_cut``.
    """

    criteria_id: str
    description: str
    predicate: dict[str, Any]


class DerivationSignals(StrictModel):
    """Content signals that identify a procedure without a channel hint.

    One predicate per procedure over the pre-extraction context (ADR-013):
    ``payload.<path>``, ``procedure_hint`` and ``channel``. A procedure without
    this block is never derived from content - silence is not a signal.
    """

    signal_id: str
    description: str
    predicate: dict[str, Any]


class NachforderungText(StrictModel):
    """Caseworker-facing wording for one requirement's gap.

    ``Requirement`` (a contract) carries the fachliche description; the request
    sentence is agency wording, changes independently of the requirement, and
    therefore lives in the loader-owned block. Placeholders are the same
    ``{name}`` form the decision table uses; unknown names stay literal.
    """

    requirement_id: str
    missing: str
    invalid: str | None = None


class ProcedureConfig(StrictModel):
    """One file in ``config/procedures/``."""

    procedure_id: str
    flags: ProcedureFlags
    requirements: RequirementList
    field_map: list[FieldMapEntry] = Field(default_factory=list)
    clear_cut: ClearCutCriteria | None = None
    derivation: DerivationSignals | None = None
    nachforderung: list[NachforderungText] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> ProcedureConfig:
        if self.flags.procedure_id != self.procedure_id:
            raise ValueError(
                f"flags.procedure_id {self.flags.procedure_id!r} does not match "
                f"{self.procedure_id!r}"
            )
        if self.requirements.procedure_id != self.procedure_id:
            raise ValueError(
                f"requirements.procedure_id {self.requirements.procedure_id!r} "
                f"does not match {self.procedure_id!r}"
            )
        known = {
            requirement.requirement_id for requirement in self.requirements.requirements
        }
        for requirement in self.requirements.requirements:
            _check_validation_keys(requirement, known)
        if self.clear_cut is not None:
            parse_predicate(self.clear_cut.predicate)
        if self.derivation is not None:
            parse_predicate(self.derivation.predicate)
        seen: set[str] = set()
        for text in self.nachforderung:
            if text.requirement_id not in known:
                raise ValueError(
                    f"nachforderung text for unknown requirement "
                    f"{text.requirement_id!r} in {self.procedure_id!r}"
                )
            if text.requirement_id in seen:
                raise ValueError(
                    f"duplicate nachforderung text for {text.requirement_id!r}"
                )
            seen.add(text.requirement_id)
        return self

    @property
    def clear_cut_predicate(self) -> PredicateNode | None:
        """Parsed clear-cut AST, or None when the procedure defines none."""
        if self.clear_cut is None:
            return None
        return parse_predicate(self.clear_cut.predicate)

    @property
    def derivation_predicate(self) -> PredicateNode | None:
        """Parsed content-signal AST, or None when the procedure defines none."""
        if self.derivation is None:
            return None
        return parse_predicate(self.derivation.predicate)

    @property
    def field_paths(self) -> dict[str, str]:
        """Field id -> payload path, the provenance a gap reports."""
        return {entry.field: entry.path for entry in self.field_map}

    def nachforderung_text(self, requirement_id: str) -> NachforderungText | None:
        """Configured request wording for a requirement, or None."""
        for text in self.nachforderung:
            if text.requirement_id == requirement_id:
                return text
        return None


class MatchPolicy(StrictModel):
    """How hard a proposal must match for one source type."""

    mode: Literal["exact", "fuzzy"]
    min_score: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _exact_is_exact(self) -> MatchPolicy:
        if self.mode == "exact" and self.min_score != 1.0:
            raise ValueError(
                "an exact match policy cannot carry a min_score below 1.0: "
                "'exact but only 90 percent' is a fuzzy policy with a "
                "misleading name"
            )
        return self


class LivePolicy(StrictModel):
    """The optional LLM extractor's settings. Off unless an agency turns it on."""

    enabled: bool = False
    base_url: str | None = None
    model: str | None = None
    timeout_seconds: float = Field(default=30.0, gt=0.0)
    attempts: int = Field(default=2, ge=1, le=5)
    chunk_chars: int = Field(default=96, ge=16, le=1024)

    @model_validator(mode="after")
    def _enabled_needs_an_endpoint(self) -> LivePolicy:
        if self.enabled and not self.base_url:
            raise ValueError(
                "live extraction is enabled but no base_url is configured; "
                "an enabled extractor that cannot be reached would degrade "
                "every item to tier 3 silently"
            )
        return self


class PromptTemplate(StrictModel):
    """The wording that produced a set of numbers, versioned with them."""

    system: str = Field(min_length=1)
    user: str = Field(min_length=1)

    @model_validator(mode="after")
    def _user_template_has_its_slots(self) -> PromptTemplate:
        missing = [slot for slot in ("{fields}", "{text}") if slot not in self.user]
        if missing:
            raise ValueError(f"prompt.user is missing the slots {missing}")
        return self


class ReplayPolicy(StrictModel):
    """Provenance for the deterministic corpus extractor."""

    extractor_id: str = Field(min_length=1)


class ConfidencePolicy(StrictModel):
    """What a verified record is worth, per match mode."""

    exact: float = Field(ge=0.0, le=1.0)
    fuzzy_floor: float = Field(ge=0.0, le=1.0)


class ExtractionConfig(StrictModel):
    """The whole of ``config/extraction/extraction_v1.yaml``."""

    version: str
    prompt_version: str
    replay: ReplayPolicy
    match: dict[str, MatchPolicy]
    confidence: ConfidencePolicy
    live: LivePolicy = Field(default_factory=LivePolicy)
    prompt: PromptTemplate

    @model_validator(mode="after")
    def _every_source_type_has_a_policy(self) -> ExtractionConfig:
        declared = set(self.match)
        required = {source.value for source in SourceType}
        missing = sorted(required - declared)
        if missing:
            raise ValueError(
                f"no match policy for source type(s) {missing}; a source type "
                f"without a policy would have no defined way to verify a span"
            )
        unknown = sorted(declared - required)
        if unknown:
            raise ValueError(f"match policy for unknown source type(s) {unknown}")
        return self

    def policy_for(self, source_type: SourceType) -> MatchPolicy:
        """The match policy for one source type."""
        return self.match[source_type.value]


class RuleFixture(StrictModel):
    """A minimal evaluation context a routing rule must still fire on."""

    fixture_id: str
    description: str
    procedure_hint: str | None = None
    procedure_id: str | None = None
    channel: str = "fit_connect"
    payload: dict[str, str] = Field(
        default_factory=dict,
        description="Dotted payload path (without the 'payload.' prefix) -> value",
    )
    extraction: dict[str, str] = Field(default_factory=dict)
    expect_rule_ids: list[str] = Field(default_factory=list)
    expect_unit_id: str | None = Field(
        default=None,
        description="Unit arbitration must pick for this fixture; None means "
        "the fixture only asserts which rules fire",
    )

    def context(self) -> dict[str, object]:
        """Flat evaluation context in the shape engine.evidence builds."""
        context: dict[str, object] = {
            "procedure_hint": self.procedure_hint,
            "procedure_id": self.procedure_id,
            "procedure_source": None,
            "channel": self.channel,
        }
        for path, value in self.payload.items():
            context[f"payload.{path}"] = value
        for field, value in self.extraction.items():
            context[f"extraction.{field}"] = value
        return context


def rule_order_key(rule: RoutingRule) -> tuple[int, str]:
    """The total order arbitration sorts rules by (ADR-014).

    Lower priority wins; ties break on ``rule_id``, so ``(priority, rule_id)``
    is total over a set of rules with unique ids and file order is never
    load-bearing.
    """
    return (rule.priority, rule.rule_id)


class RoutingConfig(StrictModel):
    """The whole of ``config/rules/routing_v3.yaml``."""

    version: str
    rules: list[RoutingRule] = Field(min_length=1)
    fixtures: list[RuleFixture] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fixtures_exist_and_predicates_parse(self) -> RoutingConfig:
        known = {fixture.fixture_id for fixture in self.fixtures}
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("duplicate rule_id in routing rules")
        for rule in self.rules:
            parse_predicate(rule.predicate)
            missing = [ref for ref in rule.fixtures if ref not in known]
            if missing:
                raise ValueError(
                    f"rule {rule.rule_id} references unknown fixtures: {missing}"
                )
        for fixture in self.fixtures:
            unknown = [rid for rid in fixture.expect_rule_ids if rid not in rule_ids]
            if unknown:
                raise ValueError(
                    f"fixture {fixture.fixture_id} expects unknown rules: {unknown}"
                )
        return self


class CalibrationBinSpec(StrictModel):
    """One step of the fitted map: scores at or below ``upper`` are worth this."""

    upper: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class CalibrationSpec(StrictModel):
    """A fitted confidence map WITH its provenance, or it is not a calibration.

    ``eval/calibration.py`` emits this block ready to paste. Every field of the
    provenance is required: a mapping whose gold set, model and date are unknown
    is a table of numbers somebody typed, and the whole point of calibration is
    that "confidence 0.9" can be traced to a measurement.
    """

    calibrated_on: str = Field(min_length=1, description="Gold set the fit used")
    model_id: str = Field(min_length=1, description="Model whose scores were fitted")
    fitted_at: str = Field(min_length=1, description="ISO date of the fit")
    expected_calibration_error: float | None = Field(default=None, ge=0.0, le=1.0)
    bins: list[CalibrationBinSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _monotone_and_complete(self) -> CalibrationSpec:
        if _parse_iso_date(self.fitted_at) is None:
            raise ValueError(f"fitted_at {self.fitted_at!r} is not an ISO date")
        uppers = [entry.upper for entry in self.bins]
        confidences = [entry.confidence for entry in self.bins]
        if uppers != sorted(uppers) or len(set(uppers)) != len(uppers):
            raise ValueError("calibration bins must be sorted by strictly rising upper")
        if confidences != sorted(confidences):
            raise ValueError(
                "calibration confidences must not fall as the raw score rises; "
                "a map that inverts would make a better match mean less"
            )
        if uppers[-1] < 1.0:
            raise ValueError(
                f"the last calibration bin ends at {uppers[-1]}, so raw scores "
                "above it have no mapping; the last upper must be 1.0"
            )
        return self


class ClassifierConfig(StrictModel):
    """The whole of ``config/classifier/classifier_v1.yaml`` (ADR-021).

    Its own file rather than a block inside the routing rules, for a reason
    that is not aesthetic: ``config/rules/routing_v3.yaml``'s version string is
    frozen into every gold-set MANIFEST, so a classifier tweak would either
    invalidate a frozen corpus or ship under a version that no longer describes
    the file. An independently versioned subsystem gets an independently
    versioned file.
    """

    version: str
    enabled: bool = False
    model_id: str = Field(min_length=1)
    min_confidence: float = Field(ge=0.0, le=1.0)
    exclude_unit_ids: list[str] = Field(
        default_factory=list,
        description="Units the classifier may never suggest (supervisory nodes "
        "and the catch-all); every id is checked against the taxonomy",
    )
    calibration: CalibrationSpec | None = None

    @model_validator(mode="after")
    def _enabled_needs_a_calibration(self) -> ClassifierConfig:
        """The refusal that makes 'log-only by default' a property, not a habit.

        Enabling the classifier lets a suggestion reach the decision table,
        where it is compared against a number (the 0.9 routing-confidence
        bound). Without a fitted map, the value being compared is a raw cosine,
        and comparing a cosine against a probability threshold is a category
        error that happens to produce a tier.
        """
        if not self.enabled:
            return self
        if self.calibration is None:
            raise ValueError(
                "classifier.enabled is true without a calibration block; a raw "
                "similarity is not a confidence and may not be compared with a "
                "confidence threshold. Fit one with python -m eval.calibrate"
            )
        if self.calibration.model_id != self.model_id:
            raise ValueError(
                f"the calibration was fitted on {self.calibration.model_id!r} but "
                f"the classifier runs {self.model_id!r}; a map fitted on one "
                "model says nothing about another"
            )
        return self


class ReviewConfig(StrictModel):
    """The whole of ``config/review/threshold_review_v1.yaml`` (P-5).

    One editable home for the review date, and the loader assembles it into
    ``AgencyRiskConfig.review_due`` - the ADR-016 contract field - exactly the
    way ``ProcedureFlags`` are assembled from ``config/procedures/``.
    """

    version: str
    review_due: date = Field(
        description="Date by which the governing thresholds must be re-reviewed "
        "(par. 88(5) Nr. 4 AO analog); the eval report warns when it has passed "
        "and never fails a gate on it"
    )
    note: str | None = None


#: Journal event types a notification template may be triggered by (ADR-005:
#: "received" triggers the instant receipt, "routed" the status update). Spelled
#: as strings rather than imported from ``EventType`` so that a config file can
#: never name an event type that exists but owes no notification.
NOTIFICATION_TRIGGERS = frozenset({"received", "routed"})

#: The par. 66 SGB I tripwire (task 07, ruling 3). A CHEAP CHECK, NOT A LEGAL
#: ANALYZER: it refuses template wording that reads like a Nachforderung, so the
#: informational boundary of ADR-005 fails loudly at startup instead of quietly
#: in front of an applicant. It cannot decide whether a sentence creates a legal
#: consequence - nothing automatic can - and it is not the reason the boundary
#: holds; the topology is (notifications are journal projections and never pass
#: the drafting path). It is here to catch the edit where somebody pastes a
#: request sentence into a receipt because it seemed helpful.
NACHFORDERUNG_TRIGGER_WORDS = (
    "mitwirkungspflicht",
    "mitzuwirken",
    "frist",  # also catches Fristablauf, Ausschlussfrist, fristgerecht
    "rechtsfolge",
    "versagung",
    "entziehung",
    "nachreichen",
    "nachzureichen",
    "vorzulegen",
    "par. 66",
    "§ 66",
)

#: Deadline-shaped literals. A date IN THE TEMPLATE SOURCE is a deadline
#: somebody typed; a date in the RENDERED output is a journal timestamp the
#: template asked for by name, which is allowed (ruling 2). This pattern
#: therefore only ever sees template text.
DEADLINE_SHAPED_RE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}|\d{4}-\d{2}-\d{2}")


class NotificationTemplate(StrictModel):
    """One outbound wording, addressed by ``template_id``.

    ``template_id`` is what the NOTIFIED event carries (``schemas/events.py``
    requires it), which is what makes "which text did this applicant actually
    receive" answerable from the journal alone.
    """

    template_id: str = Field(min_length=1)
    trigger: str = Field(description="Journal event type that owes this text")
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)

    @model_validator(mode="after")
    def _known_trigger_and_parseable_body(self) -> NotificationTemplate:
        if self.trigger not in NOTIFICATION_TRIGGERS:
            raise ValueError(
                f"template {self.template_id!r} is triggered by {self.trigger!r}; "
                f"known triggers: {sorted(NOTIFICATION_TRIGGERS)}"
            )
        # No autoescape: these are plain-text messages, not HTML. The inbox page
        # escapes them again when it displays them (api/metrics.py).
        environment = jinja2.Environment(autoescape=False)
        for label, text in (("subject", self.subject), ("body", self.body)):
            try:
                environment.parse(text)
            except jinja2.TemplateSyntaxError as error:
                raise ValueError(
                    f"template {self.template_id!r}: {label} is not valid Jinja2 "
                    f"({error.message})"
                ) from error
        return self


class NotificationChannel(StrictModel):
    """How one inbound channel is answered, and how formal that answer is."""

    channel: str
    delivery: Literal["status_event", "mail", "postal_stub"]
    display_name: str = Field(min_length=1)
    note: str | None = None

    @model_validator(mode="after")
    def _known_channel(self) -> NotificationChannel:
        known = {member.value for member in Channel}
        if self.channel not in known:
            raise ValueError(
                f"notification channel {self.channel!r} is not an inbound channel; "
                f"known: {sorted(known)}"
            )
        return self


class NotificationsConfig(StrictModel):
    """The whole of ``config/notifications/notifications_v1.yaml``.

    Validated here rather than in the worker so that a wording an agency may not
    send fails at STARTUP. A notification is the one artifact of this system a
    citizen reads, and the failure mode worth designing against is the one where
    a well-meant edit turns a receipt into something that looks like a demand.
    """

    version: str
    templates: list[NotificationTemplate] = Field(min_length=1)
    channels: list[NotificationChannel] = Field(min_length=1)
    procedure_names: dict[str, str] = Field(default_factory=dict)
    fallback_channel_name: str = Field(min_length=1)
    fallback_unit_name: str = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_complete_and_informational(self) -> NotificationsConfig:
        ids = [template.template_id for template in self.templates]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate template_id in notification templates")
        triggers = [template.trigger for template in self.templates]
        if len(triggers) != len(set(triggers)):
            raise ValueError(
                "two templates claim the same trigger; a journal event owes at "
                "most one notification, or replay would not be idempotent"
            )
        channels = [entry.channel for entry in self.channels]
        if len(channels) != len(set(channels)):
            raise ValueError("duplicate channel in notification channel mapping")
        missing = sorted({member.value for member in Channel} - set(channels))
        if missing:
            raise ValueError(
                f"no notification channel mapping for {missing}; an item that "
                f"arrived on an unmapped channel could not be answered at all"
            )
        problems = [
            problem
            for template in self.templates
            for problem in _informational_boundary_problems(template)
        ]
        if problems:
            raise ValueError(
                "notification templates may not read like a Nachforderung "
                "(ADR-005: informational Realakt, never a Verwaltungsakt): "
                + "; ".join(problems)
            )
        return self

    def template(self, template_id: str) -> NotificationTemplate | None:
        """The template with this id, or None."""
        for template in self.templates:
            if template.template_id == template_id:
                return template
        return None

    def template_for(self, trigger: str) -> NotificationTemplate | None:
        """The template a journal event type owes, or None."""
        for template in self.templates:
            if template.trigger == trigger:
                return template
        return None

    def channel(self, channel_id: str | None) -> NotificationChannel | None:
        """The channel mapping for an inbound channel id, or None."""
        if channel_id is None:
            return None
        for entry in self.channels:
            if entry.channel == channel_id:
                return entry
        return None


def _informational_boundary_problems(template: NotificationTemplate) -> list[str]:
    """Trigger words and deadline-shaped literals in one template's wording."""
    problems: list[str] = []
    for label, text in (("subject", template.subject), ("body", template.body)):
        lowered = text.lower()
        hits = sorted(word for word in NACHFORDERUNG_TRIGGER_WORDS if word in lowered)
        if hits:
            problems.append(f"{template.template_id}.{label} contains {hits}")
        if DEADLINE_SHAPED_RE.search(text):
            problems.append(
                f"{template.template_id}.{label} contains a date-shaped literal, "
                f"which in a template source is a deadline somebody typed"
            )
    return problems


#: The two kinds of draft part 08 produces (ADR-003, ADR-023). Tier 2 with gaps
#: owes a Nachforderung, tier 1 a prepared decision, tier 3 nothing at all.
DRAFT_KINDS = frozenset({"nachforderung", "prepared_decision"})

#: How a par. 66 Abs. 3 SGB I letter has to LEAVE the house (C-8). Stricter than
#: the inbound channel on purpose: par. 36a Abs. 2 SGB I lets an electronic
#: document replace the written form only with a qualified electronic signature.
DISPATCH_SHAPES = frozenset({"postal", "qualified_electronic", "print_stub"})

#: Every name a draft template may reference. ONE home for the context shape:
#: ``engine.draft.letters`` builds exactly these keys and asserts it, and the
#: loader refuses a template that names anything else - which turns what would
#: be a StrictUndefined error in front of a caseworker into a startup error.
DRAFT_CONTEXT_KEYS = frozenset(
    {
        "case_id",
        "received_at",
        "procedure_label",
        "unit_name",
        "empfaenger_anschrift",
        "versicherungsnummer",
        "geburtsdatum",
        "gaps",
        "facts",
        "window_days",
        "reply_channel",
        "rechtsfolgenhinweis",
        "rechtsfolgenhinweis_heading",
        "rechtsfolgenhinweis_body",
        "entwurf_banner",
        "no_model_notice",
        "dispatch_note",
    }
)

#: Filters a draft template may use. ``engine.draft.letters`` implements
#: exactly these and asserts it; the loader compiles a template against stubs of
#: the same names, so a template using a filter nobody implements is a startup
#: error rather than a render failure in front of a caseworker.
DRAFT_FILTERS = frozenset({"wrap"})

#: The slot a par. 66 Abs. 3 block must carry. Its presence is what makes the
#: hint case-specific rather than boilerplate, so it is required rather than
#: recommended.
REQUIREMENTS_SLOT_RE = re.compile(r"\{\{\s*requirements\s*\}\}")


def _stub_filter(value: Any, *args: Any, **kwargs: Any) -> Any:
    """Stand-in for a draft filter while a template is only being CHECKED.

    ``jinja2.meta.find_undeclared_variables`` compiles the template, so every
    filter it names has to exist in the environment doing the checking. The
    loader may not import ``engine.draft`` (that package imports this module),
    so it compiles against stubs and :data:`DRAFT_FILTERS` keeps the two lists
    from drifting.
    """
    return value


class DraftTemplate(StrictModel):
    """One prepared letter, addressed by ``template_id``.

    ``template_id`` is what the DRAFTED event carries, which is what makes
    "which wording is this draft" answerable from the journal - without the
    journal ever holding the rendered text.
    """

    template_id: str = Field(min_length=1)
    kind: str = Field(description="nachforderung | prepared_decision")
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)

    @model_validator(mode="after")
    def _known_kind_and_renderable(self) -> DraftTemplate:
        if self.kind not in DRAFT_KINDS:
            raise ValueError(
                f"draft template {self.template_id!r} has kind {self.kind!r}; "
                f"known kinds: {sorted(DRAFT_KINDS)}"
            )
        environment = jinja2.Environment(autoescape=False)
        for name in DRAFT_FILTERS:
            environment.filters[name] = _stub_filter
        for label, text in (("subject", self.subject), ("body", self.body)):
            try:
                parsed = environment.parse(text)
                names = jinja_meta.find_undeclared_variables(parsed)
            except jinja2.TemplateSyntaxError as error:
                # Covers an unknown FILTER too (TemplateAssertionError), which
                # only surfaces when the template is compiled for inspection.
                raise ValueError(
                    f"draft template {self.template_id!r}: {label} is not valid "
                    f"Jinja2 ({error.message})"
                ) from error
            unknown = sorted(names - DRAFT_CONTEXT_KEYS)
            if unknown:
                raise ValueError(
                    f"draft template {self.template_id!r}: {label} references "
                    f"{unknown}, which the draft context does not carry; "
                    f"available: {sorted(DRAFT_CONTEXT_KEYS)}"
                )
        return self


class AmtsermittlungEntry(StrictModel):
    """Requirements one procedure's applicant may be asked for more gently."""

    procedure_id: str
    requirement_ids: list[str] = Field(min_length=1)
    note: str | None = None


class AmtsermittlungPolicy(StrictModel):
    """The C-7 guard: what the agency can determine itself.

    Those requirements are still requested - an answer is faster and less
    error-prone than a lookup - but the wording softens and they are excluded
    from every par. 66 Abs. 3 scope, because par. 66 Abs. 1 SGB I only reaches
    facts whose clarification the applicant's cooperation actually enables.
    """

    note: str = Field(min_length=1)
    entries: list[AmtsermittlungEntry] = Field(default_factory=list)

    def requirement_ids(self, procedure_id: str | None) -> frozenset[str]:
        """Softened requirement ids for one procedure."""
        if procedure_id is None:
            return frozenset()
        return frozenset(
            requirement_id
            for entry in self.entries
            if entry.procedure_id == procedure_id
            for requirement_id in entry.requirement_ids
        )

    def note_for(self, procedure_id: str | None) -> str:
        """The softening sentence for a procedure; the general one by default."""
        for entry in self.entries:
            if entry.procedure_id == procedure_id and entry.note:
                return entry.note
        return self.note


class RechtsfolgenhinweisBlock(StrictModel):
    """The par. 66 Abs. 3 SGB I wording. Opt-in, and never boilerplate."""

    heading: str = Field(min_length=1)
    body: str = Field(min_length=1)

    @model_validator(mode="after")
    def _case_specific_and_cites_its_norm(self) -> RechtsfolgenhinweisBlock:
        if REQUIREMENTS_SLOT_RE.search(self.body) is None:
            raise ValueError(
                "the par. 66 Abs. 3 SGB I block must carry the "
                "{{ requirements }} slot: a Rechtsfolgenhinweis that does not "
                "name the concrete Angaben it applies to is boilerplate, and "
                "boilerplate does not satisfy par. 66 Abs. 3 SGB I"
            )
        if "66" not in self.body:
            raise ValueError(
                "the Rechtsfolgenhinweis block does not cite par. 66 SGB I; a "
                "warning that does not name its own norm is not one"
            )
        return self


class DraftFraming(StrictModel):
    """The three sentences every draft carries, in one editable home."""

    banner: str = Field(min_length=1)
    no_model_notice: str = Field(min_length=1)
    dispatch_note: str = Field(min_length=1)


class DraftChannel(StrictModel):
    """How a case is answered, and how a par. 66 letter must be dispatched."""

    channel: str
    reply: str = Field(min_length=1)
    dispatch: str = Field(
        description="Dispatch shape a Schriftform-bearing letter needs (C-8)"
    )
    note: str | None = None

    @model_validator(mode="after")
    def _known_channel_and_shape(self) -> DraftChannel:
        known = {member.value for member in Channel}
        if self.channel not in known:
            raise ValueError(
                f"draft channel {self.channel!r} is not an inbound channel; "
                f"known: {sorted(known)}"
            )
        if self.dispatch not in DISPATCH_SHAPES:
            raise ValueError(
                f"draft channel {self.channel!r} declares dispatch shape "
                f"{self.dispatch!r}; known: {sorted(DISPATCH_SHAPES)}"
            )
        return self


class AddresseeBlock(StrictModel):
    """The payload paths a letter head prints, all of them sealed.

    Not derived from a procedure's ``field_map``: the head of a letter is a
    property of the LETTER (who is being written to), not of a procedure's
    requirements - ``antragsteller.anschrift`` is a requirement of no procedure
    and is exactly what an envelope window needs.
    """

    anschrift: str | None = None
    versicherungsnummer: str | None = None
    geburtsdatum: str | None = None

    def paths(self) -> dict[str, str]:
        """Slot name -> payload path, for the declared slots only."""
        return {
            name: path
            for name, path in (
                ("anschrift", self.anschrift),
                ("versicherungsnummer", self.versicherungsnummer),
                ("geburtsdatum", self.geburtsdatum),
            )
            if path
        }


class DraftingConfig(StrictModel):
    """The whole of ``config/drafting/drafting_v1.yaml`` (ADR-023).

    Validated here rather than in the drafting engine so that wording an agency
    may not send fails at STARTUP. A draft is the artifact of this system with
    the most procedural consequence, and the failure mode worth designing
    against is a template that renders an empty slot into a letter a caseworker
    then confirms without reading closely.
    """

    version: str
    response_window_days: int = Field(
        ge=1,
        le=365,
        description="Response window the letter states relatively; the absolute "
        "deadline is computed at dispatch (engine/draft/bekanntgabe.py). The "
        "upper bound lives here and nowhere else",
    )
    framing: DraftFraming
    amtsermittlung: AmtsermittlungPolicy
    rechtsfolgenhinweis: RechtsfolgenhinweisBlock
    channels: list[DraftChannel] = Field(min_length=1)
    addressee: AddresseeBlock = Field(default_factory=AddresseeBlock)
    procedure_names: dict[str, str] = Field(default_factory=dict)
    fallback_procedure_name: str = Field(min_length=1)
    fallback_unit_name: str = Field(min_length=1)
    templates: list[DraftTemplate] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_template_per_kind_and_every_channel_answerable(self) -> DraftingConfig:
        ids = [template.template_id for template in self.templates]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate template_id in draft templates")
        kinds = [template.kind for template in self.templates]
        if len(kinds) != len(set(kinds)):
            raise ValueError(
                "two draft templates claim the same kind; a tier decision owes "
                "at most one draft, or replay would not be idempotent"
            )
        missing_kinds = sorted(DRAFT_KINDS - set(kinds))
        if missing_kinds:
            raise ValueError(
                f"no draft template for {missing_kinds}; an item of that kind "
                f"would silently get no draft at all"
            )
        channels = [entry.channel for entry in self.channels]
        if len(channels) != len(set(channels)):
            raise ValueError("duplicate channel in draft channel mapping")
        missing = sorted({member.value for member in Channel} - set(channels))
        if missing:
            raise ValueError(
                f"no draft channel mapping for {missing}; a letter answering an "
                f"item from that channel could not state a reply channel"
            )
        return self

    def template_for(self, kind: str) -> DraftTemplate | None:
        """The template one draft kind uses, or None."""
        for template in self.templates:
            if template.kind == kind:
                return template
        return None

    def channel(self, channel_id: str | None) -> DraftChannel | None:
        """The channel mapping for an inbound channel id, or None."""
        if channel_id is None:
            return None
        for entry in self.channels:
            if entry.channel == channel_id:
                return entry
        return None

    def procedure_label(self, procedure_id: str | None) -> str:
        """How a procedure is named in a Betreff; never an empty string."""
        if procedure_id is None:
            return self.fallback_procedure_name
        return self.procedure_names.get(procedure_id, self.fallback_procedure_name)


class DispatchExport(StrictModel):
    """What the confirm-and-dispatch stub writer claims to produce."""

    format_id: str = Field(min_length=1)
    note: str = Field(min_length=1)
    omit_caseworker_identity: bool = True


class DispatchConfig(StrictModel):
    """The whole of ``config/dispatch/dispatch_v1.yaml`` (part 10).

    The Land and its holidays are the only two values in this repository that a
    deployment MUST replace before a real letter goes out, and both are marked
    as placeholders rather than guessed. ``holidays`` empty means "weekends
    shift, nothing else does" - a deadline that is right except where a
    Land-specific holiday would have pushed it further out, which is the safe
    direction to be wrong in and is printed next to every computed date.
    """

    version: str
    land: str = Field(min_length=1)
    land_note: str = Field(min_length=1)
    holidays: list[date] = Field(default_factory=list)
    export: DispatchExport

    @model_validator(mode="after")
    def _holidays_are_unique(self) -> DispatchConfig:
        if len(self.holidays) != len(set(self.holidays)):
            raise ValueError(
                "duplicate date in the dispatch holiday set; a holiday listed "
                "twice is an edit that went wrong, not a longer holiday"
            )
        return self

    def holiday_set(self) -> frozenset[date]:
        """The injectable holiday set ``response_deadline`` takes."""
        return frozenset(self.holidays)


class QueueClock(StrictModel):
    """One named clock a queue displays, with the basis it rests on."""

    basis: str = Field(min_length=1)
    note: str = Field(min_length=1)


class ClearingClock(QueueClock):
    """The clearing queue's self-imposed SLA (C-10, par. 16 Abs. 2 SGB I)."""

    sla_hours: int = Field(ge=1, le=24 * 90)


class RehaClock(QueueClock):
    """The par. 14 Abs. 1 SGB IX two-week Weiterleitungsfrist (C-10)."""

    unit_ids: list[str] = Field(min_length=1)
    weiterleitung_days: int = Field(ge=1, le=365)


class WiderspruchFlag(StrictModel):
    """C-9's queue flag. A visibility marker and deliberately nothing else."""

    unit_ids: list[str] = Field(min_length=1)
    flag_label: str = Field(min_length=1)
    note: str = Field(min_length=1)


class QueuesConfig(StrictModel):
    """The whole of ``config/queues/queues_v1.yaml`` (part 10).

    Display-only by contract: nothing in this model reaches a tier, a routing
    or an exit code. The validator enforces the one thing a typo could break
    silently - a budget for a tier that does not exist would be a line nobody
    ever sees.
    """

    version: str
    clearing_unit_id: str = Field(min_length=1)
    clearing: ClearingClock
    reha: RehaClock
    widerspruch: WiderspruchFlag
    latency_budget_hours: dict[str, int]
    latency_note: str = Field(min_length=1)

    @model_validator(mode="after")
    def _budgets_cover_every_tier(self) -> QueuesConfig:
        tiers = {str(int(member)) for member in Tier}
        missing = sorted(tiers - set(self.latency_budget_hours))
        if missing:
            raise ValueError(
                f"no latency budget for tier(s) {missing}; a tier without a "
                f"budget line would silently never be reported as overdue"
            )
        unknown = sorted(set(self.latency_budget_hours) - tiers)
        if unknown:
            raise ValueError(f"latency budget for unknown tier(s) {unknown}")
        for tier, hours in self.latency_budget_hours.items():
            if hours < 1:
                raise ValueError(f"tier {tier} latency budget must be at least 1 hour")
        return self

    def budget_hours(self, tier: int | None) -> int | None:
        """The display budget for a tier, or None when the tier is unknown."""
        if tier is None:
            return None
        return self.latency_budget_hours.get(str(tier))


class TaxonomyConfig(StrictModel):
    """The whole of ``config/taxonomy/<agency>_v2.yaml``."""

    version: str
    nodes: list[TaxonomyNode] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_and_resolvable(self) -> TaxonomyConfig:
        ids = [node.unit_id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate unit_id in taxonomy")
        known = set(ids)
        for node in self.nodes:
            if node.parent_id is not None and node.parent_id not in known:
                raise ValueError(f"unknown parent_id {node.parent_id!r}")
        return self


@dataclass(frozen=True)
class ConfigBundle:
    """Everything the pipeline needs, loaded once and passed down."""

    taxonomy: TaxonomyConfig
    routing: RoutingConfig
    procedures: dict[str, ProcedureConfig]
    decision_table: DecisionTable
    risk: AgencyRiskConfig
    config_dir: Path
    redaction: IdentityFieldsPolicy
    extraction: ExtractionConfig
    classifier: ClassifierConfig | None = None
    review: ReviewConfig | None = None
    notifications: NotificationsConfig | None = None
    drafting: DraftingConfig | None = None
    scoring: ScoringConfig | None = None
    dispatch: DispatchConfig | None = None
    queues: QueuesConfig | None = None

    @property
    def scoring_dir(self) -> Path:
        """Where the scorer's config and its reference population live."""
        return self.config_dir / "scoring"

    def sealed_field_ids(self, procedure_id: str | None) -> frozenset[str]:
        """Requirement/field ids of a procedure whose payload path is sealed.

        The completeness checker reads this to know whose observed value may
        never appear in a problem string. One derivation, from the policy and
        the procedure's own ``field_map``, so classification cannot drift.
        """
        procedure = self.procedure(procedure_id)
        if procedure is None:
            return frozenset()
        return self.redaction.sealed_field_ids(procedure.field_paths)

    def procedure(self, procedure_id: str | None) -> ProcedureConfig | None:
        """Procedure config for an id, or None when the procedure is unknown."""
        if procedure_id is None:
            return None
        return self.procedures.get(procedure_id)

    def unit(self, unit_id: str) -> TaxonomyNode | None:
        """Taxonomy node for a unit id, or None."""
        for node in self.taxonomy.nodes:
            if node.unit_id == unit_id:
                return node
        return None

    def version_stamp(self, *, model_id: str | None = None) -> VersionStamp:
        """Full provenance stamp for every artifact produced from this config.

        ``model_id`` is the one part that is not a property of the config
        directory: which extractor produced the values on THIS run. The
        deterministic paths stamp their own id ("mapper:v0", "replay:v4") so
        provenance is never blank, and a live run stamps the model it called.
        """
        return VersionStamp(
            schema_version=SCHEMA_VERSION,
            taxonomy_version=self.taxonomy.version,
            rules_version=self.routing.version,
            decision_table_version=self.decision_table.version,
            thresholds_version=self.risk.version,
            prompt_version=self.extraction.prompt_version,
            model_id=model_id,
        )


def load_config(config_dir: Path | str | None = None) -> ConfigBundle:
    """Load, validate and assemble the config bundle.

    Args:
        config_dir: directory to read; defaults to ``$EINGANGSLOTSE_CONFIG_DIR``
            or the repo's ``config/``.

    Raises:
        ConfigError: if a required file is missing or a document is invalid.
    """
    directory = Path(config_dir) if config_dir is not None else _default_config_dir()
    taxonomy = TaxonomyConfig.model_validate(
        _read_yaml(_single_file(directory / "taxonomy", "taxonomy"))
    )
    routing = RoutingConfig.model_validate(
        _read_yaml(_single_file(directory / "rules", "routing rules"))
    )
    procedures = {
        procedure.procedure_id: procedure
        for procedure in (
            ProcedureConfig.model_validate(_read_yaml(path))
            for path in sorted((directory / "procedures").glob("*.yaml"))
        )
    }
    if not procedures:
        raise ConfigError(f"no procedure config found in {directory / 'procedures'}")
    decision_table = DecisionTable.model_validate(
        _read_yaml(_single_file(directory / "decision", "decision table"))
    )
    review = _load_optional(
        directory / "review", "threshold review register", ReviewConfig
    )
    risk_document = _read_yaml(directory / "thresholds.yaml")
    if "procedures" in risk_document:
        raise ConfigError(
            "thresholds.yaml must not list procedures; ProcedureFlags are edited "
            "in config/procedures/ and assembled by the loader"
        )
    if "review_due" in risk_document:
        raise ConfigError(
            "thresholds.yaml must not set review_due; the review date is edited "
            "in config/review/ and assembled by the loader, so it has exactly "
            "one editable home"
        )
    risk_document["procedures"] = [
        procedure.flags.model_dump() for procedure in procedures.values()
    ]
    if review is not None:
        risk_document["review_due"] = review.review_due.isoformat()
    risk = AgencyRiskConfig.model_validate(risk_document)
    redaction = load_policy(directory)
    extraction = ExtractionConfig.model_validate(
        _read_yaml(_single_file(directory / "extraction", "extraction policy"))
    )
    classifier = _load_optional(
        directory / "classifier", "classifier settings", ClassifierConfig
    )
    notifications = _load_optional(
        directory / "notifications", "notification templates", NotificationsConfig
    )
    drafting = _load_optional(directory / "drafting", "draft templates", DraftingConfig)
    scoring = _load_optional(directory / "scoring", "scoring settings", ScoringConfig)
    dispatch = _load_optional(
        directory / "dispatch", "dispatch settings", DispatchConfig
    )
    queues = _load_optional(directory / "queues", "queue settings", QueuesConfig)
    _check_units_exist(routing.rules, taxonomy.nodes)
    _check_classifier_units(classifier, taxonomy.nodes)
    _check_sealed_fields_are_witnessed(redaction, procedures)
    _check_identity_paths_are_presence_only(redaction, routing, procedures)
    _check_text_rules(predicate_sources(routing, procedures))
    _check_notification_names(notifications, procedures)
    _check_drafting_requirements(drafting, procedures)
    _check_drafting_addressee(drafting, redaction)
    _check_scoring(scoring, procedures, risk)
    _check_queue_units(queues, taxonomy.nodes)
    return ConfigBundle(
        taxonomy=taxonomy,
        routing=routing,
        procedures=procedures,
        decision_table=decision_table,
        risk=risk,
        config_dir=directory,
        redaction=redaction,
        extraction=extraction,
        classifier=classifier,
        review=review,
        notifications=notifications,
        drafting=drafting,
        scoring=scoring,
        dispatch=dispatch,
        queues=queues,
    )


def _default_config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_ENV)
    return Path(override) if override else DEFAULT_CONFIG_DIR


def _single_file(directory: Path, label: str) -> Path:
    candidates = sorted(directory.glob("*.yaml"))
    if len(candidates) != 1:
        raise ConfigError(
            f"expected exactly one {label} file in {directory}, found "
            f"{[c.name for c in candidates]}"
        )
    return candidates[0]


def _load_optional[ModelT: StrictModel](
    directory: Path, label: str, model: type[ModelT]
) -> ModelT | None:
    """Load a whole optional config subsystem, or None when its directory is absent.

    Absent is a legitimate state with a defined meaning, not a degraded one: no
    ``config/classifier/`` means the fallback classifier does not exist for this
    agency, and no ``config/review/`` means no review date has been set. A
    directory that IS there is validated like everything else - half a config
    is an error, missing config is a choice.
    """
    if not directory.is_dir():
        return None
    return model.model_validate(_read_yaml(_single_file(directory, label)))


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ConfigError(f"{path} must contain a YAML mapping")
    return document


def _check_validation_keys(requirement: Requirement, known_fields: set[str]) -> None:
    """Reject a validation block the evidence plane could not honour.

    Checked at load time on purpose: an unsupported key, a malformed date bound
    or a cross-field check pointing at a requirement that does not exist would
    otherwise evaluate to "no problem found" forever, which is the one failure
    mode a completeness checker must not have.
    """
    if requirement.validation is None:
        return
    label = f"requirement {requirement.requirement_id}"
    unknown = set(requirement.validation) - SUPPORTED_VALIDATION_KEYS
    if unknown:
        raise ValueError(
            f"{label} uses unsupported validation keys {sorted(unknown)}; "
            f"supported: {sorted(SUPPORTED_VALIDATION_KEYS)}"
        )
    date_block = requirement.validation.get("date")
    if date_block is not None:
        _check_date_block(date_block, label)
    cross_field = requirement.validation.get("cross_field")
    if cross_field is not None:
        _check_cross_field(cross_field, label, known_fields)


def _check_date_block(block: object, label: str) -> None:
    if not isinstance(block, dict):
        raise ValueError(f"{label}: 'date' must be a mapping")
    unknown = set(block) - SUPPORTED_DATE_KEYS
    if unknown:
        raise ValueError(
            f"{label}: unsupported date keys {sorted(unknown)}; "
            f"supported: {sorted(SUPPORTED_DATE_KEYS)}"
        )
    for key in sorted(SUPPORTED_DATE_KEYS):
        bound = block.get(key)
        if bound is None:
            continue
        if not isinstance(bound, str) or _parse_iso_date(bound) is None:
            raise ValueError(f"{label}: date bound {key}={bound!r} is not an ISO date")


def _check_cross_field(block: object, label: str, known_fields: set[str]) -> None:
    if not isinstance(block, list):
        raise ValueError(f"{label}: 'cross_field' must be a list of checks")
    for entry in block:
        if not isinstance(entry, dict):
            raise ValueError(f"{label}: every cross_field check must be a mapping")
        unknown = set(entry) - SUPPORTED_CROSS_FIELD_KEYS
        if unknown:
            raise ValueError(
                f"{label}: unsupported cross_field keys {sorted(unknown)}; "
                f"supported: {sorted(SUPPORTED_CROSS_FIELD_KEYS)}"
            )
        kind = entry.get("kind")
        if kind not in SUPPORTED_CROSS_FIELD_KINDS:
            raise ValueError(
                f"{label}: unknown cross_field kind {kind!r}; "
                f"supported: {sorted(SUPPORTED_CROSS_FIELD_KINDS)}"
            )
        other = entry.get("field")
        if not isinstance(other, str) or other not in known_fields:
            raise ValueError(
                f"{label}: cross_field check {kind!r} references {other!r}, "
                f"which is not a requirement of this procedure"
            )
        if kind in CROSS_FIELD_KINDS_WITH_YEARS and not isinstance(
            entry.get("years"), int
        ):
            raise ValueError(
                f"{label}: cross_field kind {kind!r} needs integer 'years'"
            )


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _check_units_exist(
    rules: Iterable[RoutingRule], nodes: Iterable[TaxonomyNode]
) -> None:
    known = {node.unit_id for node in nodes}
    for rule in rules:
        if rule.unit_id not in known:
            raise ConfigError(
                f"rule {rule.rule_id} routes to unknown unit {rule.unit_id!r}"
            )


def _check_queue_units(
    queues: QueuesConfig | None, nodes: Iterable[TaxonomyNode]
) -> None:
    """Every unit id a queue clock names must be a unit that exists.

    The same failure mode as the classifier exclusions: a Reha clock pointed at
    a Referat nobody spelled correctly is a clock that silently never shows,
    and the queue would look calm because a string was wrong rather than
    because the work was done.
    """
    if queues is None:
        return
    known = {node.unit_id for node in nodes}
    named = {
        "clearing_unit_id": [queues.clearing_unit_id],
        "reha.unit_ids": list(queues.reha.unit_ids),
        "widerspruch.unit_ids": list(queues.widerspruch.unit_ids),
    }
    for where, unit_ids in named.items():
        unknown = sorted(set(unit_ids) - known)
        if unknown:
            raise ConfigError(
                f"queue settings name unknown unit(s) {unknown} in {where}; a "
                f"queue clock on a unit that does not exist never fires"
            )


def _check_classifier_units(
    classifier: ClassifierConfig | None, nodes: Iterable[TaxonomyNode]
) -> None:
    """Every excluded unit id must be a unit that exists.

    An exclusion for a unit id nobody has spelled correctly is an exclusion
    that silently does nothing, which is the worst outcome available here: the
    catch-all Referat would quietly become suggestible again after a taxonomy
    supersession renamed it.
    """
    if classifier is None:
        return
    known = {node.unit_id for node in nodes}
    unknown = sorted(set(classifier.exclude_unit_ids) - known)
    if unknown:
        raise ConfigError(
            f"classifier excludes unknown unit(s) {unknown}; an exclusion that "
            f"matches nothing is an exclusion that does nothing"
        )


def _check_notification_names(
    notifications: NotificationsConfig | None, procedures: Mapping[str, ProcedureConfig]
) -> None:
    """Refuse a display name for a procedure that does not exist.

    The names in ``procedure_names`` are the ONLY way a procedure can be named to
    an applicant (engine/notify/render.py refuses to echo the submission), so an
    entry under a misspelled id is an entry that silently never renders and a
    receipt that silently never says what it is about.
    """
    if notifications is None:
        return
    unknown = sorted(set(notifications.procedure_names) - set(procedures))
    if unknown:
        raise ConfigError(
            f"notifications name unknown procedure(s) {unknown}; a display name "
            f"for a procedure that does not exist would never be rendered"
        )


def _check_drafting_addressee(
    drafting: DraftingConfig | None, policy: IdentityFieldsPolicy
) -> None:
    """Every path the letter head prints must be identity-classed.

    A letter head that named an unsealed path would print payload content that
    never passed the vault, and the re-hydration round-trip check - which only
    ever sees sealed values - would have nothing to say about it.
    """
    if drafting is None:
        return
    unsealed = sorted(
        f"{slot} -> {path}"
        for slot, path in drafting.addressee.paths().items()
        if policy.covering(path) is None
    )
    if unsealed:
        raise ConfigError(
            f"the draft addressee block names path(s) the redaction policy does "
            f"not seal: {unsealed}. A letter head may only print values that "
            f"went through the vault"
        )


def _check_drafting_requirements(
    drafting: DraftingConfig | None, procedures: Mapping[str, ProcedureConfig]
) -> None:
    """Refuse a drafting config naming a procedure or requirement nobody has.

    The C-7 half that matters: the Amtsermittlung list decides which requests
    soften AND which requirements are excluded from a par. 66 Abs. 3 scope. An
    entry under a misspelled requirement id would do neither, silently - the
    applicant would be threatened with a Versagung over a fact the agency can
    look up itself, which is precisely the outcome the guard exists to prevent.
    """
    if drafting is None:
        return
    unknown_named = sorted(set(drafting.procedure_names) - set(procedures))
    if unknown_named:
        raise ConfigError(
            f"drafting names unknown procedure(s) {unknown_named}; a Betreff for "
            f"a procedure that does not exist would never be rendered"
        )
    problems: list[str] = []
    for entry in drafting.amtsermittlung.entries:
        procedure = procedures.get(entry.procedure_id)
        if procedure is None:
            problems.append(f"unknown procedure {entry.procedure_id!r}")
            continue
        declared = {
            requirement.requirement_id
            for requirement in procedure.requirements.requirements
        }
        unknown = sorted(set(entry.requirement_ids) - declared)
        if unknown:
            problems.append(
                f"procedure {entry.procedure_id!r} does not declare {unknown}"
            )
    if problems:
        raise ConfigError(
            "drafting amtsermittlung (C-7) names requirements no procedure "
            "declares: " + "; ".join(problems)
        )


def _check_scoring(
    scoring: ScoringConfig | None,
    procedures: Mapping[str, ProcedureConfig],
    risk: AgencyRiskConfig,
) -> None:
    """Three refusals that keep the scorer from failing quietly.

    A leading-date field the named procedure does not map would make its
    feature a constant zero for every item of that procedure - a silently dead
    signal, which is worse than a missing one because the report keeps showing
    a column. A threshold id that collides with one in ``thresholds.yaml``
    would make ``AnomalyEvidence.threshold_ref`` ambiguous, and the whole point
    of the second id is that a reader can tell the calibrated number from the
    historical placeholder. And a procedure named here that does not exist is a
    rename nobody carried through.
    """
    if scoring is None:
        return
    problems: list[str] = []
    for procedure_id, field in sorted(scoring.leading_date_fields.items()):
        procedure = procedures.get(procedure_id)
        if procedure is None:
            problems.append(f"unknown procedure {procedure_id!r}")
            continue
        if field not in procedure.field_paths:
            problems.append(
                f"procedure {procedure_id!r} does not map a field {field!r}"
            )
    if problems:
        raise ConfigError(
            "scoring config names leading dates no procedure carries: "
            + "; ".join(problems)
        )
    collisions = sorted(
        threshold.threshold_id
        for threshold in risk.thresholds
        if threshold.threshold_id == scoring.threshold.threshold_id
    )
    if collisions:
        raise ConfigError(
            f"scoring threshold id {scoring.threshold.threshold_id!r} is also "
            f"used in thresholds.yaml; AnomalyEvidence.threshold_ref must name "
            f"exactly one number"
        )


def _check_sealed_fields_are_witnessed(
    policy: IdentityFieldsPolicy, procedures: Mapping[str, ProcedureConfig]
) -> None:
    """Refuse a procedure that maps a sealed path without a witness entry.

    Sealing a path removes its value from the working copy; a ``field_map``
    entry over that path would then hand the completeness checker a random
    token and get "valid" back for anything. The policy row that says
    ``witness: false`` is a promise that nothing validates that path, and this
    is where the promise is checked instead of trusted.
    """
    for procedure_id, procedure in sorted(procedures.items()):
        problems = check_witnessless_seals(policy, procedure.field_paths.values())
        if problems:
            raise ConfigError(
                f"procedure {procedure_id}: " + "; ".join(sorted(problems))
            )


def _check_identity_paths_are_presence_only(
    policy: IdentityFieldsPolicy,
    routing: RoutingConfig,
    procedures: Mapping[str, ProcedureConfig],
) -> None:
    """Identity-classed fields may only be tested for presence, never by value.

    The transparency invariant of the redaction boundary: after sealing, an
    identity path holds a random placeholder, so ``op: ne / value: null`` still
    answers the same question it did before and every other comparison quietly
    starts answering a different one. Today the shipped configs only ever ask
    whether ``payload.auftraggeber.firmenname`` is present; this check is what
    makes tomorrow's ``op: eq / value: "Musterfirma GmbH"`` a loud config error
    instead of a routing rule that silently stops firing.
    """
    sealed_fields: set[str] = set()
    for procedure in procedures.values():
        sealed_fields |= policy.sealed_field_ids(procedure.field_paths)
    problems = [
        f"{label}: {problem}"
        for label, predicate in predicate_sources(routing, procedures)
        for problem in _value_comparisons_on_identity(
            parse_predicate(predicate), policy, sealed_fields
        )
    ]
    if problems:
        raise ConfigError(
            "identity-classed fields may only be tested for presence "
            "(op: eq/ne against null): " + "; ".join(problems)
        )


def predicate_sources(
    routing: RoutingConfig, procedures: Mapping[str, ProcedureConfig]
) -> list[tuple[str, dict[str, Any]]]:
    """Every predicate the shipped config contains, with a readable label.

    Three kinds, and every lint in this module runs over all three: a routing
    rule, a procedure's derivation signal and a procedure's clear-cut criteria
    are the same kind of object - a declarative condition an agency wrote - and
    a check that covered only one of them would be a check somebody could walk
    around by moving a comparison.
    """
    sources: list[tuple[str, dict[str, Any]]] = [
        (f"routing rule {rule.rule_id}", rule.predicate) for rule in routing.rules
    ]
    for procedure_id, procedure in sorted(procedures.items()):
        if procedure.derivation is not None:
            sources.append(
                (
                    f"derivation signal {procedure.derivation.signal_id}"
                    f" ({procedure_id})",
                    procedure.derivation.predicate,
                )
            )
        if procedure.clear_cut is not None:
            sources.append(
                (
                    f"clear-cut criteria {procedure.clear_cut.criteria_id}"
                    f" ({procedure_id})",
                    procedure.clear_cut.predicate,
                )
            )
    return sources


def _check_text_rules(sources: Sequence[tuple[str, dict[str, Any]]]) -> None:
    """Three rules for the ``text.*`` namespace (ADR-020).

    **No rule may quote an identity value.** The presence-only lint of part 04
    protects identity PATHS, and it cannot see this: a text rule names no path,
    it names a literal, and ``text.normalized contains "17170459B012"`` would
    put a Versicherungsnummer into a config file, into git, and into every
    report that prints the rule that fired. The check is the part-04 detector
    union run over the literal itself - the same recognizers that decide what to
    seal decide what may not be written down.

    **Only text operators, on known text fields.** ``eq`` against a whole letter
    can never be true and ``gt`` on prose is meaningless, so both are config
    errors rather than rules that silently never fire. Presence
    (``op: ne / value: null``) stays allowed: "this item has text at all" is a
    legitimate question.
    """
    scanner = redact_detector(with_ner=False)
    problems: list[str] = []
    for label, predicate in sources:
        for node in _comparisons(parse_predicate(predicate)):
            if not node.field.startswith(TEXT_PREFIX):
                continue
            if node.field not in TEXT_FIELDS:
                problems.append(
                    f"{label}: unknown text field {node.field!r}; known: "
                    f"{sorted(TEXT_FIELDS)}"
                )
                continue
            if not isinstance(node.op, TextOp):
                if node.op in PRESENCE_OPS and node.value is None:
                    continue
                problems.append(
                    f"{label}: {node.field} uses op {node.op.value}; text fields "
                    f"take {sorted(item.value for item in TextOp)} or a "
                    f"presence test against null"
                )
                continue
            if isinstance(node.value, str) and scanner.scan(node.value):
                problems.append(
                    f"{label}: the literal compared against {node.field} looks "
                    f"like identity data and may not stand in config"
                )
    if problems:
        raise ConfigError("invalid text rule(s): " + "; ".join(problems))


def _comparisons(node: PredicateNode) -> list[Comparison]:
    """Every leaf comparison of a predicate, in order."""
    if isinstance(node, AllOf | AnyOf):
        return [leaf for child in node.nodes for leaf in _comparisons(child)]
    return [node]


def _value_comparisons_on_identity(
    node: PredicateNode, policy: IdentityFieldsPolicy, sealed_fields: Iterable[str]
) -> list[str]:
    if isinstance(node, AllOf | AnyOf):
        return [
            problem
            for child in node.nodes
            for problem in _value_comparisons_on_identity(child, policy, sealed_fields)
        ]
    if not _is_identity_field(node, policy, sealed_fields):
        return []
    if node.op in PRESENCE_OPS and node.value is None:
        return []
    return [f"{node.field} compared with op {node.op.value} against {node.value!r}"]


def _is_identity_field(
    node: Comparison, policy: IdentityFieldsPolicy, sealed_fields: Iterable[str]
) -> bool:
    if node.field.startswith(PAYLOAD_PREFIX):
        return policy.covering(node.field[len(PAYLOAD_PREFIX) :]) is not None
    if node.field.startswith(EXTRACTION_PREFIX):
        return node.field[len(EXTRACTION_PREFIX) :] in set(sealed_fields)
    return False
