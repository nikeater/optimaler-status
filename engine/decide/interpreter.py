"""The tier decision table interpreter: one pure function, four safety rails.

``decide`` is the only place in the system that assigns a tier. It reads
evidence and config and returns a record; it has no I/O, no clock dependency
that callers cannot control, and no way to reach the vault, a model, or the
journal.

The four rails, in the order they matter:

1. **First match wins, in doubt tier 3.** Rows evaluate top-down; a row
   qualifies only if every one of its conditions holds. A field that cannot be
   resolved makes its condition fail rather than raise, so missing evidence
   moves an item toward oversight, never away from it. No row matching means
   ``default_tier`` (3) with a DEFAULTED reason.
2. **The one-way valve.** Downgrades run only after the rows, read only anomaly
   fields, and are applied as ``max(tier, to_tier)``. Raising an anomaly score
   can therefore never lower a tier (ADR-004). ``pre_downgrade_tier`` always
   records the tier the deterministic rows produced.
3. **Log-only is the default.** Downgrades are evaluated whenever anomaly
   evidence is present, but applied only when
   ``AgencyRiskConfig.scorer_mode == "enforcing"``. In log-only mode the caller
   gets the full list of outcomes from :func:`evaluate_downgrades` and journals
   what *would* have fired; the record itself stays untouched, because a reason
   that claims a downgrade that did not happen would be a lie in the audit
   trail.
4. **Errors push toward tier 3.** Any exception inside evaluation produces a
   tier-3 record with an ERROR reason. The function never raises upward and can
   never return tier 1 through the error path.

A sixth rail arrived with part 09, and it is the only thing in this module that
raises a tier without reading any evidence at all: **the audit sample** (P-1,
par. 88 Abs. 5 Nr. 1 AO analog). A configured share of the items the rules
cleared to tier 1 or 2 goes to full human review because a hash said so. It is
valve-compatible by construction - ``max(tier, 3)`` can only add oversight -
and it is deterministic and recomputable, because a sample nobody can reproduce
is a story rather than an audit measure. The rate lives in the risk config
(0.0 today, so gold behaviour is unchanged); the salt is passed in.

Part 10 finished that rail with ADR-025: a drawn item's reason now carries
``ReasonKind.SAMPLED``, not the ``DOWNGRADED`` kind part 09 had to borrow. The
difference is not cosmetic. A consumer that renders "why is this in front of
me" by KIND would otherwise show a randomly audited case with the face of a
suspicious one, and the caseworker would start the review with exactly the bias
the draw exists not to carry. Journals written before the migration hold the old
shape, so :func:`is_audit_sample_reason` is what readers use rather than an
equality test on either field alone.

A fifth rail arrived with part 06, and it is the reason a classifier can exist
at all without moving a single frozen label: **not every routing suggestion is
allowed to govern a decision.** ``EvidenceRecord.routing`` is evidence, and
evidence is for the caseworker; which SOURCES of it may reach the table is a
policy, and it defaults to rules alone. A classifier suggestion therefore rides
the record and the journal in full, and the tier and the addressee are computed
as if it were not there - until an agency admits the source in config.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from engine.predicate import compare
from schemas import SCHEMA_VERSION
from schemas.anomaly import AnomalyEvidence
from schemas.common import Tier, VersionStamp
from schemas.config import (
    AgencyRiskConfig,
    DecisionRow,
    DecisionTable,
    DowngradeRule,
    ProcedureFlags,
    QualifyingCondition,
)
from schemas.decision import DecisionReason, DecisionRecord, ReasonKind
from schemas.evidence import EvidenceRecord, RoutingSource, RoutingSuggestion

ENFORCING = "enforcing"
DEFAULT_ROW_ID = "default"
ERROR_ROW_ID = "error"

#: Rule id every audit-sample reason carries. It identifies the DRAW RULE and
#: is stable across the ADR-025 migration; what changed in part 10 is the KIND
#: next to it, which is now :attr:`ReasonKind.SAMPLED` rather than the reused
#: ``DOWNGRADED``. A consumer that switches on the kind therefore gets "the
#: dice picked this" and "the scorer flagged this" apart for free, which is
#: what the review UI needs and what a string convention could not guarantee.
AUDIT_SAMPLE_RULE_ID = "audit_sample"

#: Tiers the audit sample may pull. Tier 3 is already in full review, so
#: sampling it would be a no-op that still wrote a reason.
SAMPLEABLE_TIERS = frozenset({Tier.CLEAR_AND_COMPLETE, Tier.INCOMPLETE_BUT_ROUTABLE})

#: Width of the sampling digest, in bytes. Eight gives a 64-bit draw, which is
#: far finer than any rate an agency will set and short enough to print.
SAMPLE_DIGEST_BYTES = 8

#: Routing sources a tier decision may be built on, unless a caller says
#: otherwise. Rules only: a rule is a sentence an agency wrote and a classifier
#: suggestion is a similarity, and the default has to be the one that is
#: auditable. Widening this set is what "enabling the classifier" means, and it
#: happens in exactly one place - the pipeline, from config.
ADMITTED_ROUTING_SOURCES: frozenset[RoutingSource] = frozenset({RoutingSource.RULE})

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class DowngradeOutcome:
    """What one downgrade rule did, and what it would have done."""

    rule_id: str
    to_tier: int
    fired: bool
    applied: bool
    detail: str


@dataclass(frozen=True)
class AuditSample:
    """One item's audit-sampling draw, and whether it was drawn.

    Every number a caseworker would need to redo the arithmetic by hand is
    here, and the salt deliberately is not: the salt is in the config file, the
    draw is in the journal, and the two together reproduce the decision.
    """

    case_id: str
    rate: float
    draw: float
    sampled: bool


def audit_sample_draw(case_id: str, salt: str) -> float:
    """The item's position in [0, 1), from a salted hash of its case id.

    ``blake2b(case_id, key=salt)``, the first eight bytes read big-endian and
    divided by 2**64. Chosen so a caseworker can recompute it in one line::

        python -c "import hashlib;print(int.from_bytes(hashlib.blake2b(
            b'<case_id>', key=b'<salt>', digest_size=8).digest(),'big')/2**64)"

    Deterministic, uniform, and independent of anything about the person: it
    reads the case id, which is derived from the submission id, and nothing
    else. That is what makes this a random sample rather than a second risk
    rule wearing a hash.

    Raises ``ValueError`` for a salt over 64 bytes, which is blake2b's key
    limit. The scoring config refuses one at load time for that exact reason,
    so this path is only reachable from a hand-built config - and there it
    surfaces through ``decide``'s error rail as a tier-3 record, which is the
    right direction to fail in.
    """
    digest = hashlib.blake2b(
        case_id.encode("utf-8"),
        key=salt.encode("utf-8"),
        digest_size=SAMPLE_DIGEST_BYTES,
    ).digest()
    return int.from_bytes(digest, "big") / float(1 << (8 * SAMPLE_DIGEST_BYTES))


def evaluate_audit_sample(
    case_id: str, *, rate: float, salt: str | None
) -> AuditSample | None:
    """Whether this item is in the audit sample, or None when sampling is off.

    Off means off: a rate of 0.0 - the shipped value - returns None without
    computing anything, so the frozen gold set behaves exactly as it did before
    this function existed. A rate above 0 with no salt is also off, and
    deliberately silent here rather than an exception: this function sits
    inside the decision path, and the loader is where a half-configured
    sampling policy is refused.
    """
    if rate <= 0.0 or not salt:
        return None
    draw = audit_sample_draw(case_id, salt)
    return AuditSample(case_id=case_id, rate=rate, draw=draw, sampled=draw < rate)


def is_audit_sample_reason(kind: object, rule_id: object) -> bool:
    """Whether a journaled reason is a P-1 audit draw, in EITHER shape.

    The ADR-025 migration changed the kind an audit draw carries, and a journal
    is append-only: entries written before it hold ``DOWNGRADED`` next to the
    ``audit_sample`` rule id, entries written after it hold ``SAMPLED`` next to
    the same rule id. Both are the same fact and every reader in this
    repository - the review UI, the corrections export, the metrics - asks this
    function rather than testing one field, because a reader that only knew the
    new shape would silently re-classify last month's audit draws as scorer
    findings.

    Takes ``object`` on purpose: journal payloads are ``dict[str, object]`` and
    a projection may not raise on a malformed one (the part-01 discipline in
    ``engine/journal/projection.py``). An enum member and its string value are
    both accepted.
    """
    if _reason_kind_value(kind) == ReasonKind.SAMPLED.value:
        return True
    return (
        _reason_kind_value(kind) == ReasonKind.DOWNGRADED.value
        and rule_id == AUDIT_SAMPLE_RULE_ID
    )


def _reason_kind_value(kind: object) -> str | None:
    if isinstance(kind, ReasonKind):
        return kind.value
    return kind if isinstance(kind, str) else None


def admitted_routing(
    evidence: EvidenceRecord,
    sources: Collection[RoutingSource] = ADMITTED_ROUTING_SOURCES,
) -> Sequence[RoutingSuggestion]:
    """The routing evidence this decision is allowed to be built on.

    Everything else stays on the record for the caseworker and in the journal
    for the audit trail; it simply does not participate in a tier or in the
    choice of addressee. That distinction is the whole of "log-only" for the
    classifier, and it lives here rather than in the evidence plane so that no
    future caller can produce a decision that quietly used a source the agency
    has not admitted.
    """
    return [
        suggestion for suggestion in evidence.routing if suggestion.source in sources
    ]


def resolve_qualifying_fields(
    evidence: EvidenceRecord,
    flags: ProcedureFlags | None,
    clear_cut: bool,
    sources: Collection[RoutingSource] = ADMITTED_ROUTING_SOURCES,
) -> dict[str, object]:
    """Resolve every field a qualifying condition may reference.

    Keys match ``schemas.config.QUALIFYING_FIELDS`` exactly. A value of ``None``
    means "not resolvable" and makes any condition on it fail.
    """
    routing = admitted_routing(evidence, sources)
    confidences = [suggestion.confidence for suggestion in routing]
    return {
        "routing.confidence": max(confidences) if confidences else 0.0,
        "routing.rule_hit": any(
            suggestion.source is RoutingSource.RULE for suggestion in routing
        ),
        "completeness.verdict": evidence.completeness.verdict.value,
        "completeness.gap_count": len(evidence.completeness.gaps),
        "extraction.min_confidence": evidence.extraction_min_confidence,
        "extraction.discarded_count": evidence.extraction_discarded_count,
        "procedure.tier1_enabled": flags.tier1_enabled if flags is not None else False,
        "procedure.clear_cut": clear_cut,
    }


def resolve_anomaly_fields(anomaly: AnomalyEvidence) -> dict[str, object]:
    """Resolve the only two fields a downgrade condition may reference."""
    return {"anomaly.score": anomaly.score, "anomaly.flagged": anomaly.flagged}


def evaluate_downgrades(
    anomaly: AnomalyEvidence | None,
    table: DecisionTable,
    *,
    enforcing: bool,
) -> list[DowngradeOutcome]:
    """Evaluate every downgrade rule against the anomaly evidence.

    Always evaluates when anomaly evidence exists, so log-only mode can journal
    what would have happened; ``applied`` is True only in enforcing mode.
    """
    if anomaly is None:
        return []
    fields = resolve_anomaly_fields(anomaly)
    outcomes: list[DowngradeOutcome] = []
    for rule in table.downgrades:
        fired = all(
            compare(condition.op, fields.get(condition.field), condition.value)
            for condition in rule.when_all
        )
        outcomes.append(
            DowngradeOutcome(
                rule_id=rule.row_id,
                to_tier=int(rule.to_tier),
                fired=fired,
                applied=fired and enforcing,
                detail=_render_downgrade(rule, anomaly, enforcing=enforcing)
                if fired
                else "",
            )
        )
    return outcomes


def decide(
    evidence: EvidenceRecord,
    anomaly: AnomalyEvidence | None,
    table: DecisionTable,
    risk: AgencyRiskConfig,
    flags: ProcedureFlags | None = None,
    *,
    clear_cut: bool = False,
    versions: VersionStamp | None = None,
    now: datetime | None = None,
    routing_sources: Collection[RoutingSource] = ADMITTED_ROUTING_SOURCES,
    audit_salt: str | None = None,
) -> DecisionRecord:
    """Decide the tier for one item. Pure, total, and monotone in anomaly.

    ``routing_sources`` is the admission policy: only suggestions from these
    sources may qualify a row or become the routed unit. It defaults to rules
    alone, so a caller that knows nothing about the classifier decides exactly
    as it did before the classifier existed.

    ``audit_salt`` turns on P-1 sampling together with
    ``AgencyRiskConfig.audit_sample_rate``. Both must be set, and the shipped
    rate is 0.0, so a caller that passes neither decides exactly as it did
    before part 09.
    """
    created_at = now or datetime.now(UTC)
    stamp = _version_stamp(table, risk, versions)
    try:
        fields = resolve_qualifying_fields(evidence, flags, clear_cut, routing_sources)
        pre_downgrade_tier, reasons = _evaluate_rows(table, fields)
        tier = pre_downgrade_tier
        for outcome in evaluate_downgrades(
            anomaly, table, enforcing=risk.scorer_mode == ENFORCING
        ):
            if outcome.applied:
                tier = Tier(max(tier.value, outcome.to_tier))
                reasons.append(
                    DecisionReason(
                        kind=ReasonKind.DOWNGRADED,
                        rule_id=outcome.rule_id,
                        detail=outcome.detail,
                    )
                )
        sample = evaluate_audit_sample(
            evidence.case_id, rate=risk.audit_sample_rate, salt=audit_salt
        )
        if sample is not None and sample.sampled and tier in SAMPLEABLE_TIERS:
            tier = Tier(max(tier.value, Tier.FULL_HUMAN_REVIEW.value))
            reasons.append(
                DecisionReason(
                    kind=ReasonKind.SAMPLED,
                    rule_id=AUDIT_SAMPLE_RULE_ID,
                    detail=_render_audit_sample(sample),
                )
            )
        return DecisionRecord(
            envelope_id=evidence.envelope_id,
            case_id=evidence.case_id,
            tier=tier,
            pre_downgrade_tier=pre_downgrade_tier,
            routed_unit_id=_routed_unit(evidence, routing_sources),
            reasons=reasons,
            decision_table_version=table.version,
            risk_config_version=risk.version,
            created_at=created_at,
            versions=stamp,
        )
    except Exception as error:  # errors must never escape the decision path
        return DecisionRecord(
            envelope_id=evidence.envelope_id,
            case_id=evidence.case_id,
            tier=Tier.FULL_HUMAN_REVIEW,
            pre_downgrade_tier=Tier.FULL_HUMAN_REVIEW,
            routed_unit_id=None,
            reasons=[
                DecisionReason(
                    kind=ReasonKind.ERROR,
                    rule_id=ERROR_ROW_ID,
                    detail=(
                        "Fehler bei der Auswertung der Entscheidungstabelle "
                        f"({type(error).__name__}: {error}); Tier 3 zur "
                        "vollstaendigen Pruefung"
                    ),
                )
            ],
            decision_table_version=table.version,
            risk_config_version=risk.version,
            created_at=created_at,
            versions=stamp,
        )


def _evaluate_rows(
    table: DecisionTable, fields: dict[str, object]
) -> tuple[Tier, list[DecisionReason]]:
    reasons: list[DecisionReason] = []
    for row in table.rows:
        failed = [
            condition
            for condition in row.when_all
            if not _condition_holds(condition, fields)
        ]
        if not failed:
            reasons.append(
                DecisionReason(
                    kind=ReasonKind.QUALIFIED,
                    rule_id=row.row_id,
                    detail=_render_row(row, fields),
                )
            )
            return Tier(row.tier), reasons
        reasons.append(
            DecisionReason(
                kind=ReasonKind.FAILED,
                rule_id=row.row_id,
                detail=(
                    f"Zeile {row.row_id} (Tier {int(row.tier)}) nicht erfuellt: "
                    + "; ".join(
                        _render_condition(condition, fields) for condition in failed
                    )
                ),
            )
        )
    reasons.append(
        DecisionReason(
            kind=ReasonKind.DEFAULTED,
            rule_id=DEFAULT_ROW_ID,
            detail=(
                f"Keine Zeile der Tabelle {table.version} traf zu; "
                f"Standard-Tier {int(table.default_tier)} (im Zweifel Tier 3)"
            ),
        )
    )
    return Tier(table.default_tier), reasons


def _condition_holds(condition: QualifyingCondition, fields: dict[str, object]) -> bool:
    value = fields.get(condition.field)
    if value is None:
        # Unresolvable field: fail the condition instead of raising, so missing
        # evidence can only ever cost an item its qualification.
        return False
    return compare(condition.op, value, condition.value)


def _render_row(row: DecisionRow, fields: dict[str, object]) -> str:
    return f"Zeile {row.row_id} (Tier {int(row.tier)}) erfuellt: " + "; ".join(
        _render_condition(condition, fields) for condition in row.when_all
    )


def _render_condition(condition: QualifyingCondition, fields: dict[str, object]) -> str:
    actual = fields.get(condition.field, "<nicht aufloesbar>")
    return (
        f"{condition.field} {condition.op.value} {condition.value!r} (ist: {actual!r})"
    )


def _render_downgrade(
    rule: DowngradeRule, anomaly: AnomalyEvidence, *, enforcing: bool
) -> str:
    rendered = _render_template(
        rule.reason_template,
        {
            "score": f"{anomaly.score:.3f}",
            "threshold_ref": anomaly.threshold_ref,
            "flagged": str(anomaly.flagged).lower(),
            "reasons": _render_anomaly_reasons(anomaly),
        },
    )
    if enforcing:
        return rendered
    return f"[log_only, nicht angewendet] {rendered}"


def _render_audit_sample(sample: AuditSample) -> str:
    """The sentence a caseworker reads when the dice, not the model, picked them.

    It says out loud that nothing is wrong with the item, because the opposite
    reading is the failure mode: an applicant whose case was pulled at random
    and who is then treated as suspect has been harmed by a control that was
    supposed to protect them.
    """
    return (
        f"Zufallsstichprobe der Qualitaetssicherung (Ziehung {sample.draw:.6f} "
        f"unter der Quote {sample.rate:.4f}); Tier 3 zur vollstaendigen "
        f"Pruefung. Dies ist KEIN Auffaelligkeitsbefund: die Ziehung haengt "
        f"allein an der Vorgangskennung und sagt nichts ueber den Vorgang aus "
        f"(par. 88 Abs. 5 Nr. 1 AO analog)."
    )


def _render_anomaly_reasons(anomaly: AnomalyEvidence) -> str:
    if not anomaly.reasons:
        return "keine Merkmalsbegruendungen vorhanden"
    return "; ".join(
        f"{reason.feature}: beobachtet {reason.observed}, erwartet {reason.expected} "
        f"(Beitrag {reason.contribution:+.3f})"
        for reason in anomaly.reasons
    )


def _render_template(template: str, values: dict[str, str]) -> str:
    """Substitute ``{name}`` placeholders; unknown names stay literal.

    Deliberately not ``str.format``: a typo in agency-editable config must not
    be able to raise inside the decision path.
    """
    return _PLACEHOLDER.sub(
        lambda match: values.get(match.group(1), match.group(0)), template
    )


def _routed_unit(
    evidence: EvidenceRecord, sources: Collection[RoutingSource]
) -> str | None:
    """The unit this item is handed to, out of the admitted evidence only.

    A suggestion the agency has not admitted may not put a Vorgang in a queue,
    even when it is the only suggestion there is: an unrouted item is a known
    state with a known owner (the Zentraler Eingang looks at tier 3), and a
    routed one looks decided.
    """
    routing = admitted_routing(evidence, sources)
    if not routing:
        return None
    best = max(routing, key=lambda suggestion: suggestion.confidence)
    return best.unit_id


def _version_stamp(
    table: DecisionTable, risk: AgencyRiskConfig, versions: VersionStamp | None
) -> VersionStamp:
    base = versions or VersionStamp(schema_version=SCHEMA_VERSION)
    return base.model_copy(
        update={
            "decision_table_version": table.version,
            "thresholds_version": risk.version,
        }
    )
