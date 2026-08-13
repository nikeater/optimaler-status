"""Corpus loading and metric computation.

The metric that governs everything is ``false_clear_rate``: an item the gold set
says needs human oversight (tier 2 or 3) that the system cleared to tier 1. It
is the fatal error class for this system and its budget is zero, permanently.
``false_flag_rate`` is its counterweight (tier-1 items pushed to oversight) and
is an efficiency number, not a gate: the system is allowed to be cautious.

Part 02 adds three views on top, all of them honest about their limits:

* **Completeness precision/recall** over detected gaps, micro-averaged across
  (item, requirement) pairs. On a corpus whose gaps are all deterministic field
  checks these sit at 1.0 by construction; they become informative when
  attachments (part 04) and the LLM extractor (part 05) start producing gaps
  that can be wrong. They are here now so the number has a history.
* **Per-procedure breakdown**, because one procedure with a closed tier-1 gate
  and one without average into a number that describes neither.
* **Anomalous-subset agreement**, reported separately and never mixed into the
  headline: those items are labelled with the tier today's rules produce, so
  agreement of 1.0 is the baseline the shadow scorer (part 06) will move, not a
  quality claim.

Part 04 adds a **redaction** section: per-kind recall and precision of the
detector union against the seeded German-PII golden set (P-7), plus the detector
inventory and whether the optional NER extra was installed when the number was
measured. It is reported next to the triage metrics and gated separately: a
redaction recall of 1.000 says nothing about routing, and a routing accuracy of
1.000 says nothing about what leaked.

Part 03 adds **procedure-derivation accuracy**: how often the evidence plane
arrives at the procedure the corpus declares, *and by the route it declares*.
Both halves matter. An item whose procedure the channel happened to state
correctly and an item whose procedure had to be read off the form are different
achievements, so ``hint``, ``content`` and ``none`` are also reported
separately: a system that answered every item with "no idea" would score 0 on
the derivation metric while leaving the tier metrics untouched.

Metrics live here; ``eval/run.py`` is only the CLI around them.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from engine.config_loader import ConfigBundle, load_config
from engine.decide import admitted_routing
from engine.draft import DraftOutcome, InMemoryDraftStore, draft_case
from engine.draft.projection import facts_from
from engine.evidence import Embedder
from engine.journal.store import InMemoryJournalStore
from engine.notify import (
    InMemoryOutbox,
    LatencySample,
    case_latencies,
    latency_section,
    notify_case,
)
from engine.pipeline import run_pipeline
from engine.redact import InMemoryVaultStore
from engine.redact.placeholders import PLACEHOLDER_RE, PLACEHOLDER_SHAPED_RE
from engine.redact.recall import redaction_metrics
from engine.review import ReviewIndex, ReviewState, queue_census, review_state
from engine.score import Scorer, ScoringOutcome, scorer_from_config
from eval.anomaly import anomaly_section, bias_section, reasons_gate_passed
from eval.classifier import classifier_section, observe_corpus
from eval.thresholds import threshold_review
from schemas import SCHEMA_VERSION
from schemas.anomaly import AnomalyReason
from schemas.common import StrictModel
from schemas.extraction import MatchMode

DEFAULT_GOLD_DIR = Path("corpus/gold/v4")
DEFAULT_REPORT_PATH = Path("eval/reports/latest.json")
LABEL_SUFFIX = ".labels.yaml"
MANIFEST_NAME = "MANIFEST.yaml"
UNKNOWN_PROCEDURE = "unknown"

#: What the items WITHOUT free text have to score, exactly. Not a target and not
#: a tolerance: these are the frozen items of the previous gold set, unchanged,
#: and the whole claim of the text path is that it did not touch them.
STRUCTURED_INVARIANT: dict[str, float] = {
    "routing_accuracy": 1.0,
    "tier_accuracy": 1.0,
    "false_clear_rate": 0.0,
    "false_flag_rate": 0.0,
    "derivation_accuracy": 1.0,
}


class GoldGap(StrictModel):
    """One expected gap in an item's ground truth."""

    requirement_id: str
    status: str = "missing"


class GoldLabels(StrictModel):
    """Ground truth sidecar for one corpus item.

    The part-01 fields are required; everything the part-02 generator adds
    carries a default, so the older ``corpus/gold/s1`` sidecars still load.
    """

    item_id: str
    expected_unit_id: str | None = None
    expected_tier: int
    expected_gaps: list[GoldGap] = Field(default_factory=list)
    procedure_id: str | None = None
    derived_procedure_id: str | None = None
    derivation_source: str | None = Field(
        default=None,
        description="Ground truth for procedure derivation (hint|content|none); "
        "None means this sidecar predates part 03 and is skipped by the metric",
    )
    scenario_kind: str | None = None
    anomaly_expected: bool = False
    anomaly_pattern: str | None = None
    paraphrase: str = "none"
    known_divergence: list[str] = Field(
        default_factory=list,
        description="Label fields the corpus declares today's rules get wrong; "
        "carried for the reader, deliberately NOT excluded from any metric",
    )
    divergence_reason: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class GoldItem:
    """A corpus item: submission payload plus its labels."""

    item_id: str
    payload: dict[str, Any]
    labels: GoldLabels
    path: Path


@dataclass(frozen=True)
class ItemResult:
    """What the pipeline made of one gold item."""

    item_id: str
    expected_unit_id: str | None
    actual_unit_id: str | None
    expected_tier: int
    actual_tier: int
    expected_gaps: list[str]
    actual_gaps: list[str]
    reason_kinds: list[str]
    procedure_id: str = UNKNOWN_PROCEDURE
    anomaly_expected: bool = False
    known_divergence: list[str] = field(default_factory=list)
    paraphrase: str = "none"
    expected_derivation_source: str | None = None
    actual_derivation_source: str | None = None
    expected_derived_procedure_id: str | None = None
    actual_derived_procedure_id: str | None = None
    source_types: tuple[str, ...] = ()
    proposals: int = 0
    verified: int = 0
    discarded: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    match_modes: dict[str, int] = field(default_factory=dict)
    #: What the decision table saw as routing.confidence, and whether any rule
    #: fired at all. Both are needed by the threshold-review section to say how
    #: far this item sits from the 0.9 bound - a number the report may not
    #: recompute from a different reading of the evidence than the table used.
    rule_hit: bool = False
    routing_confidence: float = 0.0
    #: The fuzzy match scores of this item's verified OCR spans, for the
    #: operating point of the span-match threshold.
    match_scores: tuple[float, ...] = ()
    #: The tier the deterministic rows produced, before any downgrade. In
    #: log-only mode it must equal ``actual_tier`` for every item, and the
    #: anomaly section checks exactly that rather than asserting it.
    pre_downgrade_tier: int = 3
    channel: str = ""
    #: What the shadow scorer made of this item (part 09). ``anomaly_score`` is
    #: None when no scorer is configured OR when scoring degraded, and the two
    #: are told apart by ``anomaly_degraded``.
    anomaly_score: float | None = None
    anomaly_flagged: bool = False
    anomaly_reasons: tuple[AnomalyReason, ...] = ()
    anomaly_contributions: dict[str, float] = field(default_factory=dict)
    anomaly_readings: tuple[float, float] | None = None
    anomaly_mean_abs_contribution: float = 0.0
    anomaly_degraded: bool = False
    anomaly_degradation: str | None = None

    @property
    def item_shape(self) -> str:
        """form, born_digital or ocr: what KIND of item this is.

        Computed from the envelope's parts rather than from the item id, for
        the reason ``is_text_item`` already gives: a naming convention is a
        property of whoever typed the id.
        """
        if not self.source_types:
            return "form"
        return "ocr" if "ocr" in self.source_types else "born_digital"

    @property
    def is_text_item(self) -> bool:
        """Whether this item carried free text at all.

        The structured subset is defined as its complement, which is why this
        is computed from the ENVELOPE rather than from the item id: "an item
        with no prose" is a property of the item, and a naming convention is a
        property of whoever typed the id.
        """
        return bool(self.source_types)

    @property
    def routing_correct(self) -> bool:
        return self.actual_unit_id == self.expected_unit_id

    @property
    def derivation_labelled(self) -> bool:
        """Whether this item carries derivation ground truth at all."""
        return self.expected_derivation_source is not None

    @property
    def derivation_correct(self) -> bool:
        """Right procedure AND right route to it. Both halves count."""
        return (
            self.derivation_labelled
            and self.actual_derivation_source == self.expected_derivation_source
            and self.actual_derived_procedure_id == self.expected_derived_procedure_id
        )

    @property
    def tier_correct(self) -> bool:
        return self.actual_tier == self.expected_tier

    @property
    def false_clear(self) -> bool:
        """Gold says oversight is needed, the system cleared it anyway."""
        return self.expected_tier > 1 and self.actual_tier == 1

    @property
    def false_flag(self) -> bool:
        """Gold says tier 1, the system asked for oversight."""
        return self.expected_tier == 1 and self.actual_tier > 1

    @property
    def gaps_correct(self) -> bool:
        return sorted(self.actual_gaps) == sorted(self.expected_gaps)

    @property
    def gap_counts(self) -> tuple[int, int, int]:
        """(true positives, false positives, false negatives) for gaps."""
        expected = set(self.expected_gaps)
        actual = set(self.actual_gaps)
        return (
            len(actual & expected),
            len(actual - expected),
            len(expected - actual),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "procedure_id": self.procedure_id,
            "expected_unit_id": self.expected_unit_id,
            "actual_unit_id": self.actual_unit_id,
            "expected_tier": self.expected_tier,
            "actual_tier": self.actual_tier,
            "expected_gaps": self.expected_gaps,
            "actual_gaps": self.actual_gaps,
            "reason_kinds": self.reason_kinds,
            "routing_correct": self.routing_correct,
            "tier_correct": self.tier_correct,
            "false_clear": self.false_clear,
            "false_flag": self.false_flag,
            "gaps_correct": self.gaps_correct,
            "anomaly_expected": self.anomaly_expected,
            "known_divergence": self.known_divergence,
            "paraphrase": self.paraphrase,
            "expected_derivation_source": self.expected_derivation_source,
            "actual_derivation_source": self.actual_derivation_source,
            "expected_derived_procedure_id": self.expected_derived_procedure_id,
            "actual_derived_procedure_id": self.actual_derived_procedure_id,
            "derivation_correct": self.derivation_correct,
            "source_types": list(self.source_types),
            "item_shape": self.item_shape,
            "channel": self.channel,
            "pre_downgrade_tier": self.pre_downgrade_tier,
            "anomaly": {
                "score": self.anomaly_score,
                "flagged": self.anomaly_flagged,
                "degraded": self.anomaly_degraded,
                "degradation": self.anomaly_degradation,
                "readings": list(self.anomaly_readings or ()),
                "contributions": dict(sorted(self.anomaly_contributions.items())),
                "reasons": [
                    {
                        "feature": reason.feature,
                        "observed": reason.observed,
                        "expected": reason.expected,
                        "contribution": reason.contribution,
                    }
                    for reason in self.anomaly_reasons
                ],
            },
            "span_verification": {
                "proposals": self.proposals,
                "verified": self.verified,
                "discarded": self.discarded,
                "failures": self.failures,
                "match_modes": self.match_modes,
            },
        }


@dataclass(frozen=True)
class EvalReport:
    """Aggregated metrics for one eval run."""

    generated_at: datetime
    gold_dir: str
    item_count: int
    routing_accuracy: float
    tier_accuracy: float
    false_clear_rate: float
    false_flag_rate: float
    gap_exact_match_rate: float
    schema_version: str
    decision_table_version: str
    rules_version: str
    taxonomy_version: str
    thresholds_version: str
    scorer_mode: str
    items: list[ItemResult]
    gap_precision: float = 0.0
    gap_recall: float = 0.0
    gap_f1: float = 0.0
    by_procedure: dict[str, dict[str, Any]] = field(default_factory=dict)
    anomalous: dict[str, Any] = field(default_factory=dict)
    paraphrase_counts: dict[str, int] = field(default_factory=dict)
    procedure_derivation: dict[str, Any] = field(default_factory=dict)
    redaction: dict[str, Any] = field(default_factory=dict)
    span_verification: dict[str, Any] = field(default_factory=dict)
    structured_subset: dict[str, Any] = field(default_factory=dict)
    thresholds_review: dict[str, Any] = field(default_factory=dict)
    #: Part 10's queue census: how the items distribute over the units, as
    #: counts. No ages and no P-6 rates - see ``queue_census``.
    review: dict[str, Any] = field(default_factory=dict)
    classifier: dict[str, Any] = field(default_factory=dict)
    notifications: dict[str, Any] = field(default_factory=dict)
    drafting: dict[str, Any] = field(default_factory=dict)
    anomaly: dict[str, Any] = field(default_factory=dict)
    bias: dict[str, Any] = field(default_factory=dict)

    @property
    def anomaly_reasons_gate_passed(self) -> bool:
        """The part-09 gate: no flag without a readable feature-level reason.

        ADR-004 has said since part 01 that a flag without readable reasons
        never ships. Everything else about the scorer is reported and never
        gated - the score distribution, the recall, the bias skew - because
        gating a quality number creates pressure to tune it. This one is
        different: it is not a quality number, it is the promise.
        """
        return reasons_gate_passed(self.anomaly)

    @property
    def gate_passed(self) -> bool:
        """The non-negotiable gate: no false clear, ever."""
        return self.false_clear_rate == 0.0

    @property
    def redaction_gate_passed(self) -> bool:
        """The part-04 gate: full recall on the deterministic kinds.

        True when the redaction section is absent, because the golden set is
        optional infrastructure and the triage gates must not start failing
        because a corpus directory moved.
        """
        if not self.redaction:
            return True
        return bool(self.redaction.get("deterministic_gate_passed", True)) and bool(
            self.redaction.get("ner_gate_passed", True)
        )

    @property
    def structured_subset_gate_passed(self) -> bool:
        """The regression identity: sealing and the text path moved nothing.

        The items with no prose in them are the frozen set the previous part
        gated on, unchanged and byte-identical. They have to score exactly what
        they scored then - not "about the same", exactly - because every
        difference would be a change the text path caused to items that have no
        text in them. True when there is no structured subset at all, because a
        corpus of nothing but letters has no such claim to make.
        """
        if not self.structured_subset:
            return True
        return bool(self.structured_subset.get("invariant_held", True))

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "gold_dir": self.gold_dir,
            "item_count": self.item_count,
            "routing_accuracy": self.routing_accuracy,
            "tier_accuracy": self.tier_accuracy,
            "false_clear_rate": self.false_clear_rate,
            "false_flag_rate": self.false_flag_rate,
            "gap_exact_match_rate": self.gap_exact_match_rate,
            "gap_precision": self.gap_precision,
            "gap_recall": self.gap_recall,
            "gap_f1": self.gap_f1,
            "by_procedure": self.by_procedure,
            "procedure_derivation": self.procedure_derivation,
            "span_verification": self.span_verification,
            "structured_subset": self.structured_subset,
            "thresholds_review": self.thresholds_review,
            "classifier": self.classifier,
            "notifications": self.notifications,
            "drafting": self.drafting,
            "anomaly": self.anomaly,
            "review": self.review,
            "bias": self.bias,
            "redaction": self.redaction,
            "anomalous": self.anomalous,
            "paraphrase_counts": self.paraphrase_counts,
            "gate_passed": self.gate_passed,
            "versions": {
                "schema": self.schema_version,
                "decision_table": self.decision_table_version,
                "rules": self.rules_version,
                "taxonomy": self.taxonomy_version,
                "thresholds": self.thresholds_version,
            },
            "scorer_mode": self.scorer_mode,
            "items": [item.to_dict() for item in self.items],
        }

    def write(self, path: Path) -> Path:
        """Write the report as JSON, creating the directory if needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    def _derivation_accuracy(self) -> str:
        """Formatted accuracy, or a dash when nothing carries ground truth."""
        value = self.procedure_derivation.get("accuracy")
        return f"{value:.3f}" if isinstance(value, float) else "n/a  "

    def _derivation_by_shape(self) -> str:
        """Form items and letter items, side by side."""
        shapes = self.procedure_derivation.get("by_shape", {})
        if not isinstance(shapes, dict) or not shapes:
            return "{}"
        return (
            "{"
            + ", ".join(
                f"{shape} {metrics['labelled_items']}/{metrics['accuracy']:.3f}"
                for shape, metrics in shapes.items()
            )
            + "}"
        )

    def _redaction_recall(self) -> str:
        """Formatted deterministic recall, or a dash when nothing was measured."""
        value = self.redaction.get("deterministic_recall")
        return f"{value:.3f}" if isinstance(value, float) else "n/a  "

    def _ner_state(self) -> str:
        """Whether the number above was measured with the optional extra."""
        return "installed" if self.redaction.get("ner_installed") else "not installed"

    def _span_line(self) -> str:
        """Verified/discarded over the text items, and how they were matched."""
        if not self.span_verification.get("text_items"):
            return "n/a    (no item in this set carries free text)"
        section = self.span_verification
        by_source = ", ".join(
            f"{name} {counts['verified']}/{counts['proposals']}"
            for name, counts in section["by_source_type"].items()
        )
        return (
            f"{section['verified_rate']:.3f}"
            f"  ({section['verified']}/{section['proposals']} spans verified, "
            f"{section['discarded']} discarded, over {section['text_items']} "
            f"text items; {by_source})"
        )

    def _subset_line(self) -> str:
        """Whether the items without prose still score what they always did."""
        if not self.structured_subset:
            return "n/a    (every item in this set carries free text)"
        section = self.structured_subset
        state = "HELD" if section.get("invariant_held") else "BROKEN"
        return (
            f"{state}  ({section['item_count']} items, routing "
            f"{section['routing_accuracy']:.3f}, tier "
            f"{section['tier_accuracy']:.3f}, false clear "
            f"{section['false_clear_rate']:.3f}, derivation "
            f"{section['derivation_accuracy']:.3f})"
        )

    def _thresholds_line(self) -> str:
        """How many numbers govern, how many are honest measurements, when next."""
        if not self.thresholds_review:
            return "n/a"
        section = self.thresholds_review
        total = len(section.get("thresholds", []))
        uncalibrated = section.get("uncalibrated_count", 0)
        due = section.get("review_due") or "not set"
        days = section.get("days_remaining")
        when = (
            "OVERDUE"
            if section.get("overdue")
            else (f"in {days} days" if isinstance(days, int) else "no date")
        )
        return (
            f"{total} thresholds, {uncalibrated} uncalibrated; "
            f"review due {due} ({when})"
        )

    def _classifier_line(self) -> str:
        """What the fallback classifier is, and what it was allowed to do."""
        if not self.classifier:
            return "n/a"
        section = self.classifier
        if not section.get("configured"):
            return "not configured"
        state = "ENABLED" if section.get("enabled") else "log-only"
        if not section.get("ran"):
            return (
                f"{state}, not run  ({section.get('addressable_items', 0)} items no "
                f"rule catches; extra "
                f"{'installed' if section.get('extra_installed') else 'not installed'})"
            )
        coverage = section.get("coverage", {})
        agreement = section.get("agreement", {})
        return (
            f"{state}, measured  (suggested for {coverage.get('suggested', 0)}/"
            f"{coverage.get('rule_less_items', 0)} rule-less items; agreement "
            f"{agreement.get('rate', 0.0):.3f} on {agreement.get('scorable_items', 0)} "
            f"labelled items)"
        )

    def _notifications_line(self) -> str:
        """How many applicants heard back, how fast, from which templates."""
        if not self.notifications:
            return "n/a"
        section = self.notifications
        if not section.get("configured"):
            return "not configured (this agency sends no applicant notifications)"
        received = section.get("by_trigger", {}).get("received", {})
        routed = section.get("by_trigger", {}).get("routed", {})
        return (
            f"{section.get('notification_count', 0)} sent to "
            f"{section.get('items_notified', 0)}/{section.get('item_count', 0)} "
            f"items (coverage {section.get('coverage', 0.0):.3f}); median latency "
            f"receipt {_ms(received.get('median_ms'))}, status "
            f"{_ms(routed.get('median_ms'))}; reported, never gated"
        )

    def _drafting_line(self) -> str:
        """How many letters were prepared, and whether every token resolved."""
        if not self.drafting:
            return "n/a"
        section = self.drafting
        if not section.get("configured"):
            return "not configured (this agency prepares no drafts)"
        kinds = section.get("by_kind", {})
        tokens = section.get("tokens", {})
        state = (
            "0 unresolved" if section.get("unresolved_tokens") == 0 else "UNRESOLVED"
        )
        return (
            f"{section.get('draft_count', 0)} drafts "
            f"({kinds.get('nachforderung', 0)} Nachforderung, "
            f"{kinds.get('prepared_decision', 0)} Entwurf; "
            f"{section.get('no_draft_items', 0)} items get none), "
            f"{tokens.get('resolved', 0)} tokens re-hydrated, {state}; "
            f"{section.get('blocked', 0)} blocked; reported, never gated"
        )

    def _anomaly_line(self) -> str:
        """What the shadow scorer marked, found and would have moved."""
        if not self.anomaly:
            return "n/a"
        section = self.anomaly
        if not section.get("configured"):
            return "not configured (this agency runs no shadow scorer)"
        flagged = section.get("flagged", {})
        expected = section.get("anomaly_expected", {})
        false_flags = section.get("false_flags", {})
        movement = section.get("tier_movement", {})
        return (
            f"{section.get('scorer_mode', '?')}, "
            f"{flagged.get('count', 0)}/{section.get('items_scored', 0)} flagged "
            f"at {section.get('threshold', {}).get('value')}; recall "
            f"{expected.get('recall', 0.0):.3f} on "
            f"{expected.get('count', 0)} labelled; false-flag rate "
            f"{false_flags.get('rate_on_tier1_eligible', 0.0):.3f} of budget "
            f"{false_flags.get('budget', 0.0)}; "
            f"{movement.get('would_downgrade', 0)} would move, "
            f"{movement.get('flag_without_tier_movement', 0)} already tier 3"
        )

    def _review_line(self) -> str:
        """The queue census, in one line. Counts only - see ``queue_census``."""
        if not self.review:
            return "not computed"
        units = self.review.get("by_unit", {})
        return (
            f"{self.review.get('open_items', 0)} open over {len(units)} queue(s); "
            f"{self.review.get('clearing_queue', 0)} unrouted (clearing, par. 16 "
            f"Abs. 2 SGB I), {self.review.get('widerspruch_frist_laeuft', 0)} "
            f"Widerspruch, {self.review.get('reha_par14_clock', 0)} par. 14 SGB IX, "
            f"{self.review.get('sampled_open', 0)} sampled; counts only, no ages "
            f"and no P-6 rates in a gold run"
        )

    def _bias_line(self) -> str:
        """The skew a human has to explain or act on (P-2)."""
        if not self.bias or not self.bias.get("configured"):
            return "n/a"
        parts = []
        for name, entry in sorted(self.bias.get("skew", {}).items()):
            ratio = entry.get("ratio")
            mark = "!" if entry.get("above_advisory") else " "
            parts.append(f"{name} {'n/a' if ratio is None else f'{ratio:.2f}'}{mark}")
        return "flag-rate skew " + ", ".join(parts) + "; reported, never gated"

    def summary(self) -> str:
        """One-screen human summary."""
        lines = [
            "EingangsLotse eval",
            f"  gold dir           {self.gold_dir}",
            f"  items              {self.item_count}",
            f"  routing accuracy   {self.routing_accuracy:.3f}",
            f"  tier accuracy      {self.tier_accuracy:.3f}",
            f"  false clear rate   {self.false_clear_rate:.3f}  (gate: 0.000)",
            f"  false flag rate    {self.false_flag_rate:.3f}",
            f"  gap exact match    {self.gap_exact_match_rate:.3f}",
            f"  completeness P/R   {self.gap_precision:.3f} / {self.gap_recall:.3f}"
            f"  (F1 {self.gap_f1:.3f})",
            f"  derivation acc     {self._derivation_accuracy()}"
            f"  ({self.procedure_derivation.get('labelled_items', 0)} labelled"
            f" items; by source "
            f"{self.procedure_derivation.get('accuracy_by_source', {})};"
            f" by shape {self._derivation_by_shape()})",
            f"  redaction recall   {self._redaction_recall()}"
            f"  (deterministic kinds; gate: 1.000; NER "
            f"{self._ner_state()})",
            f"  span verification  {self._span_line()}",
            f"  structured subset  {self._subset_line()}",
            f"  thresholds review  {self._thresholds_line()}",
            f"  classifier         {self._classifier_line()}",
            f"  notifications      {self._notifications_line()}",
            f"  drafting           {self._drafting_line()}",
            f"  anomaly scorer     {self._anomaly_line()}",
            f"  bias monitoring    {self._bias_line()}",
            f"  review queues      {self._review_line()}",
            f"  paraphrase         {self.paraphrase_counts}",
            f"  versions           schema={self.schema_version}"
            f" table={self.decision_table_version}"
            f" rules={self.rules_version}"
            f" taxonomy={self.taxonomy_version}"
            f" thresholds={self.thresholds_version}",
            f"  scorer mode        {self.scorer_mode}",
            "",
            "  per procedure      items  routing  tier   false clear",
        ]
        for procedure_id, metrics in self.by_procedure.items():
            lines.append(
                f"  {procedure_id:<18} {metrics['item_count']:<6}"
                f" {metrics['routing_accuracy']:.3f}   "
                f"{metrics['tier_accuracy']:.3f}  {metrics['false_clear_rate']:.3f}"
            )
        lines.extend(
            [
                "",
                "  anomalous subset   "
                f"{self.anomalous.get('item_count', 0)} items, tier agreement "
                f"{self.anomalous.get('tier_agreement', 0.0):.3f}"
                "  (rule-based labels; the scorer lands in part 06)",
                "",
                "  item                                     exp/act tier  unit",
            ]
        )
        for item in self.items:
            correct = item.tier_correct and item.routing_correct
            marker = "ok " if correct else ("DECL" if item.known_divergence else "DIFF")
            lines.append(
                f"  {marker} {item.item_id:<38} "
                f"{item.expected_tier}/{item.actual_tier}          "
                f"{item.actual_unit_id}"
            )
        lines.append("")
        lines.append(
            "  DECL = mismatch the corpus declares and explains "
            "(see known_divergence); it still counts against the metric."
        )
        lines.append(
            "  GATE PASSED (no false clear)"
            if self.gate_passed
            else "  GATE FAILED: false clear detected"
        )
        if self.redaction:
            lines.append(
                "  REDACTION GATE PASSED (deterministic recall 1.000)"
                if self.redaction_gate_passed
                else "  REDACTION GATE FAILED: a labelled identifier was not found"
            )
        if self.structured_subset:
            lines.append(
                "  STRUCTURED SUBSET UNCHANGED (the text path moved no form item)"
                if self.structured_subset_gate_passed
                else "  STRUCTURED SUBSET GATE FAILED: "
                + "; ".join(self.structured_subset.get("broken", []))
            )
        if self.anomaly.get("configured"):
            reasons = self.anomaly.get("reasons", {})
            lines.append(
                "  ANOMALY REASONS GATE PASSED (every flag carries a readable "
                "feature-level reason)"
                if self.anomaly_reasons_gate_passed
                else "  ANOMALY REASONS GATE FAILED: "
                + "; ".join(
                    list(reasons.get("flags_without_reasons", []))
                    + list(reasons.get("unreadable_reasons", []))
                )
            )
        return "\n".join(lines)


def load_corpus(gold_dir: Path) -> list[GoldItem]:
    """Load every submission JSON with a labels sidecar under ``gold_dir``."""
    if not gold_dir.is_dir():
        raise FileNotFoundError(f"gold directory not found: {gold_dir}")
    items: list[GoldItem] = []
    for path in sorted(gold_dir.rglob("*.json")):
        label_path = path.parent / (path.stem + LABEL_SUFFIX)
        if not label_path.is_file():
            raise FileNotFoundError(f"missing labels sidecar for {path}: {label_path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        labels = GoldLabels.model_validate(
            yaml.safe_load(label_path.read_text(encoding="utf-8"))
        )
        items.append(
            GoldItem(item_id=labels.item_id, payload=payload, labels=labels, path=path)
        )
    if not items:
        raise FileNotFoundError(f"no corpus items found under {gold_dir}")
    return items


def evaluate_corpus(
    items: Sequence[GoldItem],
    *,
    config: ConfigBundle | None = None,
    gold_dir: Path = DEFAULT_GOLD_DIR,
    embedder: Embedder | None = None,
    today: date | None = None,
) -> EvalReport:
    """Run every item through the pipeline and aggregate the metrics.

    ``embedder`` is opt-in and never passed by a gate: it fills the classifier
    section with measurements instead of with the classifier's configured state.
    ``today`` is the injectable clock the review-date notice reads; it is
    informational and touches no exit code.
    """
    bundle = config or load_config()
    now = today or datetime.now(UTC).date()
    results: list[ItemResult] = []
    latencies: list[LatencySample] = []
    drafted: list[DraftOutcome] = []
    # Part 10: the queue census reads the same journals the loop already
    # writes. Kept as folded states rather than as events so the harness holds
    # counts and ids, never a payload.
    review_states: list[ReviewState] = []
    notified_items = 0
    for item in items:
        journal = InMemoryJournalStore()
        # A fresh in-memory vault per item. Part 08 reads it back, once, at the
        # drafting step below - the only place in this harness that dereferences
        # it, and the only place in the project that dereferences it at all.
        vault = InMemoryVaultStore()
        outcome = run_pipeline(
            item.payload, config=bundle, journal=journal, vault=vault
        )
        # The notification worker, on the same journal the pipeline just wrote
        # (part 07). It changes no triage number - it reads the journal and
        # appends NOTIFIED events - and what it produces is measured below.
        case_id = outcome.envelope.case_id
        sent = notify_case(
            journal.read(case_id),
            config=bundle,
            journal=journal,
            outbox=InMemoryOutbox(),
        )
        notified_items += 1 if sent.count else 0
        latencies.extend(case_latencies(journal.read(case_id)))
        # Drafting, after the decision (part 08). Same shape: a fold over the
        # journal, appending DRAFTED events, changing no triage number. The
        # drafts go into a run-scoped store and are counted below; the letters
        # themselves never leave this loop, because they carry re-hydrated
        # identity data and an eval report may not.
        drafted.append(
            draft_case(
                journal.read(case_id),
                config=bundle,
                journal=journal,
                vault=vault,
                drafts=InMemoryDraftStore(),
                facts=facts_from(outcome.extractions),
            )
        )
        review_states.append(review_state(case_id, journal.read(case_id)))
        extraction = outcome.extraction
        stats = extraction.stats() if extraction is not None else {}
        results.append(
            ItemResult(
                item_id=item.item_id,
                expected_unit_id=item.labels.expected_unit_id,
                actual_unit_id=outcome.decision.routed_unit_id,
                expected_tier=item.labels.expected_tier,
                actual_tier=int(outcome.decision.tier),
                expected_gaps=[gap.requirement_id for gap in item.labels.expected_gaps],
                actual_gaps=[
                    gap.requirement_id for gap in outcome.evidence.completeness.gaps
                ],
                reason_kinds=[reason.kind.value for reason in outcome.decision.reasons],
                procedure_id=item.labels.procedure_id or UNKNOWN_PROCEDURE,
                anomaly_expected=item.labels.anomaly_expected,
                known_divergence=list(item.labels.known_divergence),
                paraphrase=item.labels.paraphrase,
                expected_derivation_source=item.labels.derivation_source,
                actual_derivation_source=outcome.derivation.source.value,
                expected_derived_procedure_id=item.labels.derived_procedure_id,
                actual_derived_procedure_id=outcome.derivation.procedure_id,
                source_types=tuple(
                    part.source_type.value
                    for part in outcome.envelope.parts
                    if part.redacted_text is not None
                ),
                proposals=int(stats.get("proposals", 0)),
                verified=int(stats.get("verified", 0)),
                discarded=int(stats.get("discarded", 0)),
                failures=dict(stats.get("failures", {})),
                match_modes=dict(
                    sorted(
                        Counter(
                            record.match_mode.value
                            for record in outcome.extractions.records
                        ).items()
                    )
                ),
                rule_hit=bool(outcome.routing.candidates),
                routing_confidence=max(
                    (
                        suggestion.confidence
                        for suggestion in admitted_routing(outcome.evidence)
                    ),
                    default=0.0,
                ),
                match_scores=tuple(
                    record.match_score
                    for record in outcome.extractions.records
                    if record.match_mode is MatchMode.FUZZY
                    and record.match_score is not None
                ),
                pre_downgrade_tier=int(outcome.decision.pre_downgrade_tier),
                channel=outcome.envelope.channel.value,
                **_anomaly_fields(outcome.scoring, bundle),
            )
        )
    precision, recall, f1 = gap_precision_recall(results)
    anomalous = [item for item in results if item.anomaly_expected]
    return EvalReport(
        generated_at=datetime.now(UTC),
        gold_dir=str(gold_dir),
        item_count=len(results),
        routing_accuracy=_rate(results, lambda item: item.routing_correct),
        tier_accuracy=_rate(results, lambda item: item.tier_correct),
        false_clear_rate=_rate(results, lambda item: item.false_clear),
        false_flag_rate=_rate(results, lambda item: item.false_flag),
        gap_exact_match_rate=_rate(results, lambda item: item.gaps_correct),
        schema_version=SCHEMA_VERSION,
        decision_table_version=bundle.decision_table.version,
        rules_version=bundle.routing.version,
        taxonomy_version=bundle.taxonomy.version,
        thresholds_version=bundle.risk.version,
        scorer_mode=bundle.risk.scorer_mode,
        items=results,
        gap_precision=precision,
        gap_recall=recall,
        gap_f1=f1,
        by_procedure=breakdown_by_procedure(results),
        procedure_derivation=derivation_metrics(results),
        anomalous={
            "item_count": len(anomalous),
            "tier_agreement": _rate(anomalous, lambda item: item.tier_correct),
            "false_clear_rate": _rate(anomalous, lambda item: item.false_clear),
        },
        paraphrase_counts=dict(
            sorted(Counter(item.paraphrase for item in results).items())
        ),
        span_verification=span_verification_metrics(results),
        structured_subset=structured_subset_metrics(results),
        thresholds_review=threshold_review(results, config=bundle, today=now),
        review=queue_census(ReviewIndex(states=review_states), config=bundle.queues),
        anomaly=anomaly_section(results, config=bundle),
        bias=bias_section(results, config=bundle),
        classifier=classifier_section(
            config=bundle,
            observations=(
                observe_corpus(items, config=bundle, embedder=embedder)
                if embedder is not None
                else None
            ),
            rule_less_item_ids=[item.item_id for item in results if not item.rule_hit],
            gold_dir=str(gold_dir),
            today=now,
        ),
        notifications=notification_metrics(
            latencies, config=bundle, item_count=len(results), notified=notified_items
        ),
        drafting=drafting_metrics(drafted, config=bundle, item_count=len(results)),
        redaction=redaction_metrics() or {},
    )


def _anomaly_fields(
    scoring: ScoringOutcome | None, config: ConfigBundle
) -> dict[str, Any]:
    """The shadow scorer's output for one item, flattened onto the result row.

    The scorer's own two readings are recomputed here rather than carried on
    the outcome, because the model is cached per config and the call is two
    binary searches: the eval wants to be able to say WHICH half of the score
    marked an item, and the pipeline has no business computing a number only a
    report reads.
    """
    if scoring is None:
        return {}
    evidence = scoring.evidence
    if evidence is None:
        return {
            "anomaly_degraded": scoring.degraded,
            "anomaly_degradation": scoring.degradation,
        }
    readings: tuple[float, float] | None = None
    scorer = _scorer(config)
    if scorer is not None and scoring.vector is not None:
        readings = scorer.model.readings(scoring.vector.values)
    return {
        "anomaly_score": evidence.score,
        "anomaly_flagged": evidence.flagged,
        "anomaly_reasons": tuple(evidence.reasons),
        "anomaly_contributions": {
            attribution.feature_id: attribution.contribution
            for attribution in scoring.attributions
        },
        "anomaly_readings": readings,
        "anomaly_mean_abs_contribution": scoring.mean_abs_contribution,
    }


def _scorer(config: ConfigBundle) -> Scorer | None:
    """The configured scorer, or None. Cached by the model layer, not here."""
    try:
        return scorer_from_config(config.scoring, config.scoring_dir)
    except Exception:  # a report may not fall over because a model will not load
        return None


def drafting_metrics(
    outcomes: Sequence[DraftOutcome], *, config: ConfigBundle, item_count: int
) -> dict[str, Any]:
    """What the drafting path prepared on this corpus (part 08, ruling 7).

    Reported, never gated, and the existing three gates are untouched. What the
    section is FOR is the second line of defence behind the round-trip property
    in ``tests/test_draft_rehydrate.py``: every rendered letter is scanned here
    for surviving placeholder syntax, over the whole corpus rather than over
    generated examples. The property is the gate; this is the corpus saying the
    same thing.

    ``no_draft_items`` is the number to read alongside the counts: tier 3 owes
    no draft by design, so a drafting rate below 1.000 is correct rather than a
    coverage problem, and the number makes that visible instead of implied.
    """
    if config.drafting is None:
        return {"configured": False}
    drafts = [record for outcome in outcomes for record in outcome.drafts]
    kinds = Counter(record.kind for record in drafts)
    resolved = sum(record.resolved_tokens for record in drafts)
    distinct = sum(record.distinct_tokens for record in drafts)
    token_kinds: Counter[str] = Counter()
    for record in drafts:
        token_kinds.update(record.token_kinds)
    with_identity = sum(1 for record in drafts if record.resolved_tokens)
    return {
        "configured": True,
        "version": config.drafting.version,
        "item_count": item_count,
        "draft_count": len(drafts),
        "no_draft_items": item_count - len({record.case_id for record in drafts}),
        "by_kind": dict(sorted(kinds.items())),
        "blocked": sum(len(outcome.blocked) for outcome in outcomes),
        "tokens": {
            "resolved": resolved,
            "distinct": distinct,
            "by_kind": dict(sorted(token_kinds.items())),
            "drafts_with_identity": with_identity,
            "mean_per_draft": _ratio(resolved, len(drafts)),
        },
        # The assertion, computed rather than asserted: how many rendered
        # letters still carry anything shaped like a placeholder. Anything but
        # zero means a draft went out with a token in it.
        "unresolved_tokens": sum(_placeholder_hits(record.body) for record in drafts)
        + sum(_placeholder_hits(record.subject) for record in drafts),
        "requirements_requested": sum(len(record.requirement_ids) for record in drafts),
        "amtsermittlung_softened": sum(
            len(record.amtsermittlung_ids) for record in drafts
        ),
        "rechtsfolgenhinweis": sum(
            1 for record in drafts if record.rechtsfolgenhinweis
        ),
        # Nothing was dispatched, and the report says so rather than leaving a
        # reader to assume it (part 08 has no dispatch path at all).
        "dispatched": 0,
    }


def _placeholder_hits(text: str) -> int:
    """Placeholders and placeholder-shaped imitations in a rendered letter."""
    return len(PLACEHOLDER_RE.findall(text)) + len(PLACEHOLDER_SHAPED_RE.findall(text))


def notification_metrics(
    samples: Sequence[LatencySample],
    *,
    config: ConfigBundle,
    item_count: int,
    notified: int,
) -> dict[str, Any]:
    """What the notification path did on this corpus (P-10, 07 slice).

    Reported, never gated, and for a reason worth stating: the latency here is
    the wall-clock distance between two journal writes on THIS machine, so it
    is a measurement of the run rather than a property of the system. What IS a
    property, and is asserted in ``tests/test_notify.py`` instead of here, is
    that every case owes exactly the notifications its events call for and that
    a replay owes none.

    The coverage number is the one to read: an item that produced no receipt
    would be an applicant who was told nothing, and that is visible here as a
    ratio below 1.000 rather than as silence.
    """
    notifications = config.notifications
    if notifications is None:
        return {"configured": False}
    return {
        "configured": True,
        "version": notifications.version,
        "item_count": item_count,
        "items_notified": notified,
        "coverage": _ratio(notified, item_count),
        **latency_section(samples),
    }


def gap_precision_recall(
    results: Sequence[ItemResult],
) -> tuple[float, float, float]:
    """Micro-averaged precision, recall and F1 over detected gaps.

    The unit of counting is one (item, requirement_id) pair: a gap the system
    reports that gold does not have is a false positive (a caseworker chases a
    requirement that was satisfied), a gap gold has that the system misses is a
    false negative (the applicant is never asked for it). Both directions cost
    real work, which is why neither is folded into an accuracy number.
    """
    true_positives = false_positives = false_negatives = 0
    for item in results:
        hits, misses, missed = item.gap_counts
        true_positives += hits
        false_positives += misses
        false_negatives += missed
    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, true_positives + false_negatives)
    f1 = _ratio(2 * precision * recall, precision + recall)
    return precision, recall, f1


def derivation_metrics(results: Sequence[ItemResult]) -> dict[str, Any]:
    """Procedure-derivation accuracy, overall and per declared source.

    Only items that carry derivation ground truth count. The part-01 sidecars
    do not, and silently scoring them as wrong would make the metric a
    statement about corpus age rather than about the engine.

    ``confusion`` records what the engine produced for each declared source, so
    a regression that answers "none" to everything is visible as a shape, not
    only as a number.

    ``by_shape`` splits form items from letter items, because "the procedure was
    read off a form" and "the procedure was read out of a sentence" are
    different achievements and part 05 is the first release where both exist. A
    derivation accuracy that stayed at 1.000 while the letters all failed would
    otherwise be invisible until the letters outnumbered the forms.
    """
    labelled = [item for item in results if item.derivation_labelled]
    by_source: dict[str, list[ItemResult]] = {}
    for item in labelled:
        by_source.setdefault(str(item.expected_derivation_source), []).append(item)
    confusion: dict[str, dict[str, int]] = {}
    for source, group in sorted(by_source.items()):
        confusion[source] = dict(
            sorted(
                Counter(str(item.actual_derivation_source) for item in group).items()
            )
        )
    return {
        "labelled_items": len(labelled),
        "unlabelled_items": len(results) - len(labelled),
        # None, not 0.0: a corpus with no derivation ground truth has nothing to
        # say about derivation, and "0.000" would read as "it got them all
        # wrong". That distinction matters on the superseded sets.
        "accuracy": (
            _rate(labelled, lambda item: item.derivation_correct) if labelled else None
        ),
        "accuracy_by_source": {
            source: round(_rate(group, lambda item: item.derivation_correct), 3)
            for source, group in sorted(by_source.items())
        },
        "items_by_source": {
            source: len(group) for source, group in sorted(by_source.items())
        },
        "confusion": confusion,
        "by_shape": {
            shape: {
                "labelled_items": len(group),
                "accuracy": _rate(group, lambda item: item.derivation_correct),
                "by_source": dict(
                    sorted(
                        Counter(
                            str(item.expected_derivation_source) for item in group
                        ).items()
                    )
                ),
            }
            for shape, group in (
                ("form", [item for item in labelled if not item.is_text_item]),
                ("letter", [item for item in labelled if item.is_text_item]),
            )
            if group
        },
        "mismatches": [
            {
                "item_id": item.item_id,
                "expected": [
                    item.expected_derivation_source,
                    item.expected_derived_procedure_id,
                ],
                "actual": [
                    item.actual_derivation_source,
                    item.actual_derived_procedure_id,
                ],
            }
            for item in labelled
            if not item.derivation_correct
        ],
    }


def breakdown_by_procedure(
    results: Sequence[ItemResult],
) -> dict[str, dict[str, Any]]:
    """Per-procedure metrics, in item order of first appearance."""
    grouped: dict[str, list[ItemResult]] = {}
    for item in results:
        grouped.setdefault(item.procedure_id, []).append(item)
    breakdown: dict[str, dict[str, Any]] = {}
    for procedure_id, group in sorted(grouped.items()):
        precision, recall, _ = gap_precision_recall(group)
        breakdown[procedure_id] = {
            "item_count": len(group),
            "routing_accuracy": _rate(group, lambda item: item.routing_correct),
            "tier_accuracy": _rate(group, lambda item: item.tier_correct),
            "false_clear_rate": _rate(group, lambda item: item.false_clear),
            "false_flag_rate": _rate(group, lambda item: item.false_flag),
            "gap_precision": precision,
            "gap_recall": recall,
        }
    return breakdown


def _rate(
    results: Sequence[ItemResult], predicate: Callable[[ItemResult], bool]
) -> float:
    if not results:
        return 0.0
    return sum(1 for item in results if predicate(item)) / len(results)


def _ms(value: object) -> str:
    """A millisecond figure, or a dash when nothing was measured."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return "n/a"
    return f"{float(value):.1f} ms"


def _ratio(numerator: float, denominator: float) -> float:
    """Ratio with the empty case defined as 1.0.

    No gaps predicted and none expected is perfect agreement, not a failure;
    reporting 0.0 there would make a clean corpus look broken.
    """
    if denominator == 0:
        return 1.0
    return numerator / denominator


def span_verification_metrics(results: Sequence[ItemResult]) -> dict[str, Any]:
    """What the double lock did, over the items that had prose in them (P-12).

    Reported, never gated. A verification rate is a quality number about
    extraction, and gating on it would create pressure to lower the threshold
    until the number looked good - which is the opposite of what the threshold
    is for. What IS gated is false-clear, and a discarded span pushes an item
    toward tier 3, so a collapse in this number shows up as caution rather than
    as a wrong answer.

    Split by source type because the two are matched by different rules: a
    born-digital span either stands at the offset or it does not, while an OCR
    span is accepted above a configured similarity. A discard rate that rose
    only on the scan channel would be a scanner problem; one that rose on both
    would be an extractor problem, and the split is what tells them apart.
    """
    text_items = [item for item in results if item.is_text_item]
    section: dict[str, Any] = {
        "text_items": len(text_items),
        "structured_items": len(results) - len(text_items),
        **_span_counts(text_items),
        "failures": _failure_histogram(text_items),
        "match_modes": _mode_histogram(results),
        "by_source_type": {
            source_type: _span_counts(
                [item for item in text_items if source_type in item.source_types]
            )
            for source_type in sorted(
                {source for item in text_items for source in item.source_types}
            )
        },
        "by_procedure": {
            procedure_id: _span_counts(group)
            for procedure_id, group in sorted(_grouped(text_items).items())
        },
    }
    return section


def structured_subset_metrics(results: Sequence[ItemResult]) -> dict[str, Any]:
    """The items with NO prose, scored on their own (the regression identity).

    These are the frozen items the previous part gated on, byte-identical in
    this set. Anything the text path changed about them would be a change to
    items that have no text in them, so the required values are exact rather
    than approximate: routing 1.000, tier 1.000, false clear 0.000, false flag
    0.000, derivation 1.000. ``broken`` names every one that moved, so a failure
    is a list of facts rather than a boolean.
    """
    subset = [item for item in results if not item.is_text_item]
    if not subset:
        return {}
    measured = {
        "routing_accuracy": _rate(subset, lambda item: item.routing_correct),
        "tier_accuracy": _rate(subset, lambda item: item.tier_correct),
        "false_clear_rate": _rate(subset, lambda item: item.false_clear),
        "false_flag_rate": _rate(subset, lambda item: item.false_flag),
        "derivation_accuracy": _rate(subset, lambda item: item.derivation_correct),
    }
    broken = [
        f"{name} is {value:.3f}, must be {STRUCTURED_INVARIANT[name]:.3f}"
        for name, value in measured.items()
        if value != STRUCTURED_INVARIANT[name]
    ]
    return {
        "item_count": len(subset),
        **measured,
        "required": dict(STRUCTURED_INVARIANT),
        "invariant_held": not broken,
        "broken": broken,
        "moved_items": [
            item.item_id
            for item in subset
            if not (item.routing_correct and item.tier_correct)
        ],
    }


def _span_counts(results: Sequence[ItemResult]) -> dict[str, Any]:
    proposals = sum(item.proposals for item in results)
    verified = sum(item.verified for item in results)
    discarded = sum(item.discarded for item in results)
    return {
        "items": len(results),
        "proposals": proposals,
        "verified": verified,
        "discarded": discarded,
        "verified_rate": _ratio(verified, proposals),
        "discard_rate": _ratio(discarded, proposals) if proposals else 0.0,
    }


def _failure_histogram(results: Sequence[ItemResult]) -> dict[str, int]:
    histogram: Counter[str] = Counter()
    for item in results:
        histogram.update(item.failures)
    return dict(sorted(histogram.items()))


def _mode_histogram(results: Sequence[ItemResult]) -> dict[str, int]:
    histogram: Counter[str] = Counter()
    for item in results:
        histogram.update(item.match_modes)
    return dict(sorted(histogram.items()))


def _grouped(results: Sequence[ItemResult]) -> dict[str, list[ItemResult]]:
    grouped: dict[str, list[ItemResult]] = {}
    for item in results:
        grouped.setdefault(item.procedure_id, []).append(item)
    return grouped
