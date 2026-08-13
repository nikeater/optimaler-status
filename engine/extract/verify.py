"""The double lock: a quote AND an offset, each checked on its own (P-8).

An extractor that hands back a value with a quote is easy to fool - a model that
invents both invents them consistently. An extractor that hands back a value, a
verbatim quote and a character offset has to be right about two things that were
not derived from each other, and the system checks them separately:

**Lock one - does the text at that offset say that?** ``normalized[offset :
offset + len(quote)]`` is compared with the quote. Born-digital text is compared
exactly; OCR text is compared with a bounded fuzzy ratio above a configured
threshold, because a scan that read ``rn`` as ``m`` has not lied about where the
Rentenart stands. The offset itself is never adjusted. Searching the
neighbourhood for a better window would turn a wrong offset into a right one and
collapse two locks into one, and "the model was nearly right" is not a property
this system is allowed to reward.

**Lock two - does that quote contain that value?** The proposed value must occur
in the quote, up to whitespace and case. A quote that does not contain its own
value is an extractor summarizing, and a summary is not a span.

Disagreement between the locks is a DISCARD, never a repair. The discarded
proposal increments ``ExtractionSet.discarded_count``, which the decision table
already reads as pressure toward tier 3 - the same lever the schema mapper pulls
when a payload path is missing. Nothing about this path can produce a value; the
worst it can do is produce fewer of them, and fewer values means more oversight.

Placeholders are values. A letter whose Versicherungsnummer was sealed at the
boundary literally says ``[[PII|VSNR|...]]``, so an extractor that proposes that
token is quoting the text correctly and its record is correct. The real value is
validated through the transient witness in the completeness checker (ADR-017);
nothing is ever unsealed to make a span match.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rapidfuzz import fuzz

from engine.config_loader import ExtractionConfig, MatchPolicy
from engine.extract.proposal import Proposal
from engine.textlayer import layer_part
from schemas.common import Span
from schemas.extraction import ExtractionRecord, MatchMode
from schemas.textlayer import TextLayer, TextLayerPart


class FailureKind(StrEnum):
    """Why a proposal did not become a record.

    Reported per item in the eval and journaled per case (P-12): a system whose
    extraction quietly degrades should show up as a shift in this histogram long
    before it shows up as a wrong tier.
    """

    EMPTY_VALUE = "empty_value"
    UNKNOWN_FIELD = "unknown_field"
    UNKNOWN_PART = "unknown_part"
    OFFSET_OUT_OF_RANGE = "offset_out_of_range"
    QUOTE_MISMATCH = "quote_mismatch"
    VALUE_NOT_IN_QUOTE = "value_not_in_quote"
    DUPLICATE_FIELD = "duplicate_field"


@dataclass(frozen=True)
class Verification:
    """One proposal after both locks."""

    proposal: Proposal
    record: ExtractionRecord | None = None
    failure: FailureKind | None = None
    score: float = 0.0

    @property
    def accepted(self) -> bool:
        """Whether this proposal became evidence."""
        return self.record is not None

    def describe(self) -> dict[str, object]:
        """Value-free description, for the EXTRACTED payload and the report."""
        return {
            **self.proposal.describe(),
            "accepted": self.accepted,
            "failure": None if self.failure is None else self.failure.value,
            "score": round(self.score, 4),
        }


def verify_proposal(
    proposal: Proposal,
    layer: TextLayer | None,
    *,
    config: ExtractionConfig,
    known_fields: frozenset[str] | None = None,
) -> Verification:
    """Run both locks over one proposal.

    ``known_fields`` None means "do not ask", which is what a unit test wants;
    the pipeline always passes a set, and an EMPTY set means no field is known
    and every proposal is discarded.
    """
    if not proposal.value.strip() or not proposal.quote.strip():
        return Verification(proposal, failure=FailureKind.EMPTY_VALUE)
    if known_fields is not None and proposal.field not in known_fields:
        # A field the derived procedure does not declare has nowhere to go: the
        # completeness checker would never look at it and a routing rule over
        # `extraction.*` would read a field id nobody configured.
        return Verification(proposal, failure=FailureKind.UNKNOWN_FIELD)
    part = layer_part(layer, proposal.part_id)
    if part is None:
        return Verification(proposal, failure=FailureKind.UNKNOWN_PART)
    text = part.normalized_text
    if proposal.offset < 0 or proposal.end > len(text):
        return Verification(proposal, failure=FailureKind.OFFSET_OUT_OF_RANGE)

    policy = config.policy_for(part.source_type)
    window = text[proposal.offset : proposal.end]
    score = match_score(window, proposal.quote)
    if score < policy.min_score:
        return Verification(proposal, failure=FailureKind.QUOTE_MISMATCH, score=score)
    if not value_in_quote(proposal.value, proposal.quote, policy):
        return Verification(
            proposal, failure=FailureKind.VALUE_NOT_IN_QUOTE, score=score
        )
    return Verification(proposal, record=_record(proposal, part, policy, score, config))


def verify_proposals(
    proposals: tuple[Proposal, ...],
    layer: TextLayer | None,
    *,
    config: ExtractionConfig,
    known_fields: frozenset[str] | None = None,
    taken_fields: frozenset[str] = frozenset(),
) -> tuple[Verification, ...]:
    """Verify a batch, keeping at most one record per field.

    ``taken_fields`` are fields a stronger extractor already filled - in
    practice the deterministic schema mapper. A structured payload path and a
    sentence can both claim to carry the Rentenart; when they do, the JSON key
    wins, because reading a key is not an inference. The text proposal is
    recorded as a DUPLICATE_FIELD discard rather than dropped silently, so the
    disagreement is visible in the failure histogram.

    Within one batch the first accepted proposal for a field wins. Extractors
    emit proposals in document order, so "the first place the letter says it" is
    the rule, which is what a reader would do.
    """
    seen = set(taken_fields)
    results: list[Verification] = []
    for proposal in proposals:
        if proposal.field in seen:
            results.append(Verification(proposal, failure=FailureKind.DUPLICATE_FIELD))
            continue
        outcome = verify_proposal(
            proposal, layer, config=config, known_fields=known_fields
        )
        if outcome.accepted:
            seen.add(proposal.field)
        results.append(outcome)
    return tuple(results)


def match_score(window: str, quote: str) -> float:
    """Similarity of the text at the offset and the quote, in ``[0, 1]``.

    Identity short-circuits to 1.0 so an exact policy never depends on a
    third-party ratio, and so the born-digital path stays a string comparison
    that anyone can read.
    """
    if window == quote:
        return 1.0
    if not window or not quote:
        return 0.0
    return float(fuzz.ratio(window, quote)) / 100.0


def value_in_quote(value: str, quote: str, policy: MatchPolicy) -> bool:
    """The second lock: does the quote actually carry the proposed value?

    Compared after collapsing whitespace and folding case, because a normalized
    quote may have joined a line break the sender's mail client inserted and
    because German capitalizes nouns wherever they fall in a sentence. Nothing
    else is normalized away: an accent, a digit and a hyphen all still have to
    be there, so ``17170459B012`` never matches ``1717O459BO12``.

    Under a fuzzy policy the containment test is fuzzy too, at the same
    threshold: it would be incoherent to accept an OCR-noisy quote in lock one
    and then demand a character-perfect value inside it.
    """
    needle = _fold(value)
    haystack = _fold(quote)
    if not needle:
        return False
    if needle in haystack:
        return True
    if policy.mode == "exact":
        return False
    return float(fuzz.partial_ratio(needle, haystack)) / 100.0 >= policy.min_score


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def _record(
    proposal: Proposal,
    part: TextLayerPart,
    policy: MatchPolicy,
    score: float,
    config: ExtractionConfig,
) -> ExtractionRecord:
    exact = policy.mode == "exact"
    return ExtractionRecord(
        field=proposal.field,
        value=proposal.value.strip(),
        span=Span(part_id=part.part_id, start=proposal.offset, end=proposal.end),
        match_mode=MatchMode.EXACT if exact else MatchMode.FUZZY,
        # The contract requires a score on a fuzzy record and forbids nothing on
        # an exact one; an exact record's score is 1.0 by definition and saying
        # so twice would invite the two numbers to disagree.
        match_score=None if exact else round(score, 4),
        confidence=(
            config.confidence.exact
            if exact
            else max(config.confidence.fuzzy_floor, round(score, 4))
        ),
        extractor_id=proposal.extractor_id,
    )
